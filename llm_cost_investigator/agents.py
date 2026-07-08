"""LLM-backed diagnostic agent entrypoints and fallbacks."""

from __future__ import annotations

import json
from typing import Any, Callable

from llm_cost_investigator.schemas import AgentEvidence, AnomalyWindow


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
# Prompt builders
# ---------------------------------------------------------------------------

def build_retry_loop_prompt(telemetry_json: str) -> str:
    """Build the prompt for the Retry Loop Agent."""
    return f"""You are the Retry Loop Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by an uncapped retry loop or repeated failed calls.

Look for:
- high retry_count
- repeated child calls from the same parent_call_id
- latency growth consistent with retries
- cost growth caused by repeated attempts

Use only the telemetry provided.
Do not invent missing metrics.
Return only valid JSON.
Do not include markdown.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.

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


def build_token_context_prompt(telemetry_json: str) -> str:
    """Build the prompt for the Token Context Agent."""
    return f"""You are the Token Context Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by context bloat, recursive self-calling behavior, or growing prompts.

Look for:
- input_tokens increasing over time
- deep parent_call_id chains
- agent_reflection or similar features calling themselves
- cost growth explained by larger context, not retries or model changes

Use only the telemetry provided.
Do not invent missing metrics.
Return only valid JSON.
Do not include markdown.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.

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


def build_model_routing_prompt(telemetry_json: str) -> str:
    """Build the prompt for the Model Routing Agent."""
    return f"""You are the Model Routing Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by a feature being routed to a more expensive model.

Look for:
- same feature_tag using a different model during the anomaly
- cost_usd increasing sharply
- input/output tokens staying roughly stable
- no major retry spike

Use only the telemetry provided.
Do not invent missing metrics.
Return only valid JSON.
Do not include markdown.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.

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
# Deterministic fallbacks
# ---------------------------------------------------------------------------

def fallback_retry_loop(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Retry Loop Agent when LLM calls fail."""
    signals = anomaly.signals
    retry_z_score = signals.retry_z_score
    max_retry_count = signals.max_retry_count
    avg_retry_count = signals.avg_retry_count
    repeated_parent_call_count = signals.repeated_parent_call_count

    if retry_z_score >= 5 and max_retry_count >= 5:
        confidence = 0.94
    elif retry_z_score >= 3 or repeated_parent_call_count >= 3:
        confidence = 0.82
    elif max_retry_count >= 2 or avg_retry_count >= 1:
        confidence = 0.60
    else:
        confidence = 0.30

    hypothesis = "uncapped_retry_loop" if confidence >= 0.50 else "no_strong_signal"
    
    supporting_metrics = {
        "retry_z_score": retry_z_score,
        "max_retry_count": max_retry_count,
        "avg_retry_count": avg_retry_count,
        "repeated_parent_call_count": repeated_parent_call_count,
    }
    
    explanation = (
        "Retry metrics crossed the high-confidence retry-loop thresholds."
        if hypothesis == "uncapped_retry_loop"
        else "Weak retry evidence."
    )
    explanation += " (Fallback)"

    return AgentEvidence(
        agent_name="retry_loop_agent",
        hypothesis=hypothesis,
        confidence=confidence,
        supporting_metrics=supporting_metrics,
        explanation=explanation,
    )


