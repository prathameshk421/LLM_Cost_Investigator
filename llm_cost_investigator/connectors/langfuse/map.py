"""Map Langfuse observation objects to logical ingest row dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _meta(obs: dict[str, Any]) -> dict[str, Any]:
    raw = obs.get("metadata")
    if isinstance(raw, dict):
        return raw
    return {}


def _first_present(obs: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in obs and obs[key] is not None and obs[key] != "":
            return obs[key]
    return None


def _usage_token(obs: dict[str, Any], kind: str) -> Any:
    """kind is 'input' or 'output'."""
    if kind == "input":
        direct = _first_present(obs, "inputUsage", "promptTokens")
        if direct is not None:
            return direct
    else:
        direct = _first_present(obs, "outputUsage", "completionTokens")
        if direct is not None:
            return direct

    usage_details = obs.get("usageDetails")
    if isinstance(usage_details, dict):
        val = usage_details.get(kind)
        if val is not None:
            return val

    usage = obs.get("usage")
    if isinstance(usage, dict):
        if kind == "input":
            return _first_present(
                usage, "input", "promptTokens", "prompt_tokens", "inputTokens"
            )
        return _first_present(
            usage, "output", "completionTokens", "completion_tokens", "outputTokens"
        )
    return None


def _cost_usd(obs: dict[str, Any]) -> Any:
    total = _first_present(
        obs,
        "totalCost",
        "calculatedTotalCost",
        "calculated_total_cost",
    )
    if total is not None:
        return total

    cost_details = obs.get("costDetails")
    if isinstance(cost_details, dict) and cost_details:
        try:
            return float(sum(float(v) for v in cost_details.values() if v is not None))
        except (TypeError, ValueError):
            pass

    usage = obs.get("usage")
    if isinstance(usage, dict):
        return _first_present(usage, "totalCost", "total_cost", "cost")
    return None


def _latency_ms(obs: dict[str, Any]) -> int:
    latency = obs.get("latency")
    if latency is not None and latency != "":
        try:
            val = float(latency)
            # Langfuse typically reports latency in seconds (float).
            # Values >= 100 are treated as already-ms (heuristic).
            if val >= 0 and val < 100:
                return max(0, int(round(val * 1000)))
            return max(0, int(round(val)))
        except (TypeError, ValueError):
            pass

    start = obs.get("startTime") or obs.get("start_time")
    end = obs.get("endTime") or obs.get("end_time")
    if start and end:
        try:
            from llm_cost_investigator.ingest.normalize import parse_timestamp

            s = parse_timestamp(start)
            e = parse_timestamp(end)
            delta = (e - s).total_seconds() * 1000
            return max(0, int(round(delta)))
        except Exception:
            pass
    return 0


def _feature_tag(
    obs: dict[str, Any],
    *,
    feature_default: str | None = None,
) -> str:
    meta = _meta(obs)
    for key in ("feature_tag", "feature", "featureTag"):
        val = meta.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()

    name = obs.get("name")
    if name is not None and str(name).strip():
        return str(name).strip()

    trace_name = obs.get("traceName") or obs.get("trace_name")
    if trace_name is not None and str(trace_name).strip():
        return str(trace_name).strip()

    if feature_default and feature_default.strip():
        return feature_default.strip()
    return "default"


def _model_name(obs: dict[str, Any]) -> str | None:
    for key in ("providedModelName", "model", "modelId", "internalModelId"):
        val = obs.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _retry_count(obs: dict[str, Any]) -> int:
    meta = _meta(obs)
    for key in ("retry_count", "retries", "retryCount"):
        val = meta.get(key)
        if val is None or val == "":
            continue
        try:
            return max(0, int(float(val)))
        except (TypeError, ValueError):
            continue
    return 0


def _safe_raw_snippet(obs: dict[str, Any]) -> dict[str, Any]:
    """Non-sensitive keys only for drop diagnostics."""
    keys = ("id", "name", "type", "model", "providedModelName", "startTime")
    return {k: obs.get(k) for k in keys if k in obs}


def observation_to_row(
    obs: dict[str, Any],
    *,
    feature_default: str | None = None,
) -> dict[str, Any]:
    """Convert one Langfuse observation to a logical ingest dict.

    Never forwards prompt input/output bodies.
    """
    if not isinstance(obs, dict):
        raise ValueError("observation is not an object")

    call_id = obs.get("id")
    if not call_id:
        raise ValueError("observation missing id")

    model = _model_name(obs)
    if not model:
        raise ValueError("observation missing model")

    start = obs.get("startTime") or obs.get("start_time") or obs.get("createdAt")
    if not start:
        raise ValueError("observation missing startTime")

    input_tokens = _usage_token(obs, "input")
    output_tokens = _usage_token(obs, "output")
    if input_tokens is None:
        raise ValueError("observation missing input token usage")
    if output_tokens is None:
        raise ValueError("observation missing output token usage")

    parent = obs.get("parentObservationId") or obs.get("parent_observation_id")
    if parent is not None:
        parent = str(parent).strip() or None

    row: dict[str, Any] = {
        "timestamp": start,
        "call_id": str(call_id),
        "parent_call_id": parent,
        "feature_tag": _feature_tag(obs, feature_default=feature_default),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": _latency_ms(obs),
        "retry_count": _retry_count(obs),
    }
    cost = _cost_usd(obs)
    if cost is not None:
        row["cost_usd"] = cost
    return row


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
