"""Deterministic routing stubs for diagnostic agents."""

from __future__ import annotations

from llm_cost_investigator.schemas import AnomalyWindow


def route_agents(anomaly: AnomalyWindow) -> list[str]:
    """Select the agents that should investigate an anomaly."""
    raise NotImplementedError("Agent routing is not implemented yet.")
