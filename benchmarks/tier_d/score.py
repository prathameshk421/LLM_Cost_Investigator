"""Score a Tier D take against the pre-registered rule.

Rule (benchmarks/PREREGISTRATION.md, Tier D): each induced bug is labelled
correctly within 2 windows of its start, and there are 0 alarms during the
baseline. "Within 2 windows": the first correct incident window starts at or
after t_bug - 5 min and before t_bug + 10 min.

Inputs: the app log (its first line is event time 0) and the live incidents
JSON from GET /api/incidents/live. Optional: the warehouse file, opened read
only after the API has stopped, for call counts and spend.

    python -m benchmarks.tier_d.score --app-log app.log --incidents incidents.json \
        --bugs retry_loop,misroute,bloat --baseline-minutes 75 --minutes 20 --gap-minutes 10
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

CAUSE = {"retry_loop": ("uncapped_retry_loop", "classify"),
         "misroute": ("expensive_model_misroute", "summarize"),
         "bloat": ("context_bloat_self_calling_agent", "reflect")}
NONE = "no_strong_signal"


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def app_start(app_log: str) -> datetime:
    """Event time of the app's first call; the log prints HH:MM:SS only."""
    with open(app_log) as fh:
        first = fh.readline().split()[0]
    return first


def score(incidents: list[dict], t0: datetime, bugs: list[str], baseline_min: float,
          minutes: float, gap: float) -> dict:
    inc = [dict(i, _s=_ts(i.get("start") or i.get("start_ts")),
                _e=_ts(i.get("end") or i.get("end_ts"))) for i in incidents]
    t_first_bug = t0 + timedelta(minutes=baseline_min)
    real = [i for i in inc if i["hypothesis"] != NONE]
    baseline_alarms = [i for i in real if i["_s"] < t_first_bug - timedelta(minutes=5)]
    out, all_pass = {"t0": t0.isoformat(), "bugs": []}, True
    for k, bug in enumerate(bugs):
        t_bug = t_first_bug + timedelta(minutes=k * (minutes + gap))
        cause, feature = CAUSE[bug]
        hits = sorted((i for i in real if i["hypothesis"] == cause and i["feature_tag"] == feature
                       and i["_s"] >= t_bug - timedelta(minutes=5)), key=lambda i: i["_s"])
        first = hits[0] if hits else None
        ok = bool(first and first["_s"] < t_bug + timedelta(minutes=10))
        all_pass &= ok
        out["bugs"].append({
            "bug": bug, "t_bug": t_bug.isoformat(), "expected": [cause, feature],
            "first_correct_window_start": first["_s"].isoformat() if first else None,
            "windows_after_start": round((first["_s"] - t_bug).total_seconds() / 300, 2) if first else None,
            "confidence": first.get("confidence") if first else None,
            "pass": ok,
        })
    wrong = [i for i in real if i not in baseline_alarms and not any(
        i["hypothesis"] == CAUSE[b][0] and i["feature_tag"] == CAUSE[b][1] for b in bugs)]
    out["baseline_alarms"] = [{k: v for k, v in i.items() if not k.startswith("_")} for i in baseline_alarms]
    out["wrong_label_incidents"] = [{k: v for k, v in i.items() if not k.startswith("_")} for i in wrong]
    out["pass"] = all_pass and not baseline_alarms
    return out


def warehouse_summary(path: str) -> list[dict]:
    import duckdb

    con = duckdb.connect(path, read_only=True)
    cols = [c[0] for c in con.execute("DESCRIBE calls").fetchall()]
    cost = "cost_usd" if "cost_usd" in cols else "0"
    rows = con.execute(f"""SELECT model, count(*), sum(input_tokens), sum(output_tokens), round(sum({cost}), 5)
                           FROM calls GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    return [dict(zip(["model", "calls", "input_tokens", "output_tokens", "cost_usd"], r)) for r in rows]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--app-log", required=True)
    p.add_argument("--date", required=True, help="UTC date of the app's first call, YYYY-MM-DD")
    p.add_argument("--incidents", required=True)
    p.add_argument("--bugs", default="retry_loop,misroute,bloat")
    p.add_argument("--baseline-minutes", type=float, default=75)
    p.add_argument("--minutes", type=float, default=20)
    p.add_argument("--gap-minutes", type=float, default=10)
    p.add_argument("--warehouse")
    p.add_argument("--out")
    a = p.parse_args(argv)
    t0 = _ts(f"{a.date}T{app_start(a.app_log)}")
    with open(a.incidents) as fh:
        incidents = json.load(fh)
    res = score(incidents, t0, [b for b in a.bugs.split(",") if b], a.baseline_minutes, a.minutes, a.gap_minutes)
    if a.warehouse:
        res["calls_by_model"] = warehouse_summary(a.warehouse)
    text = json.dumps(res, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(text)
    print(text)
    return res


if __name__ == "__main__":
    main()
