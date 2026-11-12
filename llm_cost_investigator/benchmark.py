"""Accuracy benchmark over seeded scenario-lab datasets.

For each (scenario, seed) the full pipeline runs: detector, router, agents,
aggregator. The prediction is the first incident (in detector rank order)
whose root cause is not ``no_strong_signal``. If there is none, the
prediction is ``no_strong_signal``.

A run is correct when the predicted root cause matches the expected one and,
for a real incident, the incident is on the feature that had the bug.

Usage::

    python -m llm_cost_investigator.benchmark --seeds 20
    python -m llm_cost_investigator.benchmark --seeds 3 --provider hf   # live, budgeted
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from llm_cost_investigator.investigate import investigate_calls
from llm_cost_investigator.scenario_lab import LAB_SCENARIOS, generate_lab_calls
from llm_cost_investigator.usage import ledger

NSS = "no_strong_signal"


def run_one(
    scenario: str,
    seed: int,
    *,
    noise: float = 0.08,
    provider: str | None = "fallback",
    model: str | None = None,
) -> dict[str, Any]:
    lab = generate_lab_calls(scenario, seed=seed, noise=noise)
    started = time.perf_counter()
    # Agents print fallback notes; keep benchmark output readable.
    with contextlib.redirect_stdout(io.StringIO()):
        result = investigate_calls(
            lab.calls,
            label=f"bench_{scenario}_{seed}",
            provider=provider,
            model=model,
            max_anomalies=5,
            write_reports=False,
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    predicted, feature, confidence = NSS, None, 0.0
    for incident in result.incidents:
        if incident.root_cause.hypothesis != NSS:
            predicted = incident.root_cause.hypothesis
            feature = incident.anomaly_window.feature_tag
            confidence = incident.root_cause.confidence
            break

    correct = predicted == lab.expected and (
        lab.expected == NSS or feature == lab.target_feature
    )
    providers = sorted(
        {run.provider for inc in result.incidents for run in inc.agent_runs}
    )
    return {
        "scenario": scenario,
        "seed": seed,
        "expected": lab.expected,
        "predicted": predicted,
        "feature": feature,
        "target_feature": lab.target_feature,
        "confidence": round(confidence, 3),
        "correct": correct,
        "anomalies": result.anomaly_count,
        "calls": len(lab.calls),
        "providers": providers,
        "elapsed_ms": elapsed_ms,
    }


def run_benchmark(
    *,
    scenarios: tuple[str, ...] | list[str] = LAB_SCENARIOS,
    seeds: int = 20,
    noise: float = 0.08,
    provider: str | None = "fallback",
    model: str | None = None,
) -> dict[str, Any]:
    runs = [
        run_one(s, seed, noise=noise, provider=provider, model=model)
        for s in scenarios
        for seed in range(seeds)
    ]

    per_scenario: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        grouped[r["scenario"]].append(r)
    for s, items in grouped.items():
        ok = sum(1 for r in items if r["correct"])
        per_scenario[s] = {
            "runs": len(items),
            "correct": ok,
            "accuracy": round(ok / len(items), 3),
            "predictions": dict(Counter(r["predicted"] for r in items)),
        }

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in runs:
        confusion[r["expected"]][r["predicted"]] += 1

    controls = [r for r in runs if r["expected"] == NSS]
    false_alarms = sum(1 for r in controls if r["predicted"] != NSS)
    total_ok = sum(1 for r in runs if r["correct"])

    return {
        "config": {
            "scenarios": list(scenarios),
            "seeds": seeds,
            "noise": noise,
            "provider": provider or "auto",
            "model": model,
        },
        "overall_accuracy": round(total_ok / len(runs), 3) if runs else 0.0,
        "runs_total": len(runs),
        "false_alarm_rate": round(false_alarms / len(controls), 3) if controls else 0.0,
        "per_scenario": per_scenario,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "failures": [r for r in runs if not r["correct"]],
        "usage": {k: v for k, v in ledger.snapshot().items() if k != "recent"},
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--noise", type=float, default=0.08)
    parser.add_argument("--scenarios", nargs="*", default=list(LAB_SCENARIOS))
    parser.add_argument(
        "--provider",
        default="fallback",
        choices=["fallback", "groq", "cerebras", "hf", "deploy"],
        help="fallback is free and offline; live providers use the budget guard",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", default="data/reports/benchmark.json")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help="exit 1 when overall accuracy is below this value (for CI)",
    )
    args = parser.parse_args(argv)

    report = run_benchmark(
        scenarios=args.scenarios,
        seeds=args.seeds,
        noise=args.noise,
        provider=args.provider,
        model=args.model,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    print(f"provider={report['config']['provider']} seeds={args.seeds} noise={args.noise}")
    print(f"{'scenario':<16}{'accuracy':>10}  predictions")
    for s, row in report["per_scenario"].items():
        print(f"{s:<16}{row['accuracy']:>10.0%}  {row['predictions']}")
    print(f"overall accuracy: {report['overall_accuracy']:.1%}")
    print(f"false alarm rate: {report['false_alarm_rate']:.1%}")
    u = report["usage"]
    if u["calls"]:
        print(f"live LLM calls: {u['calls']}  est spend: ${u['spent_usd']:.4f}")
    print(f"wrote {out}")

    if args.min_accuracy is not None and report["overall_accuracy"] < args.min_accuracy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
