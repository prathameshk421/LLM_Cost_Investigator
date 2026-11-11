"""Seeded, noisy telemetry generator for evaluation and live demos.

``simulate_telemetry`` makes one fixed dataset per scenario. That is good for
replay tests, but it cannot show how the pipeline behaves on data it has not
seen. This module makes a new dataset for each ``seed``:

- baseline traffic with random jitter on tokens, latency and call volume
- a random start time (some runs cross an hour boundary)
- a random incident size (retry attempts, chain depth, premium model)
- extra "distractor" features that do not change

Two control scenarios check for false alarms:

- ``clean``          no incident at all, expected: no anomaly or no_strong_signal
- ``traffic_surge``  more calls of the same shape (real demand, not a bug),
                     expected: no_strong_signal

Costs come from the static pricing table, so a model change gives a real
price ratio and not a hand-picked number.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall

__all__ = [
    "LAB_SCENARIOS",
    "EXPECTED_ROOT_CAUSE",
    "LabRun",
    "generate_lab_calls",
]

EXPECTED_ROOT_CAUSE: dict[str, str] = {
    "retry_loop": "uncapped_retry_loop",
    "context_bloat": "context_bloat_self_calling_agent",
    "model_misroute": "expensive_model_misroute",
    "traffic_surge": "no_strong_signal",
    "clean": "no_strong_signal",
}
LAB_SCENARIOS = tuple(EXPECTED_ROOT_CAUSE)

# feature -> (model, input_tokens, output_tokens, latency_ms, calls_per_minute)
_FEATURES: dict[str, tuple[str, int, int, int, int]] = {
    "summarizer": ("gpt-4o-mini", 900, 180, 700, 2),
    "classifier": ("gpt-4o-mini", 350, 40, 350, 3),
    "agent_reflection": ("gpt-4o-mini", 700, 160, 650, 1),
    "support_reply": ("gpt-4o-mini", 1100, 260, 850, 2),
    "search_rerank": ("gpt-4.1-mini", 1500, 60, 500, 2),
}
_TARGET_FEATURE = {
    "retry_loop": "support_reply",
    "context_bloat": "agent_reflection",
    "model_misroute": "summarizer",
    "traffic_surge": "classifier",
    "clean": "classifier",
}
_PREMIUM_MODELS = ("gpt-4.1", "gpt-4o", "claude-3-5-sonnet")

BASELINE_MINUTES = 30


@dataclass(frozen=True)
class LabRun:
    scenario: str
    seed: int
    expected: str
    target_feature: str
    incident_start: datetime
    calls: list[LLMCall]


def _jitter(rng: random.Random, value: float, noise: float) -> float:
    return max(value * (1.0 + rng.gauss(0.0, noise)), 1.0)


def _call(
    *,
    run_id: str,
    seq: int,
    ts: datetime,
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    retry_count: int = 0,
    parent: str | None = None,
    call_id: str | None = None,
    label: str,
) -> LLMCall:
    cost, _ = estimate_cost_usd(model, input_tokens, output_tokens)
    # A retried call pays for every failed attempt before it.
    cost *= 1 + retry_count
    return LLMCall(
        timestamp=ts,
        call_id=call_id or f"{run_id}_{feature}_{seq:05d}",
        parent_call_id=parent,
        feature_tag=feature,
        model=model,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=round(cost, 8),
        latency_ms=int(latency_ms),
        retry_count=retry_count,
        scenario_label=label,
    )


def generate_lab_calls(scenario: str, seed: int = 0, noise: float = 0.08) -> LabRun:
    """Make one randomized dataset for ``scenario``.

    ``noise`` is the relative standard deviation of baseline jitter.
    The same (scenario, seed, noise) always gives the same calls.
    """
    if scenario not in EXPECTED_ROOT_CAUSE:
        raise ValueError(
            f"Unknown lab scenario {scenario!r}. Use one of: {', '.join(LAB_SCENARIOS)}"
        )
    noise = min(max(float(noise), 0.0), 0.5)
    rng = random.Random(f"{scenario}:{seed}")
    run_id = f"lab_{scenario}_{seed}"

    # Random start, minute-aligned. Some seeds cross an hour boundary.
    start = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc) + timedelta(
        minutes=rng.randrange(0, 180)
    )
    incident_start = start + timedelta(minutes=BASELINE_MINUTES + rng.randrange(0, 5))
    total_minutes = BASELINE_MINUTES + 15

    calls: list[LLMCall] = []
    seq = 0
    for minute in range(total_minutes):
        minute_start = start + timedelta(minutes=minute)
        for feature, (model, tin, tout, lat, cpm) in _FEATURES.items():
            n = max(1, round(_jitter(rng, cpm, noise)))
            for _ in range(n):
                seq += 1
                ts = minute_start + timedelta(seconds=rng.randrange(0, 60))
                calls.append(
                    _call(
                        run_id=run_id,
                        seq=seq,
                        ts=ts,
                        feature=feature,
                        model=model,
                        input_tokens=round(_jitter(rng, tin, noise)),
                        output_tokens=round(_jitter(rng, tout, noise)),
                        latency_ms=round(_jitter(rng, lat, noise)),
                        label=f"{scenario}_baseline",
                    )
                )

    target = _TARGET_FEATURE[scenario]
    model, tin, tout, lat, cpm = _FEATURES[target]

    if scenario == "retry_loop":
        # One or two parents retry without a cap.
        for p in range(rng.randint(1, 2)):
            parent = f"{run_id}_retry_parent_{p}"
            attempts = rng.randint(6, 12)
            for attempt in range(attempts):
                seq += 1
                calls.append(
                    _call(
                        run_id=run_id,
                        seq=seq,
                        ts=incident_start + timedelta(seconds=p * 40 + attempt * rng.randint(8, 20)),
                        feature=target,
                        model=model,
                        input_tokens=round(_jitter(rng, tin, noise / 2)),
                        output_tokens=round(_jitter(rng, tout, noise / 2)),
                        latency_ms=lat + attempt * rng.randint(300, 600),
                        retry_count=attempt,
                        parent=parent,
                        call_id=f"{parent}_attempt_{attempt:02d}",
                        label=scenario,
                    )
                )

    elif scenario == "context_bloat":
        depth = rng.randint(5, 8)
        growth = rng.uniform(0.7, 1.2)  # input grows by ~70-120% of base per hop
        previous: str | None = None
        for hop in range(depth):
            seq += 1
            call_id = f"{run_id}_reflect_{hop:02d}"
            calls.append(
                _call(
                    run_id=run_id,
                    seq=seq,
                    ts=incident_start + timedelta(seconds=hop * rng.randint(10, 25)),
                    feature=target,
                    model=model,
                    input_tokens=round(tin * (1 + growth * hop)),
                    output_tokens=round(_jitter(rng, tout, noise)),
                    latency_ms=lat + hop * rng.randint(150, 350),
                    parent=previous,
                    call_id=call_id,
                    label=scenario,
                )
            )
            previous = call_id

    elif scenario == "model_misroute":
        premium = rng.choice(_PREMIUM_MODELS)
        # Replace the target's traffic after incident start with the premium model.
        cut = incident_start
        rerouted: list[LLMCall] = []
        for c in calls:
            if c.feature_tag == target and c.timestamp >= cut:
                cost, _ = estimate_cost_usd(premium, c.input_tokens, c.output_tokens)
                rerouted.append(
                    c.model_copy(
                        update={
                            "model": premium,
                            "cost_usd": round(cost, 8),
                            "latency_ms": int(c.latency_ms * rng.uniform(1.1, 1.5)),
                            "scenario_label": scenario,
                        }
                    )
                )
            else:
                rerouted.append(c)
        calls = rerouted

    elif scenario == "traffic_surge":
        # Real demand growth: 3-5x the calls, same shape, same model.
        factor = rng.randint(3, 5)
        for minute in range(10):
            for _ in range(cpm * (factor - 1)):
                seq += 1
                calls.append(
                    _call(
                        run_id=run_id,
                        seq=seq,
                        ts=incident_start + timedelta(minutes=minute, seconds=rng.randrange(0, 60)),
                        feature=target,
                        model=model,
                        input_tokens=round(_jitter(rng, tin, noise)),
                        output_tokens=round(_jitter(rng, tout, noise)),
                        latency_ms=round(_jitter(rng, lat, noise)),
                        label=scenario,
                    )
                )

    calls.sort(key=lambda c: (c.timestamp, c.call_id))
    return LabRun(
        scenario=scenario,
        seed=seed,
        expected=EXPECTED_ROOT_CAUSE[scenario],
        target_feature=target,
        incident_start=incident_start,
        calls=calls,
    )
