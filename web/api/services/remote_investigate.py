"""Glue: remote connector pull → investigate → stage-shaped incidents."""

from __future__ import annotations

from typing import Any

from api.models.investigate import (
    IngestSummary,
    InvestigateResponse,
    InvestigationSummary,
    LiveIncident,
    PreviewResponse,
)
from api.services.live_investigate import investigate_calls_response
from llm_cost_investigator.connectors import (
    DEFAULT_REMOTE_MAX_ROWS,
    ConnectorError,
    PullRequest,
    pull_calls,
)


def _ingest_summary(report) -> IngestSummary:
    return IngestSummary(
        adapter=report.adapter,
        accepted=report.accepted,
        dropped=report.dropped,
        warnings=list(report.warnings),
        cost_estimated_count=report.cost_estimated_count,
        sample_dropped=list(report.sample_dropped),
        detected_headers=report.detected_headers,
    )


def _resolve_request(
    *,
    since_hours: float | None,
    max_rows: int | None,
    feature_default: str | None,
    filters: dict[str, Any] | None,
    has_profile: bool,
) -> PullRequest | None:
    """Build PullRequest when overrides present; else None for profile defaults."""
    if (
        since_hours is None
        and max_rows is None
        and feature_default is None
        and not filters
        and has_profile
    ):
        return None
    return PullRequest(
        since_hours=float(since_hours) if since_hours is not None else 24.0,
        max_rows=int(max_rows) if max_rows is not None else DEFAULT_REMOTE_MAX_ROWS,
        feature_default=feature_default,
        filters=dict(filters or {}),
    )


def pull_remote(
    *,
    connection: str | None = None,
    connector: str | None = None,
    since_hours: float | None = None,
    max_rows: int | None = None,
    feature_default: str | None = None,
    filters: dict[str, Any] | None = None,
) -> tuple[list, Any, dict[str, Any]]:
    if bool(connection) == bool(connector):
        if connection and connector:
            raise ValueError("Provide only one of connection or connector")
        raise ValueError("Provide connection (profile) or connector (e.g. langfuse)")

    request = _resolve_request(
        since_hours=since_hours,
        max_rows=max_rows,
        feature_default=feature_default,
        filters=filters,
        has_profile=bool(connection),
    )
    try:
        return pull_calls(
            connection=connection,
            connector=connector,
            request=request,
        )
    except ConnectorError as exc:
        raise ValueError(str(exc)) from exc


def preview_remote(
    *,
    connection: str | None = None,
    connector: str | None = None,
    since_hours: float | None = None,
    max_rows: int | None = None,
    feature_default: str | None = None,
    filters: dict[str, Any] | None = None,
) -> PreviewResponse:
    calls, report, meta = pull_remote(
        connection=connection,
        connector=connector,
        since_hours=since_hours,
        max_rows=max_rows,
        feature_default=feature_default,
        filters=filters,
    )
    features = sorted({c.feature_tag for c in calls})
    samples = [c.model_dump(mode="json") for c in calls[:5]]
    return PreviewResponse(
        ingest=_ingest_summary(report),
        feature_tags=features,
        sample_calls=samples,
        guessed_mapping=None,
        pull_meta=meta,
    )


def test_remote(
    *,
    connection: str | None = None,
    connector: str | None = None,
    since_hours: float | None = 1.0,
) -> tuple[bool, str, int, int, dict[str, Any] | None]:
    try:
        calls, report, meta = pull_remote(
            connection=connection,
            connector=connector,
            since_hours=since_hours if since_hours is not None else 1.0,
            max_rows=50,
        )
    except ValueError as exc:
        return False, str(exc), 0, 0, None
    msg = (
        f"OK — accepted={report.accepted} generations "
        f"(host={meta.get('host')}, pages={meta.get('pages_fetched')})"
    )
    return True, msg, report.accepted, report.dropped, meta


def investigate_remote(
    *,
    connection: str | None = None,
    connector: str | None = None,
    since_hours: float | None = None,
    max_rows: int | None = None,
    feature_default: str | None = None,
    filters: dict[str, Any] | None = None,
    label: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    force_fallback: bool = False,
    max_anomalies: int = 5,
    write_reports: bool = True,
) -> InvestigateResponse:
    calls, report, meta = pull_remote(
        connection=connection,
        connector=connector,
        since_hours=since_hours,
        max_rows=max_rows,
        feature_default=feature_default,
        filters=filters,
    )
    if report.accepted == 0:
        raise ValueError("No accepted rows after remote pull (empty window or all dropped)")

    stem = label or (
        f"langfuse_{connection}" if connection else f"langfuse_{connector or 'remote'}"
    )
    response = investigate_calls_response(
        calls,
        ingest=_ingest_summary(report),
        label=stem,
        source_name=f"connector:{meta.get('connector')}",
        provider=provider,
        model=model,
        force_fallback=force_fallback,
        max_anomalies=max_anomalies,
        write_reports=write_reports,
    )
    return response.model_copy(update={"pull_meta": meta})
