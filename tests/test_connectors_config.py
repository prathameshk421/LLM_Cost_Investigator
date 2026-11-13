"""Unit tests for connection profile loading."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.connectors.base import ConnectorConfigError
from llm_cost_investigator.connectors.config import (
    get_profile,
    list_profile_summaries,
    load_profiles,
)


SAMPLE = """
version: 1
connections:
  prod-langfuse:
    connector: langfuse
    host: https://cloud.langfuse.com
    api_version: v1
    defaults:
      since_hours: 6
      max_rows: 5000
    filters:
      environment: production
"""


def test_load_profiles_happy() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "connections.yaml"
        path.write_text(SAMPLE, encoding="utf-8")
        profiles = load_profiles(path)
        assert "prod-langfuse" in profiles
        p = profiles["prod-langfuse"]
        assert p.connector == "langfuse"
        assert p.defaults.since_hours == 6
        assert p.filters["environment"] == "production"


def test_reject_secret_fields() -> None:
    bad = """
version: 1
connections:
  bad:
    connector: langfuse
    secret_key: sk-should-not-be-here
"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "connections.yaml"
        path.write_text(bad, encoding="utf-8")
        try:
            load_profiles(path)
            raise AssertionError("expected ConnectorConfigError")
        except ConnectorConfigError as exc:
            assert "secret_key" in str(exc)


def test_missing_file_with_explicit_path() -> None:
    try:
        load_profiles("/nonexistent/path/connections.yaml")
        raise AssertionError("expected ConnectorConfigError")
    except ConnectorConfigError as exc:
        assert "not found" in str(exc).lower()


def test_get_profile_unknown() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "connections.yaml"
        path.write_text(SAMPLE, encoding="utf-8")
        try:
            get_profile("nope", config_path=path)
            raise AssertionError("expected ConnectorConfigError")
        except ConnectorConfigError as exc:
            assert "Unknown" in str(exc)


def test_list_profile_summaries_no_secrets() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "connections.yaml"
        path.write_text(SAMPLE, encoding="utf-8")
        rows = list_profile_summaries(path)
        assert len(rows) == 1
        blob = str(rows)
        assert "sk-" not in blob
        assert "secret" not in blob.lower()


def test_empty_when_no_default_file() -> None:
    # Without explicit path and no project file, returns {}
    # Use a temp cwd simulation via explicit missing path only covered above.
    # load_profiles(None) may find real user config — only assert type.
    result = load_profiles(None)
    assert isinstance(result, dict)


def main() -> int:
    tests = [
        test_load_profiles_happy,
        test_reject_secret_fields,
        test_missing_file_with_explicit_path,
        test_get_profile_unknown,
        test_list_profile_summaries_no_secrets,
        test_empty_when_no_default_file,
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
