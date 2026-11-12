"""Tier C red-team runner.

    python -m benchmarks.redteam.run --seed-from 200 --seed-to 220 --workers 8 --out docs/proof/data/tier_c.json
"""
from __future__ import annotations

import argparse
import json
import traceback
from collections import defaultdict
from datetime import timedelta
from multiprocessing import Pool
from pathlib import Path

from benchmarks.heldout.stats import NSS, to_dt, wilson
from benchmarks.redteam import cases as C

CONF = 0.60
TOL = timedelta(minutes=5)


def run_one(args):
    name, seed = args
    row = dict(case=name, seed=seed, crash=None, ambiguous=name in C.AMBIGUOUS)
    try:
        from llm_cost_investigator.investigate import investigate_calls
        calls, truth, t_inc, _ = C.make_case(name, seed)
        res = investigate_calls(calls, provider="fallback", write_reports=False, max_anomalies=5)
        row.update(truth=[list(t) for t in truth], t_inc=t_inc.isoformat(), n_calls=len(calls),
                   incidents=[dict(cause=i.root_cause.hypothesis, feature=i.anomaly_window.feature_tag,
                                   start=i.anomaly_window.start_time.isoformat(),
                                   end=i.anomaly_window.end_time.isoformat(),
                                   confidence=i.root_cause.confidence) for i in res.incidents],
                   flagged=[dict(feature=w["feature_tag"], start=w["start_time"].isoformat(),
                                 end=w["end_time"].isoformat()) for w in res.flagged_windows])
    except Exception as e:  # recorded, never raised
        row["crash"] = type(e).__name__
        row["trace"] = traceback.format_exc(limit=3)
    return row


def score(row):
    """Add outcome fields to a row."""
    if row["crash"]:
        return row
    truth = {tuple(t) for t in row["truth"]}
    t_inc = to_dt(row["t_inc"])
    inc = [i for i in row["incidents"] if i["cause"] != NSS]
    rep = {(i["cause"], i["feature"]) for i in inc}
    valid = {(i["cause"], i["feature"]) for i in inc if to_dt(i["start"]) >= t_inc - TOL}
    wrong = [i for i in inc if (i["cause"], i["feature"]) not in truth]
    row["hit"] = bool(truth) and truth <= valid
    row["silent_ok"] = (not truth) and not inc
    row["miss"] = bool(truth) and not row["hit"]
    row["conf_wrong"] = any(i["confidence"] >= CONF for i in wrong)
    row["unconf_wrong"] = bool(wrong) and not row["conf_wrong"]
    row["reported"] = sorted([list(r) for r in rep])
    why = []
    for cause, feat in sorted(truth - valid):
        fl = [w for w in row["flagged"] if w["feature"] == feat and to_dt(w["end"]) > t_inc]
        mine = [i for i in row["incidents"] if i["feature"] == feat]
        if (cause, feat) in rep:
            why.append(f"{feat}: correct label only on a window before the incident")
        elif not fl and not mine:
            why.append(f"{feat}: no window flagged")
        elif not mine:
            why.append(f"{feat}: flagged but not investigated (max_anomalies cap)")
        elif all(i["cause"] == NSS for i in mine):
            why.append(f"{feat}: flagged but labelled no_strong_signal")
        else:
            why.append(f"{feat}: wrong cause " + "/".join(sorted({i['cause'] for i in mine if i['cause'] != NSS})))
    row["why"] = why
    return row


