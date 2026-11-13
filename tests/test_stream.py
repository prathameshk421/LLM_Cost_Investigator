"""Streaming ingest: OTel mapping, /v1/ingest, writer, demo + tick, SSE."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "web", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routes.stream import router  # noqa: E402
from llm_cost_investigator import ingest_service  # noqa: E402
from llm_cost_investigator.ingest_service import DemoGenerator, IngestService  # noqa: E402
from llm_cost_investigator.otel_map import to_llm_call  # noqa: E402
from llm_cost_investigator.warehouse import get_warehouse, reset_warehouse  # noqa: E402

NOW = datetime(2026, 11, 11, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def svc():
    reset_warehouse(":memory:")
    s = ingest_service.reset_service()
    yield s
    s.stop_background()
    ingest_service._service = None


@pytest.fixture()
def client(svc):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def _event(**kw):
    base = {"model": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 20, "feature_tag": "f"}
    base.update(kw)
    return base


# -- otel mapping ----------------------------------------------------------


def test_otel_canonical_and_generated_fields():
    call = to_llm_call(_event(), NOW)
    assert len(call.call_id) == 32
    assert call.timestamp == NOW
    assert call.cost_usd > 0  # priced
    assert call.retry_count == 0


def test_otel_genai_keys():
    call = to_llm_call(
        {
            "gen_ai.request.model": "gpt-4.1",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": "200",
            "llmci.feature_tag": "summarizer",
            "llmci.parent_call_id": "p1",
            "llmci.retry_count": 3,
            "duration_ms": 850,
            "timestamp": "2026-11-11T09:59:00Z",
            "call_id": "abc",
        },
        NOW,
    )
    assert (call.model, call.input_tokens, call.output_tokens) == ("gpt-4.1", 1000, 200)
    assert (call.feature_tag, call.parent_call_id, call.retry_count, call.latency_ms) == ("summarizer", "p1", 3, 850)
    assert call.timestamp == NOW - timedelta(minutes=1)
    assert call.call_id == "abc"


def test_otel_explicit_cost_nested_attributes_and_epoch():
    call = to_llm_call(
        {"attributes": {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2},
         "cost_usd": 0.5, "timestamp": 1_762_855_200_000},
        NOW,
    )
    assert call.cost_usd == 0.5
    assert call.timestamp.year == 2025


@pytest.mark.parametrize(
    "bad,msg",
    [
        ({"input_tokens": 1, "output_tokens": 1}, "model"),
        ({"model": "m", "output_tokens": 1}, "input_tokens"),
        (_event(input_tokens=-1), ">= 0"),
        (_event(input_tokens="abc"), "number"),
        (_event(timestamp="yesterday"), "ISO"),
        (_event(cost_usd=-1), "cost_usd"),
        ("nope", "object"),
    ],
)
def test_otel_errors(bad, msg):
    with pytest.raises(ValueError, match=msg):
        to_llm_call(bad, NOW)


# -- endpoint ----------------------------------------------------------------


def test_ingest_202_and_flush_lands_rows(client, svc):
    r = client.post("/v1/ingest", json={"events": [_event(), _event(model="gpt-4.1"), {"bad": 1}]})
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 2 and body["rejected"] == 1
    assert "event 2" in body["errors"][0]
    assert body["queue_depth"] == 2
    assert svc.flush() == 2
    assert get_warehouse().count() == 2


def test_ingest_validation_413_429(client, svc):
    assert client.post("/v1/ingest", json={}).status_code == 422
    assert client.post("/v1/ingest", json={"events": []}).status_code == 422
    assert client.post("/v1/ingest", json={"events": [_event()] * 5001}).status_code == 413
    svc.max_queue = 3
    assert client.post("/v1/ingest", json={"events": [_event()] * 2}).status_code == 202
    r = client.post("/v1/ingest", json={"events": [_event()] * 2})
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    assert svc.queue_depth() == 2  # all-or-nothing


def test_dedupe_same_call_id(client, svc):
    ev = _event(call_id="same")
    client.post("/v1/ingest", json={"events": [ev, ev]})
    client.post("/v1/ingest", json={"events": [ev]})
    svc.flush()
    assert get_warehouse().count() == 1


def test_writer_thread_flushes(client, svc):
    svc.start_background()
    svc.start_background()  # idempotent
    client.post("/v1/ingest", json={"events": [_event(call_id=f"w{i}") for i in range(50)]})
    deadline = time.time() + 5
    while get_warehouse().count() < 50 and time.time() < deadline:
        time.sleep(0.05)
    assert get_warehouse().count() == 50
    svc.stop_background()


# -- demo + detector tick ------------------------------------------------------


@pytest.mark.parametrize(
    "incident,expected,feature",
    [
        ("model_misroute", "expensive_model_misroute", "demo_summarizer"),
        ("retry_loop", "uncapped_retry_loop", "demo_support_reply"),
        ("context_bloat", "context_bloat_self_calling_agent", "demo_agent_reflection"),
    ],
)
def test_demo_incident_opens_with_right_hypothesis(svc, incident, expected, feature):
    got = []

    async def run():
        sub = svc.subscribe(asyncio.get_running_loop())
        demo = DemoGenerator(svc, rate=3, incident=incident, seed=7, backfill_minutes=75)
        demo.begin(NOW)
        assert get_warehouse().count() > 1000  # backfill landed
        demo.advance(NOW + timedelta(minutes=7))
        svc.flush()
        opened = await asyncio.to_thread(svc.tick_once)
        await asyncio.sleep(0.05)
        while not sub.queue.empty():
            got.append(sub.queue.get_nowait())
        return opened

    opened = asyncio.run(run())
    hits = [o for o in opened if o["feature_tag"] == feature]
    assert hits, f"no incident for {feature}: {opened}"
    assert hits[0]["hypothesis"] == expected
    assert not [o for o in opened if o["feature_tag"] != feature]
    assert any(t == "incident" for t, _ in got) and any(t == "ingest" for t, _ in got)
    rows = svc.list_incidents()
    assert rows[0]["hypothesis"] == expected and rows[0]["status"] == "open"

    # Ongoing incident: a later tick extends it, never duplicates it.
    demo2 = DemoGenerator(svc, rate=4, incident=incident, seed=8)
    demo2.incident_start = NOW
    demo2._cursor = NOW + timedelta(minutes=7)
    demo2.advance(NOW + timedelta(minutes=12))
    svc.flush()
    svc.tick_once()
    assert len(svc.list_incidents()) == 1


def test_clean_demo_opens_no_incident(svc):
    demo = DemoGenerator(svc, rate=3, incident=None, seed=3, backfill_minutes=75)
    demo.begin(NOW)
    demo.advance(NOW + timedelta(minutes=8))
    svc.flush()
    assert svc.tick_once() == []
    assert svc.list_incidents() == []


def test_incident_routes_and_diagnose(client, svc):
    demo = DemoGenerator(svc, rate=3, incident="model_misroute", seed=7, backfill_minutes=75)
    demo.begin(NOW)
    demo.advance(NOW + timedelta(minutes=7))
    svc.flush()
    svc.tick_once()
    rows = client.get("/api/incidents/live").json()
    assert rows and rows[0]["feature_tag"] == "demo_summarizer"
    r = client.post(f"/api/incidents/live/{rows[0]['incident_id']}/diagnose", json={"provider": "fallback"})
    assert r.status_code == 200, r.text
    assert r.json()["root_cause"]["hypothesis"] == "expensive_model_misroute"
    assert client.post("/api/incidents/live/nope/diagnose", json={}).status_code == 404


def test_demo_endpoint_start_stop(client, svc):
    r = client.post("/api/stream/demo", json={"action": "start", "rate": 1, "incident": "retry_loop"})
    assert r.status_code == 200
    assert r.json()["running"] is True and r.json()["incident"] == "retry_loop"
    assert get_warehouse().count() > 0
    r = client.post("/api/stream/demo", json={"action": "stop"})
    assert r.json()["running"] is False
    assert client.post("/api/stream/demo", json={"action": "start", "incident": "x"}).status_code == 422


def test_webhook_never_raises(svc, monkeypatch):
    monkeypatch.setenv("LLMCI_ALERT_WEBHOOK", "http://127.0.0.1:9/hook")
    svc._post_webhook("http://127.0.0.1:9/hook", "x")  # connection refused, swallowed


# -- SSE -----------------------------------------------------------------------


def test_sse_yields_stats_and_ingest(client, svc):
    client.post("/v1/ingest", json={"events": [_event()]})
    svc.flush()
    with client.stream("GET", "/api/stream?max_events=1") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in r.iter_lines() if ln]
    assert "event: stats" in lines
    data = json.loads(next(ln for ln in lines if ln.startswith("data: "))[6:])
    assert {"events_per_sec", "queue_depth", "total_calls", "spend_last_5m_usd", "ts"} <= set(data)
    assert data["total_calls"] == 1


def test_budget_event_published_after_flush(svc):
    from llm_cost_investigator import budgets

    budgets._last_status.clear()
    w = get_warehouse()
    w.cursor().execute("INSERT INTO budgets (budget_id, scope, scope_value, monthly_usd, alert_pct) VALUES ('b1','global',NULL,0.0001,0.8)")
    got = []

    async def run():
        sub = svc.subscribe(asyncio.get_running_loop())
        svc.enqueue([to_llm_call(_event(timestamp=NOW.isoformat(), input_tokens=1_000_000), NOW)])
        await asyncio.to_thread(svc.flush)
        await asyncio.sleep(0.05)
        while not sub.queue.empty():
            got.append(sub.queue.get_nowait())

    asyncio.run(run())
    kinds = [t for t, _ in got]
    assert "budget" in kinds
    assert dict(got)["budget"]["budget_id"] == "b1"


def test_demo_incident_found_on_top_of_seeded_history(svc):
    """Integration: demo traffic next to 60 days of seeded history.

    The demo used to share feature names with the seed, so every demo
    window looked like a model change and the injected retry storm never
    reached the investigated top 5.
    """
    from llm_cost_investigator.history_seed import seed_history

    seed_history(get_warehouse(), days=60, seed=1, incidents=True, reset=True, end=NOW)
    demo = DemoGenerator(svc, rate=3, incident="retry_loop", seed=7, backfill_minutes=75)
    demo.begin(NOW)
    demo.advance(NOW + timedelta(minutes=7))
    svc.flush()
    svc.tick_once()
    rows = svc.list_incidents()
    assert any(
        r["feature_tag"] == "demo_support_reply" and r["hypothesis"] == "uncapped_retry_loop"
        for r in rows
    ), rows
