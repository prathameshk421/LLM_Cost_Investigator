"""Tier D plumbing: the live app and wrapper, offline with a fake LLM."""

from __future__ import annotations

import sys
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "web", ROOT, ROOT / "examples" / "live_app"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import app as live  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from llmci_wrapper import instrument, llmci_context  # noqa: E402

from api.routes.stream import router  # noqa: E402
from llm_cost_investigator import ingest_service  # noqa: E402
from llm_cost_investigator.warehouse import reset_warehouse  # noqa: E402

FAST = ["--fake", "--time-scale", "1000000", "--baseline-minutes", "60", "--minutes", "6", "--gap-minutes", "3"]


def run(*extra):
    events, lock = [], threading.Lock()

    def send(ev):
        with lock:
            events.append(ev)

    args = live.parse(FAST + list(extra))
    recs = live.App(args, send=send, out=lambda s: None).run()
    return recs, events


@pytest.fixture()
def api():
    reset_warehouse(":memory:")
    svc = ingest_service.reset_service()
    a = FastAPI()
    a.include_router(router)
    with TestClient(a) as c:
        yield c
    svc.stop_background()
    ingest_service._service = None


def post_all(api, events):
    r = api.post("/v1/ingest", json={"events": events})
    assert r.status_code == 202
    return r.json()


def test_baseline_events_accepted_with_correct_fields(api):
    recs, events = run()
    assert len(events) == len(recs) == 360
    assert {e["feature_tag"] for e in events} == set(live.FEATURES)
    for e in events:
        assert e["call_id"] and e["input_tokens"] > 0 and e["retry_count"] == 0
        assert e["parent_call_id"] is None and "cost_usd" not in e
        datetime.fromisoformat(e["timestamp"])
    assert {e["model"] for e in events if e["feature_tag"] == "summarize"} == {"openai/gpt-oss-20b"}
    assert {e["model"] for e in events if e["feature_tag"] == "classify"} == {"deepseek-ai/deepseek-v4.1-flash-1017"}
    res = post_all(api, events)
    assert res["accepted"] == 360 and res["rejected"] == 0


def test_timestamps_follow_simulated_clock_and_end_now():
    _, events = run("--bug", "bloat")
    ts = [datetime.fromisoformat(e["timestamp"]) for e in events]
    assert ts == sorted(ts)
    assert (datetime.now(timezone.utc) - ts[-1]).total_seconds() < 120
    assert (ts[-1] - ts[0]).total_seconds() > 60 * 60


def test_baseline_must_be_at_least_60():
    with pytest.raises(SystemExit):
        live.parse(["--fake", "--baseline-minutes", "30"])


def test_retry_loop_chain(api):
    recs, events = run("--bug", "retry_loop")
    cl = [e for e in events if e["feature_tag"] == "classify"]
    chains = defaultdict(list)
    for e in cl:
        chains[e["parent_call_id"] or e["call_id"]].append(e)
    long = [c for c in chains.values() if len(c) > 1]
    assert long, "bug never fired"
    for c in long:
        assert len(c) == 15  # hard stop
        assert sorted(e["retry_count"] for e in c) == list(range(15))
        first = next(e for e in c if e["retry_count"] == 0)
        assert first["parent_call_id"] is None
        assert all(e["parent_call_id"] == first["call_id"] for e in c if e is not first)
    assert max(e["retry_count"] for e in events) == 14
    assert post_all(api, events)["rejected"] == 0


def test_misroute_switches_summarize_model_after_baseline():
    recs, events = run("--bug", "misroute")
    summ = [e for e in events if e["feature_tag"] == "summarize"]
    models = [e["model"] for e in summ]
    assert models[0] == "openai/gpt-oss-20b" and "openai/gpt-oss-120b" in models
    first_pricy = models.index("openai/gpt-oss-120b")
    assert first_pricy >= 60 * 6 // 3 - 2  # not before the baseline ends
    assert set(models[first_pricy:first_pricy + 5]) == {"openai/gpt-oss-120b"}
    assert models[-1] == "openai/gpt-oss-20b"  # trailing gap is clean again
    assert {e["model"] for e in events if e["feature_tag"] != "summarize"} == {"deepseek-ai/deepseek-v4.1-flash-1017"}


def test_misroute_target_is_configurable():
    _, events = run("--bug", "misroute", "--pricy-model", "zai-org/glm-5.3-flash", "--pricy-provider", "deploy")
    assert "zai-org/glm-5.3-flash" in {e["model"] for e in events}


def test_bloat_chain_depth_8_and_growing_input(api):
    recs, events = run("--bug", "bloat")
    by_id = {e["call_id"]: e for e in events}
    depth_max, chain_inputs = 0, []
    for e in events:
        if e["feature_tag"] != "reflect":
            continue
        d, cur = 1, e
        while cur["parent_call_id"]:
            cur, d = by_id[cur["parent_call_id"]], d + 1
        depth_max = max(depth_max, d)
        if d == 8:
            chain_inputs.append(e["input_tokens"])
    assert depth_max == 8
    base = next(e["input_tokens"] for e in events if e["feature_tag"] == "reflect")
    assert max(chain_inputs) > 3 * base
    assert post_all(api, events)["rejected"] == 0


def test_all_three_bugs_in_sequence():
    recs, events = run("--bug", "retry_loop,misroute,bloat")
    assert max(e["retry_count"] for e in events) == 14
    assert "openai/gpt-oss-120b" in {e["model"] for e in events}
    assert any(e["parent_call_id"] and e["feature_tag"] == "reflect" for e in events)


def test_wrapper_never_raises_when_investigator_down():
    client = instrument(live.FakeClient(), "http://127.0.0.1:9", timeout=0.2)  # nothing listens
    with llmci_context(feature_tag="t"):
        r = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert r.choices[0].message.content
    client.llmci_flush()


def test_wrapper_failure_event_and_original_error_propagates():
    seen = []

    class Boom:
        chat = live._Obj(completions=live._Obj(create=lambda **k: (_ for _ in ()).throw(RuntimeError("x"))))

    client = instrument(Boom(), send=seen.append, price=lambda m, i, o: 0.5)
    with llmci_context(feature_tag="f", retry_count=3, parent_call_id="p"):
        with pytest.raises(RuntimeError):
            client.chat.completions.create(model="m", messages=[])
    client.llmci_flush()
    assert seen[0]["input_tokens"] == 0 and seen[0]["output_tokens"] == 0
    assert seen[0]["retry_count"] == 3 and seen[0]["parent_call_id"] == "p" and seen[0]["cost_usd"] == 0.5


def test_wrapper_survives_send_exception():
    def bad(_):
        raise OSError("down")

    client = instrument(live.FakeClient(), send=bad)
    client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    client.llmci_flush()
