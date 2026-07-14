"""Shared types for telemetry ingest."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AdapterName = Literal["auto", "jsonl", "openai", "csv"]
ResolvedAdapter = Literal["jsonl", "openai", "csv"]

LOGICAL_FIELDS = (
    "timestamp",
    "call_id",
    "feature_tag",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "latency_ms",
    "retry_count",
    "parent_call_id",
)

REQUIRED_LOGICAL_FIELDS = (
    "timestamp",
    "call_id",
    "feature_tag",
    "model",
    "input_tokens",
    "output_tokens",
)

DEFAULT_MAX_ROWS = 100_000


class IngestOptions(BaseModel):
    """Options shared by all adapters."""

    model_config = ConfigDict(extra="forbid")

    feature_default: str | None = None
    column_mapping: dict[str, str] | None = None
    csv_delimiter: str | None = None
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, ge=1)
    strict: bool = False
    source_name: str | None = None


class IngestReport(BaseModel):
    """Summary of an ingest pass."""

    model_config = ConfigDict(extra="forbid")

    adapter: str
    accepted: int = 0
    dropped: int = 0
    warnings: list[str] = Field(default_factory=list)
    cost_estimated_count: int = 0
    sample_dropped: list[dict[str, Any]] = Field(default_factory=list)
    detected_headers: list[str] | None = None


class DroppedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_index: int
    reason: str
    raw: dict[str, Any] | None = None
