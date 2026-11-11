"""Shared investigation pipeline for simulated and ingested telemetry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_cost_investigator.agents import run_agents_detailed
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.reporter import write_report
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.schemas import AnomalyWindow, IncidentReport, LLMCall
from llm_cost_investigator.telemetry_store import TelemetryStore

RECOMMENDATIONS: dict[str, list[str]] = {
    "uncapped_retry_loop": [
        "Implement exponential backoff with jitter.",
        "Cap the maximum retry count in the client configuration.",
        "Add a circuit breaker pattern to prevent continuous retries during outages.",
    ],
    "context_bloat_self_calling_agent": [
        "Limit maximum reflection / chain depth to a safe threshold (e.g., 3).",
        "Implement prompt summarization or token truncation for history.",
        "Add a fail-safe budget check on the chain to terminate expanding contexts.",
    ],
    "expensive_model_misroute": [
        "Revert default model routing configurations to more cost-effective models.",
        "Add CI/CD checks to prevent unintentional model upgrades.",
        "Implement rate limiting or budget alerts for premium model endpoints.",
    ],
    "no_strong_signal": [
        "Monitor the feature's baseline metrics for continued anomalies.",
    ],
}

_X03_RECOMMENDATIONS = [
    "Check sample vs aggregate consistency (retry_count, tokens, model fields).",
    "Improve instrumentation: parent_call_id, retry_count, feature_tag metadata.",
    "Re-export or re-pull telemetry and re-run investigation after data fixes.",
]

_X02_RECOMMENDATIONS = [
    "Review legitimate traffic/volume growth before treating this as a client bug.",
    "Compare cost growth to request volume and business KPIs for the same window.",
    "Monitor the feature's baseline metrics for continued anomalies.",
]

ReportNaming = Literal["label", "label_feature_index"]


class InvestigationResult(BaseModel):
    """Outcome of investigating a batch of LLM calls."""

    model_config = ConfigDict(extra="forbid")

    label: str
    call_count: int
    feature_tags: list[str] = Field(default_factory=list)
    anomaly_count: int = 0
    incidents: list[IncidentReport] = Field(default_factory=list)
    report_paths: list[str] = Field(default_factory=list)
    no_anomaly_reason: str | None = None
    # Every flagged window, including ones merged into a neighbour incident.
    flagged_windows: list[dict[str, Any]] = Field(default_factory=list)
    incident_groups: int = 0


def sanitize_slug(value: str) -> str:
    """Make a filesystem-safe slug from a label or feature tag."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._") or "unnamed"
    return cleaned[:80]


def describe_anomaly(window: AnomalyWindow) -> str:
    """Derive a short human-readable description of the flagged anomaly."""
    signals = window.signals
    if signals.model_changed:
        old = signals.model_before or "?"
        new = signals.model_during or "?"
        return f"{window.feature_tag} model change ({old} → {new})"
    if signals.retry_z_score >= 3.0 or signals.max_retry_count >= 3:
        return (
            f"{window.feature_tag} retry loop "
            f"(max_retry_count={signals.max_retry_count})"
        )
    if signals.max_call_chain_depth >= 2:
        return (
            f"{window.feature_tag} context bloat "
            f"(chain_depth={signals.max_call_chain_depth})"
        )
    return f"{window.feature_tag} cost spike"


def _report_scenario(
    label: str,
    window: AnomalyWindow,
    index: int,
    *,
    naming: ReportNaming,
) -> str:
    if naming == "label":
        return sanitize_slug(label)
    return f"{sanitize_slug(label)}_{sanitize_slug(window.feature_tag)}_{index}"


def group_adjacent_windows(anomalies: list[AnomalyWindow]) -> list[list[AnomalyWindow]]:
    """Group flagged windows of one feature that touch each other in time.

    Input order is the detector rank. Each group lists its windows in rank
    order (so ``group[0]`` is the strongest), and groups keep the rank of
    their strongest window.
    """
    rank = {id(w): i for i, w in enumerate(anomalies)}
    by_feature: dict[str, list[AnomalyWindow]] = {}
    for w in anomalies:
        by_feature.setdefault(w.feature_tag, []).append(w)

    groups: list[list[AnomalyWindow]] = []
    for windows in by_feature.values():
        current: list[AnomalyWindow] = []
        for w in sorted(windows, key=lambda w: w.start_time):
            if current and w.start_time > current[-1].end_time:
                groups.append(current)
                current = []
            current.append(w)
        if current:
            groups.append(current)

    for g in groups:
        g.sort(key=lambda w: rank[id(w)])
    groups.sort(key=lambda g: rank[id(g[0])])
    return groups


