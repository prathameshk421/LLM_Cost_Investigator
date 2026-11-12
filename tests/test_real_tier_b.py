import os
import random
from datetime import timedelta
from pathlib import Path

import pytest

from benchmarks.real import burstgpt_adapter as bg
from benchmarks.real.inject import CAUSE, KINDS, MISROUTE_TARGETS, eligible_features, inject_meta
from benchmarks.heldout.stats import wilson


def _synthetic_slice(hours=72, per_10min=2):
    """Two features, dense enough to be eligible; a third too sparse."""
    calls = []
    n = 0
    for step in range(hours * 6):
        ts = bg.EPOCH + timedelta(minutes=10 * step)
        for j in range(per_10min):
            n += 1
            calls.append(bg.row_to_call(n, int(10 * 60 * step + j), "ChatGPT", 500 + j, 100, "API log"))
            calls.append(bg.row_to_call(n + 10**6, int(10 * 60 * step + j), "GPT-4", 900, 200, "Conversation log"))
        if step % 50 == 0:
            calls.append(bg.row_to_call(n + 2 * 10**6, int(600 * step), "GPT-4", 100, 0, "API log"))
    calls.sort(key=lambda c: (c.timestamp, c.call_id))
    return calls


def test_adapter_mapping(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("Timestamp,Model,Request tokens,Response tokens,Total tokens,Log Type\n"
                        "5,ChatGPT,472,18,490,Conversation log\n"
                        "118,GPT-4,417,0,417,API log\n")
    cols = bg.load_columns(csv_path)
    calls = bg.columns_to_calls(cols, 0, 2)
    a, b = calls
    assert a.call_id == "bg0" and a.feature_tag == "conv_chatgpt" and a.model == "gpt-3.5-turbo"
    assert a.timestamp == bg.EPOCH + timedelta(seconds=5)
    assert (a.input_tokens, a.output_tokens, a.latency_ms, a.retry_count, a.parent_call_id) == (472, 18, 0, 0, None)
    assert a.cost_usd > 0
    assert b.call_id == "bg1" and b.feature_tag == "api_gpt4" and b.model == "gpt-4-turbo"
    assert b.output_tokens == 0 and b.cost_usd > 0     # failed request kept
    assert len(bg.slice_columns(cols, 100, hours=1)) == 1
    assert len(bg.slice_trace(calls, 0, hours=1)) == 2
    assert Path(str(csv_path).replace(".csv", ".cols.pkl")).exists()


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("kind", KINDS)
def test_injector_truth_and_placement(kind, seed):
    base = _synthetic_slice()
    start = bg.EPOCH
    feats = eligible_features(base, 72)
    assert "api_chatgpt" in feats and "conv_gpt4" in feats and "api_gpt4" not in feats
    calls, truth, t_inc, meta = inject_meta(base, kind, random.Random(seed), 72, start)
    assert truth == [(CAUSE[kind], meta["feature"])] and meta["feature"] in feats
    assert start + timedelta(hours=48) <= t_inc < start + timedelta(hours=69)
    assert t_inc >= start + timedelta(hours=2)
    feat = meta["feature"]
    new = [c for c in calls if c.call_id.startswith("inj")]
    ts = [c.timestamp for c in calls]
    assert ts == sorted(ts)
    if kind == "retry_loop":
        kids = [c for c in new if c.parent_call_id]
        assert 3 <= meta["n_parents"] <= 12
        assert all(c.retry_count >= 1 and c.feature_tag == feat and c.timestamp >= t_inc for c in kids)
        assert max(c.retry_count for c in kids) >= 4
        assert {c.parent_call_id for c in kids} <= {c.call_id for c in new}
    elif kind == "context_bloat":
        ids = {c.call_id: c for c in new}
        assert all(c.parent_call_id in ids for c in new if c.parent_call_id)
        def depth(c):
            d = 0
            while c.parent_call_id:
                c, d = ids[c.parent_call_id], d + 1
            return d
        assert max(depth(c) for c in new) >= 4
        deepest = max(new, key=depth)
        assert deepest.input_tokens > ids[min(ids)].input_tokens
    else:
        assert not new and len(calls) == len(base)
        assert meta["to_model"] in MISROUTE_TARGETS[meta["from_model"]]
        orig = {c.call_id: c.model for c in base}
        changed = [c for c in calls if c.model != orig[c.call_id]]
        assert changed and all(c.feature_tag == feat and t_inc <= c.timestamp for c in changed)
        assert meta["cost_ratio"] > 1.0


def test_wilson_sane():
    r, lo, hi = wilson(95, 100)
    assert lo < r < hi


def test_end_to_end_dev_slices():
    from benchmarks.real import tier_b
    if not Path(tier_b.CSV).exists():
        pytest.skip("BurstGPT csv not available")
    import time
    t0 = time.time()
    rows = [tier_b.run_injected(s) for s in (300, 301, 302)]
    assert {r["kind"] for r in rows} == set(KINDS)
    for r in rows:
        assert r["truth"] and r["score"]["positive"] and r["n_calls"] >= r["n_base"]
    assert time.time() - t0 < 120
