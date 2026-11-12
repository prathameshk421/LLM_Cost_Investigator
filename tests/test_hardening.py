"""Regression tests for the audit fixes, budget guard, HF provider and lab API.

Each test names the failure it locks out. None of them call a real LLM.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

from llm_cost_investigator import llm_client  # noqa: E402
from llm_cost_investigator.agents import (  # noqa: E402
    _clean_json_text,
    call_agent_with_validation,
    check_agent_scope,
    run_agents_detailed,
)
from llm_cost_investigator.benchmark import run_benchmark  # noqa: E402
from llm_cost_investigator.detector import _z_score, detect_anomalies  # noqa: E402
from llm_cost_investigator.ingest.base import read_source_text  # noqa: E402
from llm_cost_investigator.ingest.pricing import lookup_rates  # noqa: E402
from llm_cost_investigator.investigate import (  # noqa: E402
    group_adjacent_windows,
    investigate_calls,
)
from llm_cost_investigator.scenario_lab import generate_lab_calls  # noqa: E402
from llm_cost_investigator.schemas import AgentEvidence, LLMCall  # noqa: E402
from llm_cost_investigator.usage import (  # noqa: E402
    BudgetedOpenAI,
    BudgetExceeded,
    UsageLedger,
)

T0 = datetime(2026, 7, 8, 9, 0, tzinfo=timezone.utc)


def _call(i: int, minute: float, feature: str = "f", **kw) -> LLMCall:
    base = dict(
        timestamp=T0 + timedelta(minutes=minute),
        call_id=f"c{i}",
        feature_tag=feature,
        model="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0003,
        latency_ms=500,
        retry_count=0,
    )
    base.update(kw)
    return LLMCall(**base)


def _top_hypothesis(calls: list[LLMCall]) -> str:
    result = investigate_calls(calls, provider="fallback", write_reports=False)
    for inc in result.incidents:
        if inc.root_cause.hypothesis != "no_strong_signal":
            return inc.root_cause.hypothesis
    return "no_strong_signal"


# ---------------------------------------------------------------- detector


def test_traffic_surge_is_not_context_bloat() -> None:
    """Audit #1: 10x calls of identical shape was diagnosed as context bloat."""
    calls = [_call(i, m) for i, m in enumerate(range(70))]
    n = len(calls)
    for m in range(70, 80):
        for j in range(10):
            calls.append(_call(n, m + j / 10))
            n += 1
    assert _top_hypothesis(calls) == "no_strong_signal"


def test_relative_z_floor_catches_cent_scale_spikes() -> None:
    """Audit #2: an absolute $1 floor hid a 100x spike on a $0.01 baseline."""
    assert _z_score(1.0, [0.01, 0.01, 0.01]) > 100


def test_mixed_naive_and_aware_timestamps_do_not_crash() -> None:
    """Audit #12: naive and aware datetimes raised TypeError in sorting."""
    calls = [_call(i, m) for i, m in enumerate(range(20))]
    naive = _call(99, 21).model_copy(update={"timestamp": (T0 + timedelta(minutes=21)).replace(tzinfo=None)})
    detect_anomalies(calls + [naive])


def test_retry_storm_detected_before_cost_moves() -> None:
    """Structural early warning: retries flagged even inside cost noise."""
    calls = [_call(i, m, cost_usd=0.0003 * (1 + (m % 5))) for i, m in enumerate(range(70))]
    for a in range(6):
        calls.append(
            _call(100 + a, 71 + a * 0.2, retry_count=a, parent_call_id="p", cost_usd=0.0003)
        )
    windows = detect_anomalies(calls)
    assert any(w.signals.max_retry_count >= 5 for w in windows)


def test_sustained_incident_is_one_investigation() -> None:
    """Adjacent flagged windows of one feature merge into one incident."""
    lab = generate_lab_calls("model_misroute", seed=7)
    result = investigate_calls(lab.calls, provider="fallback", write_reports=False)
    assert result.anomaly_count > 1
    assert result.incident_groups == 1
    assert len(result.incidents) == 1
    assert len(result.flagged_windows) == result.anomaly_count


