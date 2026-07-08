"""Incident report rendering stubs."""

from __future__ import annotations

from pathlib import Path

from llm_cost_investigator.schemas import IncidentReport


def write_report(
    report: IncidentReport,
    output_dir: str | Path = "reports",
    print_summary: bool = True,
) -> tuple[Path, Path]:
    """Write report artifacts to disk and print terminal summary."""
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
    root = report.root_cause
    anomaly = report.anomaly_window
    scenario = report.scenario

    print("─" * 50)
    print(f" INCIDENT SUMMARY  ·  {scenario}")
    print("─" * 50)
    print(f" Root cause   : {root.hypothesis}")
    print(f" Feature      : {anomaly.feature_tag}")
    print(f" Confidence   : {root.confidence:.2f}")
    print(f" Winning agent: {root.winning_agent or 'none'}")
    print("─" * 50)
    print(" RECOMMENDATIONS")
    for rec in report.recommendations:
        print(f"   • {rec}")
    print("─" * 50)

    # Check fallbacks
    fallbacks = [run for run in report.agent_runs if run.fallback_used]
    if fallbacks:
        for run in fallbacks:
            reason_str = f" — {run.fallback_reason}" if run.fallback_reason else ""
            print(f" ⚠ Fallback used: {run.evidence.agent_name}{reason_str}")
        print("─" * 50)

    print(f" Reports written → reports/{scenario}_incident.{{md,json}}")
    print("─" * 50)


def _render_markdown(report: IncidentReport) -> str:
    root = report.root_cause
    anomaly = report.anomaly_window

    # Get explanation of winning agent for Summary
    winning = next(
        (ev for ev in root.evidence if ev.agent_name == root.winning_agent),
        None,
    )
    if winning:
        summary_text = f"Root cause: {winning.explanation}"
    else:
        summary_text = "No strong diagnostic signal was detected."

    lines = [
        f"# Incident Report: {report.scenario}",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "## Summary",
        "",
        summary_text,
        "",
        "## Root Cause",
        "",
        "| Field | Value |",
        "| :--- | :--- |",
        f"| Hypothesis | {root.hypothesis} |",
        f"| Affected feature | {anomaly.feature_tag} |",
        f"| Confidence | {root.confidence:.2f} |",
        f"| Winning agent | {root.winning_agent or 'none'} |",
        "",
        "## Agent Execution",
    ]

    if not report.agent_runs:
        lines.append("No agent runs recorded.")
    else:
        for run in report.agent_runs:
            reason_suffix = f", reason: {run.fallback_reason}" if run.fallback_reason else ""
            lines.append(
                f"- **{run.evidence.agent_name}** — provider: {run.provider}, model: {run.model or 'n/a'}, "
                f"fallback: {run.fallback_used}{reason_suffix}"
            )

    lines.extend([
        "",
        "## Supporting Evidence",
    ])

    for item in root.evidence:
        lines.extend([
            "",
            f"### {item.agent_name}",
            f"- **Hypothesis**: {item.hypothesis}",
            f"- **Confidence**: {item.confidence:.2f}",
            f"- **Explanation**: {item.explanation}",
        ])
        
        # Format key metrics
        if item.supporting_metrics:
            lines.append("- **Key metrics**:")
            for k, v in item.supporting_metrics.items():
                lines.append(f"  - {k}: {v}")
        else:
            lines.append("- **Key metrics**: none")

    lines.extend([
        "",
        "## Recommendations",
    ])

    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")

    lines.append("")
    return "\n".join(lines)
