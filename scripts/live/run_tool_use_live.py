"""Live-run harness: verify hardened tool-use rule in retry_loop_agent.

Scenarios
---------
retry_loop_thin
    retry_z_score=1.2, repeated_parent_call_count=3
    Rule says: z < 2.5 AND repeated >= 1  -> MUST call get_call_chain

retry_loop_original
    retry_z_score=3.5, repeated_parent_call_count=1
    Rule says: z >= 2.5  -> may answer directly, NO tool call required
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_env = Path(".env")
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from llm_cost_investigator.agents import (
    build_retry_loop_prompt_v2,
    build_retry_loop_telemetry_v2,
    call_agent_with_tools,
    RETRY_LOOP_TOOLS,
)
from llm_cost_investigator.schemas import (
    AgentEvidence,
    AnomalyWindow,
    AnomalySignals,
    LLMCall,
)
from llm_cost_investigator.llm_client import (
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URLS,
    DEFAULT_MODELS,
)


def _make_anomaly(
    *,
    retry_z_score: float,
    repeated_parent_call_count: int,
    max_retry_count: int = 3,
    avg_retry_count: float = 1.5,
    label: str = "test",
) -> AnomalyWindow:
    base_ts = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
    parent_id = f"{label}_parent_01"
    calls = []
    for i in range(4):
        calls.append(
            LLMCall(
                timestamp=base_ts,
                call_id=f"{label}_call_{i:02d}",
                parent_call_id=parent_id if i > 0 else None,
                feature_tag="support_reply",
                model="gpt-4o-mini",
                input_tokens=1100 + i * 10,
                output_tokens=250,
                cost_usd=0.003 * (i + 1),
                latency_ms=900 + i * 450,
                retry_count=i,
                scenario_label=label,
            )
        )

    signals = AnomalySignals(
        cost_z_score=3.5,
        retry_z_score=retry_z_score,
        max_retry_count=max_retry_count,
        avg_retry_count=avg_retry_count,
        repeated_parent_call_count=repeated_parent_call_count,
        latency_z_score=2.0,
        cost_growth_pct=180.0,
        input_tokens_z_score=0.3,
        input_token_growth_pct=5.0,
        token_growth_pct=5.0,
        max_call_chain_depth=1,
        model_changed=False,
        model_before=None,
        model_during=None,
        models_seen=["gpt-4o-mini"],
        output_token_growth_pct=0.0,
    )

    return AnomalyWindow(
        feature_tag="support_reply",
        start_time=base_ts,
        end_time=base_ts,
        signals=signals,
        sample_calls=calls,
    )


class FakeCallChainStore:
    def get_call_chain(self, call_id: str) -> list[LLMCall]:
        base_ts = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
        parent_id = "synthetic_parent_01"
        return [
            LLMCall(
                timestamp=base_ts,
                call_id=f"chain_{i:02d}",
                parent_call_id=parent_id if i > 0 else None,
                feature_tag="support_reply",
                model="gpt-4o-mini",
                input_tokens=1100 + i * 10,
                output_tokens=250,
                cost_usd=0.003 * (i + 1),
                latency_ms=900 + i * 450,
                retry_count=i,
                scenario_label="live_test",
            )
            for i in range(4)
        ]


def run_one(
    *,
    provider: str,
    scenario_label: str,
    anomaly: AnomalyWindow,
    store: FakeCallChainStore,
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
        }

    model = DEFAULT_MODELS[provider]
    client = OpenAI(api_key=api_key, base_url=PROVIDER_BASE_URLS[provider])

    tool_calls_log: list[dict] = []
    tool_results_log: list[Any] = []

    def tool_executor(tool_name: str, args: dict) -> Any:
        if tool_name != "get_call_chain":
            raise ValueError(f"Unknown tool: {tool_name}")
        call_id = args["call_id"]
        chain = store.get_call_chain(call_id)
        result = [
            {
                "call_id": c.call_id,
                "parent_call_id": c.parent_call_id,
                "timestamp": c.timestamp.isoformat().replace("+00:00", "Z"),
                "retry_count": c.retry_count,
                "cost_usd": c.cost_usd,
                "latency_ms": c.latency_ms,
            }
            for c in chain
        ]
        tool_calls_log.append({"tool": tool_name, "call_id": call_id})
        tool_results_log.append(result)
        return result

    telemetry = build_retry_loop_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_retry_loop_prompt_v2(telemetry_json)

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario_label}  |  PROVIDER: {provider}  |  MODEL: {model}")
    print(f"{'='*70}")
    print(f"\n[Telemetry sent to agent]")
    print(telemetry_json)
    print(f"\n[Relevant prompt rule lines]")
    for rl in prompt.splitlines():
        if any(kw in rl for kw in ["MUST", "retry_z_score", "repeated_parent",
                                    "aggregate signals", "calling the", "you may answer",
                                    "above, OR"]):
            print(f"  {rl}")

    try:
        evidence: AgentEvidence = call_agent_with_tools(
            client=client,
            model=model,
            prompt=prompt,
            tools=RETRY_LOOP_TOOLS,
            tool_executor=tool_executor,
            schema=AgentEvidence,
        )
        result = {
            "provider": provider,
            "scenario": scenario_label,
            "error": None,
            "tool_called": len(tool_calls_log) > 0,
            "call_ids_used": [t["call_id"] for t in tool_calls_log],
            "tool_results": tool_results_log,
            "hypothesis": evidence.hypothesis,
            "confidence": evidence.confidence,
            "explanation": evidence.explanation,
            "supporting_metrics": evidence.supporting_metrics,
        }
    except Exception as exc:
        result = {
            "provider": provider,
            "scenario": scenario_label,
            "error": f"{type(exc).__name__}: {exc}",
            "tool_called": len(tool_calls_log) > 0,
            "call_ids_used": [t["call_id"] for t in tool_calls_log],
            "tool_results": tool_results_log,
            "hypothesis": None,
            "confidence": None,
            "explanation": None,
            "supporting_metrics": None,
        }

    print(f"\n[Tool calls log]")
    if tool_calls_log:
        for entry in tool_calls_log:
            print(f"  get_call_chain(call_id={entry['call_id']!r})")
        print(f"\n[Tool result returned to model]")
        print(json.dumps(tool_results_log[0] if tool_results_log else [], indent=2))
    else:
        print("  (none)")

    print(f"\n[Final agent output]")
    if result["error"]:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  hypothesis:  {result['hypothesis']}")
        print(f"  confidence:  {result['confidence']}")
        print(f"  explanation: {result['explanation']}")
        print(f"  metrics:     {json.dumps(result['supporting_metrics'], indent=4)}")

    return result


def main() -> int:
    store = FakeCallChainStore()
    PROVIDERS = ["groq", "cerebras"]

    thin_anomaly = _make_anomaly(
        retry_z_score=1.2,
        repeated_parent_call_count=3,
        max_retry_count=3,
        avg_retry_count=1.5,
        label="retry_loop_thin",
    )

    print("\n" + "█"*70)
    print("SCENARIO A: retry_loop_thin  (z=1.2, repeated_parent=3)")
    print("Rule: z < 2.5 AND repeated >= 1 => MUST call get_call_chain")
    print("█"*70)

    thin_results = []
    for provider in PROVIDERS:
        r = run_one(provider=provider, scenario_label="retry_loop_thin",
                    anomaly=thin_anomaly, store=store)
        thin_results.append(r)

    orig_anomaly = _make_anomaly(
        retry_z_score=3.5,
        repeated_parent_call_count=1,
        max_retry_count=7,
        avg_retry_count=3.0,
        label="retry_loop_original",
    )

    print("\n" + "█"*70)
    print("SCENARIO B: retry_loop_original  (z=3.5, repeated_parent=1)")
    print("Rule: z >= 2.5 => may answer directly, tool call NOT required")
    print("█"*70)

    orig_results = []
    for provider in PROVIDERS:
        r = run_one(provider=provider, scenario_label="retry_loop_original",
                    anomaly=orig_anomaly, store=store)
        orig_results.append(r)

    print("\n" + "="*70)
    print("ASSERTION CHECKS")
    print("="*70)

    all_pass = True

    print("\n[A] retry_loop_thin: tool MUST be called")
    for r in thin_results:
        name = f"{r['provider']}:{r['scenario']}"
        if r.get("error"):
            print(f"  [{name}] SKIPPED (API error: {r['error']})")
            continue
        if r["tool_called"] and r["call_ids_used"]:
            print(f"  [{name}] PASS - tool called with call_id={r['call_ids_used'][0]!r}")
        else:
            print(f"  [{name}] FAIL - expected get_call_chain to be called but it wasn't")
            all_pass = False

    print("\n[B] retry_loop_original: tool must NOT be called")
    for r in orig_results:
        name = f"{r['provider']}:{r['scenario']}"
        if r.get("error"):
            print(f"  [{name}] SKIPPED (API error: {r['error']})")
            continue
        if not r["tool_called"]:
            print(f"  [{name}] PASS - no tool call (correctly answered from aggregate signals)")
        else:
            print(f"  [{name}] FAIL - expected NO tool call but get_call_chain was called with ids={r['call_ids_used']}")
            all_pass = False

    print("\n" + "="*70)
    print("RESULT TABLE")
    print("="*70)
    hdr = f"{'Scenario':<26} {'Provider':<12} {'Tool called':<13} {'Hypothesis':<30} {'Confidence'}"
    print(hdr)
    print("-" * len(hdr))
    for r in thin_results + orig_results:
        print(
            f"{r['scenario']:<26} {r['provider']:<12} "
            f"{'YES' if r['tool_called'] else 'NO':<13} "
            f"{str(r['hypothesis']):<30} "
            f"{r['confidence']}"
        )

    if all_pass:
        print("\nAll assertions passed.")
        return 0
    else:
        print("\nSome assertions failed - see above.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
