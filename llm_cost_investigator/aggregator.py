"""Deterministic aggregation of agent evidence."""

from __future__ import annotations

from typing import Any

from llm_cost_investigator.schemas import AgentEvidence, AnomalyWindow, RootCauseResult

TIE_BREAK_RANK = {
    "expensive_model_misroute": 3,
    "uncapped_retry_loop": 2,
    "context_bloat_self_calling_agent": 1,
    "no_strong_signal": 0,
}

TIE_EPSILON = 0.05
DEMOTE_CEILING = 0.74


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


def _skill_verdict(pack: dict[str, Any] | None, skill_name: str) -> str:
    if not pack:
        return "fail"
    skills = pack.get("skills") or {}
    entry = skills.get(skill_name) or {}
    return str(entry.get("verdict", "fail"))


def _strong_model_routing(pack: dict[str, Any] | None) -> bool:
    """True when model-routing hard skills show a clean premium misroute."""
    return (
        _skill_verdict(pack, "model_changed_flag") == "pass"
        and _skill_verdict(pack, "cost_growth_threshold") == "pass"
        and _skill_verdict(pack, "token_stability_check") == "pass"
    )


def _strong_token_bloat(pack: dict[str, Any] | None) -> bool:
    return (
        _skill_verdict(pack, "token_growth_threshold") == "pass"
        and _skill_verdict(pack, "chain_depth_floor") == "pass"
    )


def _demote_reasons(
    hypothesis: str,
    all_packs: dict[str, dict[str, Any]],
) -> list[str]:
    """Return demote reason strings; empty if no demotion."""
    reasons: list[str] = []
    retry_p = all_packs.get("retry_loop_agent")
    token_p = all_packs.get("token_context_agent")
    model_p = all_packs.get("model_routing_agent")

    if hypothesis == "expensive_model_misroute":
        if _skill_verdict(token_p, "token_growth_threshold") == "pass":
            reasons.append(
                "token_context.token_growth_threshold=pass contradicts pure model misroute"
            )
        if _skill_verdict(model_p, "token_stability_check") == "fail":
            reasons.append(
                "model_routing.token_stability_check=fail (token growth confounds routing)"
            )

    elif hypothesis == "uncapped_retry_loop":
        if _strong_model_routing(model_p):
            reasons.append(
                "model_routing pack is a strong pure misroute (changed+cost+stable tokens)"
            )
        if _strong_token_bloat(token_p):
            reasons.append(
                "token_context pack is strong context bloat (growth+depth pass)"
            )

    elif hypothesis == "context_bloat_self_calling_agent":
        if _strong_model_routing(model_p):
            reasons.append(
                "model_routing pack is a strong pure misroute (changed+cost+stable tokens)"
            )

    return reasons


def apply_cross_agent_adjustments(
    evidence: list[AgentEvidence],
    all_packs: dict[str, dict[str, Any]],
) -> list[AgentEvidence]:
    """Demote confidences when another agent's hard skills contradict the story.

    Competitor signals must be **pass** (not soft_pass). Demote caps ceiling at
    0.74; does not force no_strong_signal.
    """
    adjusted: list[AgentEvidence] = []
    for item in evidence:
        if item.hypothesis == "no_strong_signal":
            adjusted.append(item)
            continue

        reasons = _demote_reasons(item.hypothesis, all_packs)
        if not reasons:
            adjusted.append(item)
            continue

        metrics = dict(item.supporting_metrics)
        pre = metrics.get("skill_band_ceiling")
        try:
            pre_f = float(pre) if pre is not None else float(item.confidence)
        except (TypeError, ValueError):
            pre_f = float(item.confidence)

        metrics["skill_band_ceiling_pre_demote"] = pre_f
        new_ceiling = min(pre_f, DEMOTE_CEILING)
        metrics["skill_band_ceiling"] = new_ceiling
        metrics["cross_demoted"] = True
        metrics["cross_demote_reason"] = "; ".join(reasons)

        new_conf = min(float(item.confidence), new_ceiling)
        adjusted.append(
            item.model_copy(
                update={
                    "confidence": new_conf,
                    "supporting_metrics": metrics,
                }
            )
        )
    return adjusted


def select_root_cause(
    evidence: list[AgentEvidence],
    *,
    anomaly: AnomalyWindow | None = None,
    skill_packs: dict[str, dict[str, Any]] | None = None,
) -> RootCauseResult:
    """Choose the winning root cause from validated agent evidence.

    Rules:
    - Optional cross-agent demote using skill_packs / anomaly
    - Drop no_strong_signal
    - Cap confidence by skill_band_ceiling in supporting_metrics
    - Tie-break order only when combined confidences are within TIE_EPSILON
    - If no winner and packs available: attach X-02/X-03 attribution
    """
    packs = skill_packs
    if packs is None and anomaly is not None:
        from llm_cost_investigator.agent_skills import score_all_agents

        packs = score_all_agents(anomaly)

    working = list(evidence)
    if packs is not None:
        working = apply_cross_agent_adjustments(working, packs)

    valid = [item for item in working if item.hypothesis != "no_strong_signal"]

    if not valid:
        attribution_pattern: str | None = None
        attribution_note: str | None = None
        if packs is not None:
            from llm_cost_investigator.knowledge_base import (
                match_cross_agent,
                primary_cross_pattern,
            )

            primary = primary_cross_pattern(match_cross_agent(packs))
            if primary:
                attribution_pattern = str(primary.get("pattern_id") or "") or None
                attribution_note = str(primary.get("story") or "") or None
        return RootCauseResult(
            hypothesis="no_strong_signal",
            confidence=0.0,
            winning_agent=None,
            evidence=working,
            attribution_pattern=attribution_pattern,
            attribution_note=attribution_note,
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
        evidence=working,
        attribution_pattern=None,
        attribution_note=None,
    )
