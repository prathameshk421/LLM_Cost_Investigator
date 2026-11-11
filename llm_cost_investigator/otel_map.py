"""Map ingest events (canonical LLMCall dicts or OTel GenAI-style dicts) to LLMCall.

Accepted keys, first match wins (flat keys; a nested ``attributes`` dict is
merged in, so a raw OTel span dict works too):

- model:        model, gen_ai.request.model, gen_ai.response.model
- input_tokens: input_tokens, gen_ai.usage.input_tokens, gen_ai.usage.prompt_tokens, prompt_tokens
- output_tokens: output_tokens, gen_ai.usage.output_tokens, gen_ai.usage.completion_tokens, completion_tokens
- feature_tag:  feature_tag, llmci.feature_tag, service.name (default "unknown")
- parent_call_id: parent_call_id, llmci.parent_call_id
- retry_count:  retry_count, llmci.retry_count (default 0)
- latency_ms:   latency_ms, duration_ms, llmci.latency_ms (default 0)
- cost_usd:     cost_usd, llmci.cost_usd (missing: priced from the static table)
- timestamp:    timestamp, ts, time (ISO string, datetime, or epoch s/ms/us/ns; default ``now``)
- call_id:      call_id, span_id, id (default: uuid4 hex)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import LLMCall

__all__ = ["to_llm_call"]

_ALIASES: dict[str, tuple[str, ...]] = {
    "model": ("model", "gen_ai.request.model", "gen_ai.response.model"),
    "input_tokens": (
        "input_tokens", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "prompt_tokens",
    ),
    "output_tokens": (
        "output_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens",
        "completion_tokens",
    ),
    "feature_tag": ("feature_tag", "llmci.feature_tag", "service.name"),
    "parent_call_id": ("parent_call_id", "llmci.parent_call_id"),
    "retry_count": ("retry_count", "llmci.retry_count"),
    "latency_ms": ("latency_ms", "duration_ms", "llmci.latency_ms"),
    "cost_usd": ("cost_usd", "llmci.cost_usd"),
    "timestamp": ("timestamp", "ts", "time"),
    "call_id": ("call_id", "span_id", "id"),
}


def _pick(flat: dict[str, Any], field: str) -> Any:
    for key in _ALIASES[field]:
        value = flat.get(key)
        if value is not None and value != "":
            return value
    return None


def _to_int(value: Any, field: str, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError(f"missing required field '{field}' (keys: {', '.join(_ALIASES[field])})")
        return default
    if isinstance(value, bool):
        raise ValueError(f"'{field}' must be a number, got a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' must be a number, got {value!r}") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"'{field}' must be finite")
    if number < 0:
        raise ValueError(f"'{field}' must be >= 0, got {value!r}")
    return int(round(number))


def _to_timestamp(value: Any, now: datetime) -> datetime:
    if value is None:
        ts = now
    elif isinstance(value, datetime):
        ts = value
    elif isinstance(value, bool):
        raise ValueError("'timestamp' must be an ISO string or epoch number")
    elif isinstance(value, (int, float)) or (isinstance(value, str) and _is_number(value)):
        num = float(value)
        mag = abs(num)
        if mag >= 1e17:      # nanoseconds
            num /= 1e9
        elif mag >= 1e14:    # microseconds
            num /= 1e6
        elif mag >= 1e11:    # milliseconds
            num /= 1e3
        try:
            ts = datetime.fromtimestamp(num, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError(f"'timestamp' out of range: {value!r}") from None
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"'timestamp' is not ISO-8601: {value!r}") from None
    else:
        raise ValueError(f"'timestamp' has unsupported type {type(value).__name__}")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def to_llm_call(event: dict[str, Any], now: datetime) -> LLMCall:
    """Convert one ingest event to an ``LLMCall``. Raises ``ValueError`` with a clear message."""
    if not isinstance(event, dict):
        raise ValueError(f"event must be an object, got {type(event).__name__}")
    flat: dict[str, Any] = dict(event)
    attrs = event.get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            flat.setdefault(key, value)

    model = _pick(flat, "model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("missing required field 'model' (keys: model, gen_ai.request.model)")
    model = model.strip()

    input_tokens = _to_int(_pick(flat, "input_tokens"), "input_tokens")
    output_tokens = _to_int(_pick(flat, "output_tokens"), "output_tokens")
    retry_count = _to_int(_pick(flat, "retry_count"), "retry_count", default=0)
    latency_ms = _to_int(_pick(flat, "latency_ms"), "latency_ms", default=0)

    cost_raw = _pick(flat, "cost_usd")
    if cost_raw is None:
        cost = estimate_cost_usd(model, input_tokens, output_tokens)[0]
    else:
        if isinstance(cost_raw, bool):
            raise ValueError("'cost_usd' must be a number, got a boolean")
        try:
            cost = float(cost_raw)
        except (TypeError, ValueError):
            raise ValueError(f"'cost_usd' must be a number, got {cost_raw!r}") from None
        if cost != cost or cost < 0 or cost == float("inf"):
            raise ValueError(f"'cost_usd' must be a finite number >= 0, got {cost_raw!r}")

    feature = _pick(flat, "feature_tag")
    feature_tag = str(feature).strip() if feature is not None else "unknown"
    parent = _pick(flat, "parent_call_id")
    call_id = _pick(flat, "call_id")

    try:
        return LLMCall(
            timestamp=_to_timestamp(_pick(flat, "timestamp"), now),
            call_id=str(call_id) if call_id is not None else uuid.uuid4().hex,
            parent_call_id=str(parent) if parent is not None else None,
            feature_tag=feature_tag or "unknown",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            retry_count=retry_count,
        )
    except ValidationError as exc:  # pragma: no cover - defensive
        first = exc.errors()[0]
        raise ValueError(f"invalid '{'.'.join(map(str, first['loc']))}': {first['msg']}") from None