def test_grouping_keeps_separate_features_apart() -> None:
    lab = generate_lab_calls("retry_loop", seed=1)
    windows = detect_anomalies(lab.calls)
    groups = group_adjacent_windows(windows)
    for g in groups:
        assert len({w.feature_tag for w in g}) == 1


# ---------------------------------------------------------------- benchmark


def test_benchmark_accuracy_floor() -> None:
    """End to end on fresh seeds: all 5 scenarios, no false alarms."""
    report = run_benchmark(seeds=8, noise=0.1, provider="fallback")
    assert report["overall_accuracy"] >= 0.95, report["failures"]
    assert report["false_alarm_rate"] <= 0.05


def test_lab_is_deterministic_per_seed() -> None:
    a = generate_lab_calls("context_bloat", seed=3).calls
    b = generate_lab_calls("context_bloat", seed=3).calls
    c = generate_lab_calls("context_bloat", seed=4).calls
    assert a == b
    assert a != c


# ---------------------------------------------------------------- agents


def _evidence_json(agent="retry_loop_agent", hyp="uncapped_retry_loop") -> str:
    return (
        '{"agent_name": "%s", "hypothesis": "%s", "confidence": 0.9,'
        ' "supporting_metrics": {}, "explanation": "x"}' % (agent, hyp)
    )


def test_markdown_fenced_json_is_accepted() -> None:
    """Audit #4: fenced JSON always fell back to the deterministic path."""
    raw = "```json\n" + _evidence_json() + "\n```"
    ev = call_agent_with_validation(lambda _p: raw, "prompt", AgentEvidence)
    assert ev.hypothesis == "uncapped_retry_loop"
    assert _clean_json_text("Sure! " + _evidence_json() + " Hope that helps").startswith("{")


def test_agent_cannot_return_another_agents_hypothesis() -> None:
    """Audit #5: retry agent returning a misroute verdict was accepted."""
    ev = AgentEvidence.model_validate_json(
        _evidence_json("retry_loop_agent", "expensive_model_misroute")
    )
    with pytest.raises(ValueError):
        check_agent_scope(ev, "retry_loop_agent")


def test_out_of_scope_llm_answer_falls_back() -> None:
    lab = generate_lab_calls("retry_loop", seed=2)
    window = next(w for w in detect_anomalies(lab.calls) if w.feature_tag == "support_reply")
    bad = _evidence_json("retry_loop_agent", "expensive_model_misroute")
    runs = run_agents_detailed(
        ["retry_loop_agent"], window, llm_client=lambda _p: bad, provider="groq"
    )
    assert runs[0].fallback_used
    assert runs[0].evidence.hypothesis != "expensive_model_misroute"


# ---------------------------------------------------------------- budget


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        msg = SimpleNamespace(content="{}", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
        )


def _fake_client(ledger: UsageLedger) -> tuple[BudgetedOpenAI, _FakeCompletions]:
    inner = _FakeCompletions()
    raw = SimpleNamespace(chat=SimpleNamespace(completions=inner))
    return BudgetedOpenAI(raw, provider="hf", usage=ledger), inner


def test_call_cap_blocks_before_spend(tmp_path) -> None:
    ledger = UsageLedger(max_calls=2, budget_usd=10.0, log_path=tmp_path / "log.jsonl")
    client, inner = _fake_client(ledger)
    for _ in range(2):
        client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
    with pytest.raises(BudgetExceeded):
        client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
    assert inner.calls == 2  # the blocked call never reached the provider
    assert ledger.blocked == 1
    assert len((tmp_path / "log.jsonl").read_text().splitlines()) == 2


