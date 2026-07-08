"""CLI entrypoint for scenario simulation and investigation."""

from __future__ import annotations

import argparse
import dataclasses

from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls
from llm_cost_investigator.telemetry_store import TelemetryStore

from llm_cost_investigator.agents import run_agents_detailed
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.reporter import write_report
from llm_cost_investigator.schemas import AnomalyWindow, IncidentReport


# ---------------------------------------------------------------------------
# Formatting constants
# ---------------------------------------------------------------------------

LABEL_WIDTH = 18  # width of the left label column, sized to longest label
PASS_CONFIDENCE_THRESHOLD = 0.70

# ---------------------------------------------------------------------------
# Recommendations map
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ScenarioResult dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ScenarioResult:
    """Outcome of one scenario run, used for compact display and table."""

    scenario: str
    anomaly_description: str   # e.g. "summarizer model change"
    routed_agents: list[str]   # e.g. ["model_routing_agent"]
    root_cause: str            # hypothesis string, or "none"
    confidence: float          # 0.0 when no anomaly detected
    report_path: str           # relative path, or "n/a"
    passed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _describe_anomaly(window: AnomalyWindow) -> str:
    """Derive a short human-readable description of the flagged anomaly.

    Priority order: most specific signal wins.
    """
    signals = window.signals
    if signals.model_changed:
        old = signals.model_before or "?"
        new = signals.model_during or "?"
        return f"{window.feature_tag} model change ({old} → {new})"
    if signals.retry_z_score >= 3.0 or signals.max_retry_count >= 3:
        return f"{window.feature_tag} retry loop (max_retry_count={signals.max_retry_count})"
    if signals.max_call_chain_depth >= 2:
        return f"{window.feature_tag} context bloat (chain_depth={signals.max_call_chain_depth})"
    return f"{window.feature_tag} cost spike"


def _print_scenario_block(result: ScenarioResult) -> None:
    """Print the compact fixed-width block for one scenario."""
    label = 17

    def row(name: str, value: str) -> None:
        print(f"{name:<{label}} {value}")

    print()
    row("Scenario:", result.scenario)
    row("Detected anomaly:", result.anomaly_description)
    
    routed_str = ", ".join(result.routed_agents) if result.routed_agents else "none"
    row("Routed agents:", routed_str)
    row("Root cause:", result.root_cause)
    row("Confidence:", f"{result.confidence:.2f}")
    row("Report:", result.report_path)
    row("Result:", "PASS" if result.passed else "FAIL")


def _print_summary_table(results: list[ScenarioResult]) -> None:
    """Print the --scenario all summary table."""
    col_scenario = 15
    col_root = 34
    col_result = 6
    sep = "─" * (col_scenario + col_root + col_result + 2)

    print()
    print("Summary")
    print(sep)
    print(f"{'Scenario':<{col_scenario}} {'Root cause':<{col_root}} {'Result':<{col_result}}")
    print(sep)
    for r in results:
        print(
            f"{r.scenario:<{col_scenario}} "
            f"{r.root_cause:<{col_root}} "
            f"{'PASS' if r.passed else 'FAIL':<{col_result}}"
        )
    print(sep)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LLM cost investigator demo.")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["retry_loop", "context_bloat", "model_misroute", "all"],
        help="Scenario to generate and investigate.",
    )
    parser.add_argument(
        "--provider",
        choices=["groq", "cerebras", "cerebus", "fallback"],
        default=None,
        help="LLM provider for diagnostic agents. Defaults to env auto-detect.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the provider model. Also configurable with LLM_MODEL.",
    )
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        help="Use deterministic fallback evidence without live LLM calls.",
    )
    return parser


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def process_scenario(
    scenario: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ScenarioResult:
    """Run the full investigation pipeline for one scenario and return its result."""
    calls = generate_scenario_calls(scenario)
    store = TelemetryStore(":memory:")
    store.insert_calls(calls)
    cursor = store._conn.execute(
        "SELECT * FROM llm_calls ORDER BY timestamp ASC, call_id ASC"
    )
    db_calls = [store._row_to_call(row) for row in cursor]
    anomalies = detect_anomalies(db_calls)

    if not anomalies:
        result = ScenarioResult(
            scenario=scenario,
            anomaly_description="none",
            routed_agents=[],
            root_cause="no_strong_signal",
            confidence=0.0,
            report_path="n/a",
            passed=False,
        )
        _print_scenario_block(result)
        return result

    top = anomalies[0]
    anomaly_description = _describe_anomaly(top)
    selected_agents = route_agents(top)

    # Run agents
    agent_runs = run_agents_detailed(
        selected_agents,
        top,
        provider=provider,
        model=model,
    )
    evidence = [run.evidence for run in agent_runs]

    # Select root cause
    root_cause = select_root_cause(evidence)

    # Write report (suppress the full terminal summary box — CLI owns presentation)
    try:
        recommendations = RECOMMENDATIONS.get(root_cause.hypothesis, ["Monitor baseline metrics."])
        report = IncidentReport(
            scenario=scenario,
            root_cause=root_cause,
            anomaly_window=top,
            agent_runs=agent_runs,
            recommendations=recommendations,
        )
        md_path, _ = write_report(report, print_summary=False)
        report_path = str(md_path)
        report_write_ok = True
    except Exception:
        report_path = "n/a"
        report_write_ok = False

    # Derive PASS/FAIL
    # PASS condition: anomaly detected, root cause is not no_strong_signal, confidence meets threshold (0.70), and report files were written.
    passed = (
        root_cause.hypothesis != "no_strong_signal"
        and root_cause.confidence >= PASS_CONFIDENCE_THRESHOLD
        and report_write_ok
    )

    result = ScenarioResult(
        scenario=scenario,
        anomaly_description=anomaly_description,
        routed_agents=selected_agents,
        root_cause=root_cause.hypothesis,
        confidence=root_cause.confidence,
        report_path=report_path,
        passed=passed,
    )
    _print_scenario_block(result)
    return result


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    scenarios = (
        ["retry_loop", "context_bloat", "model_misroute"]
        if args.scenario == "all"
        else [args.scenario]
    )
    provider = "fallback" if args.force_fallback else args.provider

    results = [
        process_scenario(scenario, provider=provider, model=args.model)
        for scenario in scenarios
    ]

    if args.scenario == "all":
        _print_summary_table(results)

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
