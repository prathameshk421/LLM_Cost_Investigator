"""Tier E: blind labels from an independent labeller.

1. ``export``: pick 40 held-out datasets (28 with incidents, 12 clean),
   write each as a CSV of per-feature 5-minute aggregates around the event,
   with no tool output and no labels. The answer key goes to a separate file.
2. ``model``: an independent model family (gpt-oss-20b on Hugging Face, not the
   DeepSeek model our agents use) labels every CSV blind.
3. ``score``: agreement of truth, tool and labeller (exact set match) and
   Cohen's kappa, plus a human answer sheet when one is filled in.

    PYTHONPATH=. python -m benchmarks.tier_e.blind_labels export
    PYTHONPATH=. python -m benchmarks.tier_e.blind_labels model      # needs HF login
    PYTHONPATH=. python -m benchmarks.tier_e.blind_labels score
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "heldout"))
import gen  # noqa: E402

OUT = Path("docs/proof/tier_e")
SLICES = OUT / "slices"
KEY = OUT / "answer_key.json"          # truth + tool output: do not show the labeller
SHEET = OUT / "human_answer_sheet.csv"
MODEL_OUT = OUT / "model_labels.json"
CAUSES = ["uncapped_retry_loop", "context_bloat_self_calling_agent", "expensive_model_misroute"]
WINDOW = timedelta(minutes=5)
SEED_BASE = 800  # unused seed range: not used for tuning or any other report


def _pick() -> list[tuple[str, int]]:
    rng = random.Random("tier-e")
    pos = [c for c, s in gen.CASES.items() if s.kind != "negative"]
    neg = [c for c, s in gen.CASES.items() if s.kind == "negative"]
    picks = [(c, SEED_BASE + i) for i, c in enumerate(rng.sample(pos * 2, 28))]
    picks += [(c, SEED_BASE + 100 + i) for i, c in enumerate(rng.sample(neg * 2, 12))]
    rng.shuffle(picks)
    return picks


def _floor(ts):
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def _table(calls, t_inc) -> list[dict]:
    lo, hi = _floor(t_inc - timedelta(hours=3)), _floor(t_inc + timedelta(hours=1))
    by_id = {c.call_id: c for c in calls}
    agg: dict = defaultdict(lambda: {"calls": 0, "cost": 0.0, "tin": 0, "tout": 0, "models": Counter(), "retry": 0, "depth": 0})
    for c in calls:
        if not (lo <= c.timestamp < hi):
            continue
        a = agg[(_floor(c.timestamp), c.feature_tag)]
        a["calls"] += 1
        a["cost"] += c.cost_usd
        a["tin"] += c.input_tokens
        a["tout"] += c.output_tokens
        a["models"][c.model] += 1
        a["retry"] = max(a["retry"], c.retry_count)
        d, p, seen = 0, c.parent_call_id, set()
        while p and p in by_id and p not in seen:
            seen.add(p)
            d += 1
            p = by_id[p].parent_call_id
        a["depth"] = max(a["depth"], d)
    rows = []
    for (w, f), a in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        rows.append({
            "window_start_utc": w.strftime("%Y-%m-%d %H:%M"),
            "feature": f,
            "calls": a["calls"],
            "cost_usd": round(a["cost"], 5),
            "avg_input_tokens": round(a["tin"] / a["calls"]),
            "avg_output_tokens": round(a["tout"] / a["calls"]),
            "models": ";".join(f"{m}:{n}" for m, n in a["models"].most_common()),
            "max_retry_count": a["retry"],
            "max_parent_chain_depth": a["depth"],
        })
    return rows


def export() -> None:
    from llm_cost_investigator.investigate import investigate_calls

    SLICES.mkdir(parents=True, exist_ok=True)
    key = {}
    for i, (case, seed) in enumerate(_pick(), 1):
        calls, truth, t_inc = gen.make_case(case, seed)
        sid = f"S{i:02d}"
        rows = _table(calls, t_inc)
        with open(SLICES / f"{sid}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        with contextlib.redirect_stdout(io.StringIO()):
            r = investigate_calls(calls, provider="fallback", write_reports=False)
        tool = sorted({(x.root_cause.hypothesis, x.anomaly_window.feature_tag) for x in r.incidents
                       if x.root_cause.hypothesis != "no_strong_signal"})
        key[sid] = {"case": case, "seed": seed, "truth": [list(t) for t in truth], "tool": [list(t) for t in tool]}
    KEY.write_text(json.dumps(key, indent=1))
    with open(SHEET, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["slice", "cause (none / uncapped_retry_loop / context_bloat_self_calling_agent / expensive_model_misroute)", "feature", "notes"])
        for sid in key:
            w.writerow([sid, "", "", ""])
    (OUT / "README.md").write_text(BRIEF)
    print(f"exported {len(key)} slices to {SLICES}; key in {KEY}")


BRIEF = """# Tier E: label these slices blind