def test_usd_budget_blocks(tmp_path) -> None:
    ledger = UsageLedger(max_calls=100, budget_usd=0.0001, log_path=tmp_path / "log.jsonl")
    client, _ = _fake_client(ledger)
    client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])  # ~$0.00035
    with pytest.raises(BudgetExceeded):
        client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
    snap = ledger.snapshot()
    assert snap["spent_usd"] > 0 and snap["remaining_usd"] == 0


# ---------------------------------------------------------------- providers


def test_hf_provider_needs_explicit_choice(monkeypatch) -> None:
    """A cached `hf auth login` token must never be spent by surprise."""
    for env in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN", "LLM_PROVIDER"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(llm_client, "_api_key_for", lambda p: "cached" if p == "hf" else "")
    auto = llm_client.resolve_llm_client()
    assert auto.provider == "fallback"
    explicit = llm_client.resolve_llm_client(llm_client.LLMClientConfig(provider="hf"))
    assert explicit.provider == "hf"
    assert explicit.model == "openai/gpt-oss-20b"
    assert isinstance(explicit.raw_client, BudgetedOpenAI)


def test_provider_aliases() -> None:
    assert llm_client._normalise_provider("HuggingFace") == "hf"
    with pytest.raises(ValueError):
        llm_client._normalise_provider("openai")


# ---------------------------------------------------------------- ingest


def test_pricing_short_keys_need_prefix() -> None:
    """Audit #10: 'o1' matched any model name containing those letters."""
    assert lookup_rates("mypro1model")[2] == "fallback_default"
    assert lookup_rates("o1-2024-12-17")[2] == "o1"
    assert lookup_rates("claude-opus-4-1-20250805")[2] == "claude-opus-4"


def test_utf16_and_bad_bytes() -> None:
    """Audit #15: UTF-16 exports raised a raw UnicodeDecodeError."""
    assert read_source_text("a,b\n1,2".encode("utf-16")) == "a,b\n1,2"
    with pytest.raises(ValueError, match="UTF-8"):
        read_source_text(b"\xff\x00abc\x80")


# ---------------------------------------------------------------- API


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api.app import app

    return TestClient(app)


@pytest.mark.parametrize(
    "scenario", ["retry_loop", "context_bloat", "model_misroute", "traffic_surge", "clean"]
)
def test_simulate_endpoint(client, scenario) -> None:
    r = client.post("/api/simulate", json={"scenario": scenario, "seed": 11})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_meta"]["correct"] is True
    assert body["run_meta"]["llm_calls"] == 0  # deterministic mode never spends
    tl = body["timeline"]
    assert len(tl["windows"]) > 5
    assert all(len(v) == len(tl["windows"]) for v in tl["series"].values())


def test_simulate_rejects_unknown_scenario(client) -> None:
    assert client.post("/api/simulate", json={"scenario": "x"}).status_code == 422


def test_usage_endpoint(client) -> None:
    body = client.get("/api/usage").json()
    assert {"spent_usd", "budget_usd", "max_calls", "providers"} <= body.keys()
    assert set(body["providers"]) == {"hf", "deploy", "groq", "cerebras"}


def test_benchmark_endpoint(client) -> None:
    body = client.get("/api/benchmark?seeds=2&refresh=true").json()
    assert body["runs_total"] == 10
    assert "runs" not in body


def test_bad_adapter_is_clear_400(client) -> None:
    r = client.post(
        "/api/investigate/preview",
        files={"file": ("x.jsonl", io.BytesIO(b"{}"))},
        data={"adapter": "bogus"},
    )
    assert r.status_code == 400
    assert "Unknown adapter" in r.json()["detail"]


def test_oversized_upload_is_413(client, monkeypatch) -> None:
    """Audit #8: the size check ran after reading the whole file."""
    import api.routes.investigate as routes

    monkeypatch.setattr(routes, "MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/api/investigate/preview",
        files={"file": ("x.jsonl", io.BytesIO(b"{}" * 50))},
    )
    assert r.status_code == 413


