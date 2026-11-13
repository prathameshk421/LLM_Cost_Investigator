"""BurstGPT adapter (Tier B background traffic).

Source: BurstGPT_1.csv, real Azure OpenAI requests, CC-BY-4.0. Header:
    Timestamp,Model,Request tokens,Response tokens,Total tokens,Log Type

Mapping (documented limits):
- timestamp  = EPOCH (2023-01-01T00:00Z) + Timestamp seconds. Whole seconds only.
- call_id    = f"bg{row_index}" (row index in the CSV, 0 based).
- feature_tag= "{conv|api}_{chatgpt|gpt4}" from Log Type and Model.
- model      = "gpt-3.5-turbo" for ChatGPT, "gpt-4-turbo" for GPT-4. The trace does not
               say the exact model version. Prices come from the static table, so cost
               is an estimate, not an invoice.
- latency_ms = 0 (not in the trace), retry_count = 0, parent_call_id = None
  (the trace has no latency, parent id or retry field).
- Rows with Response tokens == 0 are failed requests. They are kept.
"""
from __future__ import annotations

import csv
import pickle
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall

EPOCH = datetime(2023, 1, 1, tzinfo=timezone.utc)
MODEL_MAP = {"ChatGPT": ("chatgpt", "gpt-3.5-turbo"), "GPT-4": ("gpt4", "gpt-4-turbo")}
LOG_MAP = {"Conversation log": "conv", "API log": "api"}

_cost_cache: dict[tuple, float] = {}


def _cost(model: str, i: int, o: int) -> float:
    k = (model, i, o)
    v = _cost_cache.get(k)
    if v is None:
        v = _cost_cache[k] = estimate_cost_usd(model, i, o)[0]
    return v


def row_to_call(idx: int, ts: int, model: str, req: int, resp: int, log_type: str) -> LLMCall:
    short, priced = MODEL_MAP[model]
    return LLMCall(
        timestamp=EPOCH + timedelta(seconds=int(ts)),
        call_id=f"bg{idx}",
        parent_call_id=None,
        feature_tag=f"{LOG_MAP[log_type]}_{short}",
        model=priced,
        input_tokens=int(req),
        output_tokens=int(resp),
        cost_usd=_cost(priced, int(req), int(resp)),
        latency_ms=0,
        retry_count=0,
    )


def load_columns(path: str | Path) -> dict:
    """Fast columnar load, cached as a pickle next to the CSV."""
    path = Path(path)
    cache = path.with_suffix(".cols.pkl")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        with open(cache, "rb") as f:
            return pickle.load(f)
    cols = {"ts": [], "model": [], "req": [], "resp": [], "log": []}
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        assert header[0] == "Timestamp" and header[-1] == "Log Type", header
        for r in rd:
            cols["ts"].append(int(float(r[0])))
            cols["model"].append(r[1])
            cols["req"].append(int(r[2]))
            cols["resp"].append(int(r[3]))
            cols["log"].append(r[5])
    order = sorted(range(len(cols["ts"])), key=cols["ts"].__getitem__)   # stable
    if order != list(range(len(order))):
        raise ValueError("trace is not sorted by Timestamp; call_id would not match row order")
    tmp = cache.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(cols, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(cache)
    return cols


def columns_to_calls(cols: dict, lo: int, hi: int) -> list[LLMCall]:
    return [row_to_call(i, cols["ts"][i], cols["model"][i], cols["req"][i], cols["resp"][i], cols["log"][i])
            for i in range(lo, hi)]


def load_trace(path: str | Path) -> list[LLMCall]:
    """All rows as LLMCall (1.4M objects: slow and large; prefer load_columns + slice_columns)."""
    cols = load_columns(path)
    return columns_to_calls(cols, 0, len(cols["ts"]))


def slice_columns(cols: dict, start_s: int, hours: float = 72) -> list[LLMCall]:
    """Rows with start_s <= Timestamp < start_s + hours, as LLMCall."""
    ts = cols["ts"]
    lo = bisect_left(ts, start_s)
    hi = bisect_left(ts, start_s + int(hours * 3600))
    return columns_to_calls(cols, lo, hi)


def slice_trace(calls: list[LLMCall], start, hours: float = 72) -> list[LLMCall]:
    """Slice an LLMCall list. start: datetime or seconds from trace start."""
    if not isinstance(start, datetime):
        start = EPOCH + timedelta(seconds=float(start))
    end = start + timedelta(hours=hours)
    return [c for c in calls if start <= c.timestamp < end]


def trace_span_s(cols: dict) -> int:
    return cols["ts"][-1] - cols["ts"][0] if cols["ts"] else 0
