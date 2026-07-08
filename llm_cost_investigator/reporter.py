"""Incident report rendering stubs."""

from __future__ import annotations

from pathlib import Path

from llm_cost_investigator.schemas import IncidentReport


def write_report(report: IncidentReport, output_dir: str | Path = "reports") -> None:
    """Write report artifacts to disk."""
    raise NotImplementedError("Report generation is not implemented yet.")
