"""Shared helpers for adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from llm_cost_investigator.ingest.normalize import finalize_calls, normalize_row
from llm_cost_investigator.ingest.types import IngestOptions, IngestReport
from llm_cost_investigator.schemas import LLMCall


class TelemetryAdapter(Protocol):
    name: str

    def load(
        self,
        source: Path | bytes | str,
        *,
        options: IngestOptions,
    ) -> tuple[list[LLMCall], IngestReport]:
        ...


def read_source_text(source: Path | bytes | str) -> str:
    if isinstance(source, bytes):
        return source.decode("utf-8-sig")
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8-sig")
    # str path or raw text
    path = Path(source)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return source


def read_source_bytes(source: Path | bytes | str) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, Path):
        return source.read_bytes()
    path = Path(source)
    if path.exists() and path.is_file():
        return path.read_bytes()
    return source.encode("utf-8")


def materialize_rows(
    raw_rows: list[dict[str, Any]],
    *,
    adapter_name: str,
    options: IngestOptions,
    extra_warnings: list[str] | None = None,
    detected_headers: list[str] | None = None,
) -> tuple[list[LLMCall], IngestReport]:
    """Normalize a list of dict rows into LLMCalls + IngestReport."""
    warnings: list[str] = list(extra_warnings or [])
    calls: list[LLMCall] = []
    dropped = 0
    sample_dropped: list[dict[str, Any]] = []
    cost_estimated_count = 0

    if len(raw_rows) > options.max_rows:
        raise ValueError(
            f"Too many rows: {len(raw_rows)} exceeds max_rows={options.max_rows}"
        )

    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            dropped += 1
            if len(sample_dropped) < 10:
                sample_dropped.append(
                    {"row_index": index, "reason": "row is not an object", "raw": None}
                )
            continue
        try:
            call, estimated, row_warnings = normalize_row(
                raw,
                feature_default=options.feature_default,
                row_index=index,
            )
            calls.append(call)
            if estimated:
                cost_estimated_count += 1
            warnings.extend(row_warnings)
        except Exception as exc:
            dropped += 1
            if len(sample_dropped) < 10:
                sample_dropped.append(
                    {
                        "row_index": index,
                        "reason": str(exc),
                        "raw": {k: raw.get(k) for k in list(raw)[:12]},
                    }
                )

    calls, dedupe_warnings = finalize_calls(calls, max_rows=options.max_rows)
    warnings.extend(dedupe_warnings)

    # Cap warning spam
    if len(warnings) > 50:
        overflow = len(warnings) - 50
        warnings = warnings[:50] + [f"... and {overflow} more warnings"]

    report = IngestReport(
        adapter=adapter_name,
        accepted=len(calls),
        dropped=dropped,
        warnings=warnings,
        cost_estimated_count=cost_estimated_count,
        sample_dropped=sample_dropped,
        detected_headers=detected_headers,
    )
    return calls, report
