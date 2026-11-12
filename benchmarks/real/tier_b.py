"""Tier B runner (pre-registered in benchmarks/PREREGISTRATION.md).

    python -m benchmarks.real.tier_b --slices-seed-from 200 --slices-seed-to 300 \
        --clean-from 500 --clean-to 600 --workers 8 --out data/reports/tier_b.json
    python -m benchmarks.real.tier_b --raw --workers 8 --out data/reports/tier_b_raw.json

Seeds 200-299 and clean seeds 500-599 are reserved for the single reported run.
Develop on 300-399. Each seed picks a random 72 h slice start (seed-only rng) and, for
injected slices, the kind is KINDS[seed % 3] (0 retry_loop, 1 context_bloat, 2 model_misroute).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import timedelta
from multiprocessing import Pool
from pathlib import Path

from benchmarks.heldout.stats import NSS, score_dataset, wilson
from benchmarks.real import burstgpt_adapter as bg
from benchmarks.real.inject import KINDS, inject_meta

DEFAULT_CSV = ("/private/tmp/claude-501/-Volumes-SSD-Projects-Personal-LLM-Cost-Investigator--"
               "claude-worktrees-project-review-testing-57183c/c1eedabc-20d4-4c90-895a-1b295847e983/"
               "scratchpad/burstgpt/BurstGPT_1.csv")
CSV = os.environ.get("BURSTGPT_CSV", DEFAULT_CSV)
HOURS = 72
_COLS = None


def cols():
    global _COLS
    if _COLS is None:
        _COLS = bg.load_columns(CSV)
    return _COLS


def slice_start_s(seed: int, kind_salt: int = 0) -> int:
    """Seed-deterministic slice start (seconds from trace start)."""
    c = cols()
    rng = random.Random(f"tierb-slice-{seed}-{kind_salt}")
    return rng.randint(c["ts"][0], c["ts"][-1] - HOURS * 3600)


def _run(calls, max_anomalies=5):
    from llm_cost_investigator.investigate import investigate_calls
    res = investigate_calls(calls, provider="fallback", write_reports=False, max_anomalies=max_anomalies)
    return res


def _incident_dict(i, with_signals=False):
    d = dict(cause=i.root_cause.hypothesis, feature=i.anomaly_window.feature_tag,
             start=i.anomaly_window.start_time.isoformat(), end=i.anomaly_window.end_time.isoformat(),
             confidence=i.root_cause.confidence)
    if with_signals:
        s = i.anomaly_window.signals
        d["signals"] = dict(cost_z=round(s.cost_z_score, 2), calls_z=round(s.calls_z_score, 2),
                            cost_per_call_z=round(s.cost_per_call_z_score, 2),
                            avg_input_z=round(s.avg_input_tokens_z_score, 2),
                            retry_z=round(s.retry_z_score, 2), max_retry=s.max_retry_count,
                            chain_depth=s.max_call_chain_depth, model_changed=s.model_changed,
                            unit_cost_growth_pct=s.unit_cost_growth_pct,
                            models_seen=s.models_seen)
        d["attribution"] = i.root_cause.attribution_pattern
    return d


def run_injected(seed: int) -> dict:
    kind = KINDS[seed % 3]
    start_s = slice_start_s(seed)
    base = bg.slice_columns(cols(), start_s, HOURS)
    start = bg.EPOCH + timedelta(seconds=start_s)
    rng = random.Random(f"tierb-inject-{seed}")
    calls, truth, t_inc, meta = inject_meta(base, kind, rng, HOURS, start)
    t0 = time.time()
    res = _run(calls)
    incs = [_incident_dict(i) for i in res.incidents]
    truth = [list(t) for t in truth]
    return dict(seed=seed, kind=kind, slice_start_s=start_s, truth=truth, t_inc=t_inc.isoformat(), meta=meta,
                incidents=incs, score=score_dataset(truth, t_inc, incs, calls), n_calls=len(calls),
                n_base=len(base), runtime_s=time.time() - t0)


CLEAN_MAX_ANOMALIES = int(os.environ.get("TIERB_CLEAN_MAX_ANOMALIES", "5"))


def run_clean(seed: int) -> dict:
    start_s = slice_start_s(seed)
    base = bg.slice_columns(cols(), start_s, HOURS)
    t0 = time.time()
    res = _run(base, CLEAN_MAX_ANOMALIES)
    incs = [_incident_dict(i, True) for i in res.incidents]
    feats = sorted({c.feature_tag for c in base})
    false = [i for i in incs if i["cause"] != NSS]
    return dict(seed=seed, slice_start_s=start_s, n_features=len(feats), features=feats, incidents=incs,
                n_false=len(false), capped=len(incs) >= CLEAN_MAX_ANOMALIES, n_calls=len(base), runtime_s=time.time() - t0)


def run_raw_slice(start_s: int) -> dict:
    c = cols()
    hours = min(HOURS, (c["ts"][-1] - start_s) / 3600)
    base = bg.slice_columns(c, start_s, hours)
    t0 = time.time()
    res = _run(base, max_anomalies=100_000)
    incs = [_incident_dict(i, True) for i in res.incidents]
    feats = sorted({x.feature_tag for x in base})
    return dict(slice_start_s=start_s, hours=hours, features=feats, incidents=incs, n_calls=len(base),
                runtime_s=time.time() - t0)


def _rate(k, n):
    r, lo, hi = wilson(k, n)
    return dict(k=k, n=n, rate=r, lo=lo, hi=hi)


def _fmt(r):
    return f"{r['rate']*100:5.1f}% [{r['lo']*100:5.1f}, {r['hi']*100:5.1f}] ({r['k']}/{r['n']})"


def summarize_injected(rows):
    out = dict(n=len(rows), hit=_rate(sum(r["score"]["hit"] for r in rows), len(rows)),
               any_time_hit=_rate(sum(r["score"]["any_time_hit"] for r in rows), len(rows)),
               wrong_extra=_rate(sum(r["score"]["wrong_extra"] for r in rows), len(rows)), per_kind={})
    for k in KINDS:
        rs = [r for r in rows if r["kind"] == k]
        out["per_kind"][k] = _rate(sum(r["score"]["hit"] for r in rs), len(rs))
    out["pass_hit_ge_90"] = bool(rows) and out["hit"]["lo"] >= 0.90 or (bool(rows) and out["hit"]["rate"] >= 0.90)
    return out


def summarize_clean(rows):
    fw = sum(r["n_features"] * HOURS / 168.0 for r in rows)
    k = sum(r["n_false"] for r in rows)
    return dict(n_slices=len(rows), false_incidents=k, feature_weeks=fw,
                per_feature_week=(k / fw if fw else float("nan")),
                slices_with_false=sum(r["n_false"] > 0 for r in rows),
                slices_capped=sum(r["capped"] for r in rows),
                pass_le_1_per_feature_week=bool(fw) and k / fw <= 1.0)


def summarize_raw(rows):
    fd = sum(len(r["features"]) * r["hours"] / 24.0 for r in rows)
    incs = [i for r in rows for i in r["incidents"] if i["cause"] != NSS]
    return dict(n_slices=len(rows), feature_days=fd, reported=len(incs),
                per_feature_day=(len(incs) / fd if fd else float("nan")),
                falsified_gt_1_per_feature_day=bool(fd) and len(incs) / fd > 1.0)


def write_raw_markdown(rows, path):
    lines = ["# BurstGPT raw alarms (no injection)", "",
             "| slice start (s) | feature | start | cause | conf | cost_z | calls_z | cpc_z | avg_in_z | retry_z | max_retry | depth | model_changed | attribution |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for i in r["incidents"]:
            if i["cause"] == NSS:
                continue
            s = i["signals"]
            lines.append(f"| {r['slice_start_s']} | {i['feature']} | {i['start']} | {i['cause']} | {i['confidence']:.2f} | "
                         f"{s['cost_z']} | {s['calls_z']} | {s['cost_per_call_z']} | {s['avg_input_z']} | {s['retry_z']} | "
                         f"{s['max_retry']} | {s['chain_depth']} | {s['model_changed']} | {i.get('attribution') or ''} |")
    Path(path).write_text("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tier B (BurstGPT background)")
    ap.add_argument("--slices-seed-from", type=int)
    ap.add_argument("--slices-seed-to", type=int, help="exclusive")
    ap.add_argument("--clean-from", type=int)
    ap.add_argument("--clean-to", type=int, help="exclusive")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="")
    ap.add_argument("--raw", action="store_true", help="un-injected full trace in consecutive 72 h slices")
    ap.add_argument("--raw-max-slices", type=int, default=0, help="dev: first N slices only")
    a = ap.parse_args(argv)
    cols()   # build the cache once before forking
    t0 = time.time()
    result: dict = {}
    with Pool(a.workers) as p:
        if a.raw:
            c = cols()
            starts = [s for s in range(c["ts"][0], c["ts"][-1], HOURS * 3600) if c["ts"][-1] - s >= 24 * 3600]
            if a.raw_max_slices:
                starts = starts[:a.raw_max_slices]
            rows = p.map(run_raw_slice, starts, chunksize=1)
            s = summarize_raw(rows)
            result = dict(mode="raw", summary=s, rows=rows)
            print(f"raw: {s['n_slices']} slices, {s['reported']} reported incidents over {s['feature_days']:.0f} "
                  f"feature-days = {s['per_feature_day']:.3f} per feature-day "
                  f"(falsified if > 1: {s['falsified_gt_1_per_feature_day']})")
            if a.out:
                write_raw_markdown(rows, str(Path(a.out).with_suffix(".md")))
        else:
            inj = clean = []
            if a.slices_seed_from is not None:
                inj = p.map(run_injected, range(a.slices_seed_from, a.slices_seed_to), chunksize=1)
                s = summarize_injected(inj)
                result["injected"] = dict(summary=s, rows=inj)
                print(f"\ninjected slices: {s['n']}")
                print(f"  hit (time-tolerant) {_fmt(s['hit'])}")
                print(f"  hit (any time)      {_fmt(s['any_time_hit'])}")
                print(f"  wrong extra report  {_fmt(s['wrong_extra'])}")
                for k, v in s["per_kind"].items():
                    print(f"  {k:15s}     {_fmt(v)}")
                print(f"  pass (hit rate >= 90%): {s['hit']['rate'] >= 0.9}")
            if a.clean_from is not None:
                clean = p.map(run_clean, range(a.clean_from, a.clean_to), chunksize=1)
                s = summarize_clean(clean)
                result["clean"] = dict(summary=s, rows=clean)
                print(f"\nclean slices: {s['n_slices']}")
                print(f"  false incidents {s['false_incidents']} over {s['feature_weeks']:.1f} feature-weeks "
                      f"= {s['per_feature_week']:.3f} per feature-week; slices with any: {s['slices_with_false']}; "
                      f"capped at max_anomalies: {s['slices_capped']}")
                print(f"  pass (<= 1 per feature-week): {s['pass_le_1_per_feature_week']}")
    print(f"\ndone in {time.time()-t0:.0f}s")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        json.dump(result, open(a.out, "w"), default=str)
        print(f"wrote {a.out}")
    return result


if __name__ == "__main__":
    main()
