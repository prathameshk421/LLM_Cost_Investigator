"""Tier C red-team cases. Each builder: (seed) -> (calls, truth, t_inc, note).

truth is a list of (cause, feature); [] means no bug. Cases flagged ambiguous
(see AMBIGUOUS) are scored separately. Backgrounds come from the held-out
generator (benchmarks/heldout/gen.py). Dev seeds 300-399 only while building.
"""
from __future__ import annotations

import random
import zlib
from datetime import timedelta

from benchmarks.heldout import gen
from benchmarks.heldout.gen import BLOAT, MISROUTE, RETRY, Feature
from llm_cost_investigator.schemas import LLMCall


def _world(name: str, seed: int, tod: str = "day"):
    return gen.new_world(zlib.crc32(("rt_" + name).encode()) * 1000 + seed, tod)


def _done(w, truth, note):
    return gen.finish(w), truth, w.t_inc, note


def _pick_model_after(w, feat, target, start, end=None, share=lambda ts: 1.0):
    base = w.feats[feat].models
    prev = w.model_mix

    def mix(f, ts):
        if f == feat and ts >= start and (end is None or ts < end):
            s = share(ts)
            out = {m: p * (1 - s) for m, p in base.items()}
            out[target] = out.get(target, 0) + s
            return out
        return prev(f, ts)
    w.model_mix = mix


def c01_slow_ramp(seed):
    w = _world("c01", seed)
    feat = w.rng.choice(gen.MIS_FEATS)
    w.t_end = w.t_inc + timedelta(hours=7)
    target = gen.MISROUTE_TARGETS[feat]["5x"]
    ramp = timedelta(hours=6)
    _pick_model_after(w, feat, target, w.t_inc,
                      share=lambda ts: min(1.0, 0.1 + 0.9 * (ts - w.t_inc) / ramp))
    w.background()
    return _done(w, [(MISROUTE, feat)], "pricier model share 10% to 100% over 6 h")


def c02_first_hour(seed):
    w = _world("c02", seed)
    name = "support_triage"
    w.feats[name] = Feature(name, 2.0, {"gpt-4o-mini": 1.0}, 1000, 0.5, 200, 0.5)
    start = w.t_inc - timedelta(minutes=30)
    prev = w.rate_mult
    w.rate_mult = lambda f, ts: (0.0 if ts < start else 1.0) * prev(f, ts) if f == name else prev(f, ts)
    _pick_model_after(w, name, "claude-sonnet-4", w.t_inc, w.t_inc + timedelta(minutes=90))
    w.background()
    return _done(w, [(MISROUTE, name)], "feature has only 30 min of history at the incident")


def c03_retry_new_ids(seed):
    w = _world("c03", seed)
    feat = w.rng.choice(gen.POS_FEATS)
    rng = w.rng
    for _ in range(rng.randint(6, 10)):
        ts = w.t_inc + timedelta(seconds=rng.uniform(0, 1800))
        model = w.pick_model(w.feats[feat], ts)
        inp, _o = w.sizes(w.feats[feat], ts)
        for _k in range(rng.randint(6, 10)):
            ts += timedelta(seconds=rng.uniform(1, 6))
            w.add(ts, feat, model, inp * rng.uniform(0.99, 1.01), rng.uniform(0, 30),
                  latency=rng.uniform(8000, 30000))
    w.background()
    return _done(w, [(RETRY, feat)], "retries are fresh call_ids: no retry_count, no parent")


def c04_bloat_no_parent(seed):
    w = _world("c04", seed)
    feat = w.rng.choice(gen.POS_FEATS)
    rng, f = w.rng, w.feats[feat]
    for _ in range(rng.randint(6, 10)):
        ts = w.t_inc + timedelta(seconds=rng.uniform(0, 1800))
        model = w.pick_model(f, ts)
        inp, out = w.sizes(f, ts)
        g = rng.uniform(1.4, 1.7)
        for _k in range(rng.randint(5, 8)):
            ts += timedelta(seconds=rng.uniform(3, 10))
            inp = min(inp * g + out, 150_000)
            w.add(ts, feat, model, inp, out)
    w.background()
    return _done(w, [(BLOAT, feat)], "growing prompts, no parent_call_id")


def c05_unknown_model(seed):
    w = _world("c05", seed)
    feat = w.rng.choice(gen.MIS_FEATS)
    unk = "acme-ultra-1"
    _pick_model_after(w, feat, unk, w.t_inc, w.t_inc + timedelta(minutes=90))
    w.background()
    fixed = []
    for c in w.calls:
        if c.model == unk:   # source-given cost, 8x gpt-4o list price, not in the table
            c = c.model_copy(update={"cost_usd": (c.input_tokens * 20.0 + c.output_tokens * 80.0) / 1e6})
        fixed.append(c)
    w.calls = fixed
    return _done(w, [(MISROUTE, feat)], "pricier model absent from the price table; costs come from the source")


