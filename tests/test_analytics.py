"""Analytics, budgets, history seed and their HTTP routes."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "web"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from api.routes.analytics import router  # noqa: E402
from llm_cost_investigator import analytics, budgets  # noqa: E402
from llm_cost_investigator.history_seed import seed_history  # noqa: E402
from llm_cost_investigator.warehouse import reset_warehouse  # noqa: E402

END = datetime(2026, 3, 30, 12, 0, 0)  # near month end, so a "warning" state is reachable


@pytest.fixture
def empty():
    w = reset_warehouse(":memory:")
    budgets.reset_threshold_state()
    yield w
    reset_warehouse(":memory:")  # leave a usable singleton for other test files


@pytest.fixture
def seeded():
    w = reset_warehouse(":memory:")
    budgets.reset_threshold_state()
    info = seed_history(w, days=14, seed=3, end=END)
    w.info = info  # type: ignore[attr-defined]
    yield w
    reset_warehouse(":memory:")  # leave a usable singleton for other test files


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# -- seed ----------------------------------------------------------------------


def test_seed_deterministic_and_fast():
    w = reset_warehouse(":memory:")
    t0 = time.perf_counter()
    a = seed_history(w, days=30, seed=7, end=END)
    elapsed = time.perf_counter() - t0
    rows_a = w.query("SELECT call_id, ts, model, cost_usd FROM llm_calls ORDER BY call_id")
    b = seed_history(w, days=30, seed=7, end=END)
    rows_b = w.query("SELECT call_id, ts, model, cost_usd FROM llm_calls ORDER BY call_id")
    c = seed_history(w, days=30, seed=8, end=END)
    assert elapsed < 5
    assert a["inserted"] == b["inserted"] and rows_a == rows_b
    assert 30_000 <= a["inserted"] <= 80_000
    reset_warehouse(":memory:")  # leave a usable singleton for other test files


def test_seed_shapes(seeded):
    feats = {r["feature_tag"] for r in seeded.query("SELECT DISTINCT feature_tag FROM llm_calls")}
    models = {r["model"] for r in seeded.query("SELECT DISTINCT model FROM llm_calls")}
    assert len(feats) == 8 and len(models) >= 5
    assert seeded.query("SELECT max(retry_count) m FROM llm_calls")[0]["m"] >= 6
    assert seeded.query("SELECT max(ts) m FROM llm_calls")[0]["m"] <= END


def test_seed_reset_false_keeps_rows(seeded):
    n = seeded.count()
    seed_history(seeded, days=2, seed=99, reset=False, end=END)
    assert seeded.count() > n
    seed_history(seeded, days=2, seed=99, reset=True, end=END)
    assert seeded.count() < n


# -- overview ------------------------------------------------------------------


def test_overview_consistency(seeded):
    o = analytics.overview(seeded, days=7)
    assert o["as_of"].endswith("Z") and o["series"]["granularity"] == "day"
    k = o["kpis"]
    assert k["spend_usd"] > 0 and k["calls"] > 0 and k["p95_latency_ms"] > 0
    assert sum(f["spend_usd"] for f in o["by_feature"]) == pytest.approx(k["spend_usd"])
    assert sum(m["spend_usd"] for m in o["by_model"]) == pytest.approx(k["spend_usd"])
    series_total = sum(sum(v) for v in o["series"]["by_feature"].values())
    assert series_total == pytest.approx(k["spend_usd"])
    assert all(len(v) == len(o["series"]["buckets"]) for v in o["series"]["by_feature"].values())
    assert k["forecast_p10_usd"] <= k["forecast_month_usd"] <= k["forecast_p90_usd"]
    assert k["retry_waste_usd"] > 0


def test_overview_hourly_granularity(seeded):
    o = analytics.overview(seeded, days=1)
    assert o["series"]["granularity"] == "hour"
    assert len(o["series"]["buckets"]) >= 24


# -- drivers -------------------------------------------------------------------


def test_driver_effects_sum_to_delta(seeded):
    for days in (1, 3, 7):
        d = analytics.drivers(seeded, days=days)
        for row in d["drivers"]:
            total = row["volume_effect_usd"] + row["tokens_effect_usd"] + row["price_effect_usd"]
            assert abs(total - row["delta_usd"]) < 1e-9
            assert abs(row["curr_usd"] - row["prev_usd"] - row["delta_usd"]) < 1e-9


def test_decompose_edge_cases():
    # New row without any baseline: all volume.
    assert analytics.decompose((0, 0, 0), (5, 100, 2.0)) == (2.0, 0.0, 0.0)
    # Disappeared row: all volume, negative.
    assert analytics.decompose((5, 100, 2.0), (0, 0, 0)) == (-2.0, 0.0, 0.0)
    v, t, p = analytics.decompose((10, 1000, 1.0), (20, 3000, 5.0))
    assert abs(v + t + p - 4.0) < 1e-12
    assert v > 0 and t > 0 and p > 0


def test_misroute_day_is_top_driver_with_price_effect(seeded):
    as_of = seeded.info["misroute_start"] + timedelta(hours=6)
    d = analytics.drivers(seeded, days=1, as_of=as_of)
    top = d["drivers"][0]
    assert (top["feature_tag"], top["model"]) == ("summarizer", "gpt-4.1")
    assert top["price_effect_usd"] > 0
    assert top["price_effect_usd"] > abs(top["volume_effect_usd"]) + abs(top["tokens_effect_usd"])


# -- what-if -------------------------------------------------------------------


def test_whatif_downgrade_saves_money(seeded):
    r = analytics.whatif(seeded, "gpt-4o-mini", days=14)
    assert r["savings_usd"] > 0 and r["projected_usd"] < r["actual_usd"]
    assert r["monthly_savings_usd"] == pytest.approx(r["savings_usd"] / 14 * 30)
    only = analytics.whatif(seeded, "gpt-4o-mini", days=14, from_model="gpt-4.1")
    assert 0 < only["savings_usd"] < r["savings_usd"]
    assert sum(x["savings_usd"] for x in only["rows"]) == pytest.approx(only["savings_usd"])
    feat = analytics.whatif(seeded, "gpt-4o-mini", days=14, feature_tag="summarizer")
    assert {x["feature_tag"] for x in feat["rows"]} == {"summarizer"}


# -- waste ---------------------------------------------------------------------


def test_waste_finds_retry_storm_and_chains(seeded):
    r = analytics.waste(seeded, days=14)
    assert r["retry_waste_usd"] > 0
    assert r["retry_waste_by_feature"][0]["feature_tag"] == "support_reply"
    assert r["top_chains"] and r["top_chains"][0]["feature_tag"] == "agent_reflection"
    assert r["top_chains"][0]["depth"] >= 6 and r["deep_chain_cost_usd"] > 0


# -- budgets -------------------------------------------------------------------


def test_forecast_ordering(seeded):
    f = budgets.forecast_month(seeded)
    assert f["p10_usd"] <= f["forecast_usd"] <= f["p90_usd"]
    assert f["forecast_usd"] >= f["mtd_usd"] > 0
    feat = budgets.forecast_month(seeded, scope="feature", scope_value="summarizer")
    assert 0 < feat["mtd_usd"] < f["mtd_usd"]


def test_budget_status_transitions(seeded):
    mtd = budgets.forecast_month(seeded)["mtd_usd"]
    cases = {"ok": mtd * 10, "warning": mtd / 0.85, "forecast_over": mtd * 1.05, "over": mtd * 0.5}
    for expected, monthly in cases.items():
        b = budgets.create_budget(seeded, "global", None, monthly, 0.8)
        assert b["status"] == expected, (expected, b)
        assert b["pct_used"] == pytest.approx(mtd / monthly)
        assert budgets.delete_budget(seeded, b["budget_id"])
    assert budgets.list_budgets(seeded) == []


def test_check_thresholds_reports_only_worsening(seeded):
    mtd = budgets.forecast_month(seeded)["mtd_usd"]
    ok = budgets.create_budget(seeded, "global", None, mtd * 10, 0.8)
    assert budgets.check_thresholds(seeded) == []
    over = budgets.create_budget(seeded, "feature", "summarizer", 0.0001, 0.8)
    hit = budgets.check_thresholds(seeded)
    assert [h["budget_id"] for h in hit] == [over["budget_id"]]
    assert hit[0]["previous_status"] == "ok" and hit[0]["status"] == "over"
    assert budgets.check_thresholds(seeded) == []
    budgets.delete_budget(seeded, ok["budget_id"])


def test_budget_validation_errors(empty):
    for args in [("feature", None, 10, 0.8), ("global", None, 0, 0.8), ("global", None, 10, 1.5), ("team", "x", 10, 0.8)]:
        with pytest.raises(ValueError):
            budgets.create_budget(empty, *args)


# -- empty warehouse -----------------------------------------------------------


def test_empty_warehouse_returns_zeros(empty):
    o = analytics.overview(empty, days=30)
    assert o["kpis"]["spend_usd"] == 0 and o["kpis"]["calls"] == 0 and o["kpis"]["p95_latency_ms"] == 0
    assert o["by_feature"] == [] and o["by_model"] == []
    assert analytics.drivers(empty)["drivers"] == []
    w = analytics.whatif(empty, "gpt-4o-mini")
    assert w["savings_usd"] == 0 and w["rows"] == []
    wa = analytics.waste(empty)
    assert wa["retry_waste_usd"] == 0 and wa["top_chains"] == []
    assert budgets.forecast_month(empty)["forecast_usd"] == 0


# -- HTTP ----------------------------------------------------------------------


def test_http_empty(empty, client):
    for url in [
        "/api/analytics/overview",
        "/api/analytics/drivers",
        "/api/analytics/whatif?to_model=gpt-4o-mini",
        "/api/analytics/waste",
        "/api/budgets",
    ]:
        assert client.get(url).status_code == 200, url
    s = client.get("/api/warehouse/stats").json()
    assert s["calls"] == 0 and s["start"] is None and s["end"] is None


def test_http_seed_and_analytics(empty, client):
    r = client.post("/api/warehouse/seed", json={"days": 5, "seed": 2}).json()
    assert r["inserted"] > 0 and r["start"].endswith("Z") and r["end"].endswith("Z")
    s = client.get("/api/warehouse/stats").json()
    assert s["calls"] == r["inserted"] and s["path"] == ":memory:"
    o = client.get("/api/analytics/overview?days=3").json()
    assert o["kpis"]["calls"] > 0 and o["days"] == 3
    d = client.get("/api/analytics/drivers?days=2").json()
    assert "drivers" in d and "total_delta_usd" in d
    wi = client.get("/api/analytics/whatif?to_model=gpt-4o-mini&days=5&from_model=gpt-4o").json()
    assert wi["savings_usd"] > 0
    assert "top_chains" in client.get("/api/analytics/waste?days=5").json()


def test_http_validation(empty, client):
    assert client.get("/api/analytics/overview?days=0").status_code == 422
    assert client.get("/api/analytics/overview?days=366").status_code == 422
    assert client.get("/api/analytics/overview?as_of=garbage").status_code == 422
    assert client.get("/api/analytics/whatif").status_code == 422
    assert client.post("/api/warehouse/seed", json={"days": 0}).status_code == 422
    assert client.post("/api/budgets", json={"scope": "global", "monthly_usd": 0}).status_code == 422
    assert client.post("/api/budgets", json={"scope": "bad", "monthly_usd": 5}).status_code == 422
    assert client.post("/api/budgets", json={"scope": "global", "monthly_usd": 5, "alert_pct": 1.2}).status_code == 422
    assert client.post("/api/budgets", json={"scope": "feature", "monthly_usd": 5}).status_code == 422


def test_http_budget_crud(seeded, client):
    created = client.post(
        "/api/budgets", json={"scope": "feature", "scope_value": "summarizer", "monthly_usd": 50, "alert_pct": 0.7}
    )
    assert created.status_code == 200
    row = created.json()
    for key in ("budget_id", "mtd_spend_usd", "forecast_month_usd", "pct_used", "forecast_pct", "status", "days_left"):
        assert key in row
    assert row["scope"] == "feature" and row["alert_pct"] == 0.7
    assert [b["budget_id"] for b in client.get("/api/budgets").json()] == [row["budget_id"]]
    assert client.delete(f"/api/budgets/{row['budget_id']}").json() == {"deleted": True}
    assert client.delete(f"/api/budgets/{row['budget_id']}").status_code == 404
    assert client.get("/api/budgets").json() == []
