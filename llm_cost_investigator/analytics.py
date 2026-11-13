"""Spend analytics over the DuckDB warehouse.

Pure functions: each takes a ``Warehouse`` and returns a JSON-ready dict that
matches the HTTP contract in docs/ARCHITECTURE.md. All windows are
``(start, as_of]`` on the naive-UTC ``ts`` column. The previous period has the
same length and ends where the current one starts.

Retry waste: a call with ``retry_count = r`` made ``r`` failed attempts before
it succeeded, so ``cost_usd * r / (r + 1)`` is counted as waste.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from llm_cost_investigator.budgets import forecast_month, iso_z, resolve_as_of
from llm_cost_investigator.ingest.pricing import lookup_rates
from llm_cost_investigator.warehouse import Warehouse

__all__ = ["overview", "drivers", "whatif", "waste", "decompose"]

PRICING_SOURCE = "static table llm_cost_investigator/ingest/pricing.py"
RETRY_WASTE_SQL = "cost_usd * retry_count / (retry_count + 1.0)"
MAX_DRIVERS = 25
MAX_CHAINS = 10


def _pct(curr: float, prev: float) -> float | None:
    """Percent change; None when there is no previous spend to compare to."""
    return (curr - prev) / prev * 100.0 if prev > 0 else None


def _windows(w: Warehouse, days: int, as_of: datetime | str | None):
    end = resolve_as_of(w, as_of)
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    return end, start, prev_start


# -- overview ------------------------------------------------------------------


def _breakdown(w: Warehouse, col: str, prev_start, start, end) -> list[dict[str, Any]]:
    """Per-key curr aggregates plus prev spend. ``col`` is a trusted constant."""
    rows = w.query(
        f"""
        SELECT {col} AS key,
               coalesce(sum(CASE WHEN ts > $1 THEN cost_usd END), 0) AS spend,
               count(CASE WHEN ts > $1 THEN 1 END) AS calls,
               coalesce(sum(CASE WHEN ts > $1 THEN input_tokens + output_tokens END), 0) AS tokens,
               coalesce(sum(CASE WHEN ts <= $1 THEN cost_usd END), 0) AS prev_spend
        FROM llm_calls WHERE ts > $2 AND ts <= $3
        GROUP BY {col}
        """,
        [start, prev_start, end],
    )
    total = sum(r["spend"] for r in rows)
    out = []
    for r in rows:
        if r["calls"] == 0:
            continue
        out.append(
            {
                "key": r["key"],
                "spend_usd": float(r["spend"]),
                "calls": int(r["calls"]),
                "tokens": int(r["tokens"]),
                "cost_per_1k_tokens": float(r["spend"]) / r["tokens"] * 1000.0 if r["tokens"] else 0.0,
                "share": float(r["spend"]) / total if total else 0.0,
                "change_pct": _pct(float(r["spend"]), float(r["prev_spend"])),
            }
        )
    out.sort(key=lambda x: (-x["spend_usd"], x["key"]))
    return out


def overview(w: Warehouse, days: int = 30, as_of: datetime | str | None = None) -> dict[str, Any]:
    end, start, prev_start = _windows(w, days, as_of)
    cur = w.cursor()
    spend, calls, tokens, p95 = cur.execute(
        "SELECT coalesce(sum(cost_usd),0), count(*), coalesce(sum(input_tokens+output_tokens),0), "
        "quantile_cont(latency_ms, 0.95) FROM llm_calls WHERE ts > ? AND ts <= ?",
        [start, end],
    ).fetchone()
    prev_spend = cur.execute(
        "SELECT coalesce(sum(cost_usd),0) FROM llm_calls WHERE ts > ? AND ts <= ?", [prev_start, start]
    ).fetchone()[0]
    retry_waste = cur.execute(
        f"SELECT coalesce(sum({RETRY_WASTE_SQL}),0) FROM llm_calls WHERE ts > ? AND ts <= ? AND retry_count > 0",
        [start, end],
    ).fetchone()[0]
    fc = forecast_month(w, end)

    # Series: hour buckets for short windows, else day buckets (partial edge buckets included).
    unit = "hour" if days <= 2 else "day"
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    first = start.replace(minute=0, second=0, microsecond=0)
    if unit == "day":
        first = first.replace(hour=0)
    buckets: list[datetime] = []
    b = first
    while b <= end:
        buckets.append(b)
        b += step
    index = {b: i for i, b in enumerate(buckets)}
    by_feature_series: dict[str, list[float]] = {}
    for r in w.query(
        f"SELECT date_trunc('{unit}', ts) AS b, feature_tag, sum(cost_usd) AS s "
        "FROM llm_calls WHERE ts > ? AND ts <= ? GROUP BY 1, 2",
        [start, end],
    ):
        arr = by_feature_series.setdefault(r["feature_tag"], [0.0] * len(buckets))
        arr[index[r["b"]]] = float(r["s"])

    spend = float(spend)
    return {
        "as_of": iso_z(end),
        "days": days,
        "kpis": {
            "spend_usd": spend,
            "calls": int(calls),
            "tokens": int(tokens),
            "cost_per_1k_tokens": spend / tokens * 1000.0 if tokens else 0.0,
            "cost_per_call": spend / calls if calls else 0.0,
            "spend_prev_period_usd": float(prev_spend),
            "spend_change_pct": _pct(spend, float(prev_spend)),
            "mtd_spend_usd": fc["mtd_usd"],
            "forecast_month_usd": fc["forecast_usd"],
            "forecast_p10_usd": fc["p10_usd"],
            "forecast_p90_usd": fc["p90_usd"],
            "burn_rate_usd_per_day": fc["burn_usd_per_day"],
            "retry_waste_usd": float(retry_waste),
            "p95_latency_ms": float(p95) if p95 is not None else 0.0,
        },
        "series": {
            "granularity": unit,
            "buckets": [iso_z(x) for x in buckets],
            "by_feature": dict(sorted(by_feature_series.items())),
        },
        "by_feature": _breakdown(w, "feature_tag", prev_start, start, end),
        "by_model": _breakdown(w, "model", prev_start, start, end),
    }


# -- drivers -------------------------------------------------------------------


def decompose(
    prev: tuple[float, float, float],
    curr: tuple[float, float, float],
    ref: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Split delta spend into (volume, tokens-per-call, price-per-token) effects.

    ``prev`` and ``curr`` are (calls, tokens, spend). Spend = calls x tokens/call
    x price/token. Chained decomposition, in this order:
      volume = (c1 - c0) * u0 * p0
      tokens = c1 * (u1 - u0) * p0
      price  = c1 * u1 * (p1 - p0)   (computed as the remainder, so the sum is exact)
    A row with no previous calls (new model for a feature) uses ``ref``, the
    feature's previous blended (calls, tokens, spend), as its baseline. That
    way a model switch shows as a price effect, not as new volume. Without a
    usable baseline the whole delta is volume.
    """
    c0, t0, s0 = prev
    c1, t1, s1 = curr
    delta = s1 - s0
    if c0 > 0 and c1 > 0 and t0 > 0 and t1 > 0:
        u0, p0 = t0 / c0, s0 / t0
        volume = (c1 - c0) * u0 * p0
        tokens = c1 * (t1 / c1 - u0) * p0
    elif c0 == 0 and c1 > 0 and t1 > 0 and ref and ref[0] > 0 and ref[1] > 0:
        u0, p0 = ref[1] / ref[0], ref[2] / ref[1]
        volume = c1 * u0 * p0
        tokens = (t1 - c1 * u0) * p0
    else:
        return delta, 0.0, 0.0
    return volume, tokens, delta - volume - tokens