def c06_three_causes(seed):
    w = _world("c06", seed)
    gen.set_misroute(w, "chat_assistant", "5x")
    w.background()
    gen.inject_retry_storm(w, "code_review", "medium")
    gen.inject_bloat(w, "doc_summarizer", "medium")
    return _done(w, [(MISROUTE, "chat_assistant"), (RETRY, "code_review"), (BLOAT, "doc_summarizer")],
                 "three causes on three features")


def c07_dupes_shuffle(seed):
    w = _world("c07", seed)
    feat = w.rng.choice(gen.MIS_FEATS)
    gen.set_misroute(w, feat, "5x")
    w.background()
    rng = random.Random(seed)
    calls = []
    for c in w.calls:
        c = c.model_copy(update={"timestamp": c.timestamp + timedelta(seconds=rng.uniform(-120, 120))})
        calls.append(c)
        if rng.random() < 0.10:
            calls.append(c.model_copy())   # same call_id twice
    w.calls = calls
    rng.shuffle(w.calls)
    return gen.finish(w), [(MISROUTE, feat)], w.t_inc, "10% duplicated call_ids, timestamps shuffled within 2 min"


def c08_new_feature(seed):
    w = _world("c08", seed)
    name = "new_feature_x"
    w.feats[name] = Feature(name, 1.5, {"gpt-4o-mini": 1.0}, 1500, 0.6, 300, 0.5)
    prev = w.rate_mult
    w.rate_mult = lambda f, ts: (0.0 if ts < w.t_inc else 1.0) * prev(f, ts) if f == name else prev(f, ts)
    w.background()
    return _done(w, [], "brand-new feature_tag with normal behaviour")


def c09_planned_upgrade(seed):
    w = _world("c09", seed)
    feat = w.rng.choice(gen.MIS_FEATS)
    target = gen.MISROUTE_TARGETS[feat]["5x"]
    w.model_mix = lambda f, ts: {target: 1.0} if (f == feat and ts >= w.t_inc) else None
    w.background()
    return _done(w, [], "planned upgrade to a pricier model: identical to a misroute in the data")


def c10_cache_miss(seed):
    w = _world("c10", seed)
    feat = w.rng.choice(["chat_assistant", "doc_summarizer", "code_review"])
    w.background()
    w.calls = [c.model_copy(update={"cost_usd": c.cost_usd * 3.0})
               if (c.feature_tag == feat and c.timestamp >= w.t_inc) else c for c in w.calls]
    return _done(w, [], "same model and tokens, cost x3 (caching stopped). No taxonomy cause fits, so truth is none")


def c11_surge_retry(seed):
    w = _world("c11", seed)
    feat = w.rng.choice(gen.POS_FEATS)
    gen.set_surge(w, feat, 10, 10, 30, 90)
    w.background()
    gen.inject_retry_storm(w, feat, "small")
    return _done(w, [(RETRY, feat)], "10x traffic surge plus a small retry storm on the same feature")


def c12_hourly(seed):
    w = _world("c12", seed)
    name = "weekly_report"
    w.feats[name] = Feature(name, 0.0, {"gpt-4o-mini": 1.0}, 3000, 0.5, 500, 0.4)
    w.background()
    rng = w.rng
    ts = w.t0
    while ts < w.t_end:
        t = ts + timedelta(minutes=rng.uniform(0, 60))
        model = "gpt-4o" if t >= w.t_inc else "gpt-4o-mini"
        inp, out = w.sizes(w.feats[name], t)
        w.add(t, name, model, inp, out)
        ts += timedelta(hours=1)
    return _done(w, [(MISROUTE, name)], "feature with 1 call per hour, real misroute")


CASES = {
    "c01_slow_ramp": c01_slow_ramp, "c02_first_hour": c02_first_hour,
    "c03_retry_new_ids": c03_retry_new_ids, "c04_bloat_no_parent": c04_bloat_no_parent,
    "c05_unknown_model": c05_unknown_model, "c06_three_causes": c06_three_causes,
    "c07_dupes_shuffle": c07_dupes_shuffle, "c08_new_feature": c08_new_feature,
    "c09_planned_upgrade": c09_planned_upgrade, "c10_cache_miss": c10_cache_miss,
    "c11_surge_retry": c11_surge_retry, "c12_hourly": c12_hourly,
}
AMBIGUOUS = {"c09_planned_upgrade"}


def make_case(name: str, seed: int):
    return CASES[name](seed)
