"""CSV adapter with explicit column mapping."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from llm_cost_investigator.ingest.base import materialize_rows, read_source_text
from llm_cost_investigator.ingest.types import (
    LOGICAL_FIELDS,
    REQUIRED_LOGICAL_FIELDS,
    IngestOptions,
    IngestReport,
)
from llm_cost_investigator.schemas import LLMCall

# Header aliases → logical field (for auto-guess)
_HEADER_ALIASES: dict[str, str] = {
    "timestamp": "timestamp",
    "time": "timestamp",
    "created": "timestamp",
    "created_at": "timestamp",
    "ts": "timestamp",
    "call_id": "call_id",
    "id": "call_id",
    "request_id": "call_id",
    "feature_tag": "feature_tag",
    "feature": "feature_tag",
    "tag": "feature_tag",
    "model": "model",
    "model_name": "model",
    "input_tokens": "input_tokens",
    "prompt_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "completion_tokens": "output_tokens",
    "cost_usd": "cost_usd",
    "cost": "cost_usd",
    "latency_ms": "latency_ms",
    "latency": "latency_ms",
    "retry_count": "retry_count",
    "retries": "retry_count",
    "parent_call_id": "parent_call_id",
    "parent_id": "parent_call_id",
}


class CsvAdapter:
    name = "csv"

    def load(
        self,
        source: Path | bytes | str,
        *,
        options: IngestOptions,
    ) -> tuple[list[LLMCall], IngestReport]:
        text = read_source_text(source)
        if not text.strip():
            raise ValueError("Empty CSV: no telemetry rows found.")

        delimiter = options.csv_delimiter or _sniff_delimiter(text)
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        headers = [h for h in reader.fieldnames if h is not None]
        mapping = options.column_mapping or guess_column_mapping(headers)
        _validate_mapping(mapping, headers, feature_default=options.feature_default)

        raw_rows: list[dict[str, Any]] = []
        for row in reader:
            raw_rows.append(_apply_mapping(row, mapping))

        warnings: list[str] = []
        if options.column_mapping is None:
            warnings.append(
                "No column_mapping provided; used auto-guess from headers."
            )

        return materialize_rows(
            raw_rows,
            adapter_name=self.name,
            options=options,
            extra_warnings=warnings,
            detected_headers=headers,
        )


def guess_column_mapping(headers: list[str]) -> dict[str, str]:
    """Map logical field → header name using aliases."""
    mapping: dict[str, str] = {}
    lower_to_header = {h.strip().lower(): h for h in headers if h}
    for header_lower, header in lower_to_header.items():
        logical = _HEADER_ALIASES.get(header_lower)
        if logical and logical not in mapping:
            mapping[logical] = header
    return mapping


def _validate_mapping(
    mapping: dict[str, str],
    headers: list[str],
    *,
    feature_default: str | None,
) -> None:
    header_set = set(headers)
    for logical, col in mapping.items():
        if logical not in LOGICAL_FIELDS:
            raise ValueError(f"Unknown logical field in mapping: {logical!r}")
        if col not in header_set:
            raise ValueError(
                f"Mapped column {col!r} for {logical!r} not found in CSV headers: {headers}"
            )

    missing_required = []
    for field in REQUIRED_LOGICAL_FIELDS:
        if field == "feature_tag" and feature_default:
            continue
        if field not in mapping:
            missing_required.append(field)
    if missing_required:
        raise ValueError(
            "CSV mapping missing required fields: "
            + ", ".join(missing_required)
            + ". Pass --map / column_mapping."
        )


def _apply_mapping(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for logical, col in mapping.items():
        out[logical] = row.get(col)
    return out


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
        return dialect.delimiter
    except csv.Error:
        return ","