def test_full_app_routes_do_not_shadow_each_other(client) -> None:
    """Integration: /api/incidents/live was captured by /api/incidents/{id}."""
    r = client.get("/api/incidents/live")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    for path in ("/api/analytics/overview", "/api/budgets", "/api/warehouse/stats", "/api/usage"):
        assert client.get(path).status_code == 200, path


def test_llm_no_signal_cannot_erase_verified_skill_finding() -> None:
    """Live bug: the LLM said no_strong_signal on a clear retry storm and won."""
    lab = generate_lab_calls("retry_loop", seed=2)
    window = next(w for w in detect_anomalies(lab.calls) if w.feature_tag == "support_reply")
    nss = _evidence_json("retry_loop_agent", "no_strong_signal")
    runs = run_agents_detailed(["retry_loop_agent"], window, llm_client=lambda _p: nss, provider="groq")
    ev = runs[0].evidence
    assert not runs[0].fallback_used
    assert ev.hypothesis == "uncapped_retry_loop"
    assert ev.confidence <= 0.60
    assert ev.supporting_metrics["llm_disagreed"] is True
    assert ev.supporting_metrics["llm_hypothesis"] == "no_strong_signal"


def test_every_live_call_gets_an_output_cap(tmp_path) -> None:
    """DeepSeek can loop on its own tool-call text; a max_tokens cap bounds it."""
    seen = {}

    class Inner:
        def create(self, **kwargs):
            seen.update(kwargs)
            msg = SimpleNamespace(content="{}", tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

    ledger = UsageLedger(max_calls=5, budget_usd=1.0, log_path=tmp_path / "l.jsonl", restore=False)
    client = BudgetedOpenAI(SimpleNamespace(chat=SimpleNamespace(completions=Inner())), "deploy", ledger)
    client.chat.completions.create(model="m", messages=[])
    assert seen["max_tokens"] > 0


def test_tool_syntax_leak_is_an_error(tmp_path) -> None:
    """Heimdall saw DeepSeek print '<｜DSML｜tool_calls>' as text; treat it as a failed call."""
    from llm_cost_investigator.usage import ToolSyntaxLeak

    class Inner:
        def create(self, **kwargs):
            msg = SimpleNamespace(content="<｜DSML｜tool_calls> get_call_chain", tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

    ledger = UsageLedger(max_calls=5, budget_usd=1.0, log_path=tmp_path / "l.jsonl", restore=False)
    client = BudgetedOpenAI(SimpleNamespace(chat=SimpleNamespace(completions=Inner())), "deploy", ledger)
    with pytest.raises(ToolSyntaxLeak):
        client.chat.completions.create(model="m", messages=[])
    assert ledger.snapshot()["errors"] == 1


def test_deploy_provider_needs_explicit_choice(monkeypatch) -> None:
    for env in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN", "LLM_PROVIDER"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DEPLOY_API_KEY", "x")
    assert llm_client.resolve_llm_client().provider == "fallback"
    explicit = llm_client.resolve_llm_client(llm_client.LLMClientConfig(provider="deepseek"))
    assert explicit.provider == "deploy"
    assert explicit.model == "deepseek-ai/deepseek-v4.1-flash-1017"


def test_slow_live_call_hits_hard_deadline(tmp_path, monkeypatch) -> None:
    """A 304 s call was seen live: the SDK timeout is per read, not total."""
    import time as _time

    from llm_cost_investigator.usage import CallDeadlineExceeded

    class Slow:
        def create(self, **kwargs):
            _time.sleep(2)

    monkeypatch.setenv("LLM_CALL_DEADLINE_S", "0.2")
    ledger = UsageLedger(max_calls=5, budget_usd=1.0, log_path=tmp_path / "l.jsonl", restore=False)
    client = BudgetedOpenAI(SimpleNamespace(chat=SimpleNamespace(completions=Slow())), "deploy", ledger)
    t = _time.time()
    with pytest.raises(CallDeadlineExceeded):
        client.chat.completions.create(model="m", messages=[])
    assert _time.time() - t < 1.5
    assert ledger.snapshot()["errors"] == 1
