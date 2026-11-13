"""Realistic demo history for the warehouse.

``seed_history`` writes about 50k calls for 30 days: 8 features on 5 models,
a busy-hours curve, lower weekends, a mild growth trend and noise. It injects
three incidents on different days: a model misroute, a retry storm and a
context-bloat chain burst. Output is deterministic for a given ``seed`` and
``end``. It is plain Python (no numpy) and builds all rows before one insert.
"""

from __future__ import annotations

import csv
import math
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall
from llm_cost_investigator.warehouse import Warehouse

__all__ = ["seed_history", "FEATURES"]

# (feature_tag, model, calls per hour at average load, mean input, mean output, base latency ms)
FEATURES: list[tuple[str, str, float, int, int, int]] = [
    ("chat_assistant", "gpt-4o", 14.0, 1200, 400, 900),
    ("summarizer", "gpt-4o-mini", 13.0, 1800, 300, 600),
    ("support_reply", "gpt-4o", 11.0, 900, 250, 800),
    ("classifier", "gpt-4.1-mini", 11.0, 300, 12, 250),
    ("search_rerank", "claude-3-5-haiku", 8.0, 600, 40, 350),
    ("doc_extract", "gemini-2.5-flash", 4.0, 2500, 500, 1100),
    ("code_review", "claude-3-5-sonnet", 3.0, 3000, 700, 2200),
    ("agent_reflection", "gpt-4o-mini", 4.0, 700, 150, 500),
]

MISROUTE_MODEL = "gpt-4.1"
MISROUTE_FEATURE = "summarizer"


def _floor(ts: datetime, unit: str) -> datetime:
    ts = ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0) if unit == "day" else ts


def _hour_factor(hour: int) -> float:
    # Busy around 14:00 UTC, quiet at night; normalised to mean ~1.
    return (0.3 + 1.4 * math.exp(-(((hour - 14) / 5.0) ** 2))) / 0.82


