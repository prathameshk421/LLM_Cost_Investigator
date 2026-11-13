"""Live LLM agents vs deterministic agents on the held-out set.

Runs every held-out case through the full pipeline twice on the same data:
once with deterministic agents (``fallback``) and once with a live provider.
Reports accuracy for both, how often the LLM disagreed with the hard skills,
why live calls fell back, tool use, latency and spend.

    DEPLOY_API_KEY=... PYTHONPATH=. python -m benchmarks.heldout.live_llm \\
        --provider deploy --seed-from 120 --seed-to 124 --workers 8 --budget-usd 1.5

Each worker process has its own budget ledger; ``--budget-usd`` is split
evenly across workers, so the total cannot exceed it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen  # noqa: E402


def _classify(reason: str | None) -> str:
    r = (reason or "").lower()
    if not r:
        return "none"
    if "falsifier" in r:
        return "skipped_by_falsifier"
    if "budget" in r or "call cap" in r:
        return "budget"
    if "dsml" in r or "tool-call syntax" in r:
        return "tool_syntax_leak"
    if "timeout" in r or "timed out" in r:
        return "timeout"
    if "rate" in r and "limit" in r or "429" in r:
        return "rate_limited"
    if "out-of-scope" in r or "mismatch" in r:
        return "wrong_agent_or_scope"
    if "parse" in r or "json" in r or "validation" in r:
        return "invalid_json"
    return "other_error"


def _run(calls, provider: str, max_anomalies: int):
    from llm_cost_investigator.investigate import investigate_calls

    t = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        r = investigate_calls(calls, provider=provider, write_reports=False, max_anomalies=max_anomalies)
    reported = sorted(
        {(i.root_cause.hypothesis, i.anomaly_window.feature_tag) for i in r.incidents
         if i.root_cause.hypothesis != "no_strong_signal"}
    )
    runs = [run for i in r.incidents for run in i.agent_runs]
    return {
        "reported": [list(x) for x in reported],
        "sec": round(time.time() - t, 2),
        "live_runs": sum(1 for x in runs if x.provider == provider and provider != "fallback"),
        "fallback_reasons": [_classify(x.fallback_reason) for x in runs if x.fallback_used],
        "disagreed": sum(1 for x in runs if x.evidence.supporting_metrics.get("llm_disagreed")),
    }


def run_one(job):
    case, seed, provider, max_anom = job
    calls, truth, _ = gen.make_case(case, seed)
    truth_set = {tuple(t) for t in truth}
    out = {"case": case, "seed": seed, "kind": gen.CASES[case].kind, "truth": [list(t) for t in truth]}
    for side, prov in (("det", "fallback"), ("llm", provider)):
        res = _run(calls, prov, max_anom)
        rep = {tuple(x) for x in res["reported"]}
        res["hit"] = bool(truth_set) and truth_set <= rep
        res["false_alarm"] = bool(rep - truth_set)
        res["exact"] = rep == truth_set
        out[side] = res
    from llm_cost_investigator.usage import ledger

    snap = ledger.snapshot()
    out["usage_cum"] = {k: snap[k] for k in ("calls", "errors", "blocked", "prompt_tokens", "completion_tokens", "spent_usd")}
    out["pid"] = os.getpid()
    return out


def _init_worker(budget: float, max_calls: int) -> None:
    os.environ["LLM_BUDGET_USD"] = str(budget)
    os.environ["LLM_MAX_CALLS"] = str(max_calls)
    from llm_cost_investigator import usage

    usage.ledger = usage.UsageLedger(restore=False)


def summarize(rows: list[dict], provider: str) -> dict:
    s: dict = {"datasets": len(rows)}
    for side in ("det", "llm"):
        pos = [r for r in rows if r["truth"]]
        neg = [r for r in rows if not r["truth"]]
        s[side] = {
            "hit_rate": round(sum(r[side]["hit"] for r in pos) / max(len(pos), 1), 4),
            "false_alarm_rate_on_negatives": round(sum(r[side]["false_alarm"] for r in neg) / max(len(neg), 1), 4),
            "exact_match": round(sum(r[side]["exact"] for r in rows) / len(rows), 4),
            "mean_sec": round(sum(r[side]["sec"] for r in rows) / len(rows), 2),
        }
    s["llm"]["live_agent_runs"] = sum(r["llm"]["live_runs"] for r in rows)
    s["llm"]["disagreements_kept_by_skills"] = sum(r["llm"]["disagreed"] for r in rows)
    s["llm"]["fallback_reasons"] = dict(Counter(x for r in rows for x in r["llm"]["fallback_reasons"]))
    by_pid: dict = {}
    for r in rows:
        prev = by_pid.get(r["pid"])
        if prev is None or r["usage_cum"]["calls"] >= prev["calls"]:
            by_pid[r["pid"]] = r["usage_cum"]
    usage = defaultdict(float)
    for u in by_pid.values():
        for k, v in u.items():
            usage[k] += v
    s["usage"] = {k: round(v, 6) for k, v in usage.items()}
    per_case = defaultdict(lambda: {"n": 0, "det_ok": 0, "llm_ok": 0})
    for r in rows:
        c = per_case[r["case"]]
        c["n"] += 1
        c["det_ok"] += r["det"]["exact"]
        c["llm_ok"] += r["llm"]["exact"]
    s["per_case"] = dict(per_case)
    s["disagree_cases"] = [
        {"case": r["case"], "seed": r["seed"], "det": r["det"]["reported"], "llm": r["llm"]["reported"]}
        for r in rows if r["det"]["reported"] != r["llm"]["reported"]
    ]
    s["provider"] = provider
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", default="deploy")
    ap.add_argument("--seed-from", type=int, default=120)
    ap.add_argument("--seed-to", type=int, default=124)
    ap.add_argument("--cases", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--budget-usd", type=float, default=1.0, help="total cap across all workers")
    ap.add_argument("--max-calls", type=int, default=4000, help="total call cap across all workers")
    ap.add_argument("--max-anomalies", type=int, default=5)
    ap.add_argument("--out", default="data/reports/live_llm_eval.json")
    a = ap.parse_args()

    cases = a.cases.split(",") if a.cases else list(gen.CASES)
    jobs = [(c, s, a.provider, a.max_anomalies) for s in range(a.seed_from, a.seed_to) for c in cases]
    per_budget = a.budget_usd / a.workers
    per_calls = max(1, a.max_calls // a.workers)
    t = time.time()
    rows = []
    with Pool(a.workers, initializer=_init_worker, initargs=(per_budget, per_calls)) as pool:
        for i, row in enumerate(pool.imap_unordered(run_one, jobs), 1):
            rows.append(row)
            print(f"[{i}/{len(jobs)}] {row['case']:<24} seed {row['seed']:<4} det={'ok ' if row['det']['exact'] else 'BAD'} "
                  f"llm={'ok ' if row['llm']['exact'] else 'BAD'} live={row['llm']['live_runs']} "
                  f"fb={row['llm']['fallback_reasons']} {row['llm']['sec']}s", flush=True)
    summary = summarize(rows, a.provider)
    summary["wall_sec"] = round(time.time() - t, 1)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("per_case", "disagree_cases")}, indent=1))
    print("per case (exact match det / llm of n):")
    for c, v in summary["per_case"].items():
        print(f"  {c:<26} {v['det_ok']}/{v['n']}  {v['llm_ok']}/{v['n']}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