def drivers(w: Warehouse, days: int = 7, as_of: datetime | str | None = None) -> dict[str, Any]:
    end, start, prev_start = _windows(w, days, as_of)
    rows = w.query(
        """
        SELECT feature_tag, model,
               count(CASE WHEN ts <= $1 THEN 1 END) AS c0,
               coalesce(sum(CASE WHEN ts <= $1 THEN input_tokens + output_tokens END), 0) AS t0,
               coalesce(sum(CASE WHEN ts <= $1 THEN cost_usd END), 0) AS s0,
               count(CASE WHEN ts > $1 THEN 1 END) AS c1,
               coalesce(sum(CASE WHEN ts > $1 THEN input_tokens + output_tokens END), 0) AS t1,
               coalesce(sum(CASE WHEN ts > $1 THEN cost_usd END), 0) AS s1
        FROM llm_calls WHERE ts > $2 AND ts <= $3
        GROUP BY feature_tag, model
        """,
        [start, prev_start, end],
    )
    feat_prev: dict[str, list[float]] = {}
    for r in rows:
        agg = feat_prev.setdefault(r["feature_tag"], [0.0, 0.0, 0.0])
        agg[0] += r["c0"]
        agg[1] += r["t0"]
        agg[2] += r["s0"]

    out = []
    for r in rows:
        prev = (float(r["c0"]), float(r["t0"]), float(r["s0"]))
        curr = (float(r["c1"]), float(r["t1"]), float(r["s1"]))
        delta = curr[2] - prev[2]
        if delta == 0 and prev[0] == 0 and curr[0] == 0:
            continue
        vol, tok, price = decompose(prev, curr, tuple(feat_prev[r["feature_tag"]]))
        out.append(
            {
                "feature_tag": r["feature_tag"],
                "model": r["model"],
                "prev_usd": prev[2],
                "curr_usd": curr[2],
                "delta_usd": delta,
                "volume_effect_usd": vol,
                "tokens_effect_usd": tok,
                "price_effect_usd": price,
            }
        )
    total = sum(d["delta_usd"] for d in out)
    # Share of the total *absolute* movement, signed. Dividing by the net
    # delta breaks when rises and falls cancel (shares like 291% or -152%).
    gross = sum(abs(d["delta_usd"]) for d in out)
    out.sort(key=lambda d: (-abs(d["delta_usd"]), d["feature_tag"], d["model"]))
    out = out[:MAX_DRIVERS]
    for d in out:
        d["share_of_delta"] = d["delta_usd"] / gross if gross else 0.0
    return {"as_of": iso_z(end), "days": days, "total_delta_usd": total, "drivers": out}


