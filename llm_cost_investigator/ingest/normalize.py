"""Coerce raw row dicts into validated LLMCall objects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall


def parse_timestamp(value: Any) -> datetime:
    """Parse ISO-8601 strings or unix seconds/ms into aware UTC datetime."""
    if value is None or value == "":
        raise ValueError("timestamp is required")

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        # Heuristic: ms vs seconds
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        raise ValueError("timestamp is empty")

    # Numeric string
    try:
        if text.replace(".", "", 1).isdigit() or (
            text.startswith("-") and text[1:].replace(".", "", 1).isdigit()
        ):
            return parse_timestamp(float(text))
    except ValueError:
        pass

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_int(value: Any, field: str) -> int:
    if value is None or value == "":
        raise ValueError(f"{field} is required")
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer, got {value!r}") from exc


def _as_optional_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_row(
    raw: dict[str, Any],
    *,
    feature_default: str | None = None,
    row_index: int = 0,
) -> tuple[LLMCall, bool, list[str]]:
    """Normalize one raw dict into an LLMCall.

    Returns (call, cost_was_estimated, warnings).
    """
    warnings: list[str] = []
    data = dict(raw)

    # Aliases commonly seen before adapter-specific mapping
    if "call_id" not in data and "id" in data:
        data["call_id"] = data["id"]

    timestamp = parse_timestamp(data.get("timestamp") or data.get("created") or data.get("time"))
    call_id = _as_str(data.get("call_id"), "call_id")

    feature_tag = _as_optional_str(data.get("feature_tag")) or _as_optional_str(
        data.get("feature")
    )
    if not feature_tag:
        if feature_default:
            feature_tag = feature_default
        else:
            feature_tag = "default"
            warnings.append(
                f"row {row_index}: missing feature_tag; using 'default'"
            )

    model = _as_str(data.get("model"), "model")
    input_tokens = _as_int(data.get("input_tokens"), "input_tokens")
    output_tokens = _as_int(data.get("output_tokens"), "output_tokens")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be >= 0")

    cost_raw = data.get("cost_usd")
    if cost_raw is None:
        cost_raw = data.get("cost")
    cost_usd = _as_optional_float(cost_raw)
    cost_estimated = False
    if cost_usd is None:
        cost_usd, pricing_key = estimate_cost_usd(model, input_tokens, output_tokens)
        cost_estimated = True
        if pricing_key == "fallback_default":
            warnings.append(
                f"row {row_index}: cost estimated with fallback pricing for model={model!r}"
            )

    latency_ms = _as_optional_int(data.get("latency_ms"), 0)
    retry_count = _as_optional_int(data.get("retry_count"), 0)
    if latency_ms < 0:
        latency_ms = 0
    if retry_count < 0:
        retry_count = 0

    parent_call_id = _as_optional_str(data.get("parent_call_id"))
    if parent_call_id is None:
        parent_call_id = _as_optional_str(data.get("parent_id"))

    scenario_label = _as_optional_str(data.get("scenario_label"))

    call = LLMCall(
        timestamp=timestamp,
        call_id=call_id,
        parent_call_id=parent_call_id,
        feature_tag=feature_tag,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(cost_usd),
        latency_ms=latency_ms,
        retry_count=retry_count,
        scenario_label=scenario_label,
    )
    return call, cost_estimated, warnings


def finalize_calls(
    calls: list[LLMCall],
    *,
    max_rows: int,
) -> tuple[list[LLMCall], list[str]]:
    """Deduplicate by call_id and enforce max_rows."""
    warnings: list[str] = []
    if len(calls) > max_rows:
        raise ValueError(
            f"Too many rows: {len(calls)} exceeds max_rows={max_rows}"
        )

    seen: set[str] = set()
    unique: list[LLMCall] = []
    for call in calls:
        if call.call_id in seen:
            warnings.append(f"duplicate call_id={call.call_id!r}; keeping first")
            continue
        seen.add(call.call_id)
        unique.append(call)
    return unique, warnings
