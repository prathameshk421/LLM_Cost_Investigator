"""Failure-pattern knowledge base for diagnostic agents."""

from __future__ import annotations

from typing import Any

from llm_cost_investigator.schemas import AnomalyWindow

# Pattern catalog (docs/agents/agent_system_upgrade.md Section 2)
PATTERNS: list[dict[str, Any]] = [
    {
        "pattern_id": "RL-01",
        "owner": "retry_loop_agent",
        "band_low": 0.90,
        "band_high": 1.00,
        "story": "Transient upstream failures with no backoff cap (retry storm).",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "RL-02",
        "owner": "retry_loop_agent",
        "band_low": 0.75,
        "band_high": 0.89,
        "story": "Elevated retries with moderate parent fan-out.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "RL-03",
        "owner": "retry_loop_agent",
        "band_low": 0.0,
        "band_high": 0.49,
        "story": "Retries present but not driving spend (low cost attribution).",
        "forces_no_signal": True,
    },
    {
        "pattern_id": "TC-01",
        "owner": "token_context_agent",
        "band_low": 0.90,
        "band_high": 1.00,
        "story": "Recursive agent re-embeds growing history each call.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "TC-02",
        "owner": "token_context_agent",
        "band_low": 0.75,
        "band_high": 0.89,
        "story": "Gradual prompt/context creep with elevated token z-score.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "TC-03",
        "owner": "token_context_agent",
        "band_low": 0.0,
        "band_high": 0.49,
        "story": "Deep pipeline without token growth — likely not bloat.",
        "forces_no_signal": True,
    },
    {
        "pattern_id": "MR-01",
        "owner": "model_routing_agent",
        "band_low": 0.90,
        "band_high": 1.00,
        "story": "Traffic shifted to a pricier model with stable tokens.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "MR-02",
        "owner": "model_routing_agent",
        "band_low": 0.75,
        "band_high": 0.89,
        "story": "Model change with strong cost z-score; partial shift.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "MR-03",
        "owner": "model_routing_agent",
        "band_low": 0.50,
        "band_high": 0.74,
        "story": "Model change confounded by large token growth.",
        "forces_no_signal": False,
    },
    {
        "pattern_id": "X-02",
        "owner": "cross_agent",
        "band_low": 0.0,
        "band_high": 0.49,
        "story": "No agent soft-pass — likely legitimate volume, not a bug.",
        "forces_no_signal": True,
    },
    {
        "pattern_id": "X-03",
        "owner": "cross_agent",
        "band_low": 0.0,
        "band_high": 0.49,
        "story": "Sample/aggregate consistency failure — data-quality issue.",
        "forces_no_signal": True,
    },
]


def _v(skill_pack: dict[str, Any], name: str) -> str:
    skills = skill_pack.get("skills") or {}
    entry = skills.get(name) or {}
    return str(entry.get("verdict", "fail"))


