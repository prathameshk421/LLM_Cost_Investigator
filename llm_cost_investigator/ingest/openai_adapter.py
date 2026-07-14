"""OpenAI-style chat completion / usage dump adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_cost_investigator.ingest.base import materialize_rows, read_source_text
from llm_cost_investigator.ingest.types import IngestOptions, IngestReport
from llm_cost_investigator.schemas import LLMCall


class OpenAIAdapter:
    name = "openai"

    def load(
        self,
        source: Path | bytes | str,
        *,
        options: IngestOptions,
    ) -> tuple[list[LLMCall], IngestReport]:
        text = read_source_text(source).strip()
        if not text:
            raise ValueError("Empty file: no telemetry rows found.")

        objects = _load_objects(text)
        warnings: list[str] = []
        if options.feature_default is None:
            warnings.append(
                "No feature_default set; rows without metadata.feature will use 'default'."
            )

        raw_rows = [_map_openai_object(obj) for obj in objects]
        return materialize_rows(
            raw_rows,
            adapter_name=self.name,
            options=options,
            extra_warnings=warnings,
        )


def _load_objects(text: str) -> list[dict[str, Any]]:
    if text.lstrip().startswith("[") or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        else:
            if isinstance(payload, list):
                return [o for o in payload if isinstance(o, dict)]
            if isinstance(payload, dict):
                for key in ("data", "calls", "records", "items", "choices"):
                    # choices is usually not a list of calls — skip
                    if key == "choices":
                        continue
                    if isinstance(payload.get(key), list):
                        return [o for o in payload[key] if isinstance(o, dict)]
                return [payload]

    objects: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def _map_openai_object(obj: dict[str, Any]) -> dict[str, Any]:
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}

    input_tokens = (
        obj.get("input_tokens")
        if obj.get("input_tokens") is not None
        else usage.get("prompt_tokens")
    )
    if input_tokens is None:
        input_tokens = usage.get("input_tokens")

    output_tokens = (
        obj.get("output_tokens")
        if obj.get("output_tokens") is not None
        else usage.get("completion_tokens")
    )
    if output_tokens is None:
        output_tokens = usage.get("output_tokens")

    feature = (
        metadata.get("feature_tag")
        or metadata.get("feature")
        or obj.get("feature_tag")
        or obj.get("feature")
    )

    parent = (
        obj.get("parent_call_id")
        or obj.get("parent_id")
        or metadata.get("parent_call_id")
        or metadata.get("parent_id")
    )

    timestamp = obj.get("timestamp") or obj.get("created") or obj.get("time")
    call_id = obj.get("call_id") or obj.get("id")

    cost = obj.get("cost_usd")
    if cost is None:
        cost = obj.get("cost")

    return {
        "timestamp": timestamp,
        "call_id": call_id,
        "feature_tag": feature,
        "model": obj.get("model"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "latency_ms": obj.get("latency_ms") or metadata.get("latency_ms"),
        "retry_count": obj.get("retry_count") or metadata.get("retry_count"),
        "parent_call_id": parent,
        "scenario_label": obj.get("scenario_label") or metadata.get("scenario_label"),
    }
