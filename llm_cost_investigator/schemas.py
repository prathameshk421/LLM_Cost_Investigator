"""Shared data models used across the project."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMCall(BaseModel):
    timestamp: datetime
    call_id: str
    parent_call_id: str | None = None
    feature_tag: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    scenario_label: str | None = None


class AnomalyWindow(BaseModel):
    feature_tag: str
    start_time: datetime
    end_time: datetime
    signals: dict[str, Any] = Field(default_factory=dict)
    sample_call_ids: list[str] = Field(default_factory=list)


class AgentEvidence(BaseModel):
    agent_name: Literal[
        "retry_loop_agent",
        "token_context_agent",
        "model_routing_agent",
    ]
    hypothesis: Literal[
        "uncapped_retry_loop",
        "context_bloat_self_calling_agent",
        "expensive_model_misroute",
        "no_strong_signal",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: str


class RootCauseResult(BaseModel):
    hypothesis: AgentEvidence["hypothesis"]
    confidence: float = Field(ge=0.0, le=1.0)
    winning_agent: AgentEvidence["agent_name"] | None = None
    evidence: list[AgentEvidence] = Field(default_factory=list)


class IncidentReport(BaseModel):
    scenario: str
    root_cause: RootCauseResult
    anomaly_window: AnomalyWindow
    recommendations: list[str] = Field(default_factory=list)
