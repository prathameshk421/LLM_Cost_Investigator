"""Deterministic scenario-based telemetry generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from llm_cost_investigator.schemas import LLMCall

BASE_START = datetime(2026, 7, 8, 9, 0, tzinfo=timezone.utc)
BASELINE_MINUTES = 30

VALID_SCENARIOS = {"retry_loop", "context_bloat", "model_misroute"}

FEATURES: dict[str, dict[str, Any]] = {
    "summarizer": {
        "model": "gpt-4o-mini",
        "input_tokens": 900,
        "output_tokens": 180,
        "cost_usd": 0.002,
        "latency_ms": 700,
    },
    "classifier": {
        "model": "gpt-4o-mini",
        "input_tokens": 350,
        "output_tokens": 40,
        "cost_usd": 0.0007,
        "latency_ms": 350,
    },
    "agent_reflection": {
        "model": "gpt-4o-mini",
        "input_tokens": 700,
        "output_tokens": 160,
        "cost_usd": 0.0018,
        "latency_ms": 650,
    },
    "support_reply": {
        "model": "gpt-4o-mini",
        "input_tokens": 1100,
        "output_tokens": 260,
        "cost_usd": 0.003,
        "latency_ms": 850,
    },
}


def _safe_id_part(value: str) -> str:
    return value.replace("-", "_")


def _make_call(
    *,
    scenario: str,
    feature_tag: str,
    timestamp: datetime,
    seq: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    retry_count: int,
    scenario_label: str,
    call_id: str | None = None,
    parent_call_id: str | None = None,
) -> LLMCall:
    """Build one validated synthetic LLM call."""
    safe_feature = _safe_id_part(feature_tag)
    default_call_id = f"{scenario}_{safe_feature}_{int(timestamp.timestamp())}_{seq:02d}"

    return LLMCall(
        timestamp=timestamp,
        call_id=call_id or default_call_id,
        parent_call_id=parent_call_id,
        feature_tag=feature_tag,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost_usd, 6),
        latency_ms=latency_ms,
        retry_count=retry_count,
        scenario_label=scenario_label,
    )


def _baseline_calls(scenario: str) -> list[LLMCall]:
    """Generate stable per-feature baseline traffic before the anomaly."""
    calls: list[LLMCall] = []

    for minute in range(BASELINE_MINUTES):
        timestamp = BASE_START + timedelta(minutes=minute)
        for feature_tag, config in FEATURES.items():
            calls.append(
                _make_call(
                    scenario=scenario,
                    feature_tag=feature_tag,
                    timestamp=timestamp,
                    seq=minute,
                    model=config["model"],
                    input_tokens=config["input_tokens"] + (minute % 3) * 8,
                    output_tokens=config["output_tokens"] + (minute % 2) * 5,
                    cost_usd=config["cost_usd"] * (1 + (minute % 2) * 0.03),
                    latency_ms=config["latency_ms"] + (minute % 4) * 15,
                    retry_count=0,
                    scenario_label=f"{scenario}_baseline",
                )
            )

    return calls


def _retry_loop_anomaly(scenario: str) -> list[LLMCall]:
    """Inject repeated failed support_reply calls from one parent."""
    calls: list[LLMCall] = []
    parent_call_id = f"{scenario}_support_reply_parent_retry"
    start = BASE_START + timedelta(minutes=30)

    for attempt in range(8):
        calls.append(
            _make_call(
                scenario=scenario,
                feature_tag="support_reply",
                timestamp=start + timedelta(seconds=attempt * 15),
                seq=attempt,
                call_id=f"{scenario}_support_reply_retry_{attempt:02d}",
                parent_call_id=parent_call_id,
                model="gpt-4o-mini",
                input_tokens=1120 + (attempt % 2) * 6,
                output_tokens=250,
                cost_usd=0.003 * (attempt + 1),
                latency_ms=900 + attempt * 450,
                retry_count=attempt,
                scenario_label="retry_loop",
            )
        )

    return calls


def _context_bloat_anomaly(scenario: str) -> list[LLMCall]:
    """Inject an expanding agent_reflection parent-child chain."""
    calls: list[LLMCall] = []
    previous_call_id: str | None = None
    start = BASE_START + timedelta(minutes=30)

    for depth in range(6):
        call_id = f"{scenario}_agent_reflection_chain_{depth:02d}"
        input_tokens = 700 + depth * 650

        calls.append(
            _make_call(
                scenario=scenario,
                feature_tag="agent_reflection",
                timestamp=start + timedelta(seconds=depth * 20),
                seq=depth,
                call_id=call_id,
                parent_call_id=previous_call_id,
                model="gpt-4o-mini",
                input_tokens=input_tokens,
                output_tokens=180 + depth * 5,
                cost_usd=0.0018 * (1 + depth),
                latency_ms=650 + depth * 250,
                retry_count=0,
                scenario_label="context_bloat",
            )
        )
        previous_call_id = call_id

    return calls


def _model_misroute_anomaly(scenario: str) -> list[LLMCall]:
    """Inject stable summarizer tokens routed to a more expensive model."""
    calls: list[LLMCall] = []
    start = BASE_START + timedelta(minutes=30)

    for seq in range(6):
        calls.append(
            _make_call(
                scenario=scenario,
                feature_tag="summarizer",
                timestamp=start + timedelta(seconds=seq * 30),
                seq=seq,
                call_id=f"{scenario}_summarizer_misroute_{seq:02d}",
                model="gpt-4.1",
                input_tokens=930 + seq * 5,
                output_tokens=185 + seq * 2,
                cost_usd=0.012,
                latency_ms=950 + seq * 25,
                retry_count=0,
                scenario_label="model_misroute",
            )
        )

    return calls


def generate_all_scenario_calls() -> list[LLMCall]:
    """Return telemetry for every labeled bug scenario."""
    calls: list[LLMCall] = []
    for scenario in sorted(VALID_SCENARIOS):
        calls.extend(generate_scenario_calls(scenario))
    return sorted(calls, key=lambda call: (call.timestamp, call.call_id))


def generate_scenario_calls(scenario: str) -> list[LLMCall]:
    """Return synthetic telemetry for a named scenario."""
    if scenario not in VALID_SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario!r}")

    calls = _baseline_calls(scenario)

    if scenario == "retry_loop":
        calls.extend(_retry_loop_anomaly(scenario))
    elif scenario == "context_bloat":
        calls.extend(_context_bloat_anomaly(scenario))
    elif scenario == "model_misroute":
        calls.extend(_model_misroute_anomaly(scenario))

    return sorted(calls, key=lambda call: (call.timestamp, call.call_id))