Each CSV in `slices/` is 4 hours of LLM API telemetry from one app with 5
features, as 5-minute aggregates per feature. Some slices contain an incident,
some do not. Do **not** open `answer_key.json`.

For each slice, write in `human_answer_sheet.csv` every incident you see as
(cause, feature), one row per finding (add rows for a second finding), or
`none`. Causes:

- `uncapped_retry_loop`: a feature retries failing calls many times (retry counts well above 2).
- `context_bloat_self_calling_agent`: a feature calls itself in a chain and resends a growing history (parent chain deeper than normal, prompts growing).
- `expensive_model_misroute`: a feature's traffic moves to a pricier model it did not use before (a cheaper model is not this).
- `none`: normal traffic, more traffic, A/B shifts, cheaper models, longer prompts without a chain.
"""


PROMPT = """You review LLM API telemetry for cost incidents. Below is a CSV of 5-minute
aggregates per feature (calls, cost, average tokens per call, models used with counts,
max retry_count, max parent-chain depth). Decide which incidents are present.

Causes:
- uncapped_retry_loop: a feature retries failing calls many times (retry counts well above 2).
- context_bloat_self_calling_agent: a feature calls itself in a chain and resends a growing history (chain depth deeper than that feature normally has, prompts growing).
- expensive_model_misroute: a feature's traffic moves to a pricier model it did not use before (a cheaper model does not count).
More traffic, A/B ratio shifts, cheaper models, or longer prompts without a chain are NOT incidents.

Return only JSON: {"findings": [{"cause": "<cause>", "feature": "<feature>"}]} with an empty list if nothing.

CSV:
"""


def model_labels() -> None:
    from llm_cost_investigator.llm_client import LLMClientConfig, resolve_llm_client

    c = resolve_llm_client(LLMClientConfig(provider="hf", model="openai/gpt-oss-20b"))
    if c.provider != "hf":
        raise SystemExit(f"HF not available: {c.fallback_reason}")
    out = {}
    for path in sorted(SLICES.glob("S*.csv")):
        text = path.read_text()
        raw = c.client(PROMPT + text)
        try:
            s = raw[raw.find("{"): raw.rfind("}") + 1]
            found = json.loads(s).get("findings", [])
            labels = sorted({(f["cause"], f["feature"]) for f in found if f.get("cause") in CAUSES})
        except Exception as exc:  # unparseable answer counts as "none" and is recorded
            labels, found = [], [{"parse_error": str(exc)[:100]}]
        out[path.stem] = [list(x) for x in labels]
        print(path.stem, labels, flush=True)
    MODEL_OUT.write_text(json.dumps(out, indent=1))


def _kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def _human() -> dict | None:
    if not SHEET.exists():
        return None
    got: dict = defaultdict(set)
    filled = False
    with open(SHEET) as fh:
        for row in csv.reader(fh):
            if not row or row[0] == "slice":
                continue
            sid, cause = row[0].strip(), (row[1] if len(row) > 1 else "").strip()
            if not cause:
                continue
            filled = True
            if cause != "none":
                got[sid].add((cause, (row[2] if len(row) > 2 else "").strip()))
            else:
                got.setdefault(sid, set())
    return {k: sorted(v) for k, v in got.items()} if filled else None


def score() -> None:
    key = json.loads(KEY.read_text())
    model = json.loads(MODEL_OUT.read_text()) if MODEL_OUT.exists() else None
    human = _human()
    sids = sorted(key)
    lab = lambda xs: "|".join(sorted(f"{c}@{f}" for c, f in xs)) or "none"
    arms = {"truth": [lab(key[s]["truth"]) for s in sids], "tool": [lab(key[s]["tool"]) for s in sids]}
    if model:
        arms["model"] = [lab(model.get(s, [])) for s in sids]
    fable_file = OUT / "fable_labels.json"
    if fable_file.exists():
        # A blind labeller run by a different, stronger model (Fable). It
        # wrote the held-out generator earlier, so it is independent of the
        # detection rules but not of how the incidents were designed.
        fable = json.loads(fable_file.read_text())
        arms["fable"] = [lab(fable.get(s, [])) for s in sids]
    if human:
        arms["human"] = [lab(human.get(s, [])) for s in sids]
    res = {"n": len(sids)}
    names = list(arms)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            agree = sum(x == y for x, y in zip(arms[a], arms[b]))
            res[f"{a}_vs_{b}"] = {"agree": agree, "rate": round(agree / len(sids), 3),
                                  "kappa": round(_kappa(arms[a], arms[b]), 3)}
    (OUT / "scores.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["export", "model", "score"])
    {"export": export, "model": model_labels, "score": score}[ap.parse_args().step]()
