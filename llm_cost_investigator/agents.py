"""LLM-backed diagnostic agent entrypoints and fallbacks."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from llm_cost_investigator.schemas import AgentEvidence, AgentRunResult, AnomalyWindow


# ---------------------------------------------------------------------------
# Telemetry slice builders
# ---------------------------------------------------------------------------

def build_retry_loop_telemetry(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Build the allowed telemetry slice for the Retry Loop Agent."""
    signals = anomaly.signals
    calls = []
    for call in anomaly.sample_calls:
        calls.append({
            "timestamp": call.timestamp.isoformat().replace("+00:00", "Z"),
            "call_id": call.call_id,
            "parent_call_id": call.parent_call_id,
            "feature_tag": call.feature_tag,
            "model": call.model,
            "cost_usd": call.cost_usd,
            "latency_ms": call.latency_ms,
            "retry_count": call.retry_count,
        })
    return {
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
        "sample_calls": calls,
    }


def build_token_context_telemetry(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Build the allowed telemetry slice for the Token Context Agent."""
    signals = anomaly.signals
    calls = []
    for call in anomaly.sample_calls:
        calls.append({
            "timestamp": call.timestamp.isoformat().replace("+00:00", "Z"),
            "call_id": call.call_id,
            "parent_call_id": call.parent_call_id,
            "feature_tag": call.feature_tag,
            "model": call.model,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": call.cost_usd,
        })
    return {
        "feature_tag": anomaly.feature_tag,
        "start_time": anomaly.start_time.isoformat().replace("+00:00", "Z"),
        "end_time": anomaly.end_time.isoformat().replace("+00:00", "Z"),
        "signals": {
            "input_tokens_z_score": signals.input_tokens_z_score,
            "input_token_growth_pct": signals.input_token_growth_pct,
            "token_growth_pct": signals.token_growth_pct,
            "cost_growth_pct": signals.cost_growth_pct,
            "max_call_chain_depth": signals.max_call_chain_depth,
            "retry_z_score": signals.retry_z_score,
            "model_changed": signals.model_changed,
        },
        "sample_calls": calls,
    }


def build_model_routing_telemetry(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Build the allowed telemetry slice for the Model Routing Agent."""
    signals = anomaly.signals
    calls = []
    for call in anomaly.sample_calls:
        calls.append({
            "timestamp": call.timestamp.isoformat().replace("+00:00", "Z"),
            "call_id": call.call_id,
            "feature_tag": call.feature_tag,
            "model": call.model,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": call.cost_usd,
            "retry_count": call.retry_count,
        })
    return {
        "feature_tag": anomaly.feature_tag,
        "start_time": anomaly.start_time.isoformat().replace("+00:00", "Z"),
        "end_time": anomaly.end_time.isoformat().replace("+00:00", "Z"),
        "signals": {
            "model_changed": signals.model_changed,
            "model_before": signals.model_before,
            "model_during": signals.model_during,
            "models_seen": signals.models_seen,
            "cost_z_score": signals.cost_z_score,
            "cost_growth_pct": signals.cost_growth_pct,
            "token_growth_pct": signals.token_growth_pct,
            "input_token_growth_pct": signals.input_token_growth_pct,
            "output_token_growth_pct": signals.output_token_growth_pct,
            "retry_z_score": signals.retry_z_score,
            "max_retry_count": signals.max_retry_count,
        },
        "sample_calls": calls,
    }


# ---------------------------------------------------------------------------
# Prompt builders (hard skills / KB / graph injected; thresholds resolved in Python)
# ---------------------------------------------------------------------------

_SHARED_PREAMBLE = """You are a principal SRE performing differential diagnosis on an LLM cost anomaly.
You receive telemetry for one anomaly window, plus HARD_SKILL_RESULTS that have
already been computed deterministically — you must NOT recompute or override them,
only reason from them.

Rules:
- Use only the telemetry and HARD_SKILL_RESULTS provided. Never invent metrics.
- If HARD_SKILL_RESULTS marks a falsifier as "fail" for your hypothesis, you MUST
  return "no_strong_signal" — no exceptions.
- Your confidence may not exceed the band implied by HARD_SKILL_RESULTS (see rubric).
  Prefer under-confident over wrong.
- Output ONLY valid JSON. No markdown, no prose outside the JSON object.
- Weak or contradicted evidence -> hypothesis "no_strong_signal"."""


def build_retry_loop_prompt(
    telemetry_json: str,
    *,
    hard_skill_results_json: str = "{}",
    kb_matches_json: str = "[]",
    evidence_graph_summary: str = "(none)",
) -> str:
    """Build the prompt for the Retry Loop Agent."""
    return f"""{_SHARED_PREAMBLE}

ROLE: Retry Loop Agent. You test whether an uncapped/silent retry loop explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS — do not recompute):
1. retry_zscore_threshold
2. max_retry_floor
3. repeated_parent_call_check
4. latency_growth_confirmation
5. sample_call_consistency (FALSIFIER)
6. cost_attribution_check (FALSIFIER)
7. non_retry_alternative_scan

If skill 5 or 6 = "fail" -> hypothesis MUST be "no_strong_signal".

KNOWLEDGE BASE PATTERNS TO MATCH: RL-01, RL-02, RL-03 (see KB_MATCHES input).
Use the matched pattern's confidence band as your ceiling, not a target you must hit.

CONFIDENCE RUBRIC:
- 0.90-1.00: skills 1 AND 2 pass, skill 6 pass (>=50%)
- 0.75-0.89: skill 1 or 2 soft_pass/pass, skill 3 supports, skill 6 >=20%
- 0.50-0.74: some retry evidence but skill 7 flags a stronger competing signal
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY:
{evidence_graph_summary}

HARD_SKILL_RESULTS:
{hard_skill_results_json}

KB_MATCHES:
{kb_matches_json}

Telemetry:
{telemetry_json}

OUTPUT SCHEMA (JSON only):
{{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {{}},
  "explanation": "..."
}}"""


def build_token_context_prompt(
    telemetry_json: str,
    *,
    hard_skill_results_json: str = "{}",
    kb_matches_json: str = "[]",
    evidence_graph_summary: str = "(none)",
) -> str:
    """Build the prompt for the Token Context Agent."""
    return f"""{_SHARED_PREAMBLE}

ROLE: Token Context Agent. You test whether context bloat / a recursive self-calling
agent explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS):
1. token_growth_threshold
2. chain_depth_floor
3. chain_depth_recency (FALSIFIER)
4. token_zscore_check
5. prompt_content_growth_correlation (FALSIFIER)
6. self_call_pattern_check
7. sample_consistency_check

If skill 3 or 5 = "fail" -> hypothesis MUST be "no_strong_signal".
If skill 5 fails because cost growth exceeds token growth, note that this looks more
like model routing — do not claim it yourself.

KNOWLEDGE BASE PATTERNS TO MATCH: TC-01, TC-02, TC-03.

CONFIDENCE RUBRIC:
- 0.90-1.00: skill 1 pass (>=300%) AND skill 2 pass (>=5)
- 0.75-0.89: skill 4 pass (z>=3) AND chain depth >= 4
- 0.50-0.74: token growth exists but skill 2/6 evidence is weak
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY:
{evidence_graph_summary}

HARD_SKILL_RESULTS:
{hard_skill_results_json}

KB_MATCHES:
{kb_matches_json}

Telemetry:
{telemetry_json}

OUTPUT SCHEMA (JSON only):
{{
  "agent_name": "token_context_agent",
  "hypothesis": "context_bloat_self_calling_agent" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {{}},
  "explanation": "..."
}}"""


def build_model_routing_prompt(
    telemetry_json: str,
    *,
    hard_skill_results_json: str = "{}",
    kb_matches_json: str = "[]",
    evidence_graph_summary: str = "(none)",
) -> str:
    """Build the prompt for the Model Routing Agent."""
    return f"""{_SHARED_PREAMBLE}

ROLE: Model Routing Agent. You test whether accidental routing to a pricier model
(with roughly stable tokens) explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS):
1. model_changed_flag (FALSIFIER)
2. cost_growth_threshold
3. token_stability_check (caps confidence if failed)
4. cost_zscore_check
5. price_tier_consistency (FALSIFIER)
6. sample_call_confirmation
7. routing_scope_check

If skill 1 or 5 = "fail" -> hypothesis MUST be "no_strong_signal".
If skill 3 = "fail" (tokens also grew a lot), cap confidence at 0.50-0.74 and say
plainly that token growth confounds attribution to routing alone.

KNOWLEDGE BASE PATTERNS TO MATCH: MR-01, MR-02, MR-03.

CONFIDENCE RUBRIC:
- 0.90-1.00: skill 1 pass, skill 2 pass (>=200%), skill 3 pass (tokens stable)
- 0.75-0.89: skill 1 pass, skill 4 pass (z>=3)
- 0.50-0.74: cost grew but skill 3 failed (token growth confounds) or evidence partial
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY:
{evidence_graph_summary}

HARD_SKILL_RESULTS:
{hard_skill_results_json}

KB_MATCHES:
{kb_matches_json}

Telemetry:
{telemetry_json}

OUTPUT SCHEMA (JSON only):
{{
  "agent_name": "model_routing_agent",
  "hypothesis": "expensive_model_misroute" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {{}},
  "explanation": "..."
}}"""


# ---------------------------------------------------------------------------
# Tool definitions for tool-using agent upgrades
# ---------------------------------------------------------------------------

RETRY_LOOP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_call_chain",
            "description": (
                "Fetch the full parent-to-child call chain for a given call_id, "
                "to inspect retry/loop structure in detail (root-first order)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "A call_id from the available_call_ids list.",
                    }
                },
                "required": ["call_id"],
            },
        },
    }
]

