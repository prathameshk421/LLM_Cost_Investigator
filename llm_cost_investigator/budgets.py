"""Budgets: CRUD, month-to-date spend, forecast and threshold checks.

Forecast method: month-to-date spend + days left x median daily spend of the
last 7 days. P10 and P90 use the 10th and 90th percentile of those 7 daily
spends. A "day" here is a rolling 24 h bucket that ends at ``as_of``, so the
newest bucket is never a partial calendar day.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from llm_cost_investigator.warehouse import Warehouse

__all__ = [
    "resolve_as_of",
    "iso_z",
    "forecast_month",
    "create_budget",
    "delete_budget",
    "get_budget",
    "list_budgets",
    "check_thresholds",
    "reset_threshold_state",
]

STATUS_RANK = {"ok": 0, "warning": 1, "forecast_over": 2, "over": 3}

# budget_id -> last status seen by check_thresholds
_last_status: dict[str, str] = {}


# -- time helpers ------------------------------------------------------------


def iso_z(ts: datetime) -> str:
    """Naive-UTC or aware datetime to ISO string ending in Z."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat(timespec="seconds") + "Z"


def resolve_as_of(w: Warehouse, as_of: datetime | str | None = None) -> datetime:
    """Naive UTC 'now' for analytics: explicit, else latest ts, else wall clock."""
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of.strip().replace("Z", "+00:00"))
    if isinstance(as_of, datetime):
        if as_of.tzinfo is not None:
            as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
        return as_of
    row = w.cursor().execute("SELECT max(ts) FROM llm_calls").fetchone()
    if row and row[0] is not None:
        return row[0]
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile (same as quantile_cont)."""
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _month_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt


def _scope_sql(scope: str, scope_value: str | None) -> tuple[str, list[Any]]:
    if scope == "feature":
        return " AND feature_tag = ?", [scope_value]
    if scope == "model":
        return " AND model = ?", [scope_value]
    return "", []


# -- forecast ----------------------------------------------------------------


def forecast_month(
    w: Warehouse,
    as_of: datetime | str | None = None,
    scope: str = "global",
    scope_value: str | None = None,
) -> dict[str, float]:
    """MTD spend and month-end forecast for a scope.

    Returns mtd_usd, forecast_usd, p10_usd, p90_usd, burn_usd_per_day
    (median of last 7 daily spends) and days_left.
    """
    end = resolve_as_of(w, as_of)
    month_start, month_end = _month_bounds(end)
    extra, extra_params = _scope_sql(scope, scope_value)
    cur = w.cursor()
    mtd = cur.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE ts >= ? AND ts <= ?" + extra,
        [month_start, end, *extra_params],
    ).fetchone()[0]
    rows = cur.execute(
        "SELECT CAST(floor(epoch(? ::TIMESTAMP - ts) / 86400) AS INTEGER) AS b, sum(cost_usd) "
        "FROM llm_calls WHERE ts > ? AND ts <= ?" + extra + " GROUP BY b",
        [end, end - timedelta(days=7), end, *extra_params],
    ).fetchall()
    daily = [0.0] * 7
    for b, v in rows:
        if 0 <= b < 7:
            daily[b] = float(v)
    days_left = max((month_end - end).total_seconds() / 86400.0, 0.0)
    med = _quantile(daily, 0.5)
    p10 = _quantile(daily, 0.1)
    p90 = _quantile(daily, 0.9)
    mtd = float(mtd)
    return {
        "mtd_usd": mtd,
        "forecast_usd": mtd + days_left * med,
        "p10_usd": mtd + days_left * p10,
        "p90_usd": mtd + days_left * p90,
        "burn_usd_per_day": med,
        "days_left": days_left,
    }


# -- CRUD --------------------------------------------------------------------


def _validate(scope: str, scope_value: str | None, monthly_usd: float, alert_pct: float) -> str | None:
    if scope not in ("global", "feature", "model"):
        raise ValueError("scope must be global, feature or model")
    if scope != "global" and not scope_value:
        raise ValueError("scope_value is required for feature and model budgets")
    if monthly_usd <= 0:
        raise ValueError("monthly_usd must be > 0")
    if not 0.0 <= alert_pct <= 1.0:
        raise ValueError("alert_pct must be between 0 and 1")
    return None if scope == "global" else scope_value


def create_budget(
    w: Warehouse,
    scope: str,
    scope_value: str | None,
    monthly_usd: float,
    alert_pct: float = 0.8,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    scope_value = _validate(scope, scope_value, monthly_usd, alert_pct)
    budget_id = "bud_" + uuid.uuid4().hex[:10]
    with w._write_lock:
        w.cursor().execute(
            "INSERT INTO budgets (budget_id, scope, scope_value, monthly_usd, alert_pct) VALUES (?,?,?,?,?)",
            [budget_id, scope, scope_value, float(monthly_usd), float(alert_pct)],
        )
    row = get_budget(w, budget_id, as_of)
    assert row is not None
    return row


def delete_budget(w: Warehouse, budget_id: str) -> bool:
    with w._write_lock:
        cur = w.cursor()
        n = cur.execute("SELECT count(*) FROM budgets WHERE budget_id = ?", [budget_id]).fetchone()[0]
        if not n:
            return False
        cur.execute("DELETE FROM budgets WHERE budget_id = ?", [budget_id])
    _last_status.pop(budget_id, None)
    return True


def _status(pct_used: float, forecast_usd: float, monthly: float, alert_pct: float) -> str:
    if pct_used >= 1.0:
        return "over"
    if forecast_usd > monthly:
        return "forecast_over"
    if pct_used >= alert_pct:
        return "warning"
    return "ok"


def _build_row(w: Warehouse, b: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    f = forecast_month(w, as_of, b["scope"], b["scope_value"])
    monthly = float(b["monthly_usd"])
    pct = f["mtd_usd"] / monthly
    fpct = f["forecast_usd"] / monthly
    return {
        "budget_id": b["budget_id"],
        "scope": b["scope"],
        "scope_value": b["scope_value"],
        "monthly_usd": monthly,
        "alert_pct": float(b["alert_pct"]),
        "mtd_spend_usd": f["mtd_usd"],
        "forecast_month_usd": f["forecast_usd"],
        "pct_used": pct,
        "forecast_pct": fpct,
        "status": _status(pct, f["forecast_usd"], monthly, float(b["alert_pct"])),
        "days_left": f["days_left"],
    }


def get_budget(w: Warehouse, budget_id: str, as_of: datetime | str | None = None) -> dict[str, Any] | None:
    rows = w.query(
        "SELECT budget_id, scope, scope_value, monthly_usd, alert_pct FROM budgets WHERE budget_id = ?",
        [budget_id],
    )
    if not rows:
        return None
    return _build_row(w, rows[0], resolve_as_of(w, as_of))


def list_budgets(w: Warehouse, as_of: datetime | str | None = None) -> list[dict[str, Any]]:
    end = resolve_as_of(w, as_of)
    rows = w.query(
        "SELECT budget_id, scope, scope_value, monthly_usd, alert_pct FROM budgets ORDER BY created_at, budget_id"
    )
    return [_build_row(w, b, end) for b in rows]


# -- threshold checks --------------------------------------------------------


def reset_threshold_state() -> None:
    _last_status.clear()


def check_thresholds(w: Warehouse, as_of: datetime | str | None = None) -> list[dict[str, Any]]:
    """Return budget rows whose status got worse since the previous call.

    Signature: ``check_thresholds(w: Warehouse, as_of=None) -> list[dict]``.
    Each dict has the same shape as ``list_budgets`` rows plus ``previous_status``.
    The first call compares against "ok", so a budget that is already over
    is reported once. Statuses that improve are stored silently. State lives in
    the module dict ``_last_status``. The streaming builder calls this each
    tick and emits one ``budget`` event per returned row.
    """
    worsened: list[dict[str, Any]] = []
    for row in list_budgets(w, as_of):
        prev = _last_status.get(row["budget_id"], "ok")
        if STATUS_RANK[row["status"]] > STATUS_RANK[prev]:
            worsened.append({**row, "previous_status": prev})
        _last_status[row["budget_id"]] = row["status"]
    return worsened
