"""Model-isolation test: does openai/gpt-oss-120b on Groq obey prompt versions
that llama-3.3-70b-versatile ignored?

Runs the 2-scenario matrix (thin + original) through three prompt variants:
  A. soft_guidance   — original 49b68f8 wording ("ONLY IF ... not habit")
  B. hard_prohibition — "do NOT call the tool; answer directly" wording
  C. decision_inject  — current HEAD: pre-evaluated DECISION: line

Provider under test: groq with model=openai/gpt-oss-120b
Reference (for comparison): cerebras gpt-oss-120b (same model family, known-good)

Usage:
    python3 scripts/run_model_isolation_test.py
"""

from __future__ import annotations

import json
import os
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
)


# ---------------------------------------------------------------------------
# Three prompt variants to test
# ---------------------------------------------------------------------------

def _soft_guidance_prompt(telemetry_json: str) -> str:
    """Original soft-guidance version from commit 49b68f8."""
    return f"""You are the Retry Loop Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by an uncapped retry loop or repeated failed calls.

Look for:
- high retry_count
- repeated child calls from the same parent_call_id
- latency growth consistent with retries
- cost growth caused by repeated attempts

You have access to a tool: get_call_chain(call_id).
Use it to inspect the actual call chain for one or more of the
available_call_ids ONLY IF the aggregate signals below are ambiguous and
seeing real call details would change your answer.
Do not call the tool if the aggregate signals already clearly support a
confident hypothesis — only investigate when it is actually useful.
You may call this tool multiple times if needed, but each call should be
justified by genuine uncertainty, not habit.

Use only the telemetry and tool results provided.
Do not invent missing metrics.
Once you have enough evidence, return only valid JSON. No markdown, no prose.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
Confidence guide:
- 0.90-1.00: retry z-score >= 5 and max retry count >= 5
- 0.75-0.89: retry z-score >= 3 or repeated parent calls are obvious
- 0.50-0.74: retry evidence exists but another cause may explain cost
- below 0.50: return no_strong_signal

Return only valid JSON matching this shape:
{{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}}

Telemetry:
{telemetry_json}"""


def _hard_prohibition_prompt(telemetry_json: str) -> str:
    """Intermediate hard-prohibition version (never committed, tested inline)."""
    return f"""You are the Retry Loop Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by an uncapped retry loop or repeated failed calls.

Look for:
- high retry_count
- repeated child calls from the same parent_call_id
- latency growth consistent with retries
- cost growth caused by repeated attempts

You have access to a tool: get_call_chain(call_id).

You MUST call get_call_chain on at least one available_call_id before
giving your final answer if BOTH of the following are true:
- retry_z_score is below 2.5, AND
- repeated_parent_call_count is 1 or greater
In that case, aggregate signals alone are not sufficient — inspect the
actual call chain for one of the repeated parents before deciding.
If retry_z_score is 2.5 or above, OR repeated_parent_call_count is 0,
do NOT call the tool; answer directly from the aggregate signals.

Use only the telemetry and tool results provided.
Do not invent missing metrics.
Once you have enough evidence, return only valid JSON. No markdown, no prose.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
Confidence guide:
- 0.90-1.00: retry z-score >= 5 and max retry count >= 5
- 0.75-0.89: retry z-score >= 3 or repeated parent calls are obvious
- 0.50-0.74: retry evidence exists but another cause may explain cost
- below 0.50: return no_strong_signal

Return only valid JSON matching this shape:
{{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}}

Telemetry:
{telemetry_json}"""


