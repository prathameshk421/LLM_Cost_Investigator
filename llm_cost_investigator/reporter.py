"""Incident report rendering stubs."""

from __future__ import annotations

from pathlib import Path

from llm_cost_investigator.schemas import IncidentReport


def write_report(report: IncidentReport, output_dir: str | Path = "reports") -> None:
    """Write report artifacts to disk."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    file_path = out_path / f"{report.scenario}_report.json"
    file_path.write_text(report.to_json(), encoding="utf-8")

