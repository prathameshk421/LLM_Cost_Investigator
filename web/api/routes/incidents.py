"""Incident list and detail routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.replay import IncidentSummary, ReplayIncident
from api.services.catalog import catalog

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentSummary])
def list_incidents() -> list[IncidentSummary]:
    return catalog.list_summaries()


@router.get("/{incident_id}", response_model=ReplayIncident)
def get_incident(incident_id: str) -> ReplayIncident:
    incident = catalog.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return incident
