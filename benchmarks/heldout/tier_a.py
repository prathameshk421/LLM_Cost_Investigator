"""Tier A runner (pre-registered in benchmarks/PREREGISTRATION.md).

    python -m benchmarks.heldout.tier_a --seed-from 200 --seed-to 300 --workers 8 \
        --out data/reports/tier_a_200_299.json [--baselines] [--cases a,b] [--limit-cases N]

Seed range is [seed-from, seed-to). Seeds 200-299 are reserved for the single
reported run. Use 300-399 for development.
"""
from __future__ import annotations

import argparse
import json
import time
from multiprocessing import Pool
from pathlib import Path

from benchmarks.heldout import gen
from benchmarks.heldout.stats import evaluate_rows, score_dataset


def _try_baselines():
    try:
        from benchmarks import baselines  # type: ignore
        return baselines.run_baselines
    except Exception:
        return None


def run_one(args):
    name, seed, use_baselines = args
    from llm_cost_investigator.investigate import investigate_calls
    calls, truth, t_inc = gen.make_case(name, seed)
    t0 = time.time()
    res = investigate_calls(calls, provider="fallback", write_reports=False, max_anomalies=5)
    dt = time.time() - t0
    incidents = [dict(cause=i.root_cause.hypothesis, feature=i.anomaly_window.feature_tag,
                      start=i.anomaly_window.start_time.isoformat(), end=i.anomaly_window.end_time.isoformat(),
                      confidence=i.root_cause.confidence) for i in res.incidents]
    truth = [list(t) for t in truth]
    scores = {"product": score_dataset(truth, t_inc, incidents, calls)}
    base_raw = {}
    if use_baselines:
        run_baselines = _try_baselines()
        if run_baselines:
            base_raw = run_baselines(calls)
            for arm, incs in base_raw.items():
                scores[arm] = score_dataset(truth, t_inc, incs, calls)
    return dict(case=name, seed=seed, kind=gen.CASES[name].kind, truth=truth, t_inc=t_inc.isoformat(),
                incidents=incidents, scores=scores,
                baseline_incidents={k: [{**i, "start": str(i["start"]), "end": str(i["end"])} for i in v]
                                    for k, v in base_raw.items()},
                n_calls=len(calls), runtime_s=dt)


def fmt(r):
    return f"{r['rate']*100:5.1f}% [{r['lo']*100:5.1f}, {r['hi']*100:5.1f}] ({r['k']}/{r['n']})"


def print_report(summary):
    print(f"\n{summary['n_datasets']} datasets")
    for arm, o in summary["overall"].items():
        print(f"\n== {arm} ==")
        print(f"  hit (time-tolerant)  {fmt(o['hit'])}")
        print(f"  hit (any time)       {fmt(o['any_time_hit'])}")
        print(f"  false alarm (neg)    {fmt(o['false_alarm'])}")
        print(f"  wrong extra (pos)    {fmt(o['wrong_extra'])}")
        for cause, v in o["per_cause"].items():
            print(f"  {cause:34s} found {fmt(v['detected'])}  delay med {v['delay_median_min']:.1f} "
                  f"p95 {v['delay_p95_min']:.1f} min  $ med {v['dollars_median']:.2f} p95 {v['dollars_p95']:.2f}")
    print("\nPre-registered criteria:")
    for k, v in summary["criteria"].items():
        print(f"  {k:36s} {'n/a (baselines missing)' if v is None else ('PASS' if v else 'FAIL')}")
    print("Falsification (True = falsified):")
    for k, v in summary["falsification"].items():
        print(f"  {k:40s} {'n/a' if v is None else v}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tier A evaluation")
    ap.add_argument("--seed-from", type=int, required=True)
    ap.add_argument("--seed-to", type=int, required=True, help="exclusive")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="")
    ap.add_argument("--cases", default="", help="comma list; default all")
    ap.add_argument("--limit-cases", type=int, default=0, help="first N cases (dev)")
    ap.add_argument("--baselines", action="store_true")
    a = ap.parse_args(argv)
    cases = a.cases.split(",") if a.cases else list(gen.CASES)
    if a.limit_cases:
        cases = cases[:a.limit_cases]
    use_b = a.baselines
    if use_b and _try_baselines() is None:
        print("note: benchmarks/baselines.py (run_baselines) not available; skipping baselines")
        use_b = False
    jobs = [(c, s, use_b) for c in cases for s in range(a.seed_from, a.seed_to)]
    t0 = time.time()
    with Pool(a.workers) as p:
        rows = p.map(run_one, jobs, chunksize=2)
    print(f"{len(rows)} datasets in {time.time()-t0:.0f}s")
    summary = evaluate_rows(rows)
    print_report(summary)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        json.dump(dict(seed_from=a.seed_from, seed_to=a.seed_to, summary=summary, rows=rows),
                  open(a.out, "w"), default=str)
        print(f"wrote {a.out}")
    return rows, summary


if __name__ == "__main__":
    main()
