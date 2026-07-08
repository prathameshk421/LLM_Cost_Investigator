"""Replay test entrypoint for labeled anomaly scenarios."""

from __future__ import annotations

import json
from typing import Any

from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls
from llm_cost_investigator.agents import run_agents, call_agent_with_validation
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.schemas import AgentEvidence, RootCauseResult


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


def _assert_retry_loop() -> None:
    anomaly = _top_anomaly_for_scenario("retry_loop")
    assert anomaly.signals.max_retry_count >= 5
    assert anomaly.signals.repeated_parent_call_count > 0
    assert anomaly.signals.model_changed is False
    
    routed = route_agents(anomaly)
    assert routed == ["retry_loop_agent"]
    
    # Run the agents & aggregator
    evidence = run_agents(routed, anomaly)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.agent_name == "retry_loop_agent"
    assert ev.hypothesis == "uncapped_retry_loop"
    assert ev.confidence >= 0.90  # retry z-score >= 5 and max retry count >= 5 floor
    
    root_cause = select_root_cause(evidence)
    assert root_cause.hypothesis == "uncapped_retry_loop"
    assert root_cause.winning_agent == "retry_loop_agent"
    assert root_cause.confidence == ev.confidence


def _assert_context_bloat() -> None:
    anomaly = _top_anomaly_for_scenario("context_bloat")
    assert anomaly.signals.input_token_growth_pct is not None
    assert anomaly.signals.input_token_growth_pct >= 100
    assert anomaly.signals.max_call_chain_depth >= 4
    assert anomaly.signals.model_changed is False
    
    routed = route_agents(anomaly)
    assert routed == ["token_context_agent"]
    
    # Run the agents & aggregator
    evidence = run_agents(routed, anomaly)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.agent_name == "token_context_agent"
    assert ev.hypothesis == "context_bloat_self_calling_agent"
    assert ev.confidence >= 0.75  # input tokens z-score >= 3 and chain depth >= 4 floor
    
    root_cause = select_root_cause(evidence)
    assert root_cause.hypothesis == "context_bloat_self_calling_agent"
    assert root_cause.winning_agent == "token_context_agent"
    assert root_cause.confidence == ev.confidence


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
    assert routed == ["model_routing_agent"]
    
    # Run the agents & aggregator
    evidence = run_agents(routed, anomaly)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.agent_name == "model_routing_agent"
    assert ev.hypothesis == "expensive_model_misroute"
    assert ev.confidence >= 0.90  # model changed, cost growth >= 200%, token growth < 50% floor
    
    root_cause = select_root_cause(evidence)
    assert root_cause.hypothesis == "expensive_model_misroute"
    assert root_cause.winning_agent == "model_routing_agent"
    assert root_cause.confidence == ev.confidence


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


def main() -> int:
    _assert_retry_loop()
    _assert_context_bloat()
    _assert_model_misroute()
    _test_validation_wrapper_and_repair()
    print("Replay detector/router/agent/aggregator/repair tests passed successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