def investigate_calls(
    calls: list[LLMCall],
    *,
    label: str = "ingest",
    provider: str | None = None,
    model: str | None = None,
    max_anomalies: int = 5,
    write_reports: bool = True,
    output_dir: str | Path | None = None,
    report_naming: ReportNaming = "label_feature_index",
) -> InvestigationResult:
    """Run detector → router → agents → aggregator on normalized calls.

    Parameters
    ----------
    report_naming:
        ``label`` — use ``label`` alone as the report scenario (demo mode).
        ``label_feature_index`` — unique live slugs so demos are not clobbered.
    """
    if not calls:
        raise ValueError("No calls to investigate (empty input).")

    feature_tags = sorted({c.feature_tag for c in calls})
    store = TelemetryStore(":memory:")
    store.insert_calls(calls)

    cursor = store._conn.execute(
        "SELECT * FROM llm_calls ORDER BY timestamp ASC, call_id ASC"
    )
    db_calls = [store._row_to_call(row) for row in cursor]
    anomalies = detect_anomalies(db_calls)

    if not anomalies:
        return InvestigationResult(
            label=label,
            call_count=len(calls),
            feature_tags=feature_tags,
            anomaly_count=0,
            incidents=[],
            report_paths=[],
            no_anomaly_reason="No anomalous windows detected against baseline.",
        )

    groups = group_adjacent_windows(anomalies)
    flagged_windows = [
        {
            "feature_tag": w.feature_tag,
            "start_time": w.start_time,
            "end_time": w.end_time,
            "group": gi,
        }
        for gi, group in enumerate(groups)
        for w in group
    ]
    # One investigation per incident: the highest-ranked window of each run
    # of adjacent flagged windows. A sustained incident spans many windows;
    # paying agents once per window repeats the same diagnosis.
    selected_windows = [group[0] for group in groups][: max(1, max_anomalies)]
    incidents: list[IncidentReport] = []
    report_paths: list[str] = []

    for index, window in enumerate(selected_windows):
        selected_agents = route_agents(window)
        agent_runs = run_agents_detailed(
            selected_agents,
            window,
            provider=provider,
            model=model,
            telemetry_store=store,
        )
        evidence = [run.evidence for run in agent_runs]
        from llm_cost_investigator.agent_skills import score_all_agents

        skill_packs = score_all_agents(window)
        root_cause = select_root_cause(evidence, skill_packs=skill_packs)
        scenario = _report_scenario(
            label, window, index, naming=report_naming
        )
        if root_cause.hypothesis == "no_strong_signal":
            if root_cause.attribution_pattern == "X-03":
                recommendations = list(_X03_RECOMMENDATIONS)
            elif root_cause.attribution_pattern == "X-02":
                recommendations = list(_X02_RECOMMENDATIONS)
            else:
                recommendations = list(
                    RECOMMENDATIONS.get(
                        "no_strong_signal", ["Monitor baseline metrics."]
                    )
                )
        else:
            recommendations = list(
                RECOMMENDATIONS.get(
                    root_cause.hypothesis, ["Monitor baseline metrics."]
                )
            )
        report = IncidentReport(
            scenario=scenario,
            root_cause=root_cause,
            anomaly_window=window,
            agent_runs=agent_runs,
            recommendations=recommendations,
        )
        incidents.append(report)

        if write_reports:
            try:
                md_path, _ = write_report(
                    report, output_dir=output_dir, print_summary=False
                )
                report_paths.append(str(md_path))
            except Exception:
                report_paths.append("n/a")

    store.close()

    return InvestigationResult(
        label=label,
        call_count=len(calls),
        feature_tags=feature_tags,
        anomaly_count=len(anomalies),
        incidents=incidents,
        report_paths=report_paths,
        no_anomaly_reason=None,
        flagged_windows=flagged_windows,
        incident_groups=len(groups),
    )


def recommendations_for(hypothesis: str) -> list[str]:
    """Public accessor for recommendation bullets."""
    return list(RECOMMENDATIONS.get(hypothesis, ["Monitor baseline metrics."]))