def _decision_inject_prompt(telemetry_json: str) -> str:
    """Current HEAD: pre-evaluated DECISION directive injected."""
    try:
        _telem = json.loads(telemetry_json)
        _signals = _telem.get("signals", {})
        _retry_z = float(_signals.get("retry_z_score", 0.0))
        _rpc = int(_signals.get("repeated_parent_call_count", 0))
        _tool_required = _retry_z < 2.5 and _rpc >= 1
    except Exception:
        _tool_required = False

    if _tool_required:
        tool_directive = (
            f"DECISION: MUST CALL get_call_chain — "
            f"retry_z_score={_retry_z} (below 2.5) AND "
            f"repeated_parent_call_count={_rpc} (>= 1). "
            f"Call get_call_chain on at least one available_call_id before answering."
        )
    else:
        tool_directive = (
            f"DECISION: DO NOT call get_call_chain — "
            f"retry_z_score={_retry_z} (>= 2.5) OR "
            f"repeated_parent_call_count={_rpc} (= 0). "
            f"Answer directly from the aggregate signals. Skip the tool entirely."
        )

    return f"""You are the Retry Loop Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by an uncapped retry loop or repeated failed calls.

Look for:
- high retry_count
- repeated child calls from the same parent_call_id
- latency growth consistent with retries
- cost growth caused by repeated attempts

You have access to a tool: get_call_chain(call_id).

Tool-use rule:
- You MUST call get_call_chain before answering if BOTH are true:
    retry_z_score < 2.5  AND  repeated_parent_call_count >= 1
- If retry_z_score >= 2.5  OR  repeated_parent_call_count == 0:
    do NOT call the tool; answer directly from the aggregate signals.

{tool_directive}

Use only the telemetry and tool results provided.
Do not invent missing metrics.
Once you have enough evidence, return only valid JSON. No markdown, no prose.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
Confidence guide:
- 0.90-1.00: retry z-score >= 5 and max retry count >= 5
- 0.75-0.89: retry z-score >= 3 or repeated parent calls are obvious
- 0.50-0.74: retry evidence exists but another cause may explain cost
- below 0.50: return no_strong_signal

Return only valid JSON matching this shape:
{{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}}

Telemetry:
{telemetry_json}"""


PROMPT_BUILDERS = {
    "soft_guidance": _soft_guidance_prompt,
    "hard_prohibition": _hard_prohibition_prompt,
    "decision_inject": _decision_inject_prompt,
}


# ---------------------------------------------------------------------------
# Anomaly fixtures (same as run_tool_use_live.py)
# ---------------------------------------------------------------------------

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


def _build_telemetry_json(anomaly: AnomalyWindow) -> str:
    signals = anomaly.signals
    return json.dumps({
        "feature_tag": anomaly.feature_tag,
        "start_time": anomaly.start_time.isoformat().replace("+00:00", "Z"),
        "end_time": anomaly.end_time.isoformat().replace("+00:00", "Z"),
        "signals": {
            "retry_z_score": signals.retry_z_score,
            "max_retry_count": signals.max_retry_count,
            "avg_retry_count": signals.avg_retry_count,
            "repeated_parent_call_count": signals.repeated_parent_call_count,
            "latency_z_score": signals.latency_z_score,
            "cost_z_score": signals.cost_z_score,
            "cost_growth_pct": signals.cost_growth_pct,
        },
        "available_call_ids": [c.call_id for c in anomaly.sample_calls],
    }, indent=2)


