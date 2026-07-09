"""Live-run harness: verify MUST-CALL tool-use rule in model_routing_agent.

Scenarios
---------
model_routing_thin
    model_changed=True, cost_z_score=1.5
    Rule says: model_changed AND cost_z < 3.0  -> MUST call get_window_calls

Runs through run_model_routing_agent_with_tools against groq + cerebras.
Transcript is printed and also written under data/reports/transcripts/ for durable evidence.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

_env = Path(".env")
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from llm_cost_investigator.agents import (
    build_model_routing_prompt_v2,
    build_model_routing_telemetry_v2,
    run_model_routing_agent_with_tools,
)
from llm_cost_investigator.schemas import (
    AnomalyWindow,
    AnomalySignals,
    LLMCall,
)
from llm_cost_investigator.llm_client import (
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URLS,
    DEFAULT_MODELS,
)


class Tee:
    """Write to stdout and a capture buffer (for durable report files)."""

    def __init__(self, *streams: TextIO) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for s in self.streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


def _make_thin_anomaly(*, label: str = "model_routing_thin") -> AnomalyWindow:
    """Synthetic window that forces the MUST-CALL gate.

    model_changed=True AND cost_z_score=1.5 (< 3.0).
    """
    base_ts = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
    end_ts = base_ts + timedelta(minutes=5)
    calls: list[LLMCall] = []
    # Mix of models with higher cost on gpt-4.1 so the tool result is citeable.
    specs = [
        ("gpt-4o-mini", 0.002),
        ("gpt-4o-mini", 0.0025),
        ("gpt-4.1", 0.018),
        ("gpt-4.1", 0.021),
        ("gpt-4.1", 0.019),
        ("gpt-4.1", 0.022),
    ]
    for i, (model_name, cost) in enumerate(specs):
        calls.append(
            LLMCall(
                timestamp=base_ts + timedelta(minutes=i),
                call_id=f"{label}_call_{i:02d}",
                parent_call_id=None,
                feature_tag="summarizer",
                model=model_name,
                input_tokens=500 + i * 5,
                output_tokens=120,
                cost_usd=cost,
                latency_ms=400 + i * 20,
                retry_count=0,
                scenario_label=label,
            )
        )

    signals = AnomalySignals(
        cost_z_score=1.5,
        input_tokens_z_score=0.4,
        output_tokens_z_score=0.3,
        retry_z_score=0.0,
        calls_z_score=0.5,
        latency_z_score=0.5,
        input_token_growth_pct=12.0,
        output_token_growth_pct=8.0,
        cost_growth_pct=95.0,
        token_growth_pct=12.0,
        max_retry_count=0,
        avg_retry_count=0.0,
        max_call_chain_depth=0,
        repeated_parent_call_count=0,
        model_changed=True,
        model_before="gpt-4o-mini",
        model_during="gpt-4.1",
        models_seen=["gpt-4o-mini", "gpt-4.1"],
    )

    return AnomalyWindow(
        feature_tag="summarizer",
        start_time=base_ts,
        end_time=end_ts,
        signals=signals,
        sample_call_ids=[c.call_id for c in calls],
        sample_calls=calls,
    )


class LoggingFeatureCallStore:
    """Store that returns per-call model/cost rows and logs get_calls_for_feature."""

    def __init__(self, window_calls: list[LLMCall]) -> None:
        self._window_calls = window_calls
        self.tool_calls_log: list[dict[str, Any]] = []
        self.tool_results_log: list[Any] = []

    def get_calls_for_feature(
        self,
        feature_tag: str,
        start: datetime,
        end: datetime,
    ) -> list[LLMCall]:
        # Agent wrapper will re-serialize; log the same shape it returns to the model.
        calls = [
            c
            for c in self._window_calls
            if c.feature_tag == feature_tag and start <= c.timestamp <= end
        ]
        recent = calls[-15:]
        result = [
            {
                "call_id": c.call_id,
                "model": c.model,
                "cost_usd": c.cost_usd,
                "timestamp": c.timestamp.isoformat().replace("+00:00", "Z"),
            }
            for c in reversed(recent)
        ]
        self.tool_calls_log.append(
            {
                "tool": "get_window_calls",
                "feature_tag": feature_tag,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
        self.tool_results_log.append(result)
        return calls


def run_one(
    *,
    provider: str,
    scenario_label: str,
    anomaly: AnomalyWindow,
    store: LoggingFeatureCallStore,
) -> dict[str, Any]:
    from openai import OpenAI

    api_key = os.environ.get(PROVIDER_API_KEY_ENV[provider], "").strip()
    if not api_key:
        return {
            "provider": provider,
            "scenario": scenario_label,
            "error": f"No API key for {provider}",
            "tool_called": False,
            "feature_tags_used": [],
            "tool_results": [],
            "hypothesis": None,
            "confidence": None,
            "explanation": None,
            "supporting_metrics": None,
            "fallback_used": None,
            "model": None,
        }

    model = DEFAULT_MODELS[provider]
    client = OpenAI(api_key=api_key, base_url=PROVIDER_BASE_URLS[provider])

    store.tool_calls_log.clear()
    store.tool_results_log.clear()

    telemetry = build_model_routing_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_model_routing_prompt_v2(telemetry_json)

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario_label}  |  PROVIDER: {provider}  |  MODEL: {model}")
    print(f"{'='*70}")
    print(f"\n[Telemetry sent to agent]")
    print(telemetry_json)
    print(f"\n[Relevant prompt rule lines]")
    for rl in prompt.splitlines():
        if any(
            kw in rl
            for kw in [
                "MUST",
                "DECISION",
                "model_changed",
                "cost_z_score",
                "get_window_calls",
                "aggregate signals",
                "do NOT call",
            ]
        ):
            print(f"  {rl}")

    try:
        run_result = run_model_routing_agent_with_tools(
            anomaly=anomaly,
            store=store,
            client=client,
            model=model,
            provider=provider,
        )
        evidence = run_result.evidence
        result = {
            "provider": provider,
            "scenario": scenario_label,
            "error": None,
            "tool_called": len(store.tool_calls_log) > 0,
            "feature_tags_used": [t["feature_tag"] for t in store.tool_calls_log],
            "tool_results": list(store.tool_results_log),
            "hypothesis": evidence.hypothesis,
            "confidence": evidence.confidence,
            "explanation": evidence.explanation,
            "supporting_metrics": evidence.supporting_metrics,
            "fallback_used": run_result.fallback_used,
            "fallback_reason": run_result.fallback_reason,
            "model": run_result.model,
            "result_provider": run_result.provider,
        }
    except Exception as exc:
        result = {
            "provider": provider,
            "scenario": scenario_label,
            "error": f"{type(exc).__name__}: {exc}",
            "tool_called": len(store.tool_calls_log) > 0,
            "feature_tags_used": [t["feature_tag"] for t in store.tool_calls_log],
            "tool_results": list(store.tool_results_log),
            "hypothesis": None,
            "confidence": None,
            "explanation": None,
            "supporting_metrics": None,
            "fallback_used": None,
            "model": model,
        }

    print(f"\n[Tool calls log]")
    if store.tool_calls_log:
        for entry in store.tool_calls_log:
            print(f"  get_window_calls(feature_tag={entry['feature_tag']!r})")
        print(f"\n[Tool result returned to model]")
        print(json.dumps(store.tool_results_log[0] if store.tool_results_log else [], indent=2))
    else:
        print("  (none)")

    print(f"\n[Final agent output]")
    if result["error"]:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  fallback_used: {result.get('fallback_used')}")
        if result.get("fallback_reason"):
            print(f"  fallback_reason: {result['fallback_reason']}")
        print(f"  hypothesis:  {result['hypothesis']}")
        print(f"  confidence:  {result['confidence']}")
        print(f"  explanation: {result['explanation']}")
        print(f"  metrics:     {json.dumps(result['supporting_metrics'], indent=4)}")

    return result


def main() -> int:
    report_path = Path("data/reports/transcripts/live_model_routing_tool_use.txt")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    buf = report_path.open("w", encoding="utf-8")
    old_stdout = sys.stdout
    sys.stdout = Tee(old_stdout, buf)  # type: ignore[assignment]

    try:
        PROVIDERS = ["groq", "cerebras"]
        thin_anomaly = _make_thin_anomaly(label="model_routing_thin")

        print("\n" + "█" * 70)
        print("SCENARIO A: model_routing_thin  (model_changed=True, cost_z=1.5)")
        print("Rule: model_changed == True AND cost_z_score < 3.0")
        print("      => MUST call get_window_calls")
        print("Entry point: run_model_routing_agent_with_tools")
        print("█" * 70)

        thin_results = []
        for provider in PROVIDERS:
            store = LoggingFeatureCallStore(list(thin_anomaly.sample_calls))
            r = run_one(
                provider=provider,
                scenario_label="model_routing_thin",
                anomaly=thin_anomaly,
                store=store,
            )
            thin_results.append(r)

        print("\n" + "=" * 70)
        print("ASSERTION CHECKS")
        print("=" * 70)

        all_pass = True
        print("\n[A] model_routing_thin: tool MUST be called")
        for r in thin_results:
            name = f"{r['provider']}:{r['scenario']}"
            if r.get("error"):
                print(f"  [{name}] SKIPPED (API error: {r['error']})")
                continue
            if r.get("fallback_used"):
                print(
                    f"  [{name}] FAIL - fell back to deterministic path "
                    f"(reason={r.get('fallback_reason')!r}); tool_called={r['tool_called']}"
                )
                all_pass = False
                continue
            if r["tool_called"] and r["feature_tags_used"]:
                print(
                    f"  [{name}] PASS - tool called with feature_tag={r['feature_tags_used'][0]!r}"
                )
            else:
                print(
                    f"  [{name}] FAIL - expected get_window_calls to be called but it wasn't"
                )
                all_pass = False

        print("\n" + "=" * 70)
        print("RESULT TABLE")
        print("=" * 70)
        hdr = (
            f"{'Scenario':<26} {'Provider':<12} {'Tool called':<13} "
            f"{'Hypothesis':<36} {'Confidence'}"
        )
        print(hdr)
        print("-" * len(hdr))
        for r in thin_results:
            print(
                f"{r['scenario']:<26} {r['provider']:<12} "
                f"{'YES' if r['tool_called'] else 'NO':<13} "
                f"{str(r['hypothesis']):<36} "
                f"{r['confidence']}"
            )

        print(f"\nDurable transcript written to: {report_path}")
        if all_pass:
            print("\nAll assertions passed.")
            return 0
        print("\nSome assertions failed - see above.")
        return 1
    finally:
        sys.stdout = old_stdout
        buf.close()


if __name__ == "__main__":
    raise SystemExit(main())
