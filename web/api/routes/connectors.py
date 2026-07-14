"""Remote connector endpoints (Langfuse pull)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.connectors import (
    ConnectorInfo,
    ConnectorTestResponse,
    ProfileSummary,
    RemotePullBody,
)
from api.models.investigate import InvestigateResponse, PreviewResponse
from api.services.remote_investigate import (
    investigate_remote,
    preview_remote,
    test_remote,
)
from llm_cost_investigator.connectors import (
    ConnectorError,
    list_profile_summaries,
    registered_connectors,
)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


@router.get("", response_model=list[ConnectorInfo])
def list_connectors() -> list[ConnectorInfo]:
    return [ConnectorInfo(**row) for row in registered_connectors()]


@router.get("/profiles", response_model=list[ProfileSummary])
def list_profiles() -> list[ProfileSummary]:
    try:
        rows = list_profile_summaries()
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ProfileSummary(**row) for row in rows]


@router.post("/test", response_model=ConnectorTestResponse)
def test_connector(body: RemotePullBody) -> ConnectorTestResponse:
    ok, message, accepted, dropped, meta = test_remote(
        connection=body.connection,
        connector=body.connector,
        since_hours=body.since_hours if body.since_hours is not None else 1.0,
    )
    return ConnectorTestResponse(
        ok=ok,
        message=message,
        accepted=accepted,
        dropped=dropped,
        pull_meta=meta,
    )


@router.post("/preview", response_model=PreviewResponse)
def preview_connector(body: RemotePullBody) -> PreviewResponse:
    try:
        return preview_remote(
            connection=body.connection,
            connector=body.connector,
            since_hours=body.since_hours,
            max_rows=body.max_rows,
            feature_default=body.feature_default,
            filters=body.filters or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/investigate", response_model=InvestigateResponse)
def run_connector_investigate(body: RemotePullBody) -> InvestigateResponse:
    try:
        return investigate_remote(
            connection=body.connection,
            connector=body.connector,
            since_hours=body.since_hours,
            max_rows=body.max_rows,
            feature_default=body.feature_default,
            filters=body.filters or None,
            label=body.label,
            provider=body.provider,
            model=body.model,
            force_fallback=body.force_fallback,
            max_anomalies=body.max_anomalies,
            write_reports=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
