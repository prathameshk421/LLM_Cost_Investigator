"""Pydantic response models for the investigation replay API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StageId = Literal[
    "signals",
    "router",
    "decision",
    "tool_trace",
    "explanation",
    "root_cause",
]

StageKind = Literal["deterministic", "agentic"]

IncidentKind = Literal["main", "thin_must_call"]

DecisionLabel = Literal["MUST_CALL", "DO_NOT_CALL"]

HighlightStatus = Literal["pass", "fail", "neutral", "ambiguous"]

MatchKind = Literal["exact_number", "approx_number", "exact_string"]


class CitationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    path: str
    field: str
    raw: Any = None


class ExplanationSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | int | float
    display: str
    explanation_span: ExplanationSpan
    source: CitationSource
    match_kind: MatchKind


class SignalHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any
    threshold: str | None = None
    status: HighlightStatus = "neutral"


class CallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    parent_call_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    retry_count: int | None = None


class RouterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    selected: bool
    reasons: list[str] = Field(default_factory=list)


class DecisionComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: Any
    operator: str
    threshold: Any
    passed: bool


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    hypothesis: str
    confidence: float


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: str
    end_time: str


class ReplayMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    generated_at: str | None = None
    source_files: list[str] = Field(default_factory=list)


class RootCauseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    confidence: float
    winning_agent: str | None = None


class ReplayStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StageId
    title: str
    kind: StageKind
    agent_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplayIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    kind: IncidentKind
    scenario_label: str
    feature_tag: str
    window: TimeWindow
    stages: list[ReplayStage] = Field(default_factory=list)
    root_cause: RootCauseBrief
    recommendations: list[str] = Field(default_factory=list)
    meta: ReplayMeta = Field(default_factory=ReplayMeta)


class IncidentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    kind: IncidentKind
    feature_tag: str
    root_cause_hypothesis: str
    has_tool_use: bool
    winning_agent: str | None = None