def fallback_token_context(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Token Context Agent when LLM calls fail."""
    signals = anomaly.signals
    input_token_growth_pct = signals.input_token_growth_pct or 0.0
    max_call_chain_depth = signals.max_call_chain_depth
    input_tokens_z_score = signals.input_tokens_z_score

    if input_token_growth_pct >= 300 and max_call_chain_depth >= 5:
        confidence = 0.93
    elif input_tokens_z_score >= 3 and max_call_chain_depth >= 4:
        confidence = 0.84
    elif input_token_growth_pct >= 100 or input_tokens_z_score >= 3:
        confidence = 0.62
    else:
        confidence = 0.28

    hypothesis = "context_bloat_self_calling_agent" if confidence >= 0.50 else "no_strong_signal"
    
    supporting_metrics = {
        "input_token_growth_pct": input_token_growth_pct,
        "max_call_chain_depth": max_call_chain_depth,
        "input_tokens_z_score": input_tokens_z_score,
    }
    
    explanation = (
        "Token metrics crossed the context-bloat thresholds."
        if hypothesis == "context_bloat_self_calling_agent"
        else "Weak token-growth evidence."
    )
    explanation += " (Fallback)"

    return AgentEvidence(
        agent_name="token_context_agent",
        hypothesis=hypothesis,
        confidence=confidence,
        supporting_metrics=supporting_metrics,
        explanation=explanation,
    )


def fallback_model_routing(anomaly: AnomalyWindow) -> AgentEvidence:
    """Fallback logic for the Model Routing Agent when LLM calls fail."""
    signals = anomaly.signals
    model_changed = signals.model_changed
    cost_growth_pct = signals.cost_growth_pct or 0.0
    token_growth_pct = signals.token_growth_pct or 0.0
    cost_z_score = signals.cost_z_score

    if model_changed and cost_growth_pct >= 200 and abs(token_growth_pct) < 50:
        confidence = 0.95
    elif model_changed and cost_z_score >= 3:
        confidence = 0.84
    elif cost_growth_pct >= 100 and abs(token_growth_pct) < 50:
        confidence = 0.64
    else:
        confidence = 0.32

    hypothesis = "expensive_model_misroute" if confidence >= 0.50 else "no_strong_signal"

    supporting_metrics = {
        "model_changed": model_changed,
        "cost_growth_pct": cost_growth_pct,
        "token_growth_pct": token_growth_pct,
        "cost_z_score": cost_z_score,
    }

    explanation = (
        "Model routing metrics crossed the expensive model misroute thresholds."
        if hypothesis == "expensive_model_misroute"
        else "Weak model-change evidence."
    )
    explanation += " (Fallback)"

    return AgentEvidence(
        agent_name="model_routing_agent",
        hypothesis=hypothesis,
        confidence=confidence,
        supporting_metrics=supporting_metrics,
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Validation wrapper
# ---------------------------------------------------------------------------

def call_agent_with_validation(
    llm_client: Callable[[str], str],
    prompt: str,
    schema: type[AgentEvidence],
) -> AgentEvidence:
    """Call the LLM client and validate output with JSON repair retry."""
    raw = llm_client(prompt)
    try:
        return schema.model_validate_json(raw)
    except Exception as e:
        repair_prompt = f"""Your previous response was not valid JSON for the required schema.
Return only corrected JSON. No markdown. No prose.

Invalid response:
{raw}"""
        try:
            repaired = llm_client(repair_prompt)
            return schema.model_validate_json(repaired)
        except Exception as e2:
            raise ValueError(f"Failed to parse LLM output after repair: {e2}") from e2


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
) -> list[AgentEvidence]:
    """Run the requested diagnostic agents against an anomaly window."""
    if llm_client is None:
        llm_client = default_mock_llm_client

    results = []
    for name in agent_names:
        if name == "retry_loop_agent":
            telemetry = build_retry_loop_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_retry_loop_prompt(telemetry_json)
            fallback_fn = fallback_retry_loop
        elif name == "token_context_agent":
            telemetry = build_token_context_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_token_context_prompt(telemetry_json)
            fallback_fn = fallback_token_context
        elif name == "model_routing_agent":
            telemetry = build_model_routing_telemetry(anomaly)
            telemetry_json = json.dumps(telemetry, indent=2)
            prompt = build_model_routing_prompt(telemetry_json)
            fallback_fn = fallback_model_routing
        else:
            continue

        try:
            evidence = call_agent_with_validation(llm_client, prompt, AgentEvidence)
            if evidence.agent_name != name:
                raise ValueError(f"Agent name mismatch: expected {name}, got {evidence.agent_name}")
            results.append(evidence)
        except Exception:
            results.append(fallback_fn(anomaly))

    return results
