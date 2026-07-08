"""Deterministic routing for diagnostic agents."""

from __future__ import annotations

from llm_cost_investigator.schemas import AnomalyWindow

MAX_AGENTS = 2

AGENT_PRIORITY = {
    "model_routing_agent": 0,
    "retry_loop_agent": 1,
    "token_context_agent": 2,
}


def route_agents(anomaly: AnomalyWindow) -> list[str]:
    """Select the agents worth calling for an anomaly.

    The router is deterministic cost control. It chooses which diagnostic
    agents are worth paying for; it does not decide final root cause.
    """
    signals = anomaly.signals
    candidates: list[str] = []

    if _route_model_routing(signals):
        candidates.append("model_routing_agent")

    if _route_retry_loop(signals):
        candidates.append("retry_loop_agent")

    if _route_token_context(signals):
        candidates.append("token_context_agent")

    if not candidates:
        return _fallback_route(signals)

    candidates.sort(key=lambda name: AGENT_PRIORITY.get(name, 99))
    return candidates[:MAX_AGENTS]


def _route_model_routing(signals) -> bool:
    """Route to model_routing_agent when:
    - cost_z_score >= 3 and token_growth_pct < 50, or
    - model_changed is True
    """
    if signals.model_changed:
        return True
    if signals.cost_z_score >= 3.0:
        token_pct = signals.token_growth_pct
        return token_pct is not None and abs(token_pct) < 50
    return False


def _route_retry_loop(signals) -> bool:
    """Route to retry_loop_agent when:
    - retry_z_score >= 3 or max_retry_count >= 3
    """
    return signals.retry_z_score >= 3.0 or signals.max_retry_count >= 3


def _route_token_context(signals) -> bool:
    """Route to token_context_agent when:
    - input_tokens_z_score >= 3, or
    - input_token_growth_pct >= 100, or
    - max_call_chain_depth >= 4
    """
    if signals.input_tokens_z_score >= 3.0:
        return True
    if signals.input_token_growth_pct is not None and signals.input_token_growth_pct >= 100:
        return True
    return signals.max_call_chain_depth >= 4


def _fallback_route(signals) -> list[str]:
    """Fallback: route to the agent whose primary signal best matches."""
    scores: list[tuple[str, float]] = []

    if signals.max_retry_count >= 2 or signals.avg_retry_count >= 1:
        scores.append(("retry_loop_agent", signals.retry_z_score))
    if signals.max_call_chain_depth >= 2 or (signals.input_token_growth_pct or 0) >= 50:
        scores.append(("token_context_agent", signals.input_tokens_z_score))
    if signals.cost_growth_pct is not None and signals.cost_growth_pct > 50:
        scores.append(("model_routing_agent", signals.cost_z_score))

    if not scores:
        return ["model_routing_agent"]

    scores.sort(key=lambda item: (-item[1], AGENT_PRIORITY.get(item[0], 99)))
    return [scores[0][0]]
