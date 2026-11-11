"""Request/response models for one-shot investigate API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.models.replay import ReplayIncident


class IngestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    accepted: int
    dropped: int
    warnings: list[str] = Field(default_factory=list)
    cost_estimated_count: int = 0
    sample_dropped: list[dict[str, Any]] = Field(default_factory=list)
    detected_headers: list[str] | None = None


class InvestigationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    call_count: int
    feature_tags: list[str] = Field(default_factory=list)
    anomaly_count: int = 0
    no_anomaly_reason: str | None = None


class LiveIncident(BaseModel):
    """Replay-compatible incident plus optional raw report dump."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    kind: str = "main"
    scenario_label: str
    feature_tag: str
    window: dict[str, str]
    stages: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: dict[str, Any]
    recommendations: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    raw_report: dict[str, Any] | None = None

    @classmethod
    def from_replay(
        cls,
        incident: ReplayIncident,
        *,
        raw_report: dict[str, Any] | None = None,
    ) -> LiveIncident:
        data = incident.model_dump(mode="json")
        return cls(
            id=data["id"],
            title=data["title"],
            kind=data["kind"],
            scenario_label=data["scenario_label"],
            feature_tag=data["feature_tag"],
            window=data["window"],
            stages=data["stages"],
            root_cause=data["root_cause"],
            recommendations=data["recommendations"],
            meta=data["meta"],
            raw_report=raw_report,
        )


class InvestigateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingest: IngestSummary
    summary: InvestigationSummary
    incidents: list[LiveIncident] = Field(default_factory=list)
    pull_meta: dict[str, Any] | None = None
    # Per-feature cost per detector window, plus flagged windows (for charts).
    timeline: dict[str, Any] | None = None
    # Wall time and live LLM usage for this one request.
    run_meta: dict[str, Any] | None = None


class PreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingest: IngestSummary
    feature_tags: list[str] = Field(default_factory=list)
    sample_calls: list[dict[str, Any]] = Field(default_factory=list)
    guessed_mapping: dict[str, str] | None = None
    pull_meta: dict[str, Any] | None = None


class PricingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input_per_million_usd: float
    output_per_million_usd: float
