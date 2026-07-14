"""Unit tests for Langfuse connector mapping and pull."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.connectors.base import PullRequest
from llm_cost_investigator.connectors.langfuse.map import observation_to_row
from llm_cost_investigator.connectors.langfuse.connector import LangfuseConnector
from llm_cost_investigator.connectors.secrets import LangfuseCredentials
from llm_cost_investigator.ingest.base import materialize_rows
from llm_cost_investigator.ingest.types import IngestOptions

FIXTURES = ROOT / "tests" / "fixtures" / "langfuse"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_observation_to_row_basic() -> None:
    obs = _load("observations_page1.json")["data"][0]
    row = observation_to_row(obs)
    assert row["call_id"] == "gen-001"
    assert row["feature_tag"] == "summarizer"
    assert row["model"] == "gpt-4o-mini"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 20
    assert row["cost_usd"] == 0.001
    assert row["latency_ms"] == 500
    assert row["retry_count"] == 0
    assert "input" not in row
    assert "output" not in row
    assert "SECRET" not in json.dumps(row)


def test_observation_to_row_usage_details_and_parent() -> None:
    obs = _load("observations_page1.json")["data"][1]
    row = observation_to_row(obs)
    assert row["call_id"] == "gen-002"
    assert row["parent_call_id"] == "gen-001"
    assert row["input_tokens"] == 200
    assert row["output_tokens"] == 40
    assert abs(float(row["cost_usd"]) - 0.003) < 1e-9
    assert row["feature_tag"] == "summarizer"  # from name


def test_observation_to_row_retry_metadata() -> None:
    obs = _load("observations_page2.json")["data"][0]
    row = observation_to_row(obs)
    assert row["retry_count"] == 2
    assert row["feature_tag"] == "support_reply"
    assert abs(float(row["cost_usd"]) - 0.0005) < 1e-9


def test_materialize_langfuse_rows() -> None:
    page = _load("observations_page1.json")["data"]
    rows = []
    for obs in page:
        if obs.get("type") != "GENERATION":
            continue
        rows.append(observation_to_row(obs))
    calls, report = materialize_rows(
        rows, adapter_name="langfuse", options=IngestOptions(max_rows=100)
    )
    assert report.accepted == 2
    assert report.adapter == "langfuse"
    assert calls[0].call_id == "gen-001"


def test_pull_pagination_and_max_rows() -> None:
    page1 = _load("observations_page1.json")
    page2 = _load("observations_page2.json")
    pages = [page1, page2]
    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        idx = min(call_count["n"], len(pages) - 1)
        call_count["n"] += 1
        return pages[idx]

    creds = LangfuseCredentials(
        public_key="pk", secret_key="sk", host="https://example.test"
    )
    connector = LangfuseConnector(creds=creds, api_version="v1")

    with patch(
        "llm_cost_investigator.connectors.langfuse.client.request_json",
        side_effect=fake_request,
    ):
        calls, report, meta = connector.pull(
            PullRequest(since_hours=24, max_rows=2)
        )

    assert report.accepted == 2
    assert meta["truncated"] is True
    assert meta["host"] == "example.test"
    assert all(c.call_id.startswith("gen-") for c in calls)
    # secrets not in meta
    assert "secret" not in json.dumps(meta).lower() or "secret_key" not in json.dumps(meta)


def test_pull_empty() -> None:
    empty = _load("observations_empty.json")

    def fake_request(method, url, **kwargs):
        return empty

    creds = LangfuseCredentials(
        public_key="pk", secret_key="sk", host="https://example.test"
    )
    connector = LangfuseConnector(creds=creds)

    with patch(
        "llm_cost_investigator.connectors.langfuse.client.request_json",
        side_effect=fake_request,
    ):
        calls, report, meta = connector.pull(PullRequest(since_hours=1, max_rows=100))

    assert report.accepted == 0
    assert calls == []
    assert meta["pages_fetched"] >= 1


def test_missing_model_raises_in_map() -> None:
    try:
        observation_to_row(
            {
                "id": "x",
                "startTime": "2026-07-01T00:00:00Z",
                "inputUsage": 1,
                "outputUsage": 1,
            }
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "model" in str(exc).lower()


def main() -> int:
    tests = [
        test_observation_to_row_basic,
        test_observation_to_row_usage_details_and_parent,
        test_observation_to_row_retry_metadata,
        test_materialize_langfuse_rows,
        test_pull_pagination_and_max_rows,
        test_pull_empty,
        test_missing_model_raises_in_map,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"{fn.__name__} ... PASS")
        except Exception as exc:
            failed += 1
            print(f"{fn.__name__} ... FAIL: {exc}")
    print()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
