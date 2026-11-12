"""Tier A statistics and scoring (definitions from benchmarks/PREREGISTRATION.md).

Pure functions, no pipeline imports, so they can be unit tested on small inputs.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

NSS = "no_strong_signal"
TOLERANCE = timedelta(minutes=5)

# Pre-registered thresholds (Tier A).
PREREG = {
    "hit_lower_min": 0.97,
    "fa_upper_max": 0.01,
    "median_delay_max": 10.0,
    "p95_delay_max": 20.0,
    "baseline_fa_gap_min": 0.20,
    "falsify_hit_margin": 0.02,
    "falsify_fa_margin": 0.02,
    "falsify_timetol_gap": 0.03,
}


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score interval. Returns (rate, lo, hi). n == 0 gives (nan, nan, nan)."""
    if n <= 0:
        return (float("nan"),) * 3
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def percentile(xs, q: float):
    """Linear-interpolation percentile, q in [0, 100]. Empty gives nan."""
    s = sorted(xs)
    if not s:
        return float("nan")
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def median(xs):
    return percentile(xs, 50)


def p95(xs):
    return percentile(xs, 95)


def to_dt(x) -> datetime:
    if isinstance(x, str):
        x = datetime.fromisoformat(x)
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x


# ------------------------------------------------------------------ scoring
def score_dataset(truth, t_inc, incidents, calls=()) -> dict:
    """Score one dataset.

    truth: iterable of (cause, feature); empty means a negative dataset.
    incidents: iterable of dict(cause, feature, start, end); NSS ones are ignored.
    calls: iterable of objects with .feature_tag, .timestamp, .cost_usd.
    """
    truth = {(c, f) for c, f in truth}
    t_inc = to_dt(t_inc)
    reported = []
    for i in incidents:
        if i["cause"] == NSS:
            continue
        reported.append((i["cause"], i["feature"], to_dt(i["start"]), to_dt(i["end"])))
    rep_any = {(c, f) for c, f, _, _ in reported}
    # Time-tolerant: only windows that start at or after t_inc - 5 min count.
    valid = [r for r in reported if r[2] >= t_inc - TOLERANCE]
    items = []
    for cause, feat in sorted(truth):
        ends = [e for c, f, s, e in valid if (c, f) == (cause, feat)]
        first_end = min(ends) if ends else None
        delay = (first_end - t_inc).total_seconds() / 60.0 if first_end else None
        dollars = None
        if first_end is not None:
            dollars = dollars_before_detection(calls, feat, t_inc, first_end)
        items.append(dict(cause=cause, feature=feat, detected=first_end is not None,
                          any_time=(cause, feat) in rep_any,
                          first_end=first_end.isoformat() if first_end else None,
                          delay_min=delay, dollars=dollars))
    extras = sorted(rep_any - truth)
    is_pos = bool(truth)
    return dict(
        positive=is_pos,
        hit=is_pos and all(it["detected"] for it in items),
        any_time_hit=is_pos and all(it["any_time"] for it in items),
        false_alarm=(not is_pos) and bool(reported),
        wrong_extra=is_pos and bool(extras),
        extras=[list(e) for e in extras],
        items=items,
    )


def dollars_before_detection(calls, feature, t_inc, first_end) -> float:
    """Spend of `feature` with t_inc <= ts < first_end."""
    t_inc, first_end = to_dt(t_inc), to_dt(first_end)
    total = 0.0
    for c in calls:
        if c.feature_tag == feature and t_inc <= to_dt(c.timestamp) < first_end:
            total += c.cost_usd
    return total


# -------------------------------------------------------------- aggregation
def _rate(k, n):
    r, lo, hi = wilson(k, n)
    return dict(k=k, n=n, rate=r, lo=lo, hi=hi)


