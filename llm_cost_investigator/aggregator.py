"""Deterministic aggregation of agent evidence."""

from __future__ import annotations

from llm_cost_investigator.schemas import AgentEvidence, RootCauseResult

TIE_BREAK_RANK = {
    "expensive_model_misroute": 3,
    "uncapped_retry_loop": 2,
    "context_bloat_self_calling_agent": 1,
    "no_strong_signal": 0,
}

TIE_EPSILON = 0.05


def _combined_confidence(item: AgentEvidence) -> float:
    """Cap LLM/fallback confidence by skill_band_ceiling when present."""
    ceiling = item.supporting_metrics.get("skill_band_ceiling")
    conf = float(item.confidence)
    if ceiling is None:
        return conf
    try:
        return min(conf, float(ceiling))
    except (TypeError, ValueError):
        return conf


def select_root_cause(evidence: list[AgentEvidence]) -> RootCauseResult:
    """Choose the winning root cause from validated agent evidence.

    Rules:
    - Drop no_strong_signal
    - Cap confidence by skill_band_ceiling in supporting_metrics
    - Tie-break order only when combined confidences are within TIE_EPSILON
    """
    valid = [item for item in evidence if item.hypothesis != "no_strong_signal"]

    if not valid:
        return RootCauseResult(
            hypothesis="no_strong_signal",
            confidence=0.0,
            winning_agent=None,
            evidence=evidence,
        )

    scored: list[tuple[float, AgentEvidence]] = [
        (_combined_confidence(item), item) for item in valid
    ]
    scored.sort(
        key=lambda pair: (
            pair[0],
            TIE_BREAK_RANK.get(pair[1].hypothesis, 0),
        ),
        reverse=True,
    )

    best_conf, best = scored[0]
    if len(scored) > 1:
        # If top two are within epsilon, re-pick by tie-break rank among near-ties
        near = [
            (c, e)
            for c, e in scored
            if abs(c - best_conf) <= TIE_EPSILON
        ]
        if len(near) > 1:
            best_conf, best = max(
                near,
                key=lambda pair: (
                    TIE_BREAK_RANK.get(pair[1].hypothesis, 0),
                    pair[0],
                ),
            )

    return RootCauseResult(
        hypothesis=best.hypothesis,
        confidence=best_conf,
        winning_agent=best.agent_name,
        evidence=evidence,
    )
