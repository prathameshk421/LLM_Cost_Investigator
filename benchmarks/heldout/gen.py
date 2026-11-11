"""Held-out telemetry generator. Design source: blind/SPEC.md (no pipeline thresholds used).

API:
    CASES                      -> dict[name, CaseSpec]
    make_case(name, seed)      -> (list[LLMCall], truth)
    truth = list of (hypothesis, feature_tag); empty list means "none".
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall

RETRY = "uncapped_retry_loop"
BLOAT = "context_bloat_self_calling_agent"
MISROUTE = "expensive_model_misroute"

HISTORY_H = int(__import__("os").environ.get("GEN_HISTORY_H", "50"))   # hours of data before the incident time
TAIL_H = 3              # hours after the incident time


@dataclass
class Feature:
    name: str
    peak_per_min: float
    models: dict[str, float]            # baseline A/B mix
    in_median: float
    in_sigma: float
    out_median: float
    out_sigma: float
    pipeline: bool = False              # RAG: 2-3 step chain per request


def features() -> dict[str, Feature]:
    return {
        f.name: f
        for f in [
            Feature("chat_assistant", 3.0, {"gpt-4o-mini": 0.85, "gpt-4.1-mini": 0.15}, 1200, 0.6, 250, 0.5),
            Feature("rag_search", 1.2, {"gemini-2.5-flash": 1.0}, 2500, 0.5, 300, 0.5, pipeline=True),
            Feature("doc_summarizer", 0.8, {"claude-3-5-haiku": 1.0}, 6000, 0.9, 500, 0.4),
            Feature("code_review", 0.3, {"gpt-4.1": 0.7, "claude-sonnet-4": 0.3}, 4000, 0.8, 700, 0.6),
            Feature("nightly_digest", 0.1, {"gpt-4o-mini": 1.0}, 3000, 1.0, 400, 0.5),
        ]
    }


# feature -> {ratio label: pricier model that is NOT in the baseline mix}
MISROUTE_TARGETS = {
    "chat_assistant": {"2x": "gpt-5-mini", "5x": "claude-3-5-haiku", "15x": "gpt-4o"},
    "doc_summarizer": {"2x": "gpt-5", "5x": "claude-sonnet-4", "15x": "claude-opus-4"},
}
CHEAPER = {"chat_assistant": "gpt-5-nano", "doc_summarizer": "gpt-4o-mini", "code_review": "gpt-4.1-mini"}
AB_SHIFT = {
    "chat_assistant": {"gpt-4o-mini": 0.5, "gpt-4.1-mini": 0.5},
    "code_review": {"gpt-4.1": 0.3, "claude-sonnet-4": 0.7},
}


def diurnal(ts: datetime) -> float:
    h = ts.hour + ts.minute / 60
    d = 0.15 + 0.85 * (0.5 + 0.5 * math.cos(2 * math.pi * (h - 14) / 24)) ** 1.5
    return d * (0.55 if ts.weekday() >= 5 else 1.0)


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


@dataclass
class World:
    rng: random.Random
    t0: datetime
    t_inc: datetime
    t_end: datetime
    feats: dict[str, Feature]
    calls: list[LLMCall] = field(default_factory=list)
    n: int = 0
    # hooks: all take (feature_name, ts) and are set by scenarios
    rate_mult: callable = lambda f, ts: 1.0
    model_mix: callable = lambda f, ts: None
    prompt_mult: callable = lambda f, ts: 1.0
    big_prompt_p: callable = lambda f, ts: 0.0

    def cid(self) -> str:
        self.n += 1
        return f"c{self.n:07d}"

    def add(self, ts, feature, model, inp, out, parent=None, retry=0, latency=None) -> str:
        inp, out = int(max(1, inp)), int(max(0, out))
        cost, _ = estimate_cost_usd(model, inp, out)
        if latency is None:
            latency = (300 + out * 18) * self.rng.lognormvariate(0, 0.3)
        cid = self.cid()
        self.calls.append(
            LLMCall(timestamp=ts, call_id=cid, parent_call_id=parent, feature_tag=feature, model=model,
                    input_tokens=inp, output_tokens=out, cost_usd=cost, latency_ms=int(latency), retry_count=retry)
        )
        return cid

    def pick_model(self, f: Feature, ts: datetime) -> str:
        mix = self.model_mix(f.name, ts) or f.models
        r, acc = self.rng.random(), 0.0
        for m, p in mix.items():
            acc += p
            if r <= acc:
                return m
        return next(iter(mix))

    def sizes(self, f: Feature, ts: datetime) -> tuple[float, float]:
        rng = self.rng
        inp = rng.lognormvariate(math.log(f.in_median), f.in_sigma) * self.prompt_mult(f.name, ts)
        if rng.random() < self.big_prompt_p(f.name, ts):
            inp = rng.uniform(30_000, 80_000)
        out = rng.lognormvariate(math.log(f.out_median), f.out_sigma)
        return min(inp, 150_000), min(out, 8_000)

    def legit_retry(self) -> int:
        return self.rng.choice([1, 1, 2]) if self.rng.random() < 0.02 else 0

    def request(self, f: Feature, ts: datetime) -> None:
        rng = self.rng
        model = self.pick_model(f, ts)
        inp, out = self.sizes(f, ts)
        if not f.pipeline:
            self.add(ts, f.name, model, inp, out, retry=self.legit_retry())
            return
        # RAG pipeline: rewrite -> answer(with retrieved docs) -> optional cite check
        root = self.add(ts, f.name, model, inp * 0.15, out * 0.2, retry=self.legit_retry())
        ts2 = ts + timedelta(seconds=rng.uniform(0.5, 2))
        ans = self.add(ts2, f.name, model, inp, out, parent=root, retry=self.legit_retry())
        if rng.random() < 0.4:
            ts3 = ts2 + timedelta(seconds=rng.uniform(1, 4))
            self.add(ts3, f.name, model, inp * 0.5 + out, out * 0.3, parent=ans)

    def background(self) -> None:
        rng = self.rng
        for f in self.feats.values():
            ts = self.t0
            burst = 1.0
            while ts < self.t_end:
                if ts.minute % 10 == 0:
                    burst = rng.lognormvariate(0, 0.25)      # slow bursty factor
                lam = f.peak_per_min * diurnal(ts) * burst * self.rate_mult(f.name, ts)
                for _ in range(poisson(rng, lam)):
                    self.request(f, ts + timedelta(seconds=rng.uniform(0, 60)))
                ts += timedelta(minutes=1)


def new_world(seed: int, tod: str) -> World:
    rng = random.Random(seed)
    hour = rng.choice([12, 13, 14, 15, 16]) if tod == "day" else rng.choice([1, 2, 3, 4])
    day = datetime(2026, 3, 2, tzinfo=timezone.utc) + timedelta(days=rng.randrange(0, 28))
    t_inc = day.replace(hour=hour, minute=rng.randrange(0, 60))
    return World(rng=rng, t0=t_inc - timedelta(hours=HISTORY_H), t_inc=t_inc,
                 t_end=t_inc + timedelta(hours=TAIL_H), feats=features())


def finish(w: World) -> list[LLMCall]:
    calls = [c for c in w.calls if c.timestamp < w.t_end]
    calls.sort(key=lambda c: (c.timestamp, c.call_id))
    return calls


# ---------------------------------------------------------------- injections
def inject_retry_storm(w: World, feat: str, mag: str, flat: bool = False) -> None:
    rng, f = w.rng, w.feats[feat]
    n_par, lo, hi = {"small": (rng.randint(3, 4), 4, 6), "medium": (rng.randint(6, 10), 6, 10),
                     "large": (rng.randint(10, 12), 10, 15)}[mag]
    dur = rng.uniform(20, 40) * 60
    for _ in range(n_par):
        ts = w.t_inc + timedelta(seconds=rng.uniform(0, dur))
        model = w.pick_model(f, ts)
        inp, _ = w.sizes(f, ts)
        root = w.add(ts, feat, model, inp, rng.uniform(0, 30), latency=rng.uniform(8000, 30000))
        for k in range(1, rng.randint(lo, hi) + 1):
            ts += timedelta(seconds=rng.uniform(1, 4) * min(k, 4))   # capped backoff
            w.add(ts, feat, model, inp, rng.uniform(0, 30), parent=None if flat else root, retry=k,
                  latency=rng.uniform(8000, 30000))


def inject_bloat(w: World, feat: str, mag: str) -> None:
    rng, f = w.rng, w.feats[feat]
    n_ch, dlo, dhi, glo, ghi = {"subtle": (rng.randint(2, 3), 4, 4, 1.3, 1.35),
                                "medium": (rng.randint(5, 8), 5, 7, 1.4, 1.6),
                                "large": (rng.randint(10, 15), 8, 10, 1.6, 1.8)}[mag]
    dur = rng.uniform(20, 40) * 60
    for _ in range(n_ch):
        ts = w.t_inc + timedelta(seconds=rng.uniform(0, dur))
        model = w.pick_model(f, ts)
        inp, out = w.sizes(f, ts)
        parent = w.add(ts, feat, model, inp, out)
        g = rng.uniform(glo, ghi)
        for _ in range(rng.randint(dlo, dhi)):          # depth = number of self-calls below the root
            ts += timedelta(seconds=rng.uniform(3, 10))
            inp = min(inp * g + out, 150_000)           # history + last answer fed back
            out = min(rng.lognormvariate(math.log(f.out_median), f.out_sigma), 8000)
            parent = w.add(ts, feat, model, inp, out, parent=parent)


def set_misroute(w: World, feat: str, ratio: str, frac: float = 1.0) -> None:
    target = MISROUTE_TARGETS[feat][ratio]
    end = w.t_inc + timedelta(minutes=w.rng.uniform(30, 120))
    base = w.feats[feat].models
    mix = {target: frac, **{m: p * (1 - frac) for m, p in base.items()}} if frac < 1 else {target: 1.0}
    prev = w.model_mix
    w.model_mix = lambda f, ts: mix if (f == feat and w.t_inc <= ts < end) else prev(f, ts)


def set_surge(w: World, feat: str | None, lo: float, hi: float, dlo: float, dhi: float) -> None:
    mult = w.rng.uniform(lo, hi)
    end = w.t_inc + timedelta(minutes=w.rng.uniform(dlo, dhi))
    ramp = timedelta(minutes=5)
    prev = w.rate_mult

    def rate(f, ts):
        if (feat is None or f == feat) and w.t_inc <= ts < end:
            k = min(1.0, (ts - w.t_inc) / ramp)
            return prev(f, ts) * (1 + (mult - 1) * k)
        return prev(f, ts)
    w.rate_mult = rate


# -------------------------------------------------------------------- cases
@dataclass
class CaseSpec:
    kind: str                       # positive | negative | mixed
    build: callable                 # (World) -> truth list
    tod: str = "day"
    note: str = ""


POS_FEATS = ["chat_assistant", "doc_summarizer", "code_review"]
MIS_FEATS = ["chat_assistant", "doc_summarizer"]


def _retry(mag, flat=False):
    def b(w):
        feat = w.rng.choice(POS_FEATS)
        w.background(); inject_retry_storm(w, feat, mag, flat)
        return [(RETRY, feat)]
    return b


def _bloat(mag):
    def b(w):
        feat = w.rng.choice(POS_FEATS)
        w.background(); inject_bloat(w, feat, mag)
        return [(BLOAT, feat)]
    return b


def _mis(ratio, frac=1.0):
    def b(w):
        feat = w.rng.choice(MIS_FEATS)
        set_misroute(w, feat, ratio, frac); w.background()
        return [(MISROUTE, feat)]
    return b


def _baseline(w):
    w.background(); return []


def _surge(w):
    set_surge(w, None, 2, 6, 30, 90); w.background(); return []


def _marketing(w):
    set_surge(w, w.rng.choice(POS_FEATS + ["rag_search"]), 4, 8, 20, 40); w.background(); return []


def _cheaper(w):
    feat = w.rng.choice(list(CHEAPER)); target = CHEAPER[feat]
    w.model_mix = lambda f, ts: {target: 1.0} if (f == feat and ts >= w.t_inc) else None
    w.background(); return []


def _ab_shift(w):
    feat = w.rng.choice(list(AB_SHIFT)); mix = AB_SHIFT[feat]
    w.model_mix = lambda f, ts: mix if (f == feat and ts >= w.t_inc) else None
    w.background(); return []


def _prompt40(w):
    feat = w.rng.choice(POS_FEATS)
    w.prompt_mult = lambda f, ts: 1.4 if (f == feat and ts >= w.t_inc) else 1.0
    w.background(); return []


def _sparse_big(w):
    w.big_prompt_p = lambda f, ts: 0.08 if f == "nightly_digest" else 0.0
    w.background()
    for _ in range(2):      # make sure some land after the incident time too
        ts = w.t_inc + timedelta(minutes=w.rng.uniform(0, 120))
        w.add(ts, "nightly_digest", "gpt-4o-mini", w.rng.uniform(30_000, 80_000), 400)
    return []


def _gap(catchup):
    def b(w):
        w.background()
        g0, g1 = w.t_inc, w.t_inc + timedelta(minutes=20)
        kept = []
        for c in w.calls:
            if g0 <= c.timestamp < g1:
                if catchup:     # queue flush: delayed rows land in the 5 min after the gap
                    c = c.model_copy(update={"timestamp": g1 + (c.timestamp - g0) / 4})
                    kept.append(c)
            else:
                kept.append(c)
        w.calls = kept
        return []
    return b


def _mix_retry_mis(w):
    a, b_ = w.rng.sample(POS_FEATS, 2)
    if a in MIS_FEATS and b_ not in MIS_FEATS:
        a, b_ = b_, a
    set_misroute(w, b_, "5x"); w.background(); inject_retry_storm(w, a, "medium")
    return [(RETRY, a), (MISROUTE, b_)]


def _mix_bloat_mis(w):
    a, b_ = w.rng.sample(POS_FEATS, 2)
    if a in MIS_FEATS and b_ not in MIS_FEATS:
        a, b_ = b_, a
    set_misroute(w, b_, "5x"); w.background(); inject_bloat(w, a, "medium")
    return [(BLOAT, a), (MISROUTE, b_)]


def _mix_mis_surge(w):
    feat = w.rng.choice(MIS_FEATS)
    set_misroute(w, feat, "5x"); set_surge(w, feat, 3, 3, 30, 90); w.background()
    return [(MISROUTE, feat)]


CASES: dict[str, CaseSpec] = {
    "retry_small": CaseSpec("positive", _retry("small")),
    "retry_medium": CaseSpec("positive", _retry("medium")),
    "retry_large": CaseSpec("positive", _retry("large")),
    "retry_medium_night": CaseSpec("positive", _retry("medium"), "night"),
    "retry_medium_flat": CaseSpec("positive", _retry("medium", flat=True), note="attempt rows carry no parent_call_id"),
    "bloat_subtle_d4": CaseSpec("positive", _bloat("subtle")),
    "bloat_medium": CaseSpec("positive", _bloat("medium")),
    "bloat_large": CaseSpec("positive", _bloat("large")),
    "bloat_medium_night": CaseSpec("positive", _bloat("medium"), "night"),
    "misroute_2x": CaseSpec("positive", _mis("2x")),
    "misroute_5x": CaseSpec("positive", _mis("5x")),
    "misroute_15x": CaseSpec("positive", _mis("15x")),
    "misroute_5x_half": CaseSpec("positive", _mis("5x", 0.5), note="50% of traffic misrouted"),
    "misroute_2x_night": CaseSpec("positive", _mis("2x"), "night"),
    "misroute_5x_night": CaseSpec("positive", _mis("5x"), "night"),
    "neg_baseline_rag": CaseSpec("negative", _baseline),
    "neg_baseline_night": CaseSpec("negative", _baseline, "night"),
    "neg_surge_all": CaseSpec("negative", _surge),
    "neg_marketing_burst": CaseSpec("negative", _marketing),
    "neg_cheaper_model": CaseSpec("negative", _cheaper),
    "neg_ab_ratio_shift": CaseSpec("negative", _ab_shift, note="debatable label"),
    "neg_prompt_plus40": CaseSpec("negative", _prompt40),
    "neg_sparse_big_prompts": CaseSpec("negative", _sparse_big),
    "neg_clock_gap": CaseSpec("negative", _gap(False)),
    "neg_clock_gap_catchup": CaseSpec("negative", _gap(True)),
    "mix_retry+misroute": CaseSpec("mixed", _mix_retry_mis),
    "mix_bloat+misroute": CaseSpec("mixed", _mix_bloat_mis),
    "mix_misroute+surge_same": CaseSpec("mixed", _mix_mis_surge),
}


def make_case(name: str, seed: int):
    spec = CASES[name]
    # seed is mixed with the case name so cases do not share a background
    w = new_world(hash_seed(name, seed), spec.tod)
    truth = spec.build(w)
    return finish(w), truth, w.t_inc


def hash_seed(name: str, seed: int) -> int:
    import zlib
    return zlib.crc32(name.encode()) * 1000 + seed
