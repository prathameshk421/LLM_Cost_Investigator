"""Canonical JSONL / JSON-array adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_cost_investigator.ingest.base import materialize_rows, read_source_text
from llm_cost_investigator.ingest.types import IngestOptions, IngestReport
from llm_cost_investigator.schemas import LLMCall


class JsonlAdapter:
    name = "jsonl"

    def load(
        self,
        source: Path | bytes | str,
        *,
        options: IngestOptions,
    ) -> tuple[list[LLMCall], IngestReport]:
        text = read_source_text(source).strip()
        if not text:
            raise ValueError("Empty file: no telemetry rows found.")

        raw_rows = _parse_json_payload(text)
        return materialize_rows(raw_rows, adapter_name=self.name, options=options)


def _parse_json_payload(text: str) -> list[dict[str, Any]]:
    # JSON array or object with data/calls
    if text.lstrip().startswith("[") or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        else:
            if isinstance(payload, list):
                return [r for r in payload if isinstance(r, dict)]
            if isinstance(payload, dict):
                for key in ("data", "calls", "records", "items"):
                    if isinstance(payload.get(key), list):
                        return [r for r in payload[key] if isinstance(r, dict)]
                # Single object
                return [payload]

    # JSONL
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            raise ValueError(f"Line {line_no}: expected JSON object, got {type(obj).__name__}")
    return rows
