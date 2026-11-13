"""Incident injector for Tier B (real BurstGPT background, synthetic incidents).

Magnitudes reuse benchmarks/heldout/SPEC.md ranges, sampled uniformly over the
whole small..large range (not only the easy end):
- retry_loop:    3-12 parents, 4-15 retries each (child rows, parent_call_id set, retry_count 1..N).
- context_bloat: 2-15 chains, depth 4-10, prompt growth 1.3x-1.8x per step, parent ids set.
- model_misroute: chosen feature switches to a pricier model it does not use in the slice,
  for 30-120 min; 50% or 100% of its traffic (SPEC "partial and full").

Placement: t_inc uniform in [start+48h, end-3h): the last 24 h of the slice, with a 3 h tail
so the incident can be seen (Tier A uses the same 3 h tail). Never in the first 2 h.
Feature: chosen by rng among features with >= 1 call per 10 min on average in the slice, and
live at t_inc (>= 3 calls in the 30 min before and after; redrawn up to 50 times otherwise).
Prompt sizes of injected rows come from real rows of that feature before t_inc.
"""
from __future__ import annotations

import math
import random
from datetime import timedelta

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall

KINDS = ("retry_loop", "context_bloat", "model_misroute")
CAUSE = {"retry_loop": "uncapped_retry_loop", "context_bloat": "context_bloat_self_calling_agent",
         "model_misroute": "expensive_model_misroute"}
MISROUTE_TARGETS = {"gpt-3.5-turbo": ["gpt-4o", "gpt-4-turbo"], "gpt-4-turbo": ["o1", "claude-3-opus"]}
MIN_PER_10MIN = 1.0
TAIL_H = 3.0
HEAD_H = 2.0


def eligible_features(calls: list[LLMCall], hours: float) -> list[str]:
    counts: dict[str, int] = {}
    for c in calls:
        counts[c.feature_tag] = counts.get(c.feature_tag, 0) + 1
    need = MIN_PER_10MIN * hours * 6
    return sorted(f for f, n in counts.items() if n >= need)


def _mk(cid, ts, feat, model, inp, out, parent=None, retry=0, latency=0) -> LLMCall:
    inp, out = int(max(1, min(inp, 150_000))), int(max(0, out))
    return LLMCall(timestamp=ts, call_id=cid, parent_call_id=parent, feature_tag=feat, model=model,
                   input_tokens=inp, output_tokens=out, cost_usd=estimate_cost_usd(model, inp, out)[0],
                   latency_ms=latency, retry_count=retry)


def inject(calls_slice: list[LLMCall], kind: str, rng: random.Random, hours: float = 72, start=None):
    """Returns (calls, truth, t_inc). truth = [(cause, feature)]. See inject_meta for details.

    start: nominal slice start (datetime). Defaults to the first row's timestamp.
    """
    calls, truth, t_inc, _ = inject_meta(calls_slice, kind, rng, hours, start)
    return calls, truth, t_inc


def inject_meta(calls_slice, kind, rng, hours: float = 72, start=None):
    if kind not in KINDS:
        raise ValueError(kind)
    if start is None:
        start = min(c.timestamp for c in calls_slice)
    lo = start + timedelta(hours=hours - 24)
    hi = start + timedelta(hours=hours - TAIL_H)
    lo = max(lo, start + timedelta(hours=HEAD_H))
    feats = eligible_features(calls_slice, hours)
    if not feats:
        raise ValueError("no feature with >= 1 call per 10 min in this slice")
    by_feat: dict[str, list] = {}
    for c in calls_slice:
        by_feat.setdefault(c.feature_tag, []).append(c.timestamp)
    # The chosen feature must be live at t_inc: >= 3 calls in the 30 min before and after.
    # (The trace has outage gaps; an incident on a silent feature would touch no rows.)
    for _ in range(50):
        t_inc = (lo + (hi - lo) * rng.random()).replace(microsecond=0)
        feat = rng.choice(feats)
        w = timedelta(minutes=30)
        tss = by_feat[feat]
        if sum(t_inc - w <= t < t_inc for t in tss) >= 3 and sum(t_inc <= t < t_inc + w for t in tss) >= 3:
            break
    else:
        raise ValueError("no live (t_inc, feature) found")
    hist = [c for c in calls_slice if c.feature_tag == feat and c.timestamp < t_inc and c.output_tokens > 0]
    model = hist[-1].model if hist else next(c.model for c in calls_slice if c.feature_tag == feat)
    meta = dict(kind=kind, feature=feat, eligible=feats, t_inc=t_inc.isoformat())
    new: list[LLMCall] = []
    n = 0

    def cid():
        nonlocal n
        n += 1
        return f"inj{n:05d}"

    if kind == "retry_loop":
        n_par, dur = rng.randint(3, 12), rng.uniform(20, 40) * 60
        meta.update(n_parents=n_par)
        for _ in range(n_par):
            ts = t_inc + timedelta(seconds=rng.uniform(0, dur))
            inp = rng.choice(hist).input_tokens
            root = cid()
            new.append(_mk(root, ts, feat, model, inp, rng.uniform(0, 30)))
            for k in range(1, rng.randint(4, 15) + 1):
                ts += timedelta(seconds=rng.uniform(1, 4) * min(k, 4))
                new.append(_mk(cid(), ts, feat, model, inp, rng.uniform(0, 30), parent=root, retry=k))
    elif kind == "context_bloat":
        n_ch, dur = rng.randint(2, 15), rng.uniform(20, 40) * 60
        meta.update(n_chains=n_ch)
        for _ in range(n_ch):
            ts = t_inc + timedelta(seconds=rng.uniform(0, dur))
            h = rng.choice(hist)
            inp, out = h.input_tokens, h.output_tokens
            parent = cid()
            new.append(_mk(parent, ts, feat, model, inp, out))
            g = rng.uniform(1.3, 1.8)
            for _ in range(rng.randint(4, 10)):
                ts += timedelta(seconds=rng.uniform(3, 10))
                inp = min(inp * g + out, 150_000)
                out = rng.choice(hist).output_tokens
                c = cid()
                new.append(_mk(c, ts, feat, model, inp, out, parent=parent))
                parent = c
    else:
        target = rng.choice(MISROUTE_TARGETS[model])
        frac = rng.choice([0.5, 1.0])
        end = t_inc + timedelta(minutes=rng.uniform(30, 120))
        meta.update(from_model=model, to_model=target, fraction=frac, end=end.isoformat())
        out_calls = []
        before = after = 0.0
        for c in calls_slice:
            if c.feature_tag == feat and t_inc <= c.timestamp < end and rng.random() < frac:
                nc = c.model_copy(update={"model": target,
                                          "cost_usd": estimate_cost_usd(target, c.input_tokens, c.output_tokens)[0]})
                before += c.cost_usd
                after += nc.cost_usd
                out_calls.append(nc)
            else:
                out_calls.append(c)
        meta.update(cost_ratio=(after / before) if before else None)
        return out_calls, [(CAUSE[kind], feat)], t_inc, meta
    meta.update(n_injected=len(new))
    out = sorted(list(calls_slice) + new, key=lambda c: (c.timestamp, c.call_id))
    return out, [(CAUSE[kind], feat)], t_inc, meta
