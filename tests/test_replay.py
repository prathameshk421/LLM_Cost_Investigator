"""Replay test entrypoint for labeled anomaly scenarios."""

from __future__ import annotations

import json
from typing import Any

from llm_cost_investigator.detector import detect_anomalies, _z_score
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls
from llm_cost_investigator.agents import (
    call_agent_with_tools,
    call_agent_with_validation,
    run_agents,
    run_agents_detailed,
    RETRY_LOOP_TOOLS,
    TOKEN_CONTEXT_TOOLS,
    MODEL_ROUTING_TOOLS,
)
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.llm_client import LLMClientConfig, resolve_llm_client
from llm_cost_investigator.schemas import AgentEvidence


EXPECTED_FEATURES = {
    "retry_loop": "support_reply",
    "context_bloat": "agent_reflection",
    "model_misroute": "summarizer",
}


def _top_anomaly_for_scenario(scenario: str):
    calls = generate_scenario_calls(scenario)
    anomalies = detect_anomalies(calls)
    assert anomalies, f"{scenario}: expected at least one anomaly"

    anomaly = anomalies[0]
    assert anomaly.feature_tag == EXPECTED_FEATURES[scenario], (
        f"{scenario}: expected {EXPECTED_FEATURES[scenario]}, "
        f"got {anomaly.feature_tag}"
    )
    assert anomaly.signals.cost_z_score >= 3.0, (
        f"{scenario}: expected cost z-score >= 3, "
        f"got {anomaly.signals.cost_z_score}"
    )
    return anomaly


def _assert_report_files_created(
    scenario: str,
    root_cause: any,
    anomaly: any,
    agent_runs: list[any],
) -> None:
    import json
    import tempfile
    from pathlib import Path
    from llm_cost_investigator.reporter import write_report
    from llm_cost_investigator.schemas import IncidentReport

    with tempfile.TemporaryDirectory() as tmpdir:
        report = IncidentReport(
            scenario=scenario,
            root_cause=root_cause,
            anomaly_window=anomaly,
            agent_runs=agent_runs,
            recommendations=["Some recommendation"],
        )

        markdown_path, json_path = write_report(
            report, output_dir=tmpdir, print_summary=False
        )

        assert markdown_path.exists(), (
            f"Markdown report file does not exist: {markdown_path}"
        )
        assert json_path.exists(), (
            f"JSON report file does not exist: {json_path}"
        )

        # Validate Markdown structure
        md_content = markdown_path.read_text(encoding="utf-8")
        assert f"# Incident Report: {scenario}" in md_content
        assert "Root cause:" in md_content
        assert "Affected feature:" in md_content
        assert "Confidence:" in md_content
        assert "Winning agent:" in md_content
        assert "Summary:" in md_content
        assert "Supporting evidence:" in md_content
        assert "Recommendations:" in md_content

        # Validate JSON content structure
        json_content = json.loads(json_path.read_text(encoding="utf-8"))
        assert "scenario" in json_content
        assert json_content["scenario"] == scenario
        assert "root_cause" in json_content
        assert "hypothesis" in json_content["root_cause"]
        assert "confidence" in json_content["root_cause"]
        assert json_content["root_cause"]["confidence"] >= 0.70
        if root_cause.hypothesis != "no_strong_signal":
            assert "winning_agent" in json_content["root_cause"]
            assert json_content["root_cause"]["winning_agent"] is not None
        assert "recommendations" in json_content
        assert len(json_content["recommendations"]) > 0


