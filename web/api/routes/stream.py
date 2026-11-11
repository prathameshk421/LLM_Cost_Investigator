"""Live ingest, SSE stream, demo traffic and live incidents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from llm_cost_investigator.ingest_service import LOOKBACK, QueueFull, get_service
from llm_cost_investigator.otel_map import to_llm_call
from llm_cost_investigator.warehouse import get_warehouse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stream"])

MAX_EVENTS_PER_REQUEST = 5000
MAX_ERRORS_REPORTED = 20
STATS_EVERY_S = 2.0
KEEPALIVE_EVERY_S = 15.0
MAX_STREAM_S = 300.0


class IngestRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1)


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "stop"]
    rate: int = Field(default=20, ge=1, le=500)
    incident: Literal["model_misroute", "retry_loop", "context_bloat"] | None = None


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["hf", "fallback"] = "fallback"


@router.post("/v1/ingest", status_code=202)
def ingest(req: IngestRequest, source: str = Query("api", max_length=40)) -> Response:
    if len(req.events) > MAX_EVENTS_PER_REQUEST:
        raise HTTPException(413, f"Too many events: {len(req.events)} > {MAX_EVENTS_PER_REQUEST}")
    now = datetime.now(timezone.utc)
    calls, errors = [], []
    for i, event in enumerate(req.events):
        try:
            calls.append(to_llm_call(event, now))
        except ValueError as exc:
            errors.append(f"event {i}: {exc}")
    svc = get_service()
    try:
        svc.enqueue(calls, source=source)
    except QueueFull as exc:
        return JSONResponse(
            {"detail": f"ingest queue full ({exc}); retry later"},
            status_code=429,
            headers={"Retry-After": "1"},
        )
    return JSONResponse(
        {
            "accepted": len(calls),
            "rejected": len(errors),
            "errors": errors[:MAX_ERRORS_REPORTED],
            "queue_depth": svc.queue_depth(),
        },
        status_code=202,
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/api/stream")
async def stream(max_events: int | None = Query(None, ge=1, description="Close after N events (tests)")):
    svc = get_service()
    sub = svc.subscribe(asyncio.get_running_loop())

    async def gen():
        sent = 0
        next_stats = 0.0
        next_ka = time.monotonic() + KEEPALIVE_EVERY_S
        # End each stream after MAX_STREAM_S. EventSource reconnects on its
        # own (retry: 3000), and an endless stream would block a graceful
        # server shutdown, because uvicorn waits for open connections.
        close_at = time.monotonic() + MAX_STREAM_S
        try:
            yield "retry: 3000\n\n"
            while (max_events is None or sent < max_events) and time.monotonic() < close_at:
                now = time.monotonic()
                if now >= next_stats:
                    next_stats = now + STATS_EVERY_S
                    stats = await asyncio.to_thread(svc.stats)
                    yield _sse("stats", stats)
                    sent += 1
                    continue
                if now >= next_ka:
                    next_ka = now + KEEPALIVE_EVERY_S
                    yield ": keep-alive\n\n"
                    continue
                timeout = max(0.05, min(next_stats, next_ka) - now)
                try:
                    event, data = await asyncio.wait_for(sub.queue.get(), timeout)
                except asyncio.TimeoutError:
                    continue
                yield _sse(event, data)
                sent += 1
        finally:
            svc.unsubscribe(sub)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/api/stream/demo")
def demo(req: DemoRequest) -> dict[str, Any]:
    svc = get_service()
    if req.action == "stop":
        return svc.demo_stop()
    return svc.demo_start(rate=req.rate, incident=req.incident)


@router.get("/api/stream/demo")
def demo_state() -> dict[str, Any]:
    """Current demo traffic state, so a reloaded UI shows the right button."""
    return get_service().demo_state()


@router.get("/api/incidents/live")
def live_incidents(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return get_service().list_incidents(limit)


@router.post("/api/incidents/live/{incident_id}/diagnose")
def diagnose(incident_id: str, req: DiagnoseRequest | None = None) -> dict[str, Any]:
    """Re-run the agents on the stored window. LLM spend stays behind the usage ledger budget."""
    from api.models.investigate import IngestSummary
    from api.services.live_investigate import investigate_calls_response

    svc = get_service()
    inc = svc.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, f"Unknown incident {incident_id!r}")
    provider = (req.provider if req else "fallback")
    start = datetime.fromisoformat(inc["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(inc["end"].replace("Z", "+00:00"))
    # Baseline before the window is needed for the detector to see the break.
    calls = get_warehouse().calls_between(
        start - LOOKBACK, end + timedelta(seconds=1), feature_tag=inc["feature_tag"]
    )
    if not calls:
        raise HTTPException(409, "No calls stored for this incident window")
    response = investigate_calls_response(
        calls,
        ingest=IngestSummary(adapter="warehouse", accepted=len(calls), dropped=0),
        label=incident_id,
        source_name=f"warehouse:{incident_id}",
        provider=provider,
        force_fallback=provider == "fallback",
        max_anomalies=3,
        write_reports=False,
        extra_meta={"incident_id": incident_id},
    )
    if not response.incidents:
        raise HTTPException(409, "Detector found no anomaly in the stored window")

    def score(i) -> tuple[int, float]:
        rc = i.root_cause or {}
        return (rc.get("hypothesis") != "no_strong_signal", float(rc.get("confidence") or 0))

    best = max(response.incidents, key=score)
    rc = best.root_cause or {}
    raw = best.raw_report or {}
    if rc.get("hypothesis") and rc.get("hypothesis") != "no_strong_signal":
        svc.update_incident(incident_id, rc["hypothesis"], float(rc.get("confidence") or 0), json.dumps(raw))
    out = best.model_dump(mode="json")
    out["meta"] = {**(out.get("meta") or {}), "run_meta": response.run_meta, "incident_id": incident_id}
    return out
