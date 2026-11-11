"""CLI entrypoint for scenario simulation and investigation."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from llm_cost_investigator.connectors import (
    DEFAULT_REMOTE_MAX_ROWS,
    ConnectorError,
    PullRequest,
    list_profile_summaries,
    pull_calls,
)
from llm_cost_investigator.ingest import IngestOptions, load_telemetry
from llm_cost_investigator.investigate import (
    RECOMMENDATIONS,
    describe_anomaly,
    investigate_calls,
)
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls

# Re-export for any external importers
__all__ = [
    "RECOMMENDATIONS",
    "ScenarioResult",
    "build_parser",
    "main",
    "process_scenario",
]

# ---------------------------------------------------------------------------
# Formatting constants
# ---------------------------------------------------------------------------

PASS_CONFIDENCE_THRESHOLD = 0.70


@dataclasses.dataclass
class ScenarioResult:
    """Outcome of one scenario run, used for compact display and table."""

    scenario: str
    anomaly_description: str
    routed_agents: list[str]
    root_cause: str
    confidence: float
    report_path: str
    passed: bool


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


EXPECTED_ROOT_CAUSE: dict[str, str] = {
    "retry_loop": "uncapped_retry_loop",
    "context_bloat": "context_bloat_self_calling_agent",
    "model_misroute": "expensive_model_misroute",
}


def process_scenario(
    scenario: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ScenarioResult:
    """Run the full investigation pipeline for one simulated scenario."""
    calls = generate_scenario_calls(scenario)
    result = investigate_calls(
        calls,
        label=scenario,
        provider=provider,
        model=model,
        max_anomalies=1,
        write_reports=True,
        report_naming="label",
    )

    if not result.incidents:
        scenario_result = ScenarioResult(
            scenario=scenario,
            anomaly_description="none",
            routed_agents=[],
            root_cause="no_strong_signal",
            confidence=0.0,
            report_path="n/a",
            passed=False,
        )
        _print_scenario_block(scenario_result)
        return scenario_result

    report = result.incidents[0]
    report_path = result.report_paths[0] if result.report_paths else "n/a"
    report_write_ok = report_path != "n/a"
    selected_agents = route_agents(report.anomaly_window)
    # PASS needs the *right* root cause, not just any confident one.
    passed = (
        report.root_cause.hypothesis == EXPECTED_ROOT_CAUSE.get(scenario)
        and report.root_cause.confidence >= PASS_CONFIDENCE_THRESHOLD
        and report_write_ok
    )
    scenario_result = ScenarioResult(
        scenario=scenario,
        anomaly_description=describe_anomaly(report.anomaly_window),
        routed_agents=selected_agents,
        root_cause=report.root_cause.hypothesis,
        confidence=report.root_cause.confidence,
        report_path=report_path,
        passed=passed,
    )
    _print_scenario_block(scenario_result)
    return scenario_result


def _parse_map_arg(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"Invalid --map entry {part!r}; expected key=column")
        key, col = part.split("=", 1)
        mapping[key.strip()] = col.strip()
    return mapping or None


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["groq", "cerebras", "cerebus", "hf", "fallback"],
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


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser supporting legacy --scenario and investigate subcommand."""
    parser = argparse.ArgumentParser(
        description="LLM cost investigator — demo scenarios or ingest real telemetry.",
    )
    sub = parser.add_subparsers(dest="command")

    # --- demo subcommand (optional; legacy flags also work) ---
    demo = sub.add_parser("demo", help="Run labeled simulated scenarios.")
    demo.add_argument(
        "--scenario",
        required=True,
        choices=["retry_loop", "context_bloat", "model_misroute", "all"],
        help="Scenario to generate and investigate.",
    )
    _add_provider_args(demo)

    # --- investigate subcommand ---
    inv = sub.add_parser(
        "investigate",
        help="Ingest external telemetry (file or remote connector) and investigate.",
    )
    inv.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to telemetry file (mutually exclusive with --connection/--connector).",
    )
    inv.add_argument(
        "--connection",
        default=None,
        help="Named connection profile from connections.yaml.",
    )
    inv.add_argument(
        "--connector",
        choices=["langfuse"],
        default=None,
        help="Remote connector type (uses env credentials).",
    )
    inv.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Remote pull window in hours (default: 24 or profile default).",
    )
    inv.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Max rows to accept (remote default 10000; file default 100000).",
    )
    inv.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to connections.yaml (optional).",
    )
    inv.add_argument(
        "--adapter",
        choices=["auto", "jsonl", "openai", "csv"],
        default="auto",
        help="File ingest adapter (default: auto-detect).",
    )
    inv.add_argument(
        "--map",
        dest="column_map",
        default=None,
        help="CSV mapping logical=column pairs, comma-separated.",
    )
    inv.add_argument(
        "--feature-default",
        default=None,
        help="Default feature_tag when missing from rows.",
    )
    inv.add_argument(
        "--label",
        default=None,
        help="Report slug prefix (default: input stem or connector name).",
    )
    inv.add_argument(
        "--max-anomalies",
        type=int,
        default=5,
        help="Max anomaly windows to investigate (default: 5).",
    )
    inv.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing incident report files.",
    )
    inv.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full InvestigationResult JSON to this path.",
    )
    _add_provider_args(inv)

    # --- pull subcommand ---
    pull = sub.add_parser(
        "pull",
        help="Pull remote telemetry to canonical JSONL without investigating.",
    )
    pull.add_argument("--connection", default=None, help="Named connection profile.")
    pull.add_argument(
        "--connector",
        choices=["langfuse"],
        default=None,
        help="Remote connector type.",
    )
    pull.add_argument("--since-hours", type=float, default=None)
    pull.add_argument("--max-rows", type=int, default=None)
    pull.add_argument("--feature-default", default=None)
    pull.add_argument("--config", type=Path, default=None)
    pull.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output JSONL path.",
    )

    # --- connections subcommand ---
    conn = sub.add_parser("connections", help="List or test connection profiles.")
    conn_sub = conn.add_subparsers(dest="connections_cmd")
    conn_list = conn_sub.add_parser("list", help="List named profiles.")
    conn_list.add_argument("--config", type=Path, default=None)
    conn_test = conn_sub.add_parser("test", help="Test auth and fetch one page.")
    conn_test.add_argument("--connection", default=None)
    conn_test.add_argument("--connector", choices=["langfuse"], default=None)
    conn_test.add_argument("--since-hours", type=float, default=1.0)
    conn_test.add_argument("--config", type=Path, default=None)

    # --- legacy top-level flags (no subcommand) ---
    parser.add_argument(
        "--scenario",
        choices=["retry_loop", "context_bloat", "model_misroute", "all"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--provider",
        choices=["groq", "cerebras", "cerebus", "hf", "fallback"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--model", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    return parser


def _run_demo(
    scenario: str,
    *,
    provider: str | None,
    model: str | None,
    force_fallback: bool,
) -> int:
    scenarios = (
        ["retry_loop", "context_bloat", "model_misroute"]
        if scenario == "all"
        else [scenario]
    )
    resolved_provider = "fallback" if force_fallback else provider
    results = [
        process_scenario(s, provider=resolved_provider, model=model)
        for s in scenarios
    ]
    if scenario == "all":
        _print_summary_table(results)
    return 0 if all(r.passed for r in results) else 1


def _print_ingest_report(ingest, *, pull_meta: dict | None = None) -> None:
    print()
    print("Ingest")
    print(f"  adapter:            {ingest.adapter}")
    print(f"  accepted:           {ingest.accepted}")
    print(f"  dropped:            {ingest.dropped}")
    print(f"  cost_estimated:     {ingest.cost_estimated_count}")
    if pull_meta:
        print(f"  host:               {pull_meta.get('host')}")
        print(f"  window:             {pull_meta.get('from_time')} → {pull_meta.get('to_time')}")
        print(f"  pages:              {pull_meta.get('pages_fetched')}")
    if ingest.warnings:
        print(f"  warnings ({len(ingest.warnings)}):")
        for w in ingest.warnings[:15]:
            print(f"    - {w}")
        if len(ingest.warnings) > 15:
            print(f"    ... +{len(ingest.warnings) - 15} more")
    if ingest.sample_dropped:
        print("  sample dropped rows:")
        for d in ingest.sample_dropped[:5]:
            print(f"    - row {d.get('row_index')}: {d.get('reason')}")


def _run_investigate(args: argparse.Namespace) -> int:
    sources = [
        bool(args.input),
        bool(getattr(args, "connection", None)),
        bool(getattr(args, "connector", None)),
    ]
    if sum(sources) != 1:
        print(
            "Provide exactly one of --input, --connection, or --connector.",
            file=sys.stderr,
        )
        return 2

    provider = "fallback" if args.force_fallback else args.provider
    pull_meta: dict | None = None

    if args.input:
        input_path: Path = args.input
        if not input_path.is_file():
            print(f"Input not found: {input_path}", file=sys.stderr)
            return 1
        label = args.label or input_path.stem
        mapping = _parse_map_arg(args.column_map)
        options = IngestOptions(
            feature_default=args.feature_default,
            column_mapping=mapping,
            source_name=input_path.name,
            max_rows=int(args.max_rows) if args.max_rows is not None else 100_000,
        )
        try:
            calls, ingest = load_telemetry(
                input_path,
                adapter=args.adapter,
                options=options,
                filename=input_path.name,
            )
        except Exception as exc:
            print(f"Ingest failed: {exc}", file=sys.stderr)
            return 1
    else:
        label = args.label or (
            f"langfuse_{args.connection}" if args.connection else "langfuse"
        )
        # Match pull: only pass PullRequest when overrides are set; else profile defaults.
        if (
            args.since_hours is not None
            or args.max_rows is not None
            or args.feature_default is not None
        ):
            request = PullRequest(
                since_hours=float(args.since_hours)
                if args.since_hours is not None
                else 24.0,
                max_rows=int(args.max_rows)
                if args.max_rows is not None
                else DEFAULT_REMOTE_MAX_ROWS,
                feature_default=args.feature_default,
            )
        else:
            request = None
        try:
            calls, ingest, pull_meta = pull_calls(
                connection=args.connection,
                connector=args.connector,
                request=request,
                config_path=args.config,
            )
        except ConnectorError as exc:
            print(f"Pull failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Pull failed: {exc}", file=sys.stderr)
            return 1

    _print_ingest_report(ingest, pull_meta=pull_meta)

    if ingest.accepted == 0:
        print("No accepted rows; nothing to investigate.", file=sys.stderr)
        return 1

    try:
        result = investigate_calls(
            calls,
            label=label,
            provider=provider,
            model=args.model,
            max_anomalies=max(1, args.max_anomalies),
            write_reports=not args.no_write,
            report_naming="label_feature_index",
        )
    except Exception as exc:
        print(f"Investigation failed: {exc}", file=sys.stderr)
        return 1

    print()
    print("Investigation")
    print(f"  label:              {result.label}")
    print(f"  calls:              {result.call_count}")
    print(f"  features:           {', '.join(result.feature_tags) or '(none)'}")
    print(f"  anomalies found:    {result.anomaly_count}")
    print(f"  incidents written:  {len(result.incidents)}")

    if not result.incidents:
        print()
        print(result.no_anomaly_reason or "No cost anomalies detected.")
    else:
        for i, report in enumerate(result.incidents):
            path = result.report_paths[i] if i < len(result.report_paths) else "n/a"
            agents = route_agents(report.anomaly_window)
            block = ScenarioResult(
                scenario=report.scenario,
                anomaly_description=describe_anomaly(report.anomaly_window),
                routed_agents=agents,
                root_cause=report.root_cause.hypothesis,
                confidence=report.root_cause.confidence,
                report_path=path,
                passed=report.root_cause.hypothesis != "no_strong_signal",
            )
            _print_scenario_block(block)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ingest": ingest.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        if pull_meta:
            payload["pull_meta"] = pull_meta
        args.json_out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON written: {args.json_out}")

    return 0


def _run_pull(args: argparse.Namespace) -> int:
    if bool(args.connection) == bool(args.connector):
        if args.connection and args.connector:
            print("Provide only one of --connection or --connector.", file=sys.stderr)
            return 2
        print("Provide --connection or --connector.", file=sys.stderr)
        return 2

    if args.since_hours is not None or args.max_rows is not None or args.feature_default is not None:
        request = PullRequest(
            since_hours=float(args.since_hours) if args.since_hours is not None else 24.0,
            max_rows=int(args.max_rows)
            if args.max_rows is not None
            else DEFAULT_REMOTE_MAX_ROWS,
            feature_default=args.feature_default,
        )
    else:
        request = None

    try:
        calls, ingest, pull_meta = pull_calls(
            connection=args.connection,
            connector=args.connector,
            request=request,
            config_path=args.config,
        )
    except ConnectorError as exc:
        print(f"Pull failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Pull failed: {exc}", file=sys.stderr)
        return 1

    _print_ingest_report(ingest, pull_meta=pull_meta)
    if ingest.accepted == 0:
        print("No accepted rows to write.", file=sys.stderr)
        return 1

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.model_dump(mode="json"), default=str) for c in calls]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(calls)} calls → {out}")
    return 0