def _assert_retry_loop() -> None:
    anomaly = _top_anomaly_for_scenario("retry_loop")
    assert anomaly.signals.max_retry_count >= 5
    assert anomaly.signals.repeated_parent_call_count > 0
    assert anomaly.signals.model_changed is False
    
    routed = route_agents(anomaly)
    assert "retry_loop_agent" in routed
    
    # Run the agents & aggregator
    agent_runs = run_agents_detailed(routed, anomaly, provider="fallback")
    assert len(agent_runs) >= 1
    run = next(r for r in agent_runs if r.evidence.agent_name == "retry_loop_agent")
    assert run.provider == "fallback", f"retry_loop: expected fallback provider, got {run.provider}"
    assert run.fallback_used is True, "retry_loop: expected fallback_used=True"
    ev = run.evidence
    assert ev.agent_name == "retry_loop_agent"
    assert ev.hypothesis == "uncapped_retry_loop"
    assert ev.confidence >= 0.82  # retry z-score >= 3 floor (zero-variance baseline now yields z=3.5, not 10.0)
    
    root_cause = select_root_cause([r.evidence for r in agent_runs])
    assert root_cause.hypothesis == "uncapped_retry_loop"
    assert root_cause.winning_agent == "retry_loop_agent"
    assert root_cause.confidence == ev.confidence

    _assert_report_files_created("retry_loop", root_cause, anomaly, agent_runs)


def _assert_context_bloat() -> None:
    anomaly = _top_anomaly_for_scenario("context_bloat")
    assert anomaly.signals.input_token_growth_pct is not None
    assert anomaly.signals.input_token_growth_pct >= 100
    assert anomaly.signals.max_call_chain_depth >= 4
    assert anomaly.signals.model_changed is False
    
    routed = route_agents(anomaly)
    assert "token_context_agent" in routed
    
    # Run the agents & aggregator
    agent_runs = run_agents_detailed(routed, anomaly, provider="fallback")
    assert len(agent_runs) >= 1
    run = next(r for r in agent_runs if r.evidence.agent_name == "token_context_agent")
    assert run.provider == "fallback", f"context_bloat: expected fallback provider, got {run.provider}"
    assert run.fallback_used is True, "context_bloat: expected fallback_used=True"
    ev = run.evidence
    assert ev.agent_name == "token_context_agent"
    assert ev.hypothesis == "context_bloat_self_calling_agent"
    assert ev.confidence >= 0.75  # input tokens z-score >= 3 and chain depth >= 4 floor
    
    root_cause = select_root_cause([r.evidence for r in agent_runs])
    assert root_cause.hypothesis == "context_bloat_self_calling_agent"
    assert root_cause.winning_agent == "token_context_agent"
    assert root_cause.confidence == ev.confidence

    _assert_report_files_created("context_bloat", root_cause, anomaly, agent_runs)


def _assert_model_misroute() -> None:
    anomaly = _top_anomaly_for_scenario("model_misroute")
    assert anomaly.signals.model_changed is True
    assert anomaly.signals.model_before == "gpt-4o-mini"
    assert anomaly.signals.model_during == "gpt-4.1"
    assert anomaly.signals.cost_growth_pct is not None
    assert anomaly.signals.cost_growth_pct >= 200
    assert anomaly.signals.input_token_growth_pct is not None
    assert anomaly.signals.input_token_growth_pct < 50
    
    routed = route_agents(anomaly)
    assert "model_routing_agent" in routed
    
    # Run the agents & aggregator
    agent_runs = run_agents_detailed(routed, anomaly, provider="fallback")
    assert len(agent_runs) >= 1
    run = next(r for r in agent_runs if r.evidence.agent_name == "model_routing_agent")
    assert run.provider == "fallback", f"model_misroute: expected fallback provider, got {run.provider}"
    assert run.fallback_used is True, "model_misroute: expected fallback_used=True"
    ev = run.evidence
    assert ev.agent_name == "model_routing_agent"
    assert ev.hypothesis == "expensive_model_misroute"
    assert ev.confidence >= 0.90  # model changed, cost growth >= 200%, token growth < 50% floor
    
    root_cause = select_root_cause([r.evidence for r in agent_runs])
    assert root_cause.hypothesis == "expensive_model_misroute"
    assert root_cause.winning_agent == "model_routing_agent"
    assert root_cause.confidence == ev.confidence

    _assert_report_files_created("model_misroute", root_cause, anomaly, agent_runs)


