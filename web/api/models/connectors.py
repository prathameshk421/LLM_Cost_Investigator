"""Request/response models for remote connectors API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    required_env: list[str] = Field(default_factory=list)
    optional_env: list[str] = Field(default_factory=list)


class ProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    connector: str
    host: str | None = None
    api_version: str = "v1"
    defaults: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)


class RemotePullBody(BaseModel):
    """Shared body for test / preview / investigate remote pulls."""

    model_config = ConfigDict(extra="forbid")

    connection: str | None = None
    connector: str | None = None
    since_hours: float | None = None
    max_rows: int | None = None
    feature_default: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    provider: str | None = None
    model: str | None = None
    force_fallback: bool = True
    max_anomalies: int = 5


class ConnectorTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str
    accepted: int = 0
    dropped: int = 0
    pull_meta: dict[str, Any] | None = None
