"""Incident report rendering stubs."""

from __future__ import annotations

from pathlib import Path

from llm_cost_investigator.schemas import IncidentReport


def write_report(report: IncidentReport, output_dir: str | Path = "reports") -> None:
    """Write report artifacts to disk."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    json_path = out_path / f"{report.scenario}_report.json"
    json_path.write_text(report.to_json(), encoding="utf-8")

    markdown_path = out_path / f"{report.scenario}_incident.md"
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")


def _render_markdown(report: IncidentReport) -> str:
    root = report.root_cause
    anomaly = report.anomaly_window

    lines = [
        f"# Incident Report: {report.scenario}",
        "",
        f"Root cause: {root.hypothesis}",
        f"Affected feature: {anomaly.feature_tag}",
        f"Confidence: {root.confidence:.2f}",
        f"Winning agent: {root.winning_agent or 'none'}",
        "",
        "## Agent Execution",
    ]

    for run in report.agent_runs:
        lines.extend([
            "",
            f"- Agent: {run.evidence.agent_name}",
            f"- Provider: {run.provider}",
            f"- Model: {run.model or 'n/a'}",
            f"- Fallback used: {run.fallback_used}",
        ])
        if run.fallback_reason:
            lines.append(f"- Fallback reason: {run.fallback_reason}")

    lines.extend([
        "",
        "## Supporting Evidence",
    ])

    for item in root.evidence:
        lines.extend([
            "",
            f"- Agent: {item.agent_name}",
            f"- Hypothesis: {item.hypothesis}",
            f"- Confidence: {item.confidence:.2f}",
            f"- Explanation: {item.explanation}",
            f"- Metrics: {item.supporting_metrics}",
        ])

    lines.extend([
        "",
        "## Recommendations",
    ])

    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")

    lines.append("")
    return "\n".join(lines)
