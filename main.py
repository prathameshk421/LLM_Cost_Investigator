"""CLI entrypoint for scenario simulation and investigation."""

from __future__ import annotations

import argparse
import json

from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import (
    generate_all_scenario_calls,
    generate_scenario_calls,
)


from llm_cost_investigator.agents import run_agents
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.reporter import write_report
from llm_cost_investigator.schemas import IncidentReport


RECOMMENDATIONS = {
    "uncapped_retry_loop": [
        "Implement exponential backoff with jitter.",
        "Cap the maximum retry count in the client configuration.",
        "Add a circuit breaker pattern to prevent continuous retries during outages.",
    ],
    "context_bloat_self_calling_agent": [
        "Limit maximum reflection / chain depth to a safe threshold (e.g., 3).",
        "Implement prompt summarization or token truncation for history.",
        "Add a fail-safe budget check on the chain to terminate expanding contexts.",
    ],
    "expensive_model_misroute": [
        "Revert default model routing configurations to more cost-effective models.",
        "Add CI/CD checks to prevent unintentional model upgrades.",
        "Implement rate limiting or budget alerts for premium model endpoints.",
    ],
    "no_strong_signal": [
        "Monitor the feature's baseline metrics for continued anomalies.",
    ],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LLM cost investigator demo.")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["retry_loop", "context_bloat", "model_misroute", "all"],
        help="Scenario to generate and investigate.",
    )
    return parser


def process_scenario(scenario: str) -> None:
    calls = generate_scenario_calls(scenario)
    anomalies = detect_anomalies(calls)
    print(f"\nScenario: {scenario}")
    print(f"Detected anomalies: {len(anomalies)}")
    if anomalies:
        top = anomalies[0]
        selected_agents = route_agents(top)
        print(f"Routed agents: {', '.join(selected_agents)}")
        
        # Run agents
        evidence = run_agents(selected_agents, top)
        for ev in evidence:
            print(f"  Agent {ev.agent_name} hypothesis: {ev.hypothesis} (confidence: {ev.confidence:.2f})")
            
        # Select root cause
        root_cause = select_root_cause(evidence)
        print(f"Aggregated root cause: {root_cause.hypothesis} (confidence: {root_cause.confidence:.2f})")
        
        # Write report
        recommendations = RECOMMENDATIONS.get(root_cause.hypothesis, ["Monitor baseline metrics."])
        report = IncidentReport(
            scenario=scenario,
            root_cause=root_cause,
            anomaly_window=top,
            recommendations=recommendations,
        )
        write_report(report)
        print(f"Report written to reports/{scenario}_report.json")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    scenarios = ["retry_loop", "context_bloat", "model_misroute"] if args.scenario == "all" else [args.scenario]
    
    for scenario in scenarios:
        process_scenario(scenario)
        
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