# ---------------------------------------------------------------------------
# Fake call-chain store
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def run_one(
    *,
    provider: str,
    model: str,
    prompt_variant: str,
    scenario_label: str,
    anomaly: AnomalyWindow,
    store: FakeCallChainStore,
) -> dict[str, Any]:
    from openai import OpenAI

    api_key = os.environ.get(PROVIDER_API_KEY_ENV[provider], "").strip()
    if not api_key:
        return {
            "provider": provider, "model": model,
            "prompt_variant": prompt_variant, "scenario": scenario_label,
            "error": f"No API key for {provider}",
            "tool_called": False, "call_ids_used": [],
            "hypothesis": None, "confidence": None, "explanation": None,
        }

    client = OpenAI(api_key=api_key, base_url=PROVIDER_BASE_URLS[provider])
    tool_calls_log: list[dict] = []

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
        return result

    telemetry_json = _build_telemetry_json(anomaly)
    prompt = PROMPT_BUILDERS[prompt_variant](telemetry_json)

    sep = "=" * 72
    print(f"\n{sep}")
    print(f"PROVIDER: {provider}  MODEL: {model}  PROMPT: {prompt_variant}  SCENARIO: {scenario_label}")
    print(sep)

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
            "provider": provider, "model": model,
            "prompt_variant": prompt_variant, "scenario": scenario_label,
            "error": None,
            "tool_called": len(tool_calls_log) > 0,
            "call_ids_used": [t["call_id"] for t in tool_calls_log],
            "hypothesis": evidence.hypothesis,
            "confidence": evidence.confidence,
            "explanation": evidence.explanation,
        }
    except Exception as exc:
        result = {
            "provider": provider, "model": model,
            "prompt_variant": prompt_variant, "scenario": scenario_label,
            "error": f"{type(exc).__name__}: {exc}",
            "tool_called": len(tool_calls_log) > 0,
            "call_ids_used": [t["call_id"] for t in tool_calls_log],
            "hypothesis": None, "confidence": None, "explanation": None,
        }

    print(f"[Tool calls]  {result['call_ids_used'] if result['tool_called'] else '(none)'}")
    if result["error"]:
        print(f"[Error]       {result['error']}")
    else:
        print(f"[hypothesis]  {result['hypothesis']}")
        print(f"[confidence]  {result['confidence']}")
        print(f"[explanation] {result['explanation']}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    store = FakeCallChainStore()

    thin_anomaly = _make_anomaly(
        retry_z_score=1.2, repeated_parent_call_count=3,
        max_retry_count=3, avg_retry_count=1.5, label="retry_loop_thin",
    )
    orig_anomaly = _make_anomaly(
        retry_z_score=3.5, repeated_parent_call_count=1,
        max_retry_count=7, avg_retry_count=3.0, label="retry_loop_original",
    )

    SCENARIOS = [
        ("retry_loop_thin",     thin_anomaly, True),   # True = tool expected
        ("retry_loop_original", orig_anomaly, False),  # False = tool NOT expected
    ]

    # Providers/models under test
    RUNS = [
        ("groq",     "openai/gpt-oss-120b"),     # new model, same family as cerebras
        ("cerebras", "gpt-oss-120b"),             # reference — known-good
    ]

    PROMPT_VARIANTS = ["soft_guidance", "hard_prohibition", "decision_inject"]

    all_results: list[dict] = []

    for prompt_variant in PROMPT_VARIANTS:
        print(f"\n{'█'*72}")
        print(f"PROMPT VARIANT: {prompt_variant}")
        print(f"{'█'*72}")
        for scenario_label, anomaly, tool_expected in SCENARIOS:
            for provider, model in RUNS:
                r = run_one(
                    provider=provider, model=model,
                    prompt_variant=prompt_variant,
                    scenario_label=scenario_label,
                    anomaly=anomaly, store=store,
                )
                r["tool_expected"] = tool_expected
                all_results.append(r)

    # ── Assertion matrix ──────────────────────────────────────────────────
    print(f"\n\n{'='*72}")
    print("ASSERTION MATRIX")
    print(f"{'='*72}")

    all_pass = True
    for r in all_results:
        name = f"{r['prompt_variant']:<20} {r['provider']}/{r['model']:<28} {r['scenario']}"
        if r.get("error"):
            verdict = f"SKIPPED ({r['error'][:60]})"
            print(f"  {name}  {verdict}")
            continue
        tool_expected = r["tool_expected"]
        tool_called   = r["tool_called"]
        if tool_expected and tool_called:
            verdict = "PASS (tool called as required)"
        elif not tool_expected and not tool_called:
            verdict = "PASS (tool correctly skipped)"
        elif tool_expected and not tool_called:
            verdict = "FAIL - tool was required but NOT called"
            all_pass = False
        else:
            verdict = f"FAIL - tool should have been SKIPPED but was called ({r['call_ids_used']})"
            all_pass = False
        print(f"  {name}  {verdict}")

    # ── Summary table ─────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("SUMMARY TABLE")
    print(f"{'='*72}")
    hdr = (f"{'Prompt':<22} {'Provider/Model':<32} {'Scenario':<22} "
           f"{'Expected':<10} {'Called':<8} {'Pass?'}")
    print(hdr)
    print("-" * len(hdr))
    for r in all_results:
        if r.get("error"):
            called_str = "ERR"
            pass_str = "SKIP"
        else:
            called_str = "YES" if r["tool_called"] else "NO"
            expected_str = "YES" if r["tool_expected"] else "NO"
            pass_str = (
                "PASS" if (r["tool_expected"] == r["tool_called"]) else "FAIL"
            )
        expected_str = "YES" if r.get("tool_expected") else "NO"
        pm = f"{r['provider']}/{r['model']}"
        print(
            f"{r['prompt_variant']:<22} {pm:<32} {r['scenario']:<22} "
            f"{expected_str:<10} {called_str:<8} {pass_str}"
        )

    print(f"\n{'All assertions passed.' if all_pass else 'SOME ASSERTIONS FAILED — see above.'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
