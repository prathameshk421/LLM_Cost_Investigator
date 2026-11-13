"""Smoke test for FastAPI investigate endpoints (requires [web] extras)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "data" / "samples" / "canonical_sample.jsonl"


def main() -> int:
    if not SAMPLE.exists():
        print("SKIP: run scripts/export_sample_telemetry.py first")
        return 0
    try:
        from fastapi.testclient import TestClient
        from api.app import app
    except Exception as exc:
        print(f"SKIP: web deps missing ({exc})")
        return 0

    client = TestClient(app)
    with SAMPLE.open("rb") as f:
        resp = client.post(
            "/api/investigate",
            files={"file": ("canonical_sample.jsonl", f, "application/jsonl")},
            data={
                "adapter": "jsonl",
                "force_fallback": "true",
                "max_anomalies": "3",
            },
        )
    if resp.status_code != 200:
        print(f"FAIL status={resp.status_code} body={resp.text}")
        return 1
    body = resp.json()
    assert body["ingest"]["accepted"] > 0
    assert body["summary"]["anomaly_count"] >= 1
    assert body["incidents"]
    assert body["incidents"][0]["stages"]
    print("api_investigate ... PASS")
    return 0


def test_script_main() -> None:
    """Run the script-style checks under pytest (they used to be skipped)."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
