"""Shared data models used across the project.

Design rules
------------
- All models use ``extra="forbid"`` so unexpected fields raise ValidationError
  immediately rather than being silently dropped.
- ``AgentEvidence.supporting_metrics`` is intentionally ``dict[str, Any]``
  because it is populated by LLM output whose shape is not known in advance.
- ``AnomalyWindow.sample_calls`` is capped at 10 entries (``max_length=10``)
  to prevent pathological anomaly windows (e.g. a retry storm) from blowing
  up agent prompt token counts.  The detector should prefer a representative
  slice: first-of-chain + most-recent calls.
- ``scenario_label`` on ``LLMCall`` is only for replay tests and demos.
  The detector and agents must not use it as evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnomalySignals",
    "AnomalyWindow",
    "AgentEvidence",
    "LLMCall",
    "RootCauseResult",
    "IncidentReport",
]

# ---------------------------------------------------------------------------
# Primitive call record
# ---------------------------------------------------------------------------


class LLMCall(BaseModel):
    """One recorded LLM API invocation."""

    model_config = ConfigDict(extra="forbid")

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
    # Only present in simulated/replay data — never use as diagnostic evidence.
    scenario_label: str | None = None


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


class AnomalySignals(BaseModel):
    """Typed bag of z-scores and derived metrics produced by the detector.

    All fields default to zero/False so the detector can populate only what
    it computed; every downstream consumer can read any key safely.

    ``extra="forbid"`` is intentional — the detector is our own deterministic
    code, so any unknown key is a typo (e.g. ``cost_zscore`` vs
    ``cost_z_score``) that should fail loudly rather than silently disappear.
    """

    model_config = ConfigDict(extra="forbid")

    # Z-scores against the feature's own baseline windows
    cost_z_score: float = 0.0
    input_tokens_z_score: float = 0.0
    output_tokens_z_score: float = 0.0
    retry_z_score: float = 0.0
    calls_z_score: float = 0.0
    latency_z_score: float = 0.0

    # Growth percentages vs baseline mean
    input_token_growth_pct: float = 0.0
    output_token_growth_pct: float = 0.0
    cost_growth_pct: float = 0.0
    token_growth_pct: float = 0.0

    # Absolute maximums within the anomaly window
    max_retry_count: int = 0
    avg_retry_count: float = 0.0
    max_call_chain_depth: int = 0
    repeated_parent_call_count: int = 0

    # Model routing signals
    model_changed: bool = False
    models_seen: list[str] = Field(default_factory=list)
    model_before: str | None = None
    model_during: str | None = None


class AnomalyWindow(BaseModel):
    """A flagged time window for a single feature, ready for agent routing."""

    model_config = ConfigDict(extra="forbid")

    feature_tag: str
    start_time: datetime
    end_time: datetime
    signals: AnomalySignals = Field(default_factory=AnomalySignals)
    # IDs of calls included in this window (full set, for DB re-queries).
    sample_call_ids: list[str] = Field(default_factory=list)
    # Hydrated call objects passed directly into agent prompts.
    # Capped at 10 to bound prompt token usage — prefer first-of-chain +
    # most-recent as a representative slice.
    sample_calls: list[LLMCall] = Field(default_factory=list, max_length=10)


# ---------------------------------------------------------------------------
# Diagnostic agent output
# ---------------------------------------------------------------------------

# Shared Literal types — kept as module-level aliases so RootCauseResult can
# reference them without duplicating the string literals.
_AgentName = Literal[
    "retry_loop_agent",
    "token_context_agent",
    "model_routing_agent",
]

_Hypothesis = Literal[
    "uncapped_retry_loop",
    "context_bloat_self_calling_agent",
    "expensive_model_misroute",
    "no_strong_signal",
]


class AgentEvidence(BaseModel):
    """Structured output returned by a single diagnostic LLM agent.

    ``supporting_metrics`` is intentionally ``dict[str, Any]`` — it is
    populated by LLM output, so its shape is not known ahead of time.
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: _AgentName
    hypothesis: _Hypothesis
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: str


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


class RootCauseResult(BaseModel):
    """Final root cause selected by the deterministic aggregator."""

    model_config = ConfigDict(extra="forbid")

    hypothesis: _Hypothesis
    confidence: float = Field(ge=0.0, le=1.0)
    # None only when hypothesis == "no_strong_signal"
    winning_agent: _AgentName | None = None
    evidence: list[AgentEvidence] = Field(default_factory=list)


class IncidentReport(BaseModel):
    """Full incident report written to disk by the reporter."""

    model_config = ConfigDict(extra="forbid")

    scenario: str
    root_cause: RootCauseResult
    anomaly_window: AnomalyWindow
    recommendations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_json(self, **kwargs: Any) -> str:
        """Serialise the report to a pretty-printed JSON string."""
        return self.model_dump_json(indent=2, **kwargs)
