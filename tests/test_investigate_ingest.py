"""Round-trip: simulate → serialize → ingest → investigate root causes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.ingest import IngestOptions, load_telemetry
from llm_cost_investigator.investigate import investigate_calls
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls

EXPECTED = {
    "retry_loop": "uncapped_retry_loop",
    "context_bloat": "context_bloat_self_calling_agent",
    "model_misroute": "expensive_model_misroute",
}


def _round_trip(scenario: str) -> str:
    calls = generate_scenario_calls(scenario)
    payload = "\n".join(
        json.dumps(c.model_dump(mode="json")) for c in calls
    ).encode()
    loaded, report = load_telemetry(payload, adapter="jsonl")
    assert report.accepted == len(calls), report.sample_dropped
    result = investigate_calls(
        loaded,
        label=scenario,
        provider="fallback",
        max_anomalies=1,
        write_reports=False,
        report_naming="label",
    )
    assert result.incidents, f"no incidents for {scenario}"
    return result.incidents[0].root_cause.hypothesis


def test_round_trip_all_scenarios() -> None:
    for scenario, expected in EXPECTED.items():
        got = _round_trip(scenario)
        assert got == expected, f"{scenario}: expected {expected}, got {got}"


def test_openai_round_trip_model_misroute() -> None:
    calls = generate_scenario_calls("model_misroute")
    openai_rows = []
    for c in calls:
        openai_rows.append(
            {
                "id": c.call_id,
                "created": c.timestamp.timestamp(),
                "model": c.model,
                "usage": {
                    "prompt_tokens": c.input_tokens,
                    "completion_tokens": c.output_tokens,
                },
                "cost_usd": c.cost_usd,
                "metadata": {
                    "feature_tag": c.feature_tag,
                    "parent_call_id": c.parent_call_id,
                    "retry_count": c.retry_count,
                    "latency_ms": c.latency_ms,
                },
            }
        )
    payload = json.dumps(openai_rows).encode()
    loaded, report = load_telemetry(payload, adapter="openai")
    assert report.accepted == len(calls)
    result = investigate_calls(
        loaded,
        label="model_misroute",
        provider="fallback",
        max_anomalies=1,
        write_reports=False,
    )
    assert result.incidents
    assert result.incidents[0].root_cause.hypothesis == "expensive_model_misroute"


def main() -> int:
    tests = [
        ("round_trip_all_scenarios", test_round_trip_all_scenarios),
        ("openai_round_trip_model_misroute", test_openai_round_trip_model_misroute),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print(f"{name} ... PASS")
        except Exception as exc:
            ok = False
            print(f"{name} ... FAIL: {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