def seed_history(
    w: Warehouse,
    days: int = 30,
    seed: int = 1,
    incidents: bool = True,
    reset: bool = True,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Insert history ending at ``end`` (default: now, UTC). Returns inserted/start/end."""
    rng = random.Random(seed)
    if end is None:
        end = datetime.now(timezone.utc)
    end = end.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0) if end.tzinfo else end.replace(microsecond=0)
    start = end - timedelta(days=days)

    # Incident windows, spread over the history on different days.
    def anchor(frac: float) -> datetime:
        k = max(1, round(days * frac))
        return _floor(end, "day") - timedelta(days=k)

    mis_start = anchor(0.7) + timedelta(hours=9)
    mis_end = mis_start + timedelta(hours=6)
    storm_start = anchor(0.43) + timedelta(hours=11)
    bloat_start = anchor(0.2) + timedelta(hours=13)

    calls: list[LLMCall] = []
    counter = 0

    def make(ts, feature, model, tin, tout, lat, parent=None, retries=0, cost_mult=1.0) -> str:
        nonlocal counter
        counter += 1
        cid = f"seed{seed}-{counter:07d}"
        cost, _ = estimate_cost_usd(model, tin, tout)
        calls.append(
            LLMCall.model_construct(
                timestamp=ts,
                call_id=cid,
                parent_call_id=parent,
                feature_tag=feature,
                model=model,
                input_tokens=tin,
                output_tokens=tout,
                cost_usd=round(cost * cost_mult, 8),
                latency_ms=int(lat),
                retry_count=retries,
                scenario_label=None,
            )
        )
        return cid

    total_secs = max((end - start).total_seconds(), 1.0)
    hour = _floor(start, "hour")
    while hour < end:
        span_lo = max(hour, start)
        span_hi = min(hour + timedelta(hours=1), end)
        secs = (span_hi - span_lo).total_seconds()
        if secs <= 0:
            hour += timedelta(hours=1)
            continue
        frac = secs / 3600.0
        weekly = 0.55 if hour.weekday() >= 5 else 1.0
        trend = 1.0 + 0.25 * ((hour - start).total_seconds() / total_secs)
        load = _hour_factor(hour.hour) * weekly * trend
        misrouted = incidents and mis_start <= hour < mis_end
        for feature, model, rate, mean_in, mean_out, base_lat in FEATURES:
            lam = rate * load * frac
            n = max(0, round(lam + math.sqrt(lam) * rng.gauss(0, 1)))
            use_model = MISROUTE_MODEL if (misrouted and feature == MISROUTE_FEATURE) else model
            for _ in range(n):
                tin = max(1, int(mean_in * rng.lognormvariate(0, 0.35)))
                tout = max(1, int(mean_out * rng.lognormvariate(0, 0.35)))
                lat = (base_lat + tout * 4.0) * rng.lognormvariate(0, 0.25)
                ts = span_lo + timedelta(seconds=rng.random() * secs)
                make(ts, feature, use_model, tin, tout, lat)
        hour += timedelta(hours=1)

    if incidents:
        # Retry storm: support_reply parents with 6-12 retries for one hour.
        # Each parent record carries the cost of all attempts.
        if start <= storm_start and storm_start + timedelta(hours=1) <= end:
            for _ in range(40):
                r = rng.randint(6, 12)
                tin, tout = int(900 * rng.lognormvariate(0, 0.2)), int(250 * rng.lognormvariate(0, 0.2))
                ts = storm_start + timedelta(seconds=rng.random() * 3600)
                make(ts, "support_reply", "gpt-4o", tin, tout, (800 + tout * 4) * (1 + 0.6 * r), retries=r, cost_mult=r + 1)

        # Context bloat: agent_reflection self-calling chains, input grows per hop.
        if start <= bloat_start and bloat_start + timedelta(hours=2) <= end:
            for k in range(30):
                ts = bloat_start + timedelta(minutes=4 * k, seconds=rng.randint(0, 60))
                parent = None
                tin = int(1500 * rng.lognormvariate(0, 0.1))
                for _hop in range(rng.randint(6, 10)):
                    tout = int(300 * rng.lognormvariate(0, 0.15))
                    parent = make(ts, "agent_reflection", "gpt-4o-mini", tin, tout, 700 + tin * 0.3, parent=parent)
                    tin = int(tin * 1.45)
                    ts += timedelta(seconds=rng.randint(10, 30))

    inserted = _bulk_insert(w, calls, reset)
    return {"inserted": inserted, "start": start, "end": end, "misroute_start": mis_start if incidents else None}


_COLS = "call_id, parent_call_id, ts, feature_tag, model, input_tokens, output_tokens, cost_usd, latency_ms, retry_count, source"


def _bulk_insert(w: Warehouse, calls: list[LLMCall], reset: bool) -> int:
    """Load rows through a CSV COPY.

    ``Warehouse.insert_calls`` uses ``executemany`` and takes about 12 s for 48k
    rows, so the seed uses COPY (about 0.3 s). Duplicate call_ids are skipped.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "seed.csv")
        with open(path, "w", newline="") as fh:
            wr = csv.writer(fh)
            for c in calls:
                wr.writerow(
                    [c.call_id, c.parent_call_id or "", c.timestamp.isoformat(sep=" "), c.feature_tag, c.model,
                     c.input_tokens, c.output_tokens, repr(c.cost_usd), c.latency_ms, c.retry_count, "seed"]
                )
        with w._write_lock:
            cur = w.cursor()
            if reset:
                cur.execute("DELETE FROM llm_calls")
            before = cur.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
            cur.execute(
                "CREATE OR REPLACE TEMP TABLE _seed_stage (call_id VARCHAR, parent_call_id VARCHAR, ts TIMESTAMP, "
                "feature_tag VARCHAR, model VARCHAR, input_tokens INTEGER, output_tokens INTEGER, cost_usd DOUBLE, "
                "latency_ms INTEGER, retry_count INTEGER, source VARCHAR)"
            )
            cur.execute(f"COPY _seed_stage FROM '{path}' (FORMAT csv, HEADER false, NULLSTR '__none__')")
            cur.execute(
                f"INSERT INTO llm_calls ({_COLS}) SELECT call_id, nullif(parent_call_id, ''), ts, feature_tag, model, "
                "input_tokens, output_tokens, cost_usd, latency_ms, retry_count, source FROM _seed_stage "
                "ON CONFLICT (call_id) DO NOTHING"
            )
            cur.execute("DROP TABLE _seed_stage")
            after = cur.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
    return int(after - before)
