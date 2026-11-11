"""Analytics, budgets and warehouse endpoints."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from llm_cost_investigator import analytics, budgets
from llm_cost_investigator.budgets import iso_z
from llm_cost_investigator.history_seed import seed_history
from llm_cost_investigator.warehouse import get_warehouse

router = APIRouter(tags=["analytics"])


def _as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return budgets.resolve_as_of(get_warehouse(), value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid as_of: {exc}") from exc


@router.get("/api/analytics/overview")
def analytics_overview(
    days: int = Query(30, ge=1, le=365), as_of: str | None = Query(None)
) -> dict[str, Any]:
    return analytics.overview(get_warehouse(), days, _as_of(as_of))


@router.get("/api/analytics/drivers")
def analytics_drivers(
    days: int = Query(7, ge=1, le=365), as_of: str | None = Query(None)
) -> dict[str, Any]:
    return analytics.drivers(get_warehouse(), days, _as_of(as_of))


@router.get("/api/analytics/whatif")
def analytics_whatif(
    to_model: str = Query(..., min_length=1, max_length=120),
    days: int = Query(30, ge=1, le=365),
    feature_tag: str | None = Query(None, max_length=120),
    from_model: str | None = Query(None, max_length=120),
    as_of: str | None = Query(None),
) -> dict[str, Any]:
    return analytics.whatif(get_warehouse(), to_model, days, feature_tag, from_model, _as_of(as_of))


@router.get("/api/analytics/waste")
def analytics_waste(
    days: int = Query(30, ge=1, le=365), as_of: str | None = Query(None)
) -> dict[str, Any]:
    return analytics.waste(get_warehouse(), days, _as_of(as_of))


# -- budgets -------------------------------------------------------------------


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["global", "feature", "model"] = "global"
    scope_value: str | None = Field(default=None, max_length=120)
    monthly_usd: float = Field(gt=0)
    alert_pct: float = Field(default=0.8, ge=0.0, le=1.0)


@router.get("/api/budgets")
def list_budgets() -> list[dict[str, Any]]:
    return budgets.list_budgets(get_warehouse())


@router.post("/api/budgets")
def create_budget(req: BudgetCreate) -> dict[str, Any]:
    try:
        return budgets.create_budget(get_warehouse(), req.scope, req.scope_value, req.monthly_usd, req.alert_pct)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/budgets/{budget_id}")
def delete_budget(budget_id: str) -> dict[str, bool]:
    if not budgets.delete_budget(get_warehouse(), budget_id):
        raise HTTPException(status_code=404, detail=f"Budget {budget_id} not found")
    return {"deleted": True}


# -- warehouse -----------------------------------------------------------------


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=365)
    seed: int = Field(default=1, ge=0, le=1_000_000)
    incidents: bool = True
    reset: bool = True


@router.post("/api/warehouse/seed")
def warehouse_seed(req: SeedRequest) -> dict[str, Any]:
    res = seed_history(get_warehouse(), days=req.days, seed=req.seed, incidents=req.incidents, reset=req.reset)
    return {"inserted": res["inserted"], "start": iso_z(res["start"]), "end": iso_z(res["end"])}


@router.get("/api/warehouse/stats")
def warehouse_stats() -> dict[str, Any]:
    w = get_warehouse()
    lo, hi = w.time_range()
    size_mb = 0.0
    if w.path != ":memory:" and os.path.exists(w.path):
        size_mb = round(os.path.getsize(w.path) / 1_048_576, 3)
    return {
        "calls": w.count(),
        "start": iso_z(lo) if lo else None,
        "end": iso_z(hi) if hi else None,
        "path": w.path,
        "size_mb": size_mb,
    }
