#!/usr/bin/env python3
"""Export simulator telemetry into sample files for ingest testing.

Usage:
  python3 scripts/export_sample_telemetry.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.simulate_telemetry import generate_scenario_calls  # noqa: E402

OUT = ROOT / "data" / "samples"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # Use model_misroute as primary demo sample (clear cost spike)
    calls = generate_scenario_calls("model_misroute")

    # Canonical JSONL
    jsonl_path = OUT / "canonical_sample.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for c in calls:
            row = c.model_dump(mode="json")
            f.write(json.dumps(row) + "\n")
    print(f"wrote {jsonl_path.relative_to(ROOT)} ({len(calls)} rows)")

    # OpenAI-style JSON array
    openai_rows = []
    for c in calls:
        openai_rows.append(
            {
                "id": c.call_id,
                "created": c.timestamp.timestamp(),
                "model": c.model,
                "usage": {
                    "prompt_tokens": c.input_tokens,
                    "completion_tokens": c.output_tokens,
                },
                "cost_usd": c.cost_usd,
                "metadata": {
                    "feature_tag": c.feature_tag,
                    "parent_call_id": c.parent_call_id,
                    "retry_count": c.retry_count,
                    "latency_ms": c.latency_ms,
                },
            }
        )
    openai_path = OUT / "openai_style_sample.json"
    openai_path.write_text(json.dumps(openai_rows, indent=2), encoding="utf-8")
    print(f"wrote {openai_path.relative_to(ROOT)}")

    # CSV with alternate headers
    csv_path = OUT / "csv_sample.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "ts",
                "id",
                "feature",
                "model",
                "prompt_tokens",
                "completion_tokens",
                "cost",
                "latency",
                "retries",
                "parent_id",
            ],
        )
        writer.writeheader()
        for c in calls:
            writer.writerow(
                {
                    "ts": c.timestamp.isoformat(),
                    "id": c.call_id,
                    "feature": c.feature_tag,
                    "model": c.model,
                    "prompt_tokens": c.input_tokens,
                    "completion_tokens": c.output_tokens,
                    "cost": c.cost_usd,
                    "latency": c.latency_ms,
                    "retries": c.retry_count,
                    "parent_id": c.parent_call_id or "",
                }
            )
    print(f"wrote {csv_path.relative_to(ROOT)}")
    print(
        "CSV map example: "
        "timestamp=ts,call_id=id,feature_tag=feature,model=model,"
        "input_tokens=prompt_tokens,output_tokens=completion_tokens,"
        "cost_usd=cost,latency_ms=latency,retry_count=retries,parent_call_id=parent_id"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
