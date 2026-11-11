"""Incident report rendering and terminal summaries."""

from __future__ import annotations

import os

from pathlib import Path

from llm_cost_investigator.schemas import IncidentReport


def write_report(
    report: IncidentReport,
    output_dir: str | Path | None = None,
    print_summary: bool = True,
) -> tuple[Path, Path]:
    """Write report artifacts to disk and print terminal summary."""
    if output_dir is None:
        output_dir = os.environ.get("LLMCI_REPORTS_DIR", "data/reports/incidents")
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    json_path = out_path / f"{report.scenario}_incident.json"
    json_path.write_text(report.to_json(), encoding="utf-8")

    markdown_path = out_path / f"{report.scenario}_incident.md"
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")

    if print_summary:
        print_terminal_summary(report)

    return markdown_path, json_path


def print_terminal_summary(report: IncidentReport) -> None:
    """Print a clean terminal summary of the incident report."""
    fallback_used = any(run.fallback_used for run in report.agent_runs)
    print("─────────────────────────────────────────")
    print(f"Scenario:       {report.scenario}")
    print(f"Feature:        {report.anomaly_window.feature_tag}")
    print(f"Root cause:     {report.root_cause.hypothesis}")
    print(f"Confidence:     {report.root_cause.confidence:.2f}")
    print(f"Winning agent:  {report.root_cause.winning_agent or 'none'}")
    if report.root_cause.attribution_pattern:
        note = report.root_cause.attribution_note or ""
        print(
            f"Attribution:    {report.root_cause.attribution_pattern}"
            + (f" — {note}" if note else "")
        )
    print(f"Fallback used:  {str(fallback_used)}")
    print("─────────────────────────────────────────")


def _render_markdown(report: IncidentReport) -> str:
    root = report.root_cause
    anomaly = report.anomaly_window

    # Get explanation of winning agent for Summary
    winning = next(
        (ev for ev in root.evidence if ev.agent_name == root.winning_agent),
        None,
    )
    if winning and root.hypothesis != "no_strong_signal":
        summary_text = winning.explanation
    elif root.attribution_note:
        summary_text = root.attribution_note
    else:
        summary_text = "No strong diagnostic signal was detected."

    lines = [
        f"# Incident Report: {report.scenario}",
        "",
        f"Root cause: {root.hypothesis}",
        f"Affected feature: {anomaly.feature_tag}",
        f"Confidence: {root.confidence:.2f}",
        f"Winning agent: {root.winning_agent or 'none'}",
    ]
    if root.attribution_pattern:
        lines.append(
            f"Attribution: {root.attribution_pattern}"
            + (f" — {root.attribution_note}" if root.attribution_note else "")
        )
    lines.extend([
        "",
        "Summary:",
        summary_text,
        "",
        "Supporting evidence:",
    ])

    if not report.agent_runs:
        lines.append("No agent runs recorded.")
    else:
        for run in report.agent_runs:
            reason_suffix = f" ({run.fallback_reason})" if run.fallback_reason else ""
            lines.append(
                f"- **{run.evidence.agent_name}** — provider: {run.provider}, "
                f"model: {run.model or 'n/a'}, fallback: {run.fallback_used}{reason_suffix}"
            )
            lines.append(f"  - Hypothesis: {run.evidence.hypothesis}")
            lines.append(f"  - Confidence: {run.evidence.confidence:.2f}")
            lines.append(f"  - Explanation: {run.evidence.explanation}")
            if run.evidence.supporting_metrics:
                lines.append("  - Supporting metrics:")
                for k, v in run.evidence.supporting_metrics.items():
                    lines.append(f"    - {k}: {v}")
            else:
                lines.append("  - Supporting metrics: none")

    lines.extend([
        "",
        "Recommendations:",
    ])

    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")

    lines.append("")
    return "\n".join(lines)

