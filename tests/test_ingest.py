"""Unit tests for telemetry ingest adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.ingest import IngestOptions, load_telemetry
from llm_cost_investigator.ingest.pricing import estimate_cost_usd


def test_jsonl_happy_path() -> None:
    lines = [
        {
            "timestamp": "2026-07-01T12:00:00Z",
            "call_id": "c1",
            "feature_tag": "summarizer",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.001,
            "latency_ms": 200,
            "retry_count": 0,
        },
        {
            "timestamp": "2026-07-01T12:01:00Z",
            "call_id": "c2",
            "feature_tag": "summarizer",
            "model": "gpt-4o-mini",
            "input_tokens": 110,
            "output_tokens": 25,
            "cost_usd": 0.0012,
        },
    ]
    payload = "\n".join(json.dumps(r) for r in lines).encode()
    calls, report = load_telemetry(payload, adapter="jsonl", filename="t.jsonl")
    assert report.accepted == 2
    assert report.dropped == 0
    assert calls[0].feature_tag == "summarizer"
    assert calls[1].latency_ms == 0  # defaulted


def test_jsonl_drops_bad_row() -> None:
    payload = (
        json.dumps(
            {
                "timestamp": "2026-07-01T12:00:00Z",
                "call_id": "ok",
                "feature_tag": "f",
                "model": "m",
                "input_tokens": 1,
                "output_tokens": 1,
                "cost_usd": 0.01,
            }
        )
        + "\n"
        + json.dumps({"call_id": "bad"})
        + "\n"
    ).encode()
    calls, report = load_telemetry(payload, adapter="jsonl")
    assert report.accepted == 1
    assert report.dropped == 1
    assert calls[0].call_id == "ok"


def test_cost_estimate() -> None:
    cost, key = estimate_cost_usd("gpt-4o-mini", 1_000_000, 0)
    assert key == "gpt-4o-mini"
    assert abs(cost - 0.15) < 1e-9

    payload = json.dumps(
        {
            "timestamp": "2026-07-01T12:00:00Z",
            "call_id": "c1",
            "feature_tag": "f",
            "model": "gpt-4o-mini",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
        }
    ).encode()
    calls, report = load_telemetry(payload, adapter="jsonl")
    assert report.cost_estimated_count == 1
    assert abs(calls[0].cost_usd - 0.15) < 1e-9


def test_openai_usage_nested() -> None:
    payload = json.dumps(
        [
            {
                "id": "chatcmpl-1",
                "created": 1720000000,
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                "metadata": {"feature_tag": "support"},
            }
        ]
    ).encode()
    calls, report = load_telemetry(payload, adapter="openai")
    assert report.accepted == 1
    assert calls[0].call_id == "chatcmpl-1"
    assert calls[0].input_tokens == 50
    assert calls[0].feature_tag == "support"
    assert report.cost_estimated_count == 1


def test_openai_feature_default() -> None:
    payload = json.dumps(
        {
            "id": "x",
            "created": 1720000000,
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode()
    calls, report = load_telemetry(
        payload,
        adapter="openai",
        options=IngestOptions(feature_default="my_feature"),
    )
    assert calls[0].feature_tag == "my_feature"


def test_csv_mapping() -> None:
    csv_text = (
        "ts,id,feature,model,prompt_tokens,completion_tokens,cost\n"
        "2026-07-01T12:00:00Z,c1,sum,gpt-4o-mini,10,5,0.001\n"
    )
    mapping = {
        "timestamp": "ts",
        "call_id": "id",
        "feature_tag": "feature",
        "model": "model",
        "input_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "cost_usd": "cost",
    }
    calls, report = load_telemetry(
        csv_text.encode(),
        adapter="csv",
        options=IngestOptions(column_mapping=mapping),
        filename="x.csv",
    )
    assert report.accepted == 1
    assert calls[0].call_id == "c1"
    assert calls[0].input_tokens == 10


def test_auto_detect_csv() -> None:
    csv_text = (
        "timestamp,call_id,feature_tag,model,input_tokens,output_tokens,cost_usd\n"
        "2026-07-01T12:00:00Z,c1,f,m,1,1,0.01\n"
    )
    calls, report = load_telemetry(
        csv_text.encode(), adapter="auto", filename="data.csv"
    )
    assert report.adapter == "csv"
    assert report.accepted == 1


def main() -> int:
    tests = [
        ("jsonl_happy_path", test_jsonl_happy_path),
        ("jsonl_drops_bad_row", test_jsonl_drops_bad_row),
        ("cost_estimate", test_cost_estimate),
        ("openai_usage_nested", test_openai_usage_nested),
        ("openai_feature_default", test_openai_feature_default),
        ("csv_mapping", test_csv_mapping),
        ("auto_detect_csv", test_auto_detect_csv),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print(f"{name} ... PASS")
        except Exception as exc:
            ok = False
            print(f"{name} ... FAIL: {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
