"""Deterministic aggregation of agent evidence."""

from __future__ import annotations

from llm_cost_investigator.schemas import AgentEvidence, RootCauseResult


TIE_BREAK_RANK = {
    "expensive_model_misroute": 3,
    "uncapped_retry_loop": 2,
    "context_bloat_self_calling_agent": 1,
    "no_strong_signal": 0,
}


def select_root_cause(evidence: list[AgentEvidence]) -> RootCauseResult:
    """Choose the winning root cause from validated agent evidence."""
    valid = [item for item in evidence if item.hypothesis != "no_strong_signal"]

    if not valid:
        return RootCauseResult(
            hypothesis="no_strong_signal",
            confidence=0.0,
            winning_agent=None,
            evidence=evidence,
        )

    winner = max(
        valid,
        key=lambda item: (item.confidence, TIE_BREAK_RANK.get(item.hypothesis, 0)),
    )

    return RootCauseResult(
        hypothesis=winner.hypothesis,
        confidence=winner.confidence,
        winning_agent=winner.agent_name,
        evidence=evidence,
    )

