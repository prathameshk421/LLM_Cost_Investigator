"""The Tier D scorer applies the pre-registered rule."""
from datetime import datetime, timezone

from benchmarks.tier_d.score import score

T0 = datetime(2026, 11, 13, 6, 42, 31, tzinfo=timezone.utc)  # bug 1 at 07:57:31, 2 at 08:27:31, 3 at 08:57:31


def inc(h, f, start, end="2026-11-13T10:00:00+00:00", c=0.8):
    return {"hypothesis": h, "feature_tag": f, "start": start, "end": end, "confidence": c}


def run(incs):
    return score(incs, T0, ["retry_loop", "misroute", "bloat"], 75, 20, 10)


GOOD = [inc("uncapped_retry_loop", "classify", "2026-11-13T07:55:00+00:00"),
        inc("expensive_model_misroute", "summarize", "2026-11-13T08:30:00+00:00"),
        inc("context_bloat_self_calling_agent", "reflect", "2026-11-13T09:00:00+00:00")]


def test_all_caught_in_time_passes():
    r = run(GOOD)
    assert r["pass"] and [b["pass"] for b in r["bugs"]] == [True, True, True]
    assert r["bugs"][1]["windows_after_start"] == 0.5


def test_late_detection_fails_that_bug():
    late = GOOD[:1] + [inc("expensive_model_misroute", "summarize", "2026-11-13T08:40:00+00:00")] + GOOD[2:]
    r = run(late)
    assert not r["pass"] and r["bugs"][1]["pass"] is False


def test_baseline_alarm_fails_take_but_no_strong_signal_is_ignored():
    r = run(GOOD + [inc("no_strong_signal", "classify", "2026-11-13T07:00:00+00:00")])
    assert r["pass"]
    r = run(GOOD + [inc("expensive_model_misroute", "summarize", "2026-11-13T07:00:00+00:00")])
    assert not r["pass"] and len(r["baseline_alarms"]) == 1


def test_label_on_wrong_feature_is_reported_and_does_not_count():
    r = run([inc("uncapped_retry_loop", "reflect", "2026-11-13T07:55:00+00:00")] + GOOD[1:])
    assert not r["bugs"][0]["pass"] and len(r["wrong_label_incidents"]) == 1
