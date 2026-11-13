"""Held-out benchmark: data from a generator written blind to the rules.

benchmarks/heldout/gen.py was written from SPEC.md by an independent
evaluator *before* it read detector.py / agent_skills.py, so its data is
not shaped around the thresholds. Rules must never be tuned on seeds you
report: tune on --half dev, report --half test or fresh --seed-from/--seed-to.

    python -m benchmarks.heldout.run --seed-from 30 --seed-to 60
    python -m benchmarks.heldout.run --seed-from 60 --seed-to 64 --min-hit 0.95 --max-false-alarm 0.05   # CI
"""
from __future__ import annotations

import argparse, json, sys, time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen  # noqa: E402

HYPS = [gen.RETRY, gen.BLOAT, gen.MISROUTE]


def run_one(args):
    name, seed, max_anom = args
    import io, contextlib
    from llm_cost_investigator.investigate import investigate_calls
    calls, truth, t_inc = gen.make_case(name, seed)
    t = time.time()
    res = investigate_calls(calls, provider="fallback", write_reports=False, max_anomalies=max_anom)
    dt = time.time() - t
    reported = sorted({(i.root_cause.hypothesis, i.anomaly_window.feature_tag)
                       for i in res.incidents if i.root_cause.hypothesis != "no_strong_signal"})
    detail = [(i.root_cause.hypothesis, i.anomaly_window.feature_tag, i.anomaly_window.start_time.isoformat(),
               i.anomaly_window.start_time >= t_inc.replace(second=0, microsecond=0) - __import__("datetime").timedelta(minutes=5))
              for i in res.incidents]
    flagged_feats_after = sorted({w["feature_tag"] for w in res.flagged_windows if w["end_time"] > t_inc})
    return dict(case=name, seed=seed, truth=[list(t) for t in truth], reported=[list(r) for r in reported],
                detail=detail, n_calls=len(calls), groups=res.incident_groups, n_flag=res.anomaly_count,
                flagged_after=flagged_feats_after, dt=dt)


def summarize(rows, label=""):
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    tp = defaultdict(int); fp = defaultdict(int); fn = defaultdict(int)
    out = []
    neg_n = neg_fa = 0
    for case in gen.CASES:
        rs = by_case.get(case)
        if not rs:
            continue
        kind = gen.CASES[case].kind
        n = len(rs); hits = 0; fa = 0; exp_total = 0; exp_hit = 0
        for r in rs:
            truth = {tuple(t) for t in r["truth"]}; rep = {tuple(x) for x in r["reported"]}
            for t in truth:
                exp_total += 1
                if t in rep:
                    exp_hit += 1; tp[t[0]] += 1
                else:
                    fn[t[0]] += 1
            extra = rep - truth
            for e in extra:
                fp[e[0]] += 1
            if truth and truth <= rep:
                hits += 1
            if extra:
                fa += 1
        if kind == "negative":
            neg_n += n; neg_fa += fa
        out.append((case, kind, n, hits / n if kind != "negative" else None, fa / n,
                    sum(r["groups"] for r in rs) / n))
    print(f"\n== {label} ==")
    print(f"{'case':28s} {'n':>3s} {'hit':>6s} {'extra/false alarm':>18s} {'incident groups':>16s}")
    for case, kind, n, hit, fa, g in out:
        print(f"{case:28s} {n:3d} {('%5.0f%%' % (hit*100)) if hit is not None else '     -'} {fa*100:17.0f}% {g:16.1f}")
    print("per hypothesis:")
    for h in HYPS:
        p = tp[h] / (tp[h] + fp[h]) if tp[h] + fp[h] else float('nan')
        rc = tp[h] / (tp[h] + fn[h]) if tp[h] + fn[h] else float('nan')
        print(f"  {h:36s} precision {p:5.2f} recall {rc:5.2f}  tp={tp[h]} fp={fp[h]} fn={fn[h]}")
    pos = [o for o in out if o[1] != "negative"]
    npos = sum(o[2] for o in pos)
    print(f"overall: positive+mixed full-hit {sum(o[3]*o[2] for o in pos)/max(npos,1)*100:.1f}%  "
          f"negative false-alarm {neg_fa}/{neg_n} = {neg_fa/max(neg_n,1)*100:.1f}%  "
          f"any-case extra-alarm {sum(o[4]*o[2] for o in out)/sum(o[2] for o in out)*100:.1f}%")


def overall(rows):
    pos_hit = pos_n = neg_fa = neg_n = 0
    for r in rows:
        truth = {tuple(t) for t in r["truth"]}
        rep = {tuple(x) for x in r["reported"]}
        if truth:
            pos_n += 1
            pos_hit += truth <= rep
        else:
            neg_n += 1
            neg_fa += bool(rep)
    return pos_hit / max(pos_n, 1), neg_fa / max(neg_n, 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Held-out benchmark (blind generator)")
    ap.add_argument("--seeds", type=int, default=30, help="seeds 0..N-1 (ignored with --seed-from)")
    ap.add_argument("--seed-from", type=int, default=None)
    ap.add_argument("--seed-to", type=int, default=None)
    ap.add_argument("--half", default="all", choices=["all", "dev", "test"])
    ap.add_argument("--cases", default="")
    ap.add_argument("--max-anomalies", type=int, default=5)
    ap.add_argument("--out", default="")
    ap.add_argument("--min-hit", type=float, default=None, help="exit 1 below this hit rate")
    ap.add_argument("--max-false-alarm", type=float, default=None, help="exit 1 above this rate")
    a = ap.parse_args()
    cases = a.cases.split(",") if a.cases else list(gen.CASES)
    seeds = range(a.seed_from, a.seed_to) if a.seed_from is not None else range(a.seeds)
    jobs = [(c, s, a.max_anomalies) for c in cases for s in seeds
            if a.half == "all" or (s % 2 == 0) == (a.half == "dev")]
    t = time.time()
    with Pool() as p:
        rows = p.map(run_one, jobs, chunksize=4)
    print(f"{len(rows)} datasets, {time.time()-t:.0f}s wall, mean pipeline "
          f"{sum(r['dt'] for r in rows)/len(rows):.2f}s, mean calls {sum(r['n_calls'] for r in rows)/len(rows):.0f}")
    if a.out:
        json.dump(rows, open(a.out, "w"), default=str)
    summarize(rows, f"seeds {seeds.start}-{seeds.stop - 1} ({a.half})")
    hit, fa = overall(rows)
    failed = (a.min_hit is not None and hit < a.min_hit) or (
        a.max_false_alarm is not None and fa > a.max_false_alarm)
    raise SystemExit(1 if failed else 0)
