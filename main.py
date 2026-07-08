"""CLI entrypoint for scenario simulation and investigation."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LLM cost investigator demo.")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["retry_loop", "context_bloat", "model_misroute", "all"],
        help="Scenario to generate and investigate.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    print(f"Scenario selected: {args.scenario}")
    print("Pipeline skeleton created. Investigation flow is not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
