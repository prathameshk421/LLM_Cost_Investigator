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
    candidates: list[tuple[str, int]] = []

    model_score = _model_routing_score(anomaly)
    if model_score >= 5:
        candidates.append(("model_routing_agent", model_score))

    retry_score = _retry_loop_score(anomaly)
    if retry_score >= 3:
        candidates.append(("retry_loop_agent", retry_score))

    context_score = _token_context_score(anomaly)
    if context_score >= 3:
        candidates.append(("token_context_agent", context_score))

    if not candidates and signals.cost_z_score >= 3:
        return ["model_routing_agent"]

    candidates.sort(key=lambda item: (-item[1], AGENT_PRIORITY[item[0]]))
    return [agent_name for agent_name, _score in candidates[:MAX_AGENTS]]


def _model_routing_score(anomaly: AnomalyWindow) -> int:
    signals = anomaly.signals
    score = 0

    if signals.model_changed:
        score += 3
    if signals.cost_growth_pct is not None and signals.cost_growth_pct >= 200:
        score += 2
    if signals.token_growth_pct is not None and abs(signals.token_growth_pct) < 50:
        score += 2
    if signals.retry_z_score < 3 and signals.max_retry_count < 3:
        score += 1

    return score


def _retry_loop_score(anomaly: AnomalyWindow) -> int:
    signals = anomaly.signals
    score = 0

    if signals.retry_z_score >= 3:
        score += 3
    if signals.max_retry_count >= 5:
        score += 3
    elif signals.max_retry_count >= 3:
        score += 2
    if signals.repeated_parent_call_count > 0:
        score += 2
    if signals.avg_retry_count >= 1:
        score += 1

    return score


def _token_context_score(anomaly: AnomalyWindow) -> int:
    signals = anomaly.signals
    score = 0

    if signals.input_token_growth_pct is not None and signals.input_token_growth_pct >= 100:
        score += 3
    if signals.max_call_chain_depth >= 4:
        score += 3
    elif signals.max_call_chain_depth >= 2:
        score += 1
    if (
        signals.input_tokens_z_score >= 3
        and signals.retry_z_score < 3
        and signals.max_retry_count < 3
        and not signals.model_changed
    ):
        score += 2
    if signals.retry_z_score < 3 and not signals.model_changed:
        score += 1

    return score