def _test_validation_wrapper_and_repair() -> None:
    # 1. Test JSON repair retry path
    calls_made = []
    
    def mock_llm_client_repair(prompt: str) -> str:
        calls_made.append(prompt)
        if len(calls_made) == 1:
            return "This is not valid JSON at all!"
        else:
            return json.dumps({
                "agent_name": "retry_loop_agent",
                "hypothesis": "uncapped_retry_loop",
                "confidence": 0.95,
                "supporting_metrics": {"retry_z_score": 5.0},
                "explanation": "Repaired valid JSON output."
            })
            
    prompt = "Give me diagnostic info."
    result = call_agent_with_validation(mock_llm_client_repair, prompt, AgentEvidence)
    
    assert result.agent_name == "retry_loop_agent"
    assert result.hypothesis == "uncapped_retry_loop"
    assert result.confidence == 0.95
    assert len(calls_made) == 2
    assert "Your previous response was not valid JSON" in calls_made[1]

    # 2. Test second parse failure triggering fallback
    anomaly = _top_anomaly_for_scenario("retry_loop")
    
    def mock_llm_client_always_fail(prompt: str) -> str:
        return "Still invalid JSON!"
        
    evidence = run_agents(["retry_loop_agent"], anomaly, llm_client=mock_llm_client_always_fail)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.agent_name == "retry_loop_agent"
    assert ev.hypothesis == "uncapped_retry_loop"
    assert "Fallback" in ev.explanation
    assert ev.confidence == 0.82  # zero-variance baseline fix: retry_z_score is now 3.5, not 10.0


def _test_llm_provider_selection_and_live_client_path() -> None:
    anomaly = _top_anomaly_for_scenario("model_misroute")

    fallback = resolve_llm_client(LLMClientConfig(provider="fallback"))
    assert fallback.provider == "fallback"
    assert fallback.fallback_used is True

    calls_made = []

    def fake_live_client(prompt: str) -> str:
        calls_made.append(prompt)
        return json.dumps({
            "agent_name": "model_routing_agent",
            "hypothesis": "expensive_model_misroute",
            "confidence": 0.91,
            "supporting_metrics": {"model_changed": True},
            "explanation": "Fake live client selected the model routing hypothesis."
        })

    runs = run_agents_detailed(
        ["model_routing_agent"],
        anomaly,
        llm_client=fake_live_client,
        provider="groq",
        model="test-model",
    )

    assert len(runs) == 1
    assert len(calls_made) == 1
    assert runs[0].provider == "groq"
    assert runs[0].model == "test-model"
    assert runs[0].fallback_used is False
    assert runs[0].evidence.hypothesis == "expensive_model_misroute"