def _agg(scores):
    """Aggregate a list of dataset scores (one arm)."""
    pos = [s for s in scores if s["positive"]]
    neg = [s for s in scores if not s["positive"]]
    out = dict(
        hit=_rate(sum(s["hit"] for s in pos), len(pos)),
        any_time_hit=_rate(sum(s["any_time_hit"] for s in pos), len(pos)),
        false_alarm=_rate(sum(s["false_alarm"] for s in neg), len(neg)),
        wrong_extra=_rate(sum(s["wrong_extra"] for s in pos), len(pos)),
    )
    by_cause = defaultdict(list)
    for s in pos:
        for it in s["items"]:
            by_cause[it["cause"]].append(it)
    out["per_cause"] = {}
    for cause, its in sorted(by_cause.items()):
        delays = [it["delay_min"] for it in its if it["detected"]]
        dollars = [it["dollars"] for it in its if it["detected"]]
        out["per_cause"][cause] = dict(
            truth_items=len(its),
            detected=_rate(sum(it["detected"] for it in its), len(its)),
            delay_median_min=median(delays), delay_p95_min=p95(delays),
            n_delays=len(delays),
            dollars_median=median(dollars), dollars_p95=p95(dollars),
            dollars_total=float(sum(dollars)),
        )
    return out


def evaluate_rows(rows, prereg=None) -> dict:
    """Aggregate Tier A metrics.

    Each row: dict(case, kind, scores={"product": s, "B1": s, "B2": s}) where s
    comes from score_dataset. Baseline arms are optional.
    """
    P = {**PREREG, **(prereg or {})}
    arms = []
    for r in rows:
        for k in r["scores"]:
            if k not in arms:
                arms.append(k)
    out = dict(n_datasets=len(rows), overall={}, per_case={}, criteria={}, falsification={})
    for arm in arms:
        out["overall"][arm] = _agg([r["scores"][arm] for r in rows if arm in r["scores"]])
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    for case, rs in by_case.items():
        out["per_case"][case] = {arm: {k: v for k, v in _agg([r["scores"][arm] for r in rs if arm in r["scores"]]).items()
                                       if k != "per_cause"} for arm in arms}
        out["per_case"][case]["kind"] = rs[0].get("kind")
    prod = out["overall"]["product"]
    c = out["criteria"]
    c["1_hit_lower_ge_97"] = _ok(prod["hit"]["n"] > 0 and prod["hit"]["lo"] >= P["hit_lower_min"])
    c["2_fa_upper_le_1"] = _ok(prod["false_alarm"]["n"] > 0 and prod["false_alarm"]["hi"] <= P["fa_upper_max"])
    causes = prod["per_cause"]
    c["3_delay_median_p95_per_cause"] = _ok(bool(causes) and all(
        v["n_delays"] > 0 and v["delay_median_min"] <= P["median_delay_max"]
        and v["delay_p95_min"] <= P["p95_delay_max"] for v in causes.values()))
    base = [a for a in ("B1", "B2") if a in out["overall"]]
    if len(base) == 2:
        c["4_fa_20pts_below_baselines"] = _ok(all(
            out["overall"][b]["false_alarm"]["rate"] - prod["false_alarm"]["rate"] >= P["baseline_fa_gap_min"]
            for b in base))
    else:
        c["4_fa_20pts_below_baselines"] = None  # baselines missing
    vals = [v for k, v in c.items()]
    c["all_pass"] = None if any(v is None for v in vals) else all(vals)
    f = out["falsification"]
    f["timetol_more_than_3pts_below_anytime"] = _ok(
        prod["any_time_hit"]["rate"] - prod["hit"]["rate"] > P["falsify_timetol_gap"])
    if "B2" in out["overall"]:
        b2 = out["overall"]["B2"]
        f["b2_within_2pts_of_product"] = _ok(
            b2["hit"]["rate"] >= prod["hit"]["rate"] - P["falsify_hit_margin"]
            and b2["false_alarm"]["rate"] <= prod["false_alarm"]["rate"] + P["falsify_fa_margin"])
    else:
        f["b2_within_2pts_of_product"] = None
    return out


def _ok(x):
    return bool(x)