def _run_connections(args: argparse.Namespace) -> int:
    cmd = getattr(args, "connections_cmd", None)
    if cmd == "list":
        try:
            profiles = list_profile_summaries(getattr(args, "config", None))
        except ConnectorError as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            return 1
        if not profiles:
            print("No connection profiles found.")
            print("Create .llmci/connections.yaml (see docs/connectors/connections.example.yaml)")
            return 0
        for p in profiles:
            host = p.get("host") or "(env LANGFUSE_HOST / default cloud)"
            print(f"{p['name']:<24} connector={p['connector']:<10} host={host}")
        return 0

    if cmd == "test":
        if bool(args.connection) == bool(args.connector):
            if not args.connection and not args.connector:
                print("Provide --connection or --connector.", file=sys.stderr)
                return 2
            if args.connection and args.connector:
                print("Provide only one of --connection or --connector.", file=sys.stderr)
                return 2
        request = PullRequest(
            since_hours=float(args.since_hours or 1.0),
            max_rows=50,
        )
        try:
            calls, ingest, pull_meta = pull_calls(
                connection=args.connection,
                connector=args.connector,
                request=request,
                config_path=args.config,
            )
        except ConnectorError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print(
            f"OK host={pull_meta.get('host')} "
            f"accepted={ingest.accepted} dropped={ingest.dropped} "
            f"pages={pull_meta.get('pages_fetched')}"
        )
        return 0

    print("Usage: connections list | connections test --connection NAME")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Subcommand: investigate
    if args.command == "investigate":
        return _run_investigate(args)

    if args.command == "pull":
        return _run_pull(args)

    if args.command == "connections":
        return _run_connections(args)

    # Subcommand: demo
    if args.command == "demo":
        return _run_demo(
            args.scenario,
            provider=args.provider,
            model=args.model,
            force_fallback=args.force_fallback,
        )

    # Legacy: top-level --scenario (no subcommand)
    if args.scenario:
        return _run_demo(
            args.scenario,
            provider=args.provider,
            model=args.model,
            force_fallback=args.force_fallback,
        )

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
