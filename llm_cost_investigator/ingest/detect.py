"""Auto-detect telemetry adapter from filename and content."""

from __future__ import annotations

import json
from pathlib import Path

from llm_cost_investigator.ingest.base import read_source_text
from llm_cost_investigator.ingest.types import ResolvedAdapter


def detect_adapter(
    source: Path | bytes | str,
    *,
    filename: str | None = None,
) -> ResolvedAdapter:
    """Return best-guess adapter. Raises ValueError if ambiguous."""
    name = filename or ""
    if isinstance(source, Path):
        name = name or source.name
    elif isinstance(source, str) and Path(source).exists():
        name = name or Path(source).name

    lower = name.lower()
    if lower.endswith(".csv"):
        return "csv"

    text = read_source_text(source)
    stripped = text.lstrip()
    if not stripped:
        raise ValueError("Empty input; cannot detect adapter.")

    # CSV sniff: no JSON structure, has commas/tabs and a header-like first line
    if not stripped.startswith("{") and not stripped.startswith("["):
        first = stripped.splitlines()[0] if stripped.splitlines() else ""
        if "," in first or "\t" in first or ";" in first:
            # If every non-empty line is not JSON, treat as CSV
            non_json_lines = 0
            checked = 0
            for line in stripped.splitlines()[:20]:
                line = line.strip()
                if not line:
                    continue
                checked += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    non_json_lines += 1
            if checked and non_json_lines == checked:
                return "csv"

    # Try parse first object
    sample_obj = _first_json_object(stripped)
    if sample_obj is not None:
        keys = set(sample_obj.keys())
        if "usage" in keys or (
            "model" in keys and ("id" in keys or "created" in keys) and "feature_tag" not in keys
        ):
            return "openai"
        if "feature_tag" in keys or (
            "call_id" in keys and "input_tokens" in keys and "model" in keys
        ):
            return "jsonl"
        if "prompt_tokens" in keys or (
            isinstance(sample_obj.get("usage"), dict)
        ):
            return "openai"
        # Nested list wrappers already unwrapped by _first_json_object for arrays
        if "call_id" in keys or "input_tokens" in keys:
            return "jsonl"

    if lower.endswith(".jsonl"):
        return "jsonl"
    if lower.endswith(".json"):
        raise ValueError(
            "Ambiguous .json file. Specify adapter explicitly: "
            "jsonl (canonical LLMCall) or openai (usage-style dumps)."
        )

    raise ValueError(
        "Could not auto-detect adapter. Pass adapter=jsonl|openai|csv explicitly."
    )


def _first_json_object(text: str) -> dict | None:
    text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # JSONL first line
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                return None
            return obj if isinstance(obj, dict) else None
        return None

    if isinstance(payload, list) and payload:
        first = payload[0]
        return first if isinstance(first, dict) else None
    if isinstance(payload, dict):
        for key in ("data", "calls", "records", "items"):
            val = payload.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val[0]
        return payload
    return None
