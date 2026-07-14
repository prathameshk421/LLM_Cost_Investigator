"""Connector protocol, pull request types, and errors."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from llm_cost_investigator.ingest.types import IngestReport
from llm_cost_investigator.schemas import LLMCall

DEFAULT_REMOTE_MAX_ROWS = 10_000
MAX_SINCE_HOURS = 24 * 30  # 30 days


class ConnectorError(Exception):
    """Base error for remote connectors."""


class ConnectorAuthError(ConnectorError):
    """Missing credentials or HTTP 401/403."""


class ConnectorRateLimitError(ConnectorError):
    """HTTP 429 after retries exhausted."""


class ConnectorConfigError(ConnectorError):
    """Invalid profile or connector configuration."""


class PullRequest(BaseModel):
    """Parameters for a one-shot remote pull."""

    model_config = ConfigDict(extra="forbid")

    since_hours: float = Field(default=24.0, gt=0, le=MAX_SINCE_HOURS)
    max_rows: int = Field(default=DEFAULT_REMOTE_MAX_ROWS, ge=1, le=100_000)
    feature_default: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class PullMeta(BaseModel):
    """Non-secret metadata about a completed pull."""

    model_config = ConfigDict(extra="forbid")

    connector: str
    host: str
    from_time: str
    to_time: str
    pages_fetched: int = 0
    raw_observations: int = 0
    api_version: str = "v1"
    connection: str | None = None
    truncated: bool = False


class LogConnector(Protocol):
    """Remote log source that yields normalized LLMCall rows."""

    name: str

    def pull(
        self, request: PullRequest
    ) -> tuple[list[LLMCall], IngestReport, dict[str, Any]]:
        ...
