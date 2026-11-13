"""Lightweight in-memory evidence graph → compact prompt summary."""

from __future__ import annotations

from typing import Any

from llm_cost_investigator.schemas import AnomalyWindow

HYPOTHESIS_BY_AGENT = {
    "retry_loop_agent": "uncapped_retry_loop",
    "token_context_agent": "context_bloat_self_calling_agent",
    "model_routing_agent": "expensive_model_misroute",
}


def build_graph(
    anomaly: AnomalyWindow,
    all_packs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a simple dict graph (nodes + edges)."""
    nodes: list[dict[str, Any]] = [
        {
            "id": "anomaly",
            "type": "AnomalyNode",
            "feature_tag": anomaly.feature_tag,
            "start": anomaly.start_time.isoformat(),
            "end": anomaly.end_time.isoformat(),
        }
    ]
    edges: list[dict[str, Any]] = []

    for agent_name, pack in all_packs.items():
        hyp = HYPOTHESIS_BY_AGENT.get(agent_name, agent_name)
        nodes.append({"id": f"H:{hyp}", "type": "HypothesisNode", "agent": agent_name})
        for skill_name, skill in (pack.get("skills") or {}).items():
            sid = f"S:{agent_name}:{skill_name}"
            nodes.append(
                {
                    "id": sid,
                    "type": "SignalNode",
                    "skill": skill_name,
                    "verdict": skill.get("verdict"),
                    "falsifier": skill.get("falsifier", False),
                    "value": skill.get("value"),
                }
            )
            verdict = skill.get("verdict")
            if verdict in ("pass", "soft_pass") and not (
                skill.get("falsifier") and verdict == "fail"
            ):
                if verdict == "pass" or skill_name in (
                    "retry_zscore_threshold",
                    "max_retry_floor",
                    "token_growth_threshold",
                    "chain_depth_floor",
                    "model_changed_flag",
                    "cost_growth_threshold",
                    "token_stability_check",
                    "cost_attribution_check",
                ):
                    edges.append(
                        {
                            "type": "explains",
                            "from": sid,
                            "to": f"H:{hyp}",
                            "label": f"{skill_name}={verdict}",
                        }
                    )
            if skill.get("falsifier") and verdict == "fail":
                edges.append(
                    {
                        "type": "contradicts",
                        "from": sid,
                        "to": f"H:{hyp}",
                        "label": f"{skill_name}=fail",
                    }
                )
            elif verdict == "fail" and skill_name in (
                "non_retry_alternative_scan",
                "token_stability_check",
                "prompt_content_growth_correlation",
            ):
                edges.append(
                    {
                        "type": "contradicts",
                        "from": sid,
                        "to": f"H:{hyp}",
                        "label": f"{skill_name}=fail",
                    }
                )

    # Cross-hypothesis co_occurs when two agents both pass strongly
    strong = [
        HYPOTHESIS_BY_AGENT[a]
        for a, p in all_packs.items()
        if float(p.get("band_ceiling", 0)) >= 0.75
        and p.get("suggested_hypothesis") != "no_strong_signal"
    ]
    if len(strong) >= 2:
        edges.append(
            {
                "type": "co_occurs",
                "from": f"H:{strong[0]}",
                "to": f"H:{strong[1]}",
                "label": "both_strong",
            }
        )

    return {"nodes": nodes, "edges": edges}


def summarize_graph(graph: dict[str, Any]) -> str:
    """One line per hypothesis: explains / contradicts."""
    hyps = [
        n["id"]
        for n in graph.get("nodes", [])
        if n.get("type") == "HypothesisNode"
    ]
    lines: list[str] = []
    for hid in hyps:
        explains: list[str] = []
        contradicts: list[str] = []
        for e in graph.get("edges", []):
            if e.get("to") != hid and e.get("from") != hid:
                # only edges targeting this hypothesis
                if e.get("to") != hid:
                    continue
            if e.get("to") != hid:
                continue
            label = e.get("label") or e.get("type")
            if e.get("type") == "explains":
                explains.append(str(label))
            elif e.get("type") == "contradicts":
                contradicts.append(str(label))
        exp = ", ".join(explains[:6]) if explains else "none"
        con = ", ".join(contradicts[:6]) if contradicts else "none"
        lines.append(f"{hid} — explains: {exp} — contradicts: {con}")
    if not lines:
        return "(empty evidence graph)"
    return "\n".join(lines)