# token_context_agent reuses the same get_call_chain tool — chain inspection is
# equally useful for detecting context bloat / recursion as for retry loops.
TOKEN_CONTEXT_TOOLS = RETRY_LOOP_TOOLS

MODEL_ROUTING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_window_calls",
            "description": (
                "Fetch the actual calls within the anomaly window for this "
                "feature, showing model and cost_usd per call, to verify "
                "whether a cost increase correlates with a specific model "
                "rather than trusting the aggregate model_changed flag alone."
            ),
            "parameters": {
                "type": "object",
                "properties": {"feature_tag": {"type": "string"}},
                "required": ["feature_tag"],
            },
        },
    }
]

MAX_RETRY_LOOP_TOOL_CALLS = 4

# ---------------------------------------------------------------------------
# Deterministic fallbacks (driven by hard skills)
# ---------------------------------------------------------------------------

def _enrich_supporting_metrics(
    base: dict[str, Any],
    skill_pack: dict[str, Any],
    kb_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out = dict(base)
    out.update(skill_pack.get("metrics") or {})
    out["hard_skills"] = skill_pack.get("skills") or {}
    out["skill_band_ceiling"] = skill_pack.get("band_ceiling", 1.0)
    out["forced_no_strong_signal"] = bool(skill_pack.get("forced_no_strong_signal"))
    if kb_matches is not None:
        out["kb_matches"] = [
            {"pattern_id": m.get("pattern_id"), "story": m.get("story")}
            for m in kb_matches
        ]
    return out


def _apply_skill_pack_to_evidence(
    evidence: AgentEvidence,
    skill_pack: dict[str, Any],
    kb_matches: list[dict[str, Any]] | None = None,
) -> AgentEvidence:
    """Cap confidence / force no_strong_signal from hard skills; attach metrics.

    Rubric: confidence below 0.50 must not carry a causal hypothesis. Force
    ``no_strong_signal`` when a falsifier failed or the skill band ceiling is
    below 0.50 (weak/insufficient hard evidence).
    """
    from llm_cost_investigator.knowledge_base import pattern_band_ceiling

    ceiling = float(skill_pack.get("band_ceiling", 1.0))
    if kb_matches:
        ceiling = pattern_band_ceiling(kb_matches, ceiling)
    hyp = evidence.hypothesis
    conf = min(float(evidence.confidence), ceiling)
    if skill_pack.get("forced_no_strong_signal") or ceiling < 0.50:
        hyp = "no_strong_signal"
        conf = min(conf, 0.49)
    metrics = _enrich_supporting_metrics(
        dict(evidence.supporting_metrics),
        {**skill_pack, "band_ceiling": ceiling},
        kb_matches,
    )
    return evidence.model_copy(
        update={
            "hypothesis": hyp,
            "confidence": conf,
            "supporting_metrics": metrics,
        }
    )


def fallback_retry_loop(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Retry Loop Agent when LLM calls fail."""
    from llm_cost_investigator.agent_skills import score_retry_loop
    from llm_cost_investigator.knowledge_base import match_patterns

    pack = score_retry_loop(anomaly)
    kb = match_patterns("retry_loop_agent", anomaly, pack)
    hyp = pack["suggested_hypothesis"]
    conf = float(pack["suggested_confidence"])
    explanation = (
        "Hard skills indicate uncapped retry loop / repeated failed calls."
        if hyp == "uncapped_retry_loop"
        else "Hard skills: weak or falsified retry evidence."
    )
    if kb:
        explanation += f" Pattern: {kb[0].get('pattern_id')}."
    explanation += " (Fallback)"
    return AgentEvidence(
        agent_name="retry_loop_agent",
        hypothesis=hyp,
        confidence=conf,
        supporting_metrics=_enrich_supporting_metrics({}, pack, kb),
        explanation=explanation,
    )


def fallback_token_context(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Token Context Agent when LLM calls fail."""
    from llm_cost_investigator.agent_skills import score_token_context
    from llm_cost_investigator.knowledge_base import match_patterns

    pack = score_token_context(anomaly)
    kb = match_patterns("token_context_agent", anomaly, pack)
    hyp = pack["suggested_hypothesis"]
    conf = float(pack["suggested_confidence"])
    explanation = (
        "Hard skills indicate context bloat / recursive self-calling growth."
        if hyp == "context_bloat_self_calling_agent"
        else "Hard skills: weak or falsified token-context evidence."
    )
    if kb:
        explanation += f" Pattern: {kb[0].get('pattern_id')}."
    explanation += " (Fallback)"
    return AgentEvidence(
        agent_name="token_context_agent",
        hypothesis=hyp,
        confidence=conf,
        supporting_metrics=_enrich_supporting_metrics({}, pack, kb),
        explanation=explanation,
    )


def fallback_model_routing(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Model Routing Agent when LLM calls fail."""
    from llm_cost_investigator.agent_skills import score_model_routing
    from llm_cost_investigator.knowledge_base import match_patterns

    pack = score_model_routing(anomaly)
    kb = match_patterns("model_routing_agent", anomaly, pack)
    hyp = pack["suggested_hypothesis"]
    conf = float(pack["suggested_confidence"])
    explanation = (
        "Hard skills indicate expensive model misroute with stable tokens."
        if hyp == "expensive_model_misroute"
        else "Hard skills: weak or falsified model-routing evidence."
    )
    if kb:
        explanation += f" Pattern: {kb[0].get('pattern_id')}."
    explanation += " (Fallback)"
    return AgentEvidence(
        agent_name="model_routing_agent",
        hypothesis=hyp,
        confidence=conf,
        supporting_metrics=_enrich_supporting_metrics({}, pack, kb),
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# V2 telemetry/prompt builders for tool-using retry_loop_agent
# ---------------------------------------------------------------------------

def build_retry_loop_telemetry_v2(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Signals + call_id list only. The agent must use get_call_chain to see
    per-call details — this is what makes tool use meaningful rather than
    decorative."""
    signals = anomaly.signals
    return {
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
    }


def build_retry_loop_prompt_v2(telemetry_json: str) -> str:
    """Same diagnostic instructions as build_retry_loop_prompt, plus tool-use
    guidance with a pre-evaluated DECISION directive computed from the actual
    telemetry values so the model never has to evaluate the conditional itself."""
    # Pre-evaluate the tool-call condition from the actual telemetry values.
    # This makes the directive unambiguous regardless of model instruction-following.
    try:
        _telem = json.loads(telemetry_json)
        _signals = _telem.get("signals", {})
        _retry_z = float(_signals.get("retry_z_score", 0.0))
        _rpc = int(_signals.get("repeated_parent_call_count", 0))
        _tool_required = _retry_z < 2.5 and _rpc >= 1
    except Exception:
        # Safe default: allow direct answer if parsing fails (do not reference
        # unbound _retry_z / _rpc in the directive).
        tool_directive = (
            "DECISION: DO NOT call get_call_chain — could not parse signals. "
            "Answer directly from the aggregate signals."
        )
        _tool_required = None  # marker: directive already set

    if _tool_required is True:
        tool_directive = (
            f"DECISION: MUST CALL get_call_chain — "
            f"retry_z_score={_retry_z} (below 2.5) AND "
            f"repeated_parent_call_count={_rpc} (>= 1). "
            f"Call get_call_chain on at least one available_call_id before answering."
        )
    elif _tool_required is False:
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


# ---------------------------------------------------------------------------
# V2 telemetry/prompt builders for tool-using token_context_agent
# ---------------------------------------------------------------------------

def _token_context_tool_required(signals: dict) -> tuple[bool, str]:
    """Gate: decide whether get_call_chain must be called before answering.

    Tool is required when the aggregate z-score is below the high-confidence
    threshold BUT chain depth suggests possible recursion — chain inspection
    is needed to break the ambiguity.
    """
    input_z = float(signals.get("input_tokens_z_score", 0.0))
    chain_depth = int(signals.get("max_call_chain_depth", 0))
    tool_required = input_z < 3.0 and chain_depth >= 2
    if tool_required:
        reason = (
            f"DECISION: MUST CALL get_call_chain — input_tokens_z_score={input_z} "
            f"(below 3.0) AND max_call_chain_depth={chain_depth} (>= 2). "
            f"Aggregate token growth alone is inconclusive but chain depth "
            f"suggests possible recursion — inspect the actual chain before answering."
        )
    else:
        reason = (
            f"DECISION: DO NOT call get_call_chain — input_tokens_z_score={input_z} "
            f"(>= 3.0) OR max_call_chain_depth={chain_depth} (< 2). "
            f"Answer directly from the aggregate signals. Skip the tool entirely."
        )
    return tool_required, reason


def build_token_context_telemetry_v2(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Signals-only slice + available_call_ids for the tool-using token_context_agent.
    The agent uses get_call_chain to inspect the actual chain rather than relying
    only on aggregate depth metrics."""
    signals = anomaly.signals
    return {
        "feature_tag": anomaly.feature_tag,
        "start_time": anomaly.start_time.isoformat().replace("+00:00", "Z"),
        "end_time": anomaly.end_time.isoformat().replace("+00:00", "Z"),
        "signals": {
            "input_tokens_z_score": signals.input_tokens_z_score,
            "input_token_growth_pct": signals.input_token_growth_pct,
            "token_growth_pct": signals.token_growth_pct,
            "cost_growth_pct": signals.cost_growth_pct,
            "max_call_chain_depth": signals.max_call_chain_depth,
            "retry_z_score": signals.retry_z_score,
            "model_changed": signals.model_changed,
        },
        "available_call_ids": [c.call_id for c in anomaly.sample_calls],
    }


def build_token_context_prompt_v2(telemetry_json: str) -> str:
    """Token context diagnostic prompt with DECISION-injection tool-use gating.
    The pre-evaluated DECISION line is injected so the model never has to
    evaluate the conditional itself (eliminates ambiguity on all providers)."""
    try:
        _telem = json.loads(telemetry_json)
        _signals = _telem.get("signals", {})
        _, tool_directive = _token_context_tool_required(_signals)
    except Exception:
        tool_directive = (
            "DECISION: DO NOT call get_call_chain — could not parse signals. "
            "Answer directly from the aggregate signals."
        )

    return f"""You are the Token Context Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by context bloat, recursive self-calling behavior, or growing prompts.

Look for:
- input_tokens increasing over time
- deep parent_call_id chains
- agent_reflection or similar features calling themselves
- cost growth explained by larger context, not retries or model changes

You have access to a tool: get_call_chain(call_id).

Tool-use rule:
- You MUST call get_call_chain before answering if BOTH are true:
    input_tokens_z_score < 3.0  AND  max_call_chain_depth >= 2
- If input_tokens_z_score >= 3.0  OR  max_call_chain_depth < 2:
    do NOT call the tool; answer directly from the aggregate signals.

{tool_directive}

Use only the telemetry and tool results provided.
Do not invent missing metrics.
Once you have enough evidence, return only valid JSON. No markdown, no prose.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
Confidence guide:
- 0.90-1.00: input tokens grow by >= 300% and chain depth >= 5
- 0.75-0.89: input token z-score >= 3 and chain depth >= 4
- 0.50-0.74: token growth exists but chain evidence is weak
- below 0.50: return no_strong_signal

Return only valid JSON matching this shape:
{{
  "agent_name": "token_context_agent",
  "hypothesis": "context_bloat_self_calling_agent" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}}

Telemetry:
{telemetry_json}"""


# ---------------------------------------------------------------------------
# V2 telemetry/prompt builders for tool-using model_routing_agent
# ---------------------------------------------------------------------------

def _model_routing_tool_required(signals: dict) -> tuple[bool, str]:
    """Gate: decide whether get_window_calls must be called before answering.

    Tool is required when a model change was detected but the cost z-score is
    not strong enough on its own — per-call model/cost data is needed to
    confirm the attribution.
    """
    model_changed = bool(signals.get("model_changed", False))
    cost_z = float(signals.get("cost_z_score", 0.0))
    tool_required = model_changed and cost_z < 3.0
    if tool_required:
        reason = (
            f"DECISION: MUST CALL get_window_calls — model_changed=True but "
            f"cost_z_score={cost_z} (below 3.0). A model change was detected "
            f"but the cost signal alone is not strong enough to confidently "
            f"attribute the anomaly to it — inspect actual per-call model/cost "
            f"data before answering."
        )
    else:
        reason = (
            f"DECISION: DO NOT call get_window_calls — model_changed="
            f"{model_changed} and cost_z_score={cost_z}. "
            f"Answer directly from the aggregate signals. Skip the tool entirely."
        )
    return tool_required, reason


def build_model_routing_telemetry_v2(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Signals-only slice for the tool-using model_routing_agent.
    No available_call_ids — get_window_calls takes feature_tag, not call_id."""
    signals = anomaly.signals
    return {
        "feature_tag": anomaly.feature_tag,
        "start_time": anomaly.start_time.isoformat().replace("+00:00", "Z"),
        "end_time": anomaly.end_time.isoformat().replace("+00:00", "Z"),
        "signals": {
            "model_changed": signals.model_changed,
            "model_before": signals.model_before,
            "model_during": signals.model_during,
            "models_seen": signals.models_seen,
            "cost_z_score": signals.cost_z_score,
            "cost_growth_pct": signals.cost_growth_pct,
            "token_growth_pct": signals.token_growth_pct,
            "input_token_growth_pct": signals.input_token_growth_pct,
            "output_token_growth_pct": signals.output_token_growth_pct,
            "retry_z_score": signals.retry_z_score,
            "max_retry_count": signals.max_retry_count,
        },
    }


def build_model_routing_prompt_v2(telemetry_json: str) -> str:
    """Model routing diagnostic prompt with DECISION-injection tool-use gating.
    The pre-evaluated DECISION line is injected so the model never has to
    evaluate the conditional itself (eliminates ambiguity on all providers)."""
    try:
        _telem = json.loads(telemetry_json)
        _signals = _telem.get("signals", {})
        _, tool_directive = _model_routing_tool_required(_signals)
    except Exception:
        tool_directive = (
            "DECISION: DO NOT call get_window_calls — could not parse signals. "
            "Answer directly from the aggregate signals."
        )

    return f"""You are the Model Routing Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by a feature being routed to a more expensive model.

Look for:
- same feature_tag using a different model during the anomaly
- cost_usd increasing sharply
- input/output tokens staying roughly stable
- no major retry spike

You have access to a tool: get_window_calls(feature_tag).

Tool-use rule:
- You MUST call get_window_calls before answering if BOTH are true:
    model_changed == True  AND  cost_z_score < 3.0
- If model_changed == False  OR  cost_z_score >= 3.0:
    do NOT call the tool; answer directly from the aggregate signals.

{tool_directive}

Use only the telemetry and tool results provided.
Do not invent missing metrics.
Once you have enough evidence, return only valid JSON. No markdown, no prose.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
Confidence guide:
- 0.90-1.00: model changed, cost grew >= 200%, token growth < 50%
- 0.75-0.89: model changed and cost z-score >= 3
- 0.50-0.74: cost grew without token growth, but model evidence is partial
- below 0.50: return no_strong_signal

Return only valid JSON matching this shape:
{{
  "agent_name": "model_routing_agent",
  "hypothesis": "expensive_model_misroute" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}}

Telemetry:
{telemetry_json}"""


# ---------------------------------------------------------------------------
# Validation wrapper
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

ALLOWED_HYPOTHESES: dict[str, set[str]] = {
    "retry_loop_agent": {"uncapped_retry_loop", "no_strong_signal"},
    "token_context_agent": {"context_bloat_self_calling_agent", "no_strong_signal"},
    "model_routing_agent": {"expensive_model_misroute", "no_strong_signal"},
}


def _clean_json_text(raw: str) -> str:
    """Strip a markdown code fence or prose around one JSON object.

    Many models wrap JSON in ```json fences even when told not to. Removing
    the fence is safe; the schema still validates every field.
    """
    text = (raw or "").strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def parse_agent_evidence(raw: str, schema: type[AgentEvidence] = AgentEvidence) -> AgentEvidence:
    return schema.model_validate_json(_clean_json_text(raw))


def check_agent_scope(evidence: AgentEvidence, expected_agent: str) -> None:
    """Reject evidence from the wrong agent or outside the agent's role."""
    if evidence.agent_name != expected_agent:
        raise ValueError(
            f"Agent name mismatch: expected {expected_agent}, got {evidence.agent_name}"
        )
    allowed = ALLOWED_HYPOTHESES.get(expected_agent, set())
    if evidence.hypothesis not in allowed:
        raise ValueError(
            f"{expected_agent} returned out-of-scope hypothesis {evidence.hypothesis!r}"
        )


def call_agent_with_validation(
    llm_client: Callable[[str], str],
    prompt: str,
    schema: type[AgentEvidence],
) -> AgentEvidence:
    """Call the LLM client and validate output with JSON repair retry."""
    raw = llm_client(prompt)
    try:
        return parse_agent_evidence(raw, schema)
    except Exception as e:
        repair_prompt = f"""Your previous response was not valid JSON for the required schema.
Return only corrected JSON. No markdown. No prose.

Invalid response:
{raw}"""
        try:
            repaired = llm_client(repair_prompt)
            return parse_agent_evidence(repaired, schema)
        except Exception as e2:
            raise ValueError(f"Failed to parse LLM output after repair: {e2}") from e2


# ---------------------------------------------------------------------------
# Multi-turn tool-calling loop for retry_loop_agent
# ---------------------------------------------------------------------------

def call_agent_with_tools(
    client: Any,
    model: str,
    prompt: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Any],
    schema: type[AgentEvidence],
    max_tool_calls: int = MAX_RETRY_LOOP_TOOL_CALLS,
) -> AgentEvidence:
    """Multi-turn tool-calling loop. tool_executor maps (tool_name, args) -> result.
    Falls back to a single repair attempt if the final answer isn't valid JSON."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tool_calls_made = 0

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=0,
        )
        msg = response.choices[0].message

        if msg.tool_calls and tool_calls_made < max_tool_calls:
            messages.append(msg.model_dump(exclude_unset=True))
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = tool_executor(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
            tool_calls_made += 1
            continue

        raw = msg.content or ""
        try:
            return parse_agent_evidence(raw, schema)
        except Exception as e:
            repair_prompt = (
                "Your previous response was not valid JSON for the required "
                f"schema. Return only corrected JSON. No markdown. No prose.\n\n"
                f"Invalid response:\n{raw}"
            )
            messages.append({"role": "user", "content": repair_prompt})
            response2 = client.chat.completions.create(
                model=model, messages=messages, temperature=0,
            )
            repaired = response2.choices[0].message.content or ""
            try:
                return parse_agent_evidence(repaired, schema)
            except Exception as e2:
                raise ValueError(
                    f"Failed to parse LLM output after repair: {e2}"
                ) from e2


def run_retry_loop_agent_with_tools(
    anomaly: AnomalyWindow,
    store: Any,
    client: Any,
    model: str,
    provider: str,
) -> AgentRunResult:
    """Tool-using version of the retry_loop_agent. Falls back to the
    deterministic fallback_retry_loop on ANY failure."""

    def tool_executor(tool_name: str, args: dict) -> Any:
        if tool_name != "get_call_chain":
            raise ValueError(f"Unknown tool requested: {tool_name}")
        call_id = args["call_id"]
        chain = store.get_call_chain(call_id)
        return [
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

    telemetry = build_retry_loop_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_retry_loop_prompt_v2(telemetry_json)

    try:
        evidence = call_agent_with_tools(
            client=client,
            model=model,
            prompt=prompt,
            tools=RETRY_LOOP_TOOLS,
            tool_executor=tool_executor,
            schema=AgentEvidence,
        )
        check_agent_scope(evidence, "retry_loop_agent")
        return AgentRunResult(
            evidence=evidence,
            provider=provider,
            model=model,
            fallback_used=False,
            fallback_reason=None,
        )
    except Exception as exc:
        reason = f"Tool-use call failed: {type(exc).__name__}: {exc}"
        print(f"Fallback used: retry_loop_agent ({reason})")
        return AgentRunResult(
            evidence=fallback_retry_loop(anomaly),
            provider="fallback",
            model=model,
            fallback_used=True,
            fallback_reason=reason,
        )


def run_token_context_agent_with_tools(
    anomaly: AnomalyWindow,
    store: Any,
    client: Any,
    model: str,
    provider: str,
) -> AgentRunResult:
    """Tool-using version of the token_context_agent. Reuses get_call_chain
    (same tool as retry_loop_agent) since chain inspection is equally useful
    for detecting context bloat / recursion. Falls back to the deterministic
    fallback_token_context on ANY failure."""

    def tool_executor(tool_name: str, args: dict) -> Any:
        if tool_name != "get_call_chain":
            raise ValueError(f"Unknown tool requested: {tool_name}")
        call_id = args["call_id"]
        chain = store.get_call_chain(call_id)
        return [
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

    telemetry = build_token_context_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_token_context_prompt_v2(telemetry_json)

    try:
        evidence = call_agent_with_tools(
            client=client,
            model=model,
            prompt=prompt,
            tools=TOKEN_CONTEXT_TOOLS,
            tool_executor=tool_executor,
            schema=AgentEvidence,
        )
        check_agent_scope(evidence, "token_context_agent")
        return AgentRunResult(
            evidence=evidence,
            provider=provider,
            model=model,
            fallback_used=False,
            fallback_reason=None,
        )
    except Exception as exc:
        reason = f"Tool-use call failed: {type(exc).__name__}: {exc}"
        print(f"Fallback used: token_context_agent ({reason})")
        return AgentRunResult(
            evidence=fallback_token_context(anomaly),
            provider="fallback",
            model=model,
            fallback_used=True,
            fallback_reason=reason,
        )


def run_model_routing_agent_with_tools(
    anomaly: AnomalyWindow,
    store: Any,
    client: Any,
    model: str,
    provider: str,
) -> AgentRunResult:
    """Tool-using version of the model_routing_agent. Uses get_window_calls
    (wraps TelemetryStore.get_calls_for_feature) to retrieve per-call
    model/cost evidence. Falls back to fallback_model_routing on ANY failure."""

    def tool_executor(tool_name: str, args: dict) -> Any:
        if tool_name != "get_window_calls":
            raise ValueError(f"Unknown tool requested: {tool_name}")
        feature_tag = args["feature_tag"]
        calls = store.get_calls_for_feature(
            feature_tag, anomaly.start_time, anomaly.end_time
        )
        # Cap at 15 most-recent entries to avoid an oversized tool result on
        # a busy anomaly window — the model only needs a representative sample
        # to confirm whether cost growth tracks a specific model.
        recent_calls = calls[-15:]
        return [
            {
                "call_id": c.call_id,
                "model": c.model,
                "cost_usd": c.cost_usd,
                "timestamp": c.timestamp.isoformat().replace("+00:00", "Z"),
            }
            for c in reversed(recent_calls)  # most-recent first
        ]

    telemetry = build_model_routing_telemetry_v2(anomaly)
    telemetry_json = json.dumps(telemetry, indent=2)
    prompt = build_model_routing_prompt_v2(telemetry_json)

    try:
        evidence = call_agent_with_tools(
            client=client,
            model=model,
            prompt=prompt,
            tools=MODEL_ROUTING_TOOLS,
            tool_executor=tool_executor,
            schema=AgentEvidence,
        )
        check_agent_scope(evidence, "model_routing_agent")
        return AgentRunResult(
            evidence=evidence,
            provider=provider,
            model=model,
            fallback_used=False,
            fallback_reason=None,
        )
    except Exception as exc:
        reason = f"Tool-use call failed: {type(exc).__name__}: {exc}"
        print(f"Fallback used: model_routing_agent ({reason})")
        return AgentRunResult(
            evidence=fallback_model_routing(anomaly),
            provider="fallback",
            model=model,
            fallback_used=True,
            fallback_reason=reason,
        )


# ---------------------------------------------------------------------------
# Default simulated LLM client
# ---------------------------------------------------------------------------

def default_mock_llm_client(prompt: str) -> str:
    """A default simulated LLM client that responds based on prompt metrics."""
    parts = prompt.split("Telemetry:\n")
    if len(parts) < 2:
        parts = prompt.split("Telemetry:")
    
    telemetry = {}
    if len(parts) >= 2:
        telemetry_str = parts[1].strip()
        try:
            telemetry = json.loads(telemetry_str)
        except Exception:
            pass

    signals = telemetry.get("signals", {})

    if "retry_loop_agent" in prompt:
        retry_z_score = signals.get("retry_z_score", 0.0)
        max_retry_count = signals.get("max_retry_count", 0)
        avg_retry_count = signals.get("avg_retry_count", 0.0)
        repeated_parent_call_count = signals.get("repeated_parent_call_count", 0)

        if retry_z_score >= 5 and max_retry_count >= 5:
            confidence = 0.94
        elif retry_z_score >= 3 or repeated_parent_call_count >= 3:
            confidence = 0.82
        elif max_retry_count >= 2 or avg_retry_count >= 1:
            confidence = 0.60
        else:
            confidence = 0.30

        hypothesis = "uncapped_retry_loop" if confidence >= 0.50 else "no_strong_signal"
        return json.dumps({
            "agent_name": "retry_loop_agent",
            "hypothesis": hypothesis,
            "confidence": confidence,
            "supporting_metrics": {
                "retry_z_score": retry_z_score,
                "max_retry_count": max_retry_count,
                "avg_retry_count": avg_retry_count,
                "repeated_parent_call_count": repeated_parent_call_count,
            },
            "explanation": "Detected uncapped retry loop with high retry count and z-score." if hypothesis == "uncapped_retry_loop" else "No strong retry loop signal."
        })

    elif "token_context_agent" in prompt:
        input_token_growth_pct = signals.get("input_token_growth_pct", 0.0)
        max_call_chain_depth = signals.get("max_call_chain_depth", 0)
        input_tokens_z_score = signals.get("input_tokens_z_score", 0.0)

        if input_token_growth_pct >= 300 and max_call_chain_depth >= 5:
            confidence = 0.93
        elif input_tokens_z_score >= 3 and max_call_chain_depth >= 4:
            confidence = 0.84
        elif input_token_growth_pct >= 100 or input_tokens_z_score >= 3:
            confidence = 0.62
        else:
            confidence = 0.28

        hypothesis = "context_bloat_self_calling_agent" if confidence >= 0.50 else "no_strong_signal"
        return json.dumps({
            "agent_name": "token_context_agent",
            "hypothesis": hypothesis,
            "confidence": confidence,
            "supporting_metrics": {
                "input_token_growth_pct": input_token_growth_pct,
                "max_call_chain_depth": max_call_chain_depth,
                "input_tokens_z_score": input_tokens_z_score,
            },
            "explanation": "Detected significant input token growth and call chain depth." if hypothesis == "context_bloat_self_calling_agent" else "No strong token context bloat signal."
        })

    elif "model_routing_agent" in prompt:
        model_changed = signals.get("model_changed", False)
        cost_growth_pct = signals.get("cost_growth_pct", 0.0)
        token_growth_pct = signals.get("token_growth_pct", 0.0)
        cost_z_score = signals.get("cost_z_score", 0.0)

        if model_changed and cost_growth_pct >= 200 and abs(token_growth_pct) < 50:
            confidence = 0.95
        elif model_changed and cost_z_score >= 3:
            confidence = 0.84
        elif cost_growth_pct >= 100 and abs(token_growth_pct) < 50:
            confidence = 0.64
        else:
            confidence = 0.32

        hypothesis = "expensive_model_misroute" if confidence >= 0.50 else "no_strong_signal"
        return json.dumps({
            "agent_name": "model_routing_agent",
            "hypothesis": hypothesis,
            "confidence": confidence,
            "supporting_metrics": {
                "model_changed": model_changed,
                "cost_growth_pct": cost_growth_pct,
                "token_growth_pct": token_growth_pct,
                "cost_z_score": cost_z_score,
            },
            "explanation": "Detected expensive model misroute with model change and stable tokens." if hypothesis == "expensive_model_misroute" else "No strong model routing signal."
        })

    return "{}"


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_agents(
    agent_names: list[str],
    anomaly: AnomalyWindow,
    llm_client: Callable[[str], str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[AgentEvidence]:
    """Run the requested diagnostic agents against an anomaly window."""
    return [
        result.evidence
        for result in run_agents_detailed(
            agent_names,
            anomaly,
            llm_client=llm_client,
            provider=provider,
            model=model,
        )
    ]


def run_agents_detailed(
    agent_names: list[str],
    anomaly: AnomalyWindow,
    llm_client: Callable[[str], str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    telemetry_store: Any | None = None,
    raw_sdk_client: Any | None = None,
) -> list[AgentRunResult]:
    """Run diagnostic agents and include provider/fallback provenance.
    
    When telemetry_store and raw_sdk_client are both provided and the provider
    is not fallback, the retry_loop_agent uses the tool-using path instead of
    the single-shot prompt."""
    if llm_client is None:
        from llm_cost_investigator.llm_client import LLMClientConfig, resolve_llm_client

        try:
            resolved = resolve_llm_client(LLMClientConfig(provider=provider, model=model))
            llm_client = resolved.client
            raw_sdk_client = raw_sdk_client or resolved.raw_client
            resolved_provider = resolved.provider
            resolved_model = resolved.model
            selection_fallback_reason = resolved.fallback_reason
        except Exception as exc:
            llm_client = default_mock_llm_client
            resolved_provider = "fallback"
            resolved_model = model
            selection_fallback_reason = (
                "LLM client setup failed: "
                f"{type(exc).__name__}: {exc}"
            )
    else:
        resolved_provider = provider or "fallback"
        resolved_model = model
        selection_fallback_reason = None

    from llm_cost_investigator.agent_skills import score_all_agents
    from llm_cost_investigator.evidence_graph import build_graph, summarize_graph
    from llm_cost_investigator.knowledge_base import match_patterns

    all_packs = score_all_agents(anomaly)
    graph_summary = summarize_graph(build_graph(anomaly, all_packs))

    results = []
    for name in agent_names:
        if name not in (
            "retry_loop_agent",
            "token_context_agent",
            "model_routing_agent",
        ):
            continue

        skill_pack = all_packs[name]
        kb = match_patterns(name, anomaly, skill_pack)
        skills_json = json.dumps(skill_pack.get("skills") or {}, indent=2, default=str)
        kb_json = json.dumps(kb, indent=2, default=str)

        # Falsifier failed → skip LLM, return deterministic no_strong_signal
        if skill_pack.get("forced_no_strong_signal"):
            if name == "retry_loop_agent":
                evidence = fallback_retry_loop(anomaly)
            elif name == "token_context_agent":
                evidence = fallback_token_context(anomaly)
            else:
                evidence = fallback_model_routing(anomaly)
            results.append(
                AgentRunResult(
                    evidence=evidence,
                    provider="fallback",
                    model=resolved_model,
                    fallback_used=True,
                    fallback_reason="hard skill falsifier failed",
                )
            )
            continue

        # Tool-using path when live client + store available
        if (
            resolved_provider != "fallback"
            and telemetry_store is not None
            and raw_sdk_client is not None
        ):
            if name == "retry_loop_agent":
                result = run_retry_loop_agent_with_tools(
                    anomaly=anomaly,
                    store=telemetry_store,
                    client=raw_sdk_client,
                    model=resolved_model,
                    provider=resolved_provider,
                )
            elif name == "token_context_agent":
                result = run_token_context_agent_with_tools(
                    anomaly=anomaly,
                    store=telemetry_store,
                    client=raw_sdk_client,
                    model=resolved_model,
                    provider=resolved_provider,
                )
            else:
                result = run_model_routing_agent_with_tools(
                    anomaly=anomaly,
                    store=telemetry_store,
                    client=raw_sdk_client,
                    model=resolved_model,
                    provider=resolved_provider,
                )
            capped = _apply_skill_pack_to_evidence(result.evidence, skill_pack, kb)
            results.append(result.model_copy(update={"evidence": capped}))
            continue

        # Single-shot path
        if name == "retry_loop_agent":
            telemetry = build_retry_loop_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_retry_loop_prompt(
                telemetry_json,
                hard_skill_results_json=skills_json,
                kb_matches_json=kb_json,
                evidence_graph_summary=graph_summary,
            )
            fallback_fn = fallback_retry_loop
        elif name == "token_context_agent":
            telemetry = build_token_context_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_token_context_prompt(
                telemetry_json,
                hard_skill_results_json=skills_json,
                kb_matches_json=kb_json,
                evidence_graph_summary=graph_summary,
            )
            fallback_fn = fallback_token_context
        else:
            telemetry = build_model_routing_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_model_routing_prompt(
                telemetry_json,
                hard_skill_results_json=skills_json,
                kb_matches_json=kb_json,
                evidence_graph_summary=graph_summary,
            )
            fallback_fn = fallback_model_routing

        if resolved_provider == "fallback":
            evidence = fallback_fn(anomaly)
            reason_suffix = f" ({selection_fallback_reason})" if selection_fallback_reason else ""
            print(f"Fallback used: {name}{reason_suffix}")
            results.append(
                AgentRunResult(
                    evidence=evidence,
                    provider="fallback",
                    model=resolved_model,
                    fallback_used=True,
                    fallback_reason=selection_fallback_reason,
                )
            )
            continue

        try:
            evidence = call_agent_with_validation(llm_client, prompt, AgentEvidence)
            check_agent_scope(evidence, name)
            evidence = _apply_skill_pack_to_evidence(evidence, skill_pack, kb)
            results.append(
                AgentRunResult(
                    evidence=evidence,
                    provider=resolved_provider,
                    model=resolved_model,
                    fallback_used=False,
                    fallback_reason=None,
                )
            )
        except Exception as exc:
            fallback_reason = (
                "LLM call failed or returned invalid agent evidence: "
                f"{type(exc).__name__}: {exc}"
            )
            reason_suffix = f" ({fallback_reason})" if fallback_reason else ""
            print(f"Fallback used: {name}{reason_suffix}")
            results.append(
                AgentRunResult(
                    evidence=fallback_fn(anomaly),
                    provider="fallback",
                    model=resolved_model,
                    fallback_used=True,
                    fallback_reason=fallback_reason,
                )
            )

    return results
