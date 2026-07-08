"""Replay test entrypoint for labeled anomaly scenarios."""

from __future__ import annotations

import json

from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls
from llm_cost_investigator.agents import (
    call_agent_with_validation,
    run_agents,
    run_agents_detailed,
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
    assert ev.confidence >= 0.90  # retry z-score >= 5 and max retry count >= 5 floor
    
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
    assert ev.confidence == 0.94


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
