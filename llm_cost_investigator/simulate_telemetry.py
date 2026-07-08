"""Scenario-based telemetry generation stubs."""

from __future__ import annotations

from llm_cost_investigator.schemas import LLMCall


def generate_scenario_calls(scenario: str) -> list[LLMCall]:
    """Return synthetic telemetry for a named scenario."""
    raise NotImplementedError(f"Telemetry simulation is not implemented for scenario={scenario!r}.")
