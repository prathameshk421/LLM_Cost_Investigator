"""Scenario lab, live usage and benchmark endpoints."""

from __future__ import annotations

import contextlib
import io
import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.models.investigate import IngestSummary, InvestigateResponse
from api.services.live_investigate import investigate_calls_response
from llm_cost_investigator.benchmark import run_benchmark
from llm_cost_investigator.llm_client import (
    DEFAULT_MODELS,
    PROVIDER_API_KEY_ENV,
    _api_key_for,
)
from llm_cost_investigator.scenario_lab import (
    EXPECTED_ROOT_CAUSE,
    LAB_SCENARIOS,
    generate_lab_calls,
)
from llm_cost_investigator.usage import ledger

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["lab"])

BENCHMARK_FILE = Path("data/reports/benchmark.json")
MAX_BENCH_SEEDS = 40


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["retry_loop", "context_bloat", "model_misroute", "traffic_surge", "clean"]
    seed: int = Field(default=0, ge=0, le=1_000_000)
    noise: float = Field(default=0.08, ge=0.0, le=0.5)
    provider: Literal["fallback", "hf", "groq", "cerebras"] = "fallback"
    model: str | None = Field(default=None, max_length=120)


@router.get("/lab/scenarios")
def list_scenarios() -> list[dict[str, str]]:
    return [{"id": s, "expected": EXPECTED_ROOT_CAUSE[s]} for s in LAB_SCENARIOS]


@router.post("/simulate", response_model=InvestigateResponse)
def simulate(req: SimulateRequest) -> InvestigateResponse:
    lab = generate_lab_calls(req.scenario, seed=req.seed, noise=req.noise)
    logger.info(
        "simulate scenario=%s seed=%s provider=%s calls=%d",
        req.scenario, req.seed, req.provider, len(lab.calls),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        response = investigate_calls_response(
            lab.calls,
            ingest=IngestSummary(adapter="scenario_lab", accepted=len(lab.calls), dropped=0),
            label=f"lab_{req.scenario}_{req.seed}",
            source_name=f"scenario_lab:{req.scenario}:{req.seed}",
            provider=req.provider,
            model=req.model,
            force_fallback=req.provider == "fallback",
            max_anomalies=5,
            write_reports=False,
            extra_meta={
                "scenario": req.scenario,
                "seed": req.seed,
                "noise": req.noise,
                "expected": lab.expected,
                "target_feature": lab.target_feature,
            },
        )
    predicted = next(
        (
            (i.raw_report or {}).get("root_cause", {}).get("hypothesis")
            for i in response.incidents
            if (i.raw_report or {}).get("root_cause", {}).get("hypothesis") != "no_strong_signal"
        ),
        "no_strong_signal",
    )
    meta = dict(response.run_meta or {})
    meta["predicted"] = predicted
    meta["correct"] = predicted == lab.expected
    return response.model_copy(update={"run_meta": meta})


@router.get("/usage")
def usage() -> dict[str, Any]:
    """Live LLM spend in this API process, limits, and recent calls."""
    providers = {}
    for name in ("hf", "groq", "cerebras"):
        providers[name] = {
            "configured": bool(_api_key_for(name)),
            "key_env": PROVIDER_API_KEY_ENV[name],
            "default_model": DEFAULT_MODELS[name],
        }
    return {**ledger.snapshot(), "providers": providers}


@router.get("/benchmark")
def benchmark(
    seeds: int = Query(10, ge=1, le=MAX_BENCH_SEEDS),
    noise: float = Query(0.08, ge=0.0, le=0.5),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Offline accuracy benchmark (deterministic fallback agents, no spend).

    Returns the saved result unless ``refresh`` is set or the saved config
    differs from the request.
    """
    if not refresh and BENCHMARK_FILE.exists():
        try:
            saved = json.loads(BENCHMARK_FILE.read_text())
            cfg = saved.get("config", {})
            if (
                cfg.get("seeds") == seeds
                and cfg.get("noise") == noise
                and cfg.get("provider") == "fallback"
            ):
                return _trim(saved)
        except (OSError, ValueError):
            pass
    try:
        report = run_benchmark(seeds=seeds, noise=noise, provider="fallback")
    except Exception as exc:  # pragma: no cover - surfaced to UI
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {exc}") from exc
    return _trim(report)


def _trim(report: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in report.items() if k != "runs"}
    out["failures"] = list(report.get("failures", []))[:20]
    return out
