"""API smoke tests for remote connectors (mocked pull)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

from llm_cost_investigator.ingest.types import IngestReport
from llm_cost_investigator.schemas import LLMCall
from datetime import datetime, timezone


def _sample_calls() -> list[LLMCall]:
    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    return [
        LLMCall(
            timestamp=base,
            call_id="c1",
            feature_tag="summarizer",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.001,
            latency_ms=100,
            retry_count=0,
        ),
        LLMCall(
            timestamp=base,
            call_id="c2",
            feature_tag="summarizer",
            model="gpt-4o-mini",
            input_tokens=110,
            output_tokens=22,
            cost_usd=0.0011,
            latency_ms=110,
            retry_count=0,
        ),
    ]


def _fake_pull(**kwargs):
    calls = _sample_calls()
    report = IngestReport(
        adapter="langfuse",
        accepted=len(calls),
        dropped=0,
        warnings=[],
        cost_estimated_count=0,
    )
    meta = {
        "connector": "langfuse",
        "host": "cloud.langfuse.com",
        "from_time": "2026-07-01T00:00:00+00:00",
        "to_time": "2026-07-02T00:00:00+00:00",
        "pages_fetched": 1,
        "raw_observations": 2,
        "api_version": "v1",
        "connection": None,
        "truncated": False,
    }
    return calls, report, meta


def main() -> int:
    try:
        from fastapi.testclient import TestClient
        from api.app import app
    except Exception as exc:
        print(f"SKIP: web deps unavailable ({exc})")
        return 0

    client = TestClient(app)
    failed = 0

    # list connectors
    res = client.get("/api/connectors")
    if res.status_code != 200:
        print(f"list connectors ... FAIL {res.status_code}")
        failed += 1
    else:
        data = res.json()
        names = [c["name"] for c in data]
        assert "langfuse" in names
        assert "LANGFUSE_PUBLIC_KEY" in data[0]["required_env"]
        print("list connectors ... PASS")

    # profiles (may be empty)
    res = client.get("/api/connectors/profiles")
    if res.status_code != 200:
        print(f"list profiles ... FAIL {res.status_code} {res.text}")
        failed += 1
    else:
        print("list profiles ... PASS")

    with patch(
        "api.services.remote_investigate.pull_calls",
        side_effect=lambda **kw: _fake_pull(**kw),
    ):
        res = client.post(
            "/api/connectors/preview",
            json={"connector": "langfuse", "since_hours": 24, "max_rows": 100},
        )
        if res.status_code != 200:
            print(f"preview ... FAIL {res.status_code} {res.text}")
            failed += 1
        else:
            body = res.json()
            assert body["ingest"]["adapter"] == "langfuse"
            assert body["ingest"]["accepted"] == 2
            assert body["pull_meta"]["host"] == "cloud.langfuse.com"
            print("preview ... PASS")

        res = client.post(
            "/api/connectors/investigate",
            json={
                "connector": "langfuse",
                "since_hours": 24,
                "force_fallback": True,
                "max_anomalies": 3,
                "label": "test_langfuse",
            },
        )
        if res.status_code != 200:
            print(f"investigate ... FAIL {res.status_code} {res.text}")
            failed += 1
        else:
            body = res.json()
            assert body["summary"]["call_count"] == 2
            assert body["pull_meta"] is not None
            print("investigate ... PASS")

        res = client.post(
            "/api/connectors/test",
            json={"connector": "langfuse", "since_hours": 1},
        )
        if res.status_code != 200:
            print(f"test ... FAIL {res.status_code} {res.text}")
            failed += 1
        else:
            body = res.json()
            assert body["ok"] is True
            assert body["accepted"] == 2
            print("test ... PASS")

    # mutual exclusion error
    res = client.post(
        "/api/connectors/preview",
        json={"connection": "x", "connector": "langfuse"},
    )
    if res.status_code != 400:
        print(f"mutual exclusion ... FAIL {res.status_code}")
        failed += 1
    else:
        print("mutual exclusion ... PASS")

    print()
    print("done" if failed == 0 else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