def match_patterns(
    agent_name: str,
    anomaly: AnomalyWindow,
    skill_pack: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return matched KB patterns for one agent (plus cross-agent when relevant)."""
    matches: list[dict[str, Any]] = []
    sig = anomaly.signals
    metrics = skill_pack.get("metrics") or {}

    if agent_name == "retry_loop_agent":
        attr = metrics.get("cost_attribution_pct")
        if _v(skill_pack, "cost_attribution_check") == "fail" or (
            attr is not None and float(attr) < 20
        ):
            matches.append(_row("RL-03"))
        elif (
            _v(skill_pack, "retry_zscore_threshold") == "pass"
            and _v(skill_pack, "max_retry_floor") == "pass"
            and _v(skill_pack, "cost_attribution_check") == "pass"
        ):
            matches.append(_row("RL-01"))
        elif _v(skill_pack, "retry_zscore_threshold") in ("pass", "soft_pass") and _v(
            skill_pack, "max_retry_floor"
        ) in ("pass", "soft_pass"):
            matches.append(_row("RL-02"))

        if _v(skill_pack, "sample_call_consistency") == "fail":
            matches.append(_row("X-03"))

    elif agent_name == "token_context_agent":
        growth_raw = sig.input_token_growth_pct
        depth = int(sig.max_call_chain_depth)
        # TC-03 only when growth is known and low (None ≠ 0% growth).
        if depth >= 5 and growth_raw is not None and growth_raw < 100:
            matches.append(_row("TC-03"))
        elif (
            _v(skill_pack, "token_growth_threshold") == "pass"
            and _v(skill_pack, "chain_depth_floor") == "pass"
        ):
            matches.append(_row("TC-01"))
        elif _v(skill_pack, "token_zscore_check") == "pass" and depth >= 3:
            matches.append(_row("TC-02"))

        if _v(skill_pack, "sample_consistency_check") == "fail":
            matches.append(_row("X-03"))

    elif agent_name == "model_routing_agent":
        if (
            _v(skill_pack, "model_changed_flag") == "pass"
            and _v(skill_pack, "cost_growth_threshold") == "pass"
            and _v(skill_pack, "token_stability_check") == "pass"
        ):
            matches.append(_row("MR-01"))
        elif (
            _v(skill_pack, "model_changed_flag") == "pass"
            and _v(skill_pack, "token_stability_check") == "fail"
        ):
            matches.append(_row("MR-03"))
        elif (
            _v(skill_pack, "model_changed_flag") == "pass"
            and _v(skill_pack, "cost_zscore_check") == "pass"
        ):
            matches.append(_row("MR-02"))

    # Deduplicate by pattern_id
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in matches:
        pid = m["pattern_id"]
        if pid not in seen:
            seen.add(pid)
            out.append(m)
    return out


def match_cross_agent(
    all_packs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Cross-agent patterns when all three score weak or data quality fails."""
    matches: list[dict[str, Any]] = []
    any_strong = False
    any_x3 = False
    for pack in all_packs.values():
        if pack.get("suggested_hypothesis") != "no_strong_signal" and not pack.get(
            "forced_no_strong_signal"
        ):
            if float(pack.get("band_ceiling", 0)) >= 0.50:
                any_strong = True
        for name, skill in (pack.get("skills") or {}).items():
            if skill.get("falsifier") and skill.get("verdict") == "fail":
                if "sample" in name or "consistency" in name:
                    any_x3 = True
    # Prefer X-03 first when both apply (data quality > "volume" explanation)
    if any_x3:
        matches.append(_row("X-03"))
    if not any_strong:
        matches.append(_row("X-02"))
    return matches


def primary_cross_pattern(
    matches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick a single cross-agent pattern: X-03 over X-02 over other."""
    by_id = {m.get("pattern_id"): m for m in matches if m.get("pattern_id")}
    if "X-03" in by_id:
        return by_id["X-03"]
    if "X-02" in by_id:
        return by_id["X-02"]
    if matches:
        return matches[0]
    return None


def _row(pattern_id: str) -> dict[str, Any]:
    for p in PATTERNS:
        if p["pattern_id"] == pattern_id:
            return {
                "pattern_id": p["pattern_id"],
                "owner": p["owner"],
                "band_low": p["band_low"],
                "band_high": p["band_high"],
                "story": p["story"],
                "forces_no_signal": p["forces_no_signal"],
            }
    return {"pattern_id": pattern_id, "story": "unknown pattern"}


def pattern_band_ceiling(matches: list[dict[str, Any]], skill_ceiling: float) -> float:
    """Tighten ceiling using matched pattern high band when present.

    Any ``forces_no_signal`` match (e.g. X-03 data quality) caps at 0.49 even
    when a strong narrative pattern is also listed.
    """
    if not matches:
        return skill_ceiling
    if any(m.get("forces_no_signal") for m in matches):
        return min(skill_ceiling, 0.49)
    highs = [float(m.get("band_high", 1.0)) for m in matches]
    if highs:
        return min(skill_ceiling, max(highs))
    return skill_ceiling
