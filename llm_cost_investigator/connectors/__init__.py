"""Remote log connectors: pull → normalize → list[LLMCall]."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_cost_investigator.connectors.base import (
    DEFAULT_REMOTE_MAX_ROWS,
    MAX_SINCE_HOURS,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorRateLimitError,
    PullRequest,
)
from llm_cost_investigator.connectors.config import (
    ConnectionProfile,
    get_profile,
    list_profile_summaries,
    load_profiles,
)
from llm_cost_investigator.connectors.registry import (
    build_connector,
    registered_connectors,
)
from llm_cost_investigator.ingest.types import IngestReport
from llm_cost_investigator.schemas import LLMCall

__all__ = [
    "DEFAULT_REMOTE_MAX_ROWS",
    "MAX_SINCE_HOURS",
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorError",
    "ConnectorRateLimitError",
    "ConnectionProfile",
    "PullRequest",
    "build_connector",
    "get_profile",
    "list_profile_summaries",
    "load_profiles",
    "pull_calls",
    "registered_connectors",
]


def pull_calls(
    *,
    connector: str | None = None,
    connection: str | None = None,
    request: PullRequest | None = None,
    config_path: Path | str | None = None,
    host: str | None = None,
    api_version: str | None = None,
) -> tuple[list[LLMCall], IngestReport, dict[str, Any]]:
    """Resolve profile/connector, pull remote logs, return LLMCalls + report + meta.

    Provide exactly one of ``connection`` (named profile) or ``connector`` (type name).

    When both a profile and an explicit ``request`` are provided, request fields
    override profile defaults; request ``filters`` are merged on top of profile filters.
    """
    if connection and connector:
        raise ConnectorConfigError("Provide only one of connection= or connector=")
    if not connection and not connector:
        raise ConnectorConfigError(
            "Provide connection= (named profile) or connector= (e.g. 'langfuse')"
        )

    since_hours = 24.0
    max_rows = DEFAULT_REMOTE_MAX_ROWS
    feature_default: str | None = None
    filters: dict[str, Any] = {}
    resolved_host = host
    resolved_api = api_version or "v1"
    connection_name: str | None = connection
    connector_name: str

    if connection:
        profile = get_profile(connection, config_path=config_path)
        connector_name = profile.connector
        resolved_host = profile.host or resolved_host
        resolved_api = api_version or profile.api_version
        if profile.defaults.since_hours is not None:
            since_hours = float(profile.defaults.since_hours)
        if profile.defaults.max_rows is not None:
            max_rows = int(profile.defaults.max_rows)
        if profile.defaults.feature_default is not None:
            feature_default = profile.defaults.feature_default
        filters = dict(profile.filters)
    else:
        assert connector is not None
        connector_name = connector

    if request is not None:
        since_hours = request.since_hours
        max_rows = request.max_rows
        if request.feature_default is not None:
            feature_default = request.feature_default
        # request filters override profile keys
        merged = dict(filters)
        merged.update(request.filters)
        filters = merged

    final_request = PullRequest(
        since_hours=since_hours,
        max_rows=max_rows,
        feature_default=feature_default,
        filters=filters,
    )
    client = build_connector(
        connector_name,
        host=resolved_host,
        api_version=resolved_api,
        connection_name=connection_name,
    )
    return client.pull(final_request)
