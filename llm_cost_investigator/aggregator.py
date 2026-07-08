"""Deterministic aggregation of agent evidence."""

from __future__ import annotations

from llm_cost_investigator.schemas import AgentEvidence, RootCauseResult


def select_root_cause(evidence: list[AgentEvidence]) -> RootCauseResult:
    """Choose the winning root cause from validated agent evidence."""
    raise NotImplementedError("Evidence aggregation is not implemented yet.")