def summarize(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["case"]].append(score(r))
    out = {}

    def rate(k, n):
        p, lo, hi = wilson(k, n)
        return dict(k=k, n=n, rate=p, lo=lo, hi=hi)

    for name in C.CASES:
        rs = by.get(name, [])
        if not rs:
            continue
        ok = [r for r in rs if not r["crash"]]
        n = len(rs)
        pos = [r for r in ok if r["truth"]]
        d = dict(n=n, crashes=sum(1 for r in rs if r["crash"]),
                 crash_types=sorted({r["crash"] for r in rs if r["crash"]}),
                 ambiguous=name in C.AMBIGUOUS, kind="positive" if pos else "negative",
                 conf_wrong=rate(sum(r["conf_wrong"] for r in ok), len(ok)),
                 unconf_wrong=rate(sum(r["unconf_wrong"] for r in ok), len(ok)))
        if pos:
            d["hit"] = rate(sum(r["hit"] for r in pos), len(pos))
            d["miss"] = rate(sum(r["miss"] for r in pos), len(pos))
        else:
            d["silent_ok"] = rate(sum(r["silent_ok"] for r in ok), len(ok))
        reasons = defaultdict(int)
        for r in ok:
            for w in r.get("why", []):
                reasons[w.split(": ", 1)[1] if name != "c06_three_causes" else w] += 1
        d["why"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
        d["reported_causes"] = dict(defaultdict(int, _count(ok)))
        out[name] = d
    scored = [r for rs in by.values() for r in rs if not r["crash"] and not r["ambiguous"]]
    allr = [r for rs in by.values() for r in rs]
    cw = sum(r["conf_wrong"] for r in scored)
    summ = dict(n_datasets=len(allr), crashes=sum(1 for r in allr if r["crash"]),
                n_scored=len(scored), conf_wrong=rate(cw, len(scored)),
                unconf_wrong=rate(sum(r["unconf_wrong"] for r in scored), len(scored)))
    summ["pass"] = summ["crashes"] == 0 and summ["conf_wrong"]["rate"] <= 0.10
    return dict(summary=summ, per_case=out), by


def _count(rs):
    c = defaultdict(int)
    for r in rs:
        for cause, _f in r["reported"]:
            c[cause] += 1
    return c


def pct(d):
    return f"{d['rate']*100:.0f}% [{d['lo']*100:.0f}-{d['hi']*100:.0f}]"


def markdown(res):
    L = ["# Tier C failure analysis (draft, auto-generated)", "",
         f"Datasets: {res['summary']['n_datasets']}. Crashes: {res['summary']['crashes']}. "
         f"Confident wrong (non-ambiguous): {pct(res['summary']['conf_wrong'])}. "
         f"Pass: {res['summary']['pass']}.", "",
         "| case | n | crash | hit / silent ok | miss | confident wrong | unconfident wrong | why (count) |",
         "|---|---|---|---|---|---|---|---|"]
    for name, d in res["per_case"].items():
        good = pct(d["hit"]) if "hit" in d else pct(d["silent_ok"]) + " silent"
        miss = pct(d["miss"]) if "miss" in d else "-"
        why = "; ".join(f"{k} ({v})" for k, v in d["why"].items()) or "-"
        tag = " (ambiguous)" if d["ambiguous"] else ""
        L.append(f"| {name}{tag} | {d['n']} | {d['crashes']} | {good} | {miss} | {pct(d['conf_wrong'])} "
                 f"| {pct(d['unconf_wrong'])} | {why} |")
    L += ["", "Intervals are Wilson 95%. Confident means root cause confidence >= 0.60.",
          "Ambiguous cases are excluded from the pass rate."]
    return "\n".join(L) + "\n"


def run(case_names, seeds, workers=1):
    jobs = [(c, s) for c in case_names for s in seeds]
    if workers > 1:
        with Pool(workers) as p:
            rows = p.map(run_one, jobs, chunksize=1)
    else:
        rows = [run_one(j) for j in jobs]
    res, by = summarize(rows)
    res["rows"] = [r for rs in by.values() for r in rs]
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-from", type=int, required=True)
    ap.add_argument("--seed-to", type=int, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cases", default="")
    ap.add_argument("--out", default="docs/proof/data/tier_c.json")
    ap.add_argument("--md", default="docs/proof/failure_analysis_draft.md")
    a = ap.parse_args()
    names = a.cases.split(",") if a.cases else list(C.CASES)
    res = run(names, range(a.seed_from, a.seed_to), a.workers)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, default=str))
    Path(a.md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.md).write_text(markdown(res))
    print(markdown(res))
    raise SystemExit(0 if res["summary"]["pass"] else 1)
