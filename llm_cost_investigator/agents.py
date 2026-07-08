"""LLM-backed diagnostic agent entrypoints and fallbacks."""

from __future__ import annotations

from llm_cost_investigator.schemas import AgentEvidence, AnomalyWindow


def run_agents(agent_names: list[str], anomaly: AnomalyWindow) -> list[AgentEvidence]:
    """Run the requested diagnostic agents against an anomaly window."""
    raise NotImplementedError("Diagnostic agent execution is not implemented yet.")
