"""Tiny 3-feature app for the Tier D live proof. See README.md.

Features: summarize, classify, reflect. After the baseline, --bug switches on a
real fault: retry_loop (classify), misroute (summarize) or bloat (reflect).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llmci_wrapper import instrument, llmci_context  # noqa: E402

BASE_URLS = {"deploy": "https://api.deploy.com/api/v1", "hf": "https://router.huggingface.co/v1"}
KEY_ENV = {"deploy": "DEPLOY_API_KEY", "hf": "HF_TOKEN"}
DEFAULT_MODEL = {"deploy": "deepseek-ai/deepseek-v4.1-flash-1017", "hf": "openai/gpt-oss-20b"}
BUGS = ("retry_loop", "misroute", "bloat")
FEATURES = ("summarize", "classify", "reflect")
MAX_ATTEMPTS, BLOAT_DEPTH = 15, 8
TEXT = ("Our payments team shipped a new retry policy last week. Error rates fell, but support "
        "tickets about duplicate charges rose. Finance asked for a weekly report on both numbers. ")


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """OpenAI-shaped client with no network. Token counts follow text length."""

    def __init__(self):
        self.chat = _Obj(completions=_Obj(create=self._create))

    @staticmethod
    def _create(model="fake", messages=(), max_tokens=100, **_):
        text = " ".join(m["content"] for m in messages)
        out = "A fake answer. " * max(1, min(max_tokens, 120) // 4)
        usage = _Obj(prompt_tokens=len(text) // 4 + 1, completion_tokens=len(out) // 4 + 1)
        return _Obj(choices=[_Obj(message=_Obj(content=out))], usage=usage)


def build_client(provider, fake):
    if fake:
        return FakeClient()
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    from llm_cost_investigator.llm_client import _api_key_for  # also reads the cached hf login
    from openai import OpenAI

    key = _api_key_for(provider)
    if not key:
        raise SystemExit(f"{KEY_ENV[provider]} is not set")
    return OpenAI(api_key=key, base_url=BASE_URLS[provider], timeout=60)


class App:
    def __init__(self, args, send=None, out=print):
        self.args, self.out, self.send = args, out, send
        self.clients, self.records = {}, []
        self.start = datetime.now(timezone.utc) - timedelta(seconds=args.total_minutes * 60) \
            if args.time_scale > 1 else datetime.now(timezone.utc)
        self.t0 = time.monotonic()
        self.sim_min = 0.0
        self.bugs = [b for b in args.bug.split(",") if b]
        self.last_summary = TEXT

    def now(self):
        return self.start + timedelta(minutes=self.sim_min)

    def active_bug(self):
        a = self.args
        m = self.sim_min - a.baseline_minutes
        for i, b in enumerate(self.bugs):
            lo = i * (a.minutes + a.gap_minutes)
            if lo <= m < lo + a.minutes:
                return b
        return None

    def client(self, provider):
        if provider not in self.clients:
            kw = {"now": self.now, "send": self.send}
            self.clients[provider] = instrument(
                build_client(provider, self.args.fake), self.args.investigator,
                token=os.environ.get("LLMCI_API_TOKEN"), **kw)
        return self.clients[provider]

    def call(self, provider, model, feature, content, call_id, parent=None, retry=0, max_tokens=200):
        t = time.perf_counter()
        status, text = "ok", ""
        try:
            with llmci_context(feature_tag=feature, call_id=call_id, parent_call_id=parent,
                               retry_count=retry):
                r = self.client(provider).chat.completions.create(
                    model=model, messages=[{"role": "user", "content": content}],
                    max_tokens=max_tokens)
            text = r.choices[0].message.content or ""
            u = r.usage
            tin, tout = u.prompt_tokens, u.completion_tokens
        except Exception as exc:  # the app keeps running on LLM errors
            status, tin, tout = f"error:{type(exc).__name__}", 0, 0
        rec = dict(call_id=call_id, parent=parent, feature=feature, model=model, retry=retry,
                   tin=tin, tout=tout, status=status)
        self.records.append(rec)
        self.out(f"{self.now().strftime('%H:%M:%S')} {feature:9} {model} in={tin} out={tout} "
                 f"retry={retry} id={call_id[:8]} parent={(parent or '-')[:8]} "
                 f"{status} {int((time.perf_counter() - t) * 1000)}ms")
        return text

    def summarize(self, bug):
        a = self.args
        prov, model = (a.pricy_provider, a.pricy_model) if bug == "misroute" \
            else (a.summarize_provider, a.summarize_model)
        self.last_summary = self.call(prov, model, "summarize", "Summarize in 2 sentences:\n" + TEXT,
                                      str(uuid.uuid4())) or TEXT

    def classify(self, bug):
        a, model = self.args, self.args.model or DEFAULT_MODEL[self.args.provider]
        if bug != "retry_loop":
            self.call(a.provider, model, "classify", "Give one word label (billing, bug, other):\n" + TEXT,
                      str(uuid.uuid4()), max_tokens=10)
            return
        first = str(uuid.uuid4())
        for n in range(MAX_ATTEMPTS):  # validator never passes, no cap except the hard stop
            cid = first if n == 0 else str(uuid.uuid4())
            text = self.call(a.provider, model, "classify",
                             'Reply ONLY strict JSON {"label": "...", "checksum": "..."}:\n' + TEXT,
                             cid, parent=None if n == 0 else first, retry=n, max_tokens=60)
            if validator(text):
                break

    def reflect(self, bug):
        a, model = self.args, self.args.model or DEFAULT_MODEL[self.args.provider]
        if bug != "bloat":
            self.call(a.provider, model, "reflect", "Critique this answer in one sentence:\n" + self.last_summary,
                      str(uuid.uuid4()), max_tokens=60)
            return
        history, parent = [TEXT, self.last_summary], None
        for depth in range(BLOAT_DEPTH):  # the agent calls itself with its full history
            cid = str(uuid.uuid4())
            text = self.call(a.provider, model, "reflect",
                             "Critique and improve your previous answers:\n" + "\n---\n".join(history),
                             cid, parent=parent, max_tokens=200)
            history.append(text)
            parent = cid

    def run(self):
        a = self.args
        gap = 60.0 / a.rate
        for i in range(round(a.total_minutes * a.rate)):  # integer ticks: no float drift
            self.sim_min = i / a.rate
            getattr(self, FEATURES[i % 3])(self.active_bug())
            if a.time_scale > 0:
                time.sleep(gap / a.time_scale)
        for c in self.clients.values():
            c.llmci_flush()
        return self.records


def validator(text):
    """Strict-JSON check that always fails: it demands a checksum no model can know."""
    try:
        return json.loads(text).get("checksum") == "sha256:" + uuid.uuid4().hex
    except Exception:
        return False


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--minutes", type=float, default=30, help="minutes each bug stays on")
    p.add_argument("--rate", type=float, default=6, help="app calls per minute (retries and chains add more)")
    p.add_argument("--baseline-minutes", type=float, default=75)
    p.add_argument("--gap-minutes", type=float, default=15, help="clean gap between bugs")
    p.add_argument("--bug", default="", help="comma list of: " + ",".join(BUGS))
    p.add_argument("--investigator", default="http://127.0.0.1:8000")
    p.add_argument("--provider", choices=["deploy", "hf"], default="deploy", help="classify and reflect")
    p.add_argument("--model", default=None, help="classify and reflect model")
    p.add_argument("--summarize-provider", choices=["deploy", "hf"], default="hf")
    p.add_argument("--summarize-model", default="openai/gpt-oss-20b")
    p.add_argument("--pricy-provider", choices=["deploy", "hf"], default="hf")
    p.add_argument("--pricy-model", default="openai/gpt-oss-120b", help="summarize model under --bug misroute")
    p.add_argument("--fake", action="store_true", help="no network LLM; plumbing rehearsal only")
    p.add_argument("--time-scale", type=float, default=1.0, help="simulated seconds per wall second (fake runs)")
    a = p.parse_args(argv)
    bad = [b for b in a.bug.split(",") if b and b not in BUGS]
    if bad:
        p.error(f"unknown bug {bad}")
    if a.baseline_minutes < 60:
        p.error("--baseline-minutes must be >= 60")
    if a.time_scale != 1.0 and not a.fake:
        p.error("--time-scale needs --fake")
    n = len([b for b in a.bug.split(",") if b])
    a.total_minutes = a.baseline_minutes + n * a.minutes + max(0, n - 1) * a.gap_minutes + (a.gap_minutes if n else 0)
    return a


def main(argv=None):
    return App(parse(argv)).run()


if __name__ == "__main__":
    main()