# -- what-if -------------------------------------------------------------------


def whatif(
    w: Warehouse,
    to_model: str,
    days: int = 30,
    feature_tag: str | None = None,
    from_model: str | None = None,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    end, start, _ = _windows(w, days, as_of)
    sql = (
        "SELECT feature_tag, model, sum(input_tokens) AS i, sum(output_tokens) AS o, sum(cost_usd) AS cost "
        "FROM llm_calls WHERE ts > ? AND ts <= ?"
    )
    params: list[Any] = [start, end]
    if feature_tag:
        sql += " AND feature_tag = ?"
        params.append(feature_tag)
    sql += " GROUP BY 1, 2 ORDER BY 1, 2"
    in_rate, out_rate, _key = lookup_rates(to_model)
    rows = []
    actual_total = projected_total = 0.0
    for r in w.query(sql, params):
        actual = float(r["cost"])
        affected = r["model"] != to_model and (from_model is None or r["model"] == from_model)
        projected = (r["i"] * in_rate + r["o"] * out_rate) / 1_000_000.0 if affected else actual
        actual_total += actual
        projected_total += projected
        rows.append(
            {
                "feature_tag": r["feature_tag"],
                "model": r["model"],
                "actual_usd": actual,
                "projected_usd": projected,
                "savings_usd": actual - projected,
            }
        )
    savings = actual_total - projected_total
    return {
        "to_model": to_model,
        "days": days,
        "actual_usd": actual_total,
        "projected_usd": projected_total,
        "savings_usd": savings,
        "savings_pct": savings / actual_total * 100.0 if actual_total else 0.0,
        "monthly_savings_usd": savings / days * 30.0,
        "rows": rows,
        "pricing_source": PRICING_SOURCE,
    }


# -- waste ---------------------------------------------------------------------

_CHAIN_SQL = """
WITH RECURSIVE w AS (
    SELECT call_id, parent_call_id, feature_tag, cost_usd
    FROM llm_calls WHERE ts > ? AND ts <= ?
), chain AS (
    SELECT call_id, call_id AS root, feature_tag, cost_usd, 1 AS depth
    FROM w
    WHERE parent_call_id IS NULL
       OR parent_call_id NOT IN (SELECT call_id FROM w)
    UNION ALL
    SELECT w.call_id, c.root, c.feature_tag, w.cost_usd, c.depth + 1
    FROM w JOIN chain c ON w.parent_call_id = c.call_id
    WHERE c.depth < 100
)
SELECT root AS root_call_id, any_value(feature_tag) AS feature_tag,
       max(depth) AS depth, count(*) AS calls, sum(cost_usd) AS usd
FROM chain GROUP BY root HAVING max(depth) >= 3
ORDER BY usd DESC
"""

DEEP_CHAIN_DEPTH = 4


def waste(w: Warehouse, days: int = 30, as_of: datetime | str | None = None) -> dict[str, Any]:
    end, start, _ = _windows(w, days, as_of)
    by_feature = w.query(
        f"""
        SELECT feature_tag, sum({RETRY_WASTE_SQL}) AS usd, count(*) AS retried_calls
        FROM llm_calls WHERE ts > ? AND ts <= ? AND retry_count > 0
        GROUP BY feature_tag ORDER BY usd DESC, feature_tag
        """,
        [start, end],
    )
    chains = w.query(_CHAIN_SQL, [start, end])
    return {
        "retry_waste_usd": float(sum(r["usd"] for r in by_feature)),
        "retry_waste_by_feature": [
            {"feature_tag": r["feature_tag"], "usd": float(r["usd"]), "retried_calls": int(r["retried_calls"])}
            for r in by_feature
        ],
        "deep_chain_cost_usd": float(sum(c["usd"] for c in chains if c["depth"] >= DEEP_CHAIN_DEPTH)),
        "top_chains": [
            {
                "root_call_id": c["root_call_id"],
                "feature_tag": c["feature_tag"],
                "depth": int(c["depth"]),
                "calls": int(c["calls"]),
                "usd": float(c["usd"]),
            }
            for c in chains[:MAX_CHAINS]
        ],
    }