def _test_retry_loop_tool_use_calls_tool() -> None:
    """Agent decides to call get_call_chain before answering."""
    call_log = []

    class FakeToolCallFunction:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class FakeToolCall:
        def __init__(self, id_, function):
            self.id = id_
            self.function = function

    class FakeMessage:
        def __init__(self, tool_calls=None, content=None):
            self.tool_calls = tool_calls
            self.content = content
        def model_dump(self, exclude_unset=False):
            d = {"role": "assistant", "content": self.content}
            if self.tool_calls:
                d["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in self.tool_calls
                ]
            return d

    class FakeChoice:
        def __init__(self, message):
            self.message = message

    class FakeResponse:
        def __init__(self, message):
            self.choices = [FakeChoice(message)]

    calls = [0]

    class FakeClient:
        def __init__(self):
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()

        def _create(self, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                tc = FakeToolCall("tc1", FakeToolCallFunction("get_call_chain", json.dumps({"call_id": "c1"})))
                return FakeResponse(FakeMessage(tool_calls=[tc]))
            return FakeResponse(FakeMessage(content=json.dumps({
                "agent_name": "retry_loop_agent",
                "hypothesis": "uncapped_retry_loop",
                "confidence": 0.9,
                "supporting_metrics": {},
                "explanation": "Confirmed via call chain inspection.",
            })))

    def fake_tool_executor(name: str, args: dict) -> Any:
        call_log.append((name, args))
        return [{"call_id": "c1", "retry_count": 6}]

    result = call_agent_with_tools(
        client=FakeClient(), model="test-model", prompt="Test prompt",
        tools=RETRY_LOOP_TOOLS, tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert len(call_log) == 1, f"Expected exactly one tool call, got {len(call_log)}"
    assert call_log[0] == ("get_call_chain", {"call_id": "c1"}), f"Unexpected tool call: {call_log[0]}"
    assert result.hypothesis == "uncapped_retry_loop"
    assert result.confidence == 0.9


def _test_retry_loop_tool_use_skips_tool() -> None:
    """Agent decides signals are clear enough and answers immediately, with
    no tool call at all."""
    class FakeMessage:
        def __init__(self, content):
            self.tool_calls = None
            self.content = content
        def model_dump(self, exclude_unset=False):
            return {"role": "assistant", "content": self.content, "tool_calls": None}

    class FakeChoice:
        def __init__(self, message):
            self.message = message

    class FakeResponse:
        def __init__(self, message):
            self.choices = [FakeChoice(message)]

    class FakeClient:
        def __init__(self):
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()
        def _create(self, **kwargs):
            return FakeResponse(FakeMessage(content=json.dumps({
                "agent_name": "retry_loop_agent",
                "hypothesis": "uncapped_retry_loop",
                "confidence": 0.95,
                "supporting_metrics": {},
                "explanation": "Aggregate signals alone were conclusive.",
            })))

    def fake_tool_executor(name: str, args: dict) -> Any:
        raise AssertionError("Tool should not have been called")

    result = call_agent_with_tools(
        client=FakeClient(), model="test-model", prompt="Test prompt",
        tools=RETRY_LOOP_TOOLS, tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert result.confidence == 0.95


def _test_z_score_behavior() -> None:
    """Verify z-score clamp fix: zero-variance baseline should NOT clamp
    all deviations to 10.0; small deviations should produce small z-scores."""
    # Case 1: baseline_std == 0, current == baseline_mean → z = 0.0
    z = _z_score(0.0, [0.0, 0.0, 0.0])
    assert z == 0.0, f"Expected 0.0, got {z}"

    # Case 2: baseline_std == 0, mild deviation → small z (not clamped to 10.0)
    z = _z_score(1.5, [0.0, 0.0, 0.0])
    assert 1.0 <= z < 5.0, f"Expected z in ~1-3 range for mild deviation, got {z}"

    # Case 3: baseline_std == 0, larger deviation → proportionally larger z
    z_mild = _z_score(1.5, [0.0, 0.0, 0.0])
    z_severe = _z_score(7.0, [0.0, 0.0, 0.0])
    assert z_severe > z_mild, (
        f"Severe deviation should produce larger z than mild: "
        f"mild={z_mild}, severe={z_severe}"
    )

    # Case 4: non-zero baseline_std, mild deviation → z in 1-3 range
    z = _z_score(108.0, [100.0, 95.0, 105.0, 100.0, 98.0, 102.0])
    assert 1.0 <= z <= 3.0, f"Expected z ~1.5 for mild deviation, got {z}"

    # Case 5: non-zero baseline_std, extreme deviation → large z
    z = _z_score(200.0, [100.0, 95.0, 105.0, 100.0, 98.0, 102.0])
    assert z > 5.0, f"Expected large z for extreme deviation, got {z}"


# ---------------------------------------------------------------------------
# Shared FakeClient infrastructure (reused across all tool-use tests)
# ---------------------------------------------------------------------------

class _FakeToolCallFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id_: str, function: _FakeToolCallFunction) -> None:
        self.id = id_
        self.function = function


class _FakeMessage:
    def __init__(self, tool_calls=None, content=None) -> None:
        self.tool_calls = tool_calls
        self.content = content

    def model_dump(self, exclude_unset: bool = False) -> dict:
        d: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        return d


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


# ---------------------------------------------------------------------------
# token_context_agent tool-use tests
# ---------------------------------------------------------------------------

def _test_token_context_tool_use_calls_tool() -> None:
    """Gate fires (input_z < 3.0 AND chain_depth >= 2):
    agent calls get_call_chain before returning its answer."""
    call_log: list[tuple[str, dict]] = []
    calls = [0]

    class FakeClient:
        def __init__(self) -> None:
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()

        def _create(self, **kwargs: Any) -> _FakeResponse:
            calls[0] += 1
            if calls[0] == 1:
                tc = _FakeToolCall(
                    "tc_ctx1",
                    _FakeToolCallFunction(
                        "get_call_chain",
                        json.dumps({"call_id": "ctx_c1"}),
                    ),
                )
                return _FakeResponse(_FakeMessage(tool_calls=[tc]))
            return _FakeResponse(
                _FakeMessage(
                    content=json.dumps({
                        "agent_name": "token_context_agent",
                        "hypothesis": "context_bloat_self_calling_agent",
                        "confidence": 0.87,
                        "supporting_metrics": {"chain_depth_seen": 3},
                        "explanation": "Chain inspection confirmed recursive calls growing context.",
                    })
                )
            )

    def fake_tool_executor(name: str, args: dict) -> Any:
        call_log.append((name, args))
        # Simulate a 3-deep chain showing growing input_tokens
        return [
            {"call_id": "ctx_root", "parent_call_id": None, "input_tokens": 800, "output_tokens": 100, "cost_usd": 0.001},
            {"call_id": "ctx_c1",   "parent_call_id": "ctx_root", "input_tokens": 1600, "output_tokens": 120, "cost_usd": 0.002},
        ]

    result = call_agent_with_tools(
        client=FakeClient(),
        model="test-model",
        prompt="Test prompt",
        tools=TOKEN_CONTEXT_TOOLS,
        tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert len(call_log) == 1, f"Expected exactly one tool call, got {len(call_log)}"
    assert call_log[0] == ("get_call_chain", {"call_id": "ctx_c1"}), (
        f"Unexpected tool call: {call_log[0]}"
    )
    assert result.hypothesis == "context_bloat_self_calling_agent"
    assert result.confidence == 0.87


def _test_token_context_tool_use_skips_tool() -> None:
    """Gate does NOT fire (input_z >= 3.0 OR chain_depth < 2):
    agent answers directly without calling any tool."""

    class FakeClient:
        def __init__(self) -> None:
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()

        def _create(self, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(
                _FakeMessage(
                    content=json.dumps({
                        "agent_name": "token_context_agent",
                        "hypothesis": "context_bloat_self_calling_agent",
                        "confidence": 0.93,
                        "supporting_metrics": {"input_token_growth_pct": 420},
                        "explanation": "Aggregate signals alone were conclusive.",
                    })
                )
            )

    def fake_tool_executor(name: str, args: dict) -> Any:
        raise AssertionError("Tool should not have been called")

    result = call_agent_with_tools(
        client=FakeClient(),
        model="test-model",
        prompt="Test prompt",
        tools=TOKEN_CONTEXT_TOOLS,
        tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert result.confidence == 0.93
    assert result.hypothesis == "context_bloat_self_calling_agent"


# ---------------------------------------------------------------------------
# model_routing_agent tool-use tests
# ---------------------------------------------------------------------------

def _test_model_routing_tool_use_calls_tool() -> None:
    """Gate fires (model_changed=True AND cost_z < 3.0):
    agent calls get_window_calls with feature_tag before returning its answer."""
    call_log: list[tuple[str, dict]] = []
    calls = [0]

    class FakeClient:
        def __init__(self) -> None:
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()

        def _create(self, **kwargs: Any) -> _FakeResponse:
            calls[0] += 1
            if calls[0] == 1:
                tc = _FakeToolCall(
                    "tc_mr1",
                    _FakeToolCallFunction(
                        "get_window_calls",
                        json.dumps({"feature_tag": "summarizer"}),
                    ),
                )
                return _FakeResponse(_FakeMessage(tool_calls=[tc]))
            return _FakeResponse(
                _FakeMessage(
                    content=json.dumps({
                        "agent_name": "model_routing_agent",
                        "hypothesis": "expensive_model_misroute",
                        "confidence": 0.82,
                        "supporting_metrics": {"model_switch_confirmed": True},
                        "explanation": "Per-call data confirms shift to gpt-4.1 with higher cost.",
                    })
                )
            )

    def fake_tool_executor(name: str, args: dict) -> Any:
        # model_routing_agent passes feature_tag, NOT call_id — assert that here.
        assert name == "get_window_calls", f"Wrong tool name: {name}"
        assert "feature_tag" in args, f"Expected feature_tag in args, got: {args}"
        assert "call_id" not in args, f"Unexpected call_id in args: {args}"
        call_log.append((name, args))
        return [
            {"call_id": "mr1", "model": "gpt-4.1", "cost_usd": 0.05, "timestamp": "2024-01-01T01:00:00Z"},
            {"call_id": "mr2", "model": "gpt-4.1", "cost_usd": 0.06, "timestamp": "2024-01-01T00:55:00Z"},
        ]

    result = call_agent_with_tools(
        client=FakeClient(),
        model="test-model",
        prompt="Test prompt",
        tools=MODEL_ROUTING_TOOLS,
        tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert len(call_log) == 1, f"Expected exactly one tool call, got {len(call_log)}"
    assert call_log[0] == ("get_window_calls", {"feature_tag": "summarizer"}), (
        f"Unexpected tool call: {call_log[0]}"
    )
    assert result.hypothesis == "expensive_model_misroute"
    assert result.confidence == 0.82


def _test_model_routing_tool_use_skips_tool() -> None:
    """Gate does NOT fire (model_changed=False OR cost_z >= 3.0):
    agent answers directly without calling any tool."""

    class FakeClient:
        def __init__(self) -> None:
            comp = type("Completions", (), {"create": self._create})()
            self.chat = type("Chat", (), {"completions": comp})()

        def _create(self, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(
                _FakeMessage(
                    content=json.dumps({
                        "agent_name": "model_routing_agent",
                        "hypothesis": "expensive_model_misroute",
                        "confidence": 0.95,
                        "supporting_metrics": {"cost_growth_pct": 350},
                        "explanation": "Aggregate signals (model change + cost spike) conclusive.",
                    })
                )
            )

    def fake_tool_executor(name: str, args: dict) -> Any:
        raise AssertionError("Tool should not have been called")

    result = call_agent_with_tools(
        client=FakeClient(),
        model="test-model",
        prompt="Test prompt",
        tools=MODEL_ROUTING_TOOLS,
        tool_executor=fake_tool_executor,
        schema=AgentEvidence,
    )
    assert result.confidence == 0.95
    assert result.hypothesis == "expensive_model_misroute"


def main() -> int:
    success = True

    # 1. Run the scenario assertions with the PASS/FAIL runner
    scenarios = [
        ("retry_loop", _assert_retry_loop),
        ("context_bloat", _assert_context_bloat),
        ("model_misroute", _assert_model_misroute),
    ]

    for name, test_fn in scenarios:
        try:
            test_fn()
            print(f"{name} ... PASS")
        except AssertionError as exc:
            print(f"{name} ... FAIL")
            print(f"AssertionError: {exc}")
            success = False
        except Exception as exc:
            print(f"{name} ... FAIL")
            print(f"Error: {exc}")
            success = False

    # 2. Run unit tests (non-scenario tests)
    unit_tests = [
        ("validation_wrapper_and_repair", _test_validation_wrapper_and_repair),
        ("llm_provider_selection_and_live_client_path", _test_llm_provider_selection_and_live_client_path),
        ("retry_loop_tool_use_calls_tool", _test_retry_loop_tool_use_calls_tool),
        ("retry_loop_tool_use_skips_tool", _test_retry_loop_tool_use_skips_tool),
        ("z_score_behavior", _test_z_score_behavior),
        ("token_context_tool_use_calls_tool", _test_token_context_tool_use_calls_tool),
        ("token_context_tool_use_skips_tool", _test_token_context_tool_use_skips_tool),
        ("model_routing_tool_use_calls_tool", _test_model_routing_tool_use_calls_tool),
        ("model_routing_tool_use_skips_tool", _test_model_routing_tool_use_skips_tool),
    ]
    for name, test_fn in unit_tests:
        try:
            test_fn()
        except AssertionError as exc:
            print(f"Unit test {name} failed: {exc}")
            success = False
        except Exception as exc:
            print(f"Unit test {name} failed: {exc}")
            success = False

    if success:
        print("Replay detector/router/agent/aggregator/repair tests passed successfully!")
        return 0
    else:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
