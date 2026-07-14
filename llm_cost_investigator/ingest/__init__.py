"""Telemetry ingest adapters for external project logs."""

from __future__ import annotations

from pathlib import Path

from llm_cost_investigator.ingest.csv_adapter import CsvAdapter, guess_column_mapping
from llm_cost_investigator.ingest.detect import detect_adapter
from llm_cost_investigator.ingest.jsonl_adapter import JsonlAdapter
from llm_cost_investigator.ingest.openai_adapter import OpenAIAdapter
from llm_cost_investigator.ingest.pricing import estimate_cost_usd, known_models
from llm_cost_investigator.ingest.types import (
    DEFAULT_MAX_ROWS,
    LOGICAL_FIELDS,
    REQUIRED_LOGICAL_FIELDS,
    AdapterName,
    IngestOptions,
    IngestReport,
    ResolvedAdapter,
)
from llm_cost_investigator.schemas import LLMCall

__all__ = [
    "AdapterName",
    "DEFAULT_MAX_ROWS",
    "IngestOptions",
    "IngestReport",
    "LOGICAL_FIELDS",
    "REQUIRED_LOGICAL_FIELDS",
    "ResolvedAdapter",
    "detect_adapter",
    "estimate_cost_usd",
    "guess_column_mapping",
    "known_models",
    "load_telemetry",
]

_ADAPTERS = {
    "jsonl": JsonlAdapter(),
    "openai": OpenAIAdapter(),
    "csv": CsvAdapter(),
}


def load_telemetry(
    source: Path | bytes | str,
    *,
    adapter: AdapterName = "auto",
    options: IngestOptions | None = None,
    filename: str | None = None,
) -> tuple[list[LLMCall], IngestReport]:
    """Load and normalize telemetry from a file path, bytes, or text.

    Parameters
    ----------
    adapter:
        ``auto`` detects format; otherwise force ``jsonl``, ``openai``, or ``csv``.
    filename:
        Optional original filename (useful when ``source`` is raw bytes from an upload).
    """
    opts = options or IngestOptions()
    if filename and not opts.source_name:
        opts = opts.model_copy(update={"source_name": filename})

    resolved: ResolvedAdapter
    if adapter == "auto":
        resolved = detect_adapter(source, filename=filename or opts.source_name)
    else:
        resolved = adapter

    impl = _ADAPTERS[resolved]
    calls, report = impl.load(source, options=opts)
    # Ensure adapter field matches resolved name even if nested wrapper differs
    if report.adapter != resolved:
        report = report.model_copy(update={"adapter": resolved})
    return calls, report
