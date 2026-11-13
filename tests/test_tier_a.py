import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from benchmarks.heldout.stats import (dollars_before_detection, evaluate_rows, median, p95,
                                      score_dataset, wilson)

T = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
R, M = "uncapped_retry_loop", "expensive_model_misroute"


def inc(cause, feat, start_min, end_min):
    return dict(cause=cause, feature=feat, start=T + timedelta(minutes=start_min), end=T + timedelta(minutes=end_min))


def call(feat, minute, cost):
    return SimpleNamespace(feature_tag=feat, timestamp=T + timedelta(minutes=minute), cost_usd=cost)


def test_wilson_known_values():
    r, lo, hi = wilson(0, 1000)
    assert r == 0 and lo == 0 and abs(hi - 0.0038) < 0.0002
    r, lo, hi = wilson(95, 100)
    assert abs(lo - 0.8884) < 0.001 and abs(hi - 0.9784) < 0.001


def test_median_p95():
    assert median([1, 2, 3]) == 2
    assert abs(p95(list(range(101))) - 95) < 1e-9


def test_time_tolerant_rule():
    truth = [(R, "f")]
    early = score_dataset(truth, T, [inc(R, "f", -30, -25)])
    assert not early["hit"] and early["any_time_hit"]
    edge = score_dataset(truth, T, [inc(R, "f", -5, 0)])
    assert edge["hit"]
    late = score_dataset(truth, T, [inc(R, "f", 5, 10)])
    assert late["hit"]


def test_delay_uses_first_correct_window():
    s = score_dataset([(R, "f")], T, [inc(R, "f", 10, 15), inc(R, "f", 0, 5), inc(M, "f", 0, 5)])
    it = s["items"][0]
    assert it["delay_min"] == 5.0
    assert s["wrong_extra"] and s["extras"] == [[M, "f"]]


def test_dollars_before_detection():
    calls = [call("f", -1, 9), call("f", 0, 1), call("f", 4, 2), call("f", 5, 100), call("g", 1, 50)]
    assert dollars_before_detection(calls, "f", T, T + timedelta(minutes=5)) == 3.0
    s = score_dataset([(R, "f")], T, [inc(R, "f", 0, 5)], calls)
    assert s["items"][0]["dollars"] == 3.0


def test_false_alarm_logic():
    assert not score_dataset([], T, [])["false_alarm"]
    assert not score_dataset([], T, [inc("no_strong_signal", "f", 0, 5)])["false_alarm"]
    assert score_dataset([], T, [inc(R, "f", 100, 105)])["false_alarm"]


def test_mixed_needs_every_truth():
    s = score_dataset([(R, "f"), (M, "g")], T, [inc(R, "f", 0, 5)])
    assert not s["hit"] and not s["any_time_hit"]


def test_evaluate_rows_criteria():
    rows = []
    for i in range(150):
        rows.append(dict(case="c", kind="positive", scores={"product": score_dataset([(R, "f")], T, [inc(R, "f", 0, 5)])}))
    for i in range(500):
        rows.append(dict(case="n", kind="negative", scores={"product": score_dataset([], T, [])}))
    ev = evaluate_rows(rows)
    c = ev["criteria"]
    assert c["1_hit_lower_ge_97"] and c["2_fa_upper_le_1"] and c["3_delay_median_p95_per_cause"]
    assert c["4_fa_20pts_below_baselines"] is None and c["all_pass"] is None
    assert ev["overall"]["product"]["per_cause"][R]["delay_median_min"] == 5.0


def test_end_to_end_small():
    from benchmarks.heldout import tier_a
    t0 = time.time()
    rows, summary = tier_a.main(["--seed-from", "300", "--seed-to", "301", "--workers", "2",
                                 "--cases", "retry_large,neg_baseline_rag"])
    assert time.time() - t0 < 20
    assert len(rows) == 2
    assert {r["case"] for r in rows} == {"retry_large", "neg_baseline_rag"}
    assert summary["overall"]["product"]["hit"]["n"] == 1
    assert summary["overall"]["product"]["false_alarm"]["n"] == 1
