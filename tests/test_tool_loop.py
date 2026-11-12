"""Tests for the tool-call loop state machine (call_agent_with_tools)."""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from llm_cost_investigator.agents import (
    RETRY_LOOP_TOOLS,
    call_agent_with_tools,
    run_model_routing_agent_with_tools,
    run_retry_loop_agent_with_tools,
    run_token_context_agent_with_tools,
)
from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.schemas import AgentEvidence
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls
from llm_cost_investigator.telemetry_store import TelemetryStore


# ---------------------------------------------------------------------------
# Fake SDK client
# ---------------------------------------------------------------------------

class _Function:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id_: str, name: str, arguments: str) -> None:
        self.id = id_
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, tool_calls: list[_ToolCall] | None = None, content: str | None = None) -> None:
        self.tool_calls = tool_calls
        self.content = content

    def model_dump(self, exclude_unset: bool = False) -> dict:
        dumped: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            dumped["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        return dumped


class _Choice:
    def __init__(self, message: _Message) -> None:
        self.message = message


class _Response:
    def __init__(self, message: _Message) -> None:
        self.choices = [_Choice(message)]


class ScriptedClient:
    """`script` maps (call_number, request kwargs) -> _Message."""

    def __init__(self, script: Callable[[int, dict], _Message]) -> None:
        self.requests: list[dict] = []
        self._script = script
        completions = type("Completions", (), {"create": self._create})()
        self.chat = type("Chat", (), {"completions": completions})()

    def _create(self, **kwargs: Any) -> _Response:
        # Copy the messages: the loop mutates the same list after this call.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return _Response(self._script(len(self.requests), kwargs))


def _evidence(agent_name: str = "retry_loop_agent", hypothesis: str = "uncapped_retry_loop") -> str:
    return json.dumps({
        "agent_name": agent_name,
        "hypothesis": hypothesis,
        "confidence": 0.8,
        "supporting_metrics": {},
        "explanation": "Test evidence.",
    })


def _chain_call(id_: str, arguments: str = '{"call_id": "c1"}') -> _ToolCall:
    return _ToolCall(id_, "get_call_chain", arguments)


def _tool_messages(request: dict) -> list[dict]:
    return [m for m in request["messages"] if m.get("role") == "tool"]


def _run_loop(client: ScriptedClient, tool_executor: Callable[[str, dict], Any], **kwargs: Any) -> AgentEvidence:
    return call_agent_with_tools(
        client=client,
        model="test-model",
        prompt="Test prompt",
        tools=RETRY_LOOP_TOOLS,
        tool_executor=tool_executor,
        schema=AgentEvidence,
        **kwargs,
    )


def _must_not_run(name: str, args: dict) -> Any:
    raise AssertionError("The tool must not be executed")


# ---------------------------------------------------------------------------
# Budget, tool errors, repair
# ---------------------------------------------------------------------------

def test_budget_counts_tool_executions_and_forces_answer() -> None:
    executed: list[dict] = []

    def script(n: int, kwargs: dict) -> _Message:
        if "tools" in kwargs:
            # The model always asks for two parallel tool calls.
            return _Message(tool_calls=[_chain_call(f"tc_{n}_a"), _chain_call(f"tc_{n}_b")])
        return _Message(content=_evidence())

    client = ScriptedClient(script)
    result = _run_loop(client, lambda name, args: executed.append(args) or [], max_tool_calls=3)

    assert len(executed) == 3
    assert len(client.requests) == 3
    final = client.requests[-1]
    assert "tools" not in final
    assert "budget is used" in final["messages"][-1]["content"]
    # Each of the 4 requested tool calls has a tool message (API contract).
    assert len(_tool_messages(final)) == 4
    assert "Not executed" in _tool_messages(final)[-1]["content"]
    assert result.hypothesis == "uncapped_retry_loop"


def test_tool_errors_go_back_to_the_model() -> None:
    def script(n: int, kwargs: dict) -> _Message:
        if n == 1:
            return _Message(tool_calls=[_chain_call("tc_bad_json", "{not json"), _chain_call("tc_raises")])
        return _Message(content=_evidence())

    def failing_executor(name: str, args: dict) -> Any:
        raise KeyError("call_id")

    client = ScriptedClient(script)
    result = _run_loop(client, failing_executor)

    tool_msgs = _tool_messages(client.requests[-1])
    assert len(tool_msgs) == 2
    assert "not valid JSON" in tool_msgs[0]["content"]
    assert "KeyError" in tool_msgs[1]["content"]
    assert result.confidence == 0.8


def test_invalid_json_gets_one_repair_then_raises() -> None:
    client = ScriptedClient(lambda n, kwargs: _Message(content="not json"))
    with pytest.raises(ValueError, match="after repair"):
        _run_loop(client, lambda name, args: [])
    assert len(client.requests) == 2
    assert "Invalid response:\nnot json" in client.requests[-1]["messages"][-1]["content"]


def test_good_repair_returns_evidence() -> None:
    client = ScriptedClient(lambda n, kwargs: _Message(content="oops" if n == 1 else _evidence()))
    assert _run_loop(client, lambda name, args: []).hypothesis == "uncapped_retry_loop"
    assert len(client.requests) == 2


# ---------------------------------------------------------------------------
# Tool gate enforcement
# ---------------------------------------------------------------------------

def test_required_policy_reminds_once_when_the_tool_is_skipped() -> None:
    executed: list[dict] = []

    def script(n: int, kwargs: dict) -> _Message:
        if n == 1:
            return _Message(content=_evidence())  # skips the required tool
        if n == 2:
            return _Message(tool_calls=[_chain_call("tc_1")])
        return _Message(content=_evidence())

    client = ScriptedClient(script)
    result = _run_loop(client, lambda name, args: executed.append(args) or [], tool_policy="required")

    assert len(client.requests) == 3
    assert "requires a tool call" in client.requests[1]["messages"][-1]["content"]
    assert all("tool_choice" not in request for request in client.requests)
    assert len(executed) == 1
    assert result.hypothesis == "uncapped_retry_loop"


def test_required_policy_raises_when_the_reminder_is_ignored() -> None:
    client = ScriptedClient(lambda n, kwargs: _Message(content=_evidence()))
    with pytest.raises(ValueError, match="required a tool call"):
        _run_loop(client, _must_not_run, tool_policy="required")
    assert len(client.requests) == 2


def test_forbidden_policy_executes_no_tool_call() -> None:
    def script(n: int, kwargs: dict) -> _Message:
        if n == 1:
            return _Message(tool_calls=[_chain_call("tc_1")])
        return _Message(content=_evidence())

    client = ScriptedClient(script)
    result = _run_loop(client, _must_not_run, tool_policy="forbidden")

    assert len(client.requests) == 2
    final = client.requests[-1]
    assert "tools" not in final
    assert "not allowed" in final["messages"][-1]["content"]
    assert len(_tool_messages(final)) == 1
    assert "Not executed" in _tool_messages(final)[0]["content"]
    assert result.hypothesis == "uncapped_retry_loop"


def test_no_policy_lets_the_model_decide() -> None:
    client = ScriptedClient(lambda n, kwargs: _Message(content=_evidence()))
    assert _run_loop(client, _must_not_run).hypothesis == "uncapped_retry_loop"
    assert len(client.requests) == 1


# ---------------------------------------------------------------------------
# Each agent passes its own gate to the loop
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def retry_scenario():
    calls = generate_scenario_calls("retry_loop")
    store = TelemetryStore(":memory:")
    store.insert_calls(calls)
    yield detect_anomalies(calls)[0], store
    store.close()


# (runner, agent, hypothesis, tool name, signals: gate fires, signals: gate does not fire)
_AGENT_GATES = [
    (
        run_retry_loop_agent_with_tools, "retry_loop_agent", "uncapped_retry_loop", "get_call_chain",
        {"retry_z_score": 1.0, "repeated_parent_call_count": 1},
        {"retry_z_score": 6.0, "repeated_parent_call_count": 1},
    ),
    (
        run_token_context_agent_with_tools, "token_context_agent",
        "context_bloat_self_calling_agent", "get_call_chain",
        {"input_tokens_z_score": 1.0, "max_call_chain_depth": 3},
        {"input_tokens_z_score": 5.0, "max_call_chain_depth": 3},
    ),
    (
        run_model_routing_agent_with_tools, "model_routing_agent",
        "expensive_model_misroute", "get_window_calls",
        {"model_changed": True, "cost_z_score": 1.0},
        {"model_changed": False, "cost_z_score": 1.0},
    ),
]
_AGENT_IDS = [case[1] for case in _AGENT_GATES]


def _with_signals(anomaly, update: dict):
    return anomaly.model_copy(update={"signals": anomaly.signals.model_copy(update=update)})


def _agent_script(anomaly, agent_name: str, hypothesis: str, tool_name: str, *, tool_first: bool):
    args = (
        {"call_id": anomaly.sample_calls[0].call_id}
        if tool_name == "get_call_chain"
        else {"feature_tag": anomaly.feature_tag}
    )

    def script(n: int, kwargs: dict) -> _Message:
        if tool_first and n == 1:
            return _Message(tool_calls=[_ToolCall("tc_1", tool_name, json.dumps(args))])
        return _Message(content=_evidence(agent_name, hypothesis))

    return script


@pytest.mark.parametrize("runner, agent_name, hypothesis, tool_name, fires, quiet", _AGENT_GATES, ids=_AGENT_IDS)
def test_agent_runs_the_tool_when_its_gate_fires(
    retry_scenario, runner, agent_name, hypothesis, tool_name, fires, quiet
) -> None:
    base, store = retry_scenario
    anomaly = _with_signals(base, fires)
    client = ScriptedClient(_agent_script(anomaly, agent_name, hypothesis, tool_name, tool_first=True))

    run = runner(anomaly=anomaly, store=store, client=client, model="m", provider="groq")

    assert not run.fallback_used, run.fallback_reason
    tool_msg = client.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "error" not in tool_msg["content"]


@pytest.mark.parametrize("runner, agent_name, hypothesis, tool_name, fires, quiet", _AGENT_GATES, ids=_AGENT_IDS)
def test_agent_refuses_the_tool_when_its_gate_does_not_fire(
    retry_scenario, runner, agent_name, hypothesis, tool_name, fires, quiet
) -> None:
    base, store = retry_scenario
    anomaly = _with_signals(base, quiet)
    client = ScriptedClient(_agent_script(anomaly, agent_name, hypothesis, tool_name, tool_first=True))

    run = runner(anomaly=anomaly, store=store, client=client, model="m", provider="groq")

    assert not run.fallback_used, run.fallback_reason
    assert "Not executed" in _tool_messages(client.requests[1])[0]["content"]


@pytest.mark.parametrize("runner, agent_name, hypothesis, tool_name, fires, quiet", _AGENT_GATES, ids=_AGENT_IDS)
def test_agent_falls_back_when_the_model_skips_a_required_tool(
    retry_scenario, runner, agent_name, hypothesis, tool_name, fires, quiet
) -> None:
    base, store = retry_scenario
    anomaly = _with_signals(base, fires)
    client = ScriptedClient(_agent_script(anomaly, agent_name, hypothesis, tool_name, tool_first=False))

    run = runner(anomaly=anomaly, store=store, client=client, model="m", provider="groq")

    assert run.fallback_used
    assert "required a tool call" in (run.fallback_reason or "")
    assert len(client.requests) == 2
