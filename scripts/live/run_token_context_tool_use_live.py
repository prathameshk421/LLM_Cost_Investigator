"""Live-run harness: verify MUST-CALL tool-use rule in token_context_agent.

Scenarios
---------
token_context_thin
    input_tokens_z_score=1.5, max_call_chain_depth=3
    Rule says: z < 3.0 AND depth >= 2  -> MUST call get_call_chain

Runs through run_token_context_agent_with_tools against groq + cerebras.
Transcript is printed and also written under reports/ for durable evidence.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
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
    build_token_context_prompt_v2,
    build_token_context_telemetry_v2,
    run_token_context_agent_with_tools,
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


def _make_thin_anomaly(*, label: str = "token_context_thin") -> AnomalyWindow:
    """Synthetic window that forces the MUST-CALL gate.

    input_tokens_z_score=1.5 (< 3.0) AND max_call_chain_depth=3 (>= 2).
    """
    base_ts = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
    # Depth-3 parent chain available as sample_call_ids for the agent to pick.
    calls: list[LLMCall] = []
    parent: str | None = None
    for i in range(4):
        call_id = f"{label}_call_{i:02d}"
        calls.append(
            LLMCall(
                timestamp=base_ts,
                call_id=call_id,
                parent_call_id=parent,
                feature_tag="agent_reflection",
                model="gpt-4o-mini",
                input_tokens=800 + i * 400,
                output_tokens=120 + i * 10,
                cost_usd=0.001 * (i + 1),
                latency_ms=500 + i * 100,
                retry_count=0,
                scenario_label=label,
            )
        )
        parent = call_id

    signals = AnomalySignals(
        cost_z_score=1.8,
        input_tokens_z_score=1.5,
        output_tokens_z_score=0.4,
        retry_z_score=0.0,
        calls_z_score=0.5,
        latency_z_score=0.6,
        input_token_growth_pct=45.0,
        output_token_growth_pct=10.0,
        cost_growth_pct=55.0,
        token_growth_pct=45.0,
        max_retry_count=0,
        avg_retry_count=0.0,
        max_call_chain_depth=3,
        repeated_parent_call_count=0,
        model_changed=False,
        model_before="gpt-4o-mini",
        model_during="gpt-4o-mini",
        models_seen=["gpt-4o-mini"],
    )

    return AnomalyWindow(
        feature_tag="agent_reflection",
        start_time=base_ts,
        end_time=base_ts,
        signals=signals,
        sample_call_ids=[c.call_id for c in calls],
        sample_calls=calls,
    )


class LoggingCallChainStore:
    """Store that returns a growing-token chain and logs every get_call_chain hit."""

    def __init__(self) -> None:
        self.tool_calls_log: list[dict[str, Any]] = []
        self.tool_results_log: list[Any] = []

    def get_call_chain(self, call_id: str) -> list[LLMCall]:
        base_ts = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
        # Root-first chain with clearly growing input_tokens so the model can cite them.
        chain = [
            LLMCall(
                timestamp=base_ts,
                call_id="ctx_chain_00",
                parent_call_id=None,
                feature_tag="agent_reflection",
                model="gpt-4o-mini",
                input_tokens=800,
                output_tokens=100,
                cost_usd=0.001,
                latency_ms=500,
                retry_count=0,
                scenario_label="live_test",
            ),
            LLMCall(
                timestamp=base_ts,
                call_id="ctx_chain_01",
                parent_call_id="ctx_chain_00",
                feature_tag="agent_reflection",
                model="gpt-4o-mini",
                input_tokens=1600,
                output_tokens=120,
                cost_usd=0.002,
                latency_ms=650,
                retry_count=0,
                scenario_label="live_test",
            ),
            LLMCall(
                timestamp=base_ts,
                call_id="ctx_chain_02",
                parent_call_id="ctx_chain_01",
                feature_tag="agent_reflection",
                model="gpt-4o-mini",
                input_tokens=3200,
                output_tokens=140,
                cost_usd=0.004,
                latency_ms=800,
                retry_count=0,
                scenario_label="live_test",
            ),
            LLMCall(
                timestamp=base_ts,
                call_id="ctx_chain_03",
                parent_call_id="ctx_chain_02",
                feature_tag="agent_reflection",
                model="gpt-4o-mini",
                input_tokens=6400,
                output_tokens=160,
                cost_usd=0.008,
                latency_ms=950,
                retry_count=0,
                scenario_label="live_test",
            ),
        ]
        # Same shape the agent wrapper serializes for the model.
        result = [
            {
                "call_id": c.call_id,
                "parent_call_id": c.parent_call_id,
                "timestamp": c.timestamp.isoformat().replace("+00:00", "Z"),
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cost_usd": c.cost_usd,
            }
            for c in chain
        ]
        self.tool_calls_log.append({"tool": "get_call_chain", "call_id": call_id})
        self.tool_results_log.append(result)
        return chain


def run_one(
    *,
    provider: str,
    scenario_label: str,
    anomaly: AnomalyWindow,
    store: LoggingCallChainStore,
) -> dict[str, Any]:
    from openai import OpenAI

    api_key = os.environ.get(PROVIDER_API_KEY_ENV[provider], "").strip()
    if not api_key:
        return {
            "provider": provider,
            "scenario": scenario_label,
            "error": f"No API key for {provider}",
            "tool_called": False,
            "call_ids_used": [],
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

    # Reset per-run logs on the shared store type by using a fresh store each call
    # (caller passes a new store instance per provider run).
    store.tool_calls_log.clear()
    store.tool_results_log.clear()

    telemetry = build_token_context_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_token_context_prompt_v2(telemetry_json)

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
                "input_tokens_z_score",
                "max_call_chain_depth",
                "get_call_chain",
                "aggregate signals",
                "do NOT call",
            ]
        ):
            print(f"  {rl}")

    try:
        run_result = run_token_context_agent_with_tools(
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
            "call_ids_used": [t["call_id"] for t in store.tool_calls_log],
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
            "call_ids_used": [t["call_id"] for t in store.tool_calls_log],
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
            print(f"  get_call_chain(call_id={entry['call_id']!r})")
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
    report_path = Path("reports/live_token_context_tool_use.txt")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    buf = report_path.open("w", encoding="utf-8")
    old_stdout = sys.stdout
    sys.stdout = Tee(old_stdout, buf)  # type: ignore[assignment]

    try:
        PROVIDERS = ["groq", "cerebras"]
        thin_anomaly = _make_thin_anomaly(label="token_context_thin")

        print("\n" + "█" * 70)
        print("SCENARIO A: token_context_thin  (input_z=1.5, chain_depth=3)")
        print("Rule: input_tokens_z_score < 3.0 AND max_call_chain_depth >= 2")
        print("      => MUST call get_call_chain")
        print("Entry point: run_token_context_agent_with_tools")
        print("█" * 70)

        thin_results = []
        for provider in PROVIDERS:
            store = LoggingCallChainStore()
            r = run_one(
                provider=provider,
                scenario_label="token_context_thin",
                anomaly=thin_anomaly,
                store=store,
            )
            thin_results.append(r)

        print("\n" + "=" * 70)
        print("ASSERTION CHECKS")
        print("=" * 70)

        all_pass = True
        print("\n[A] token_context_thin: tool MUST be called")
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
            if r["tool_called"] and r["call_ids_used"]:
                print(
                    f"  [{name}] PASS - tool called with call_id={r['call_ids_used'][0]!r}"
                )
            else:
                print(
                    f"  [{name}] FAIL - expected get_call_chain to be called but it wasn't"
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
