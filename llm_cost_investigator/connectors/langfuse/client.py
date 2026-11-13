"""Langfuse Public API client for listing GENERATION observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import urljoin

from llm_cost_investigator.connectors.base import ConnectorError
from llm_cost_investigator.connectors.http_util import request_json
from llm_cost_investigator.connectors.secrets import LangfuseCredentials

PAGE_LIMIT = 100


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def list_observations_pages(
    creds: LangfuseCredentials,
    *,
    from_time: datetime,
    to_time: datetime,
    max_rows: int,
    api_version: str = "v1",
    filters: dict[str, Any] | None = None,
) -> Iterator[tuple[list[dict[str, Any]], int]]:
    """Yield (observations, page_index) until max_rows or exhausted."""
    filters = filters or {}
    collected = 0
    page = 0

    if api_version == "v2":
        yield from _list_v2(
            creds,
            from_time=from_time,
            to_time=to_time,
            max_rows=max_rows,
            filters=filters,
        )
        return

    # v1 offset pagination
    while collected < max_rows:
        page += 1
        params: dict[str, Any] = {
            "type": "GENERATION",
            "page": page,
            "limit": min(PAGE_LIMIT, max_rows - collected),
            "fromStartTime": _iso(from_time),
            "toStartTime": _iso(to_time),
        }
        if filters.get("name"):
            params["name"] = filters["name"]
        if filters.get("userId") or filters.get("user_id"):
            params["userId"] = filters.get("userId") or filters.get("user_id")
        if filters.get("environment"):
            params["environment"] = filters["environment"]

        url = urljoin(creds.host + "/", "api/public/observations")
        data = request_json(
            "GET",
            url,
            auth=(creds.public_key, creds.secret_key),
            params=params,
        )
        items = data.get("data")
        if items is None:
            # some versions nest differently
            items = data.get("observations") or []
        if not isinstance(items, list):
            raise ConnectorError("Langfuse v1 observations response missing data list")

        # Only GENERATION (filter client-side too)
        gens = [
            o
            for o in items
            if isinstance(o, dict) and str(o.get("type", "GENERATION")).upper() == "GENERATION"
        ]
        yield gens, page
        collected += len(gens)

        # Stop if page shorter than requested limit
        meta = data.get("meta") or {}
        total_pages = meta.get("totalPages")
        if total_pages is not None:
            try:
                if page >= int(total_pages):
                    break
            except (TypeError, ValueError):
                pass
        if len(items) < params["limit"]:
            break
        if not items:
            break


def _list_v2(
    creds: LangfuseCredentials,
    *,
    from_time: datetime,
    to_time: datetime,
    max_rows: int,
    filters: dict[str, Any],
) -> Iterator[tuple[list[dict[str, Any]], int]]:
    """Cursor pagination for Observations API v2 (Langfuse Cloud)."""
    collected = 0
    page = 0
    cursor: str | None = None
    # Omit io field group — no prompts
    fields = "core,basic,model,usage,metrics,trace_context"

    while collected < max_rows:
        page += 1
        params: dict[str, Any] = {
            "type": "GENERATION",
            "limit": min(PAGE_LIMIT, max_rows - collected),
            "fromStartTime": _iso(from_time),
            "toStartTime": _iso(to_time),
            "fields": fields,
        }
        if cursor:
            params["cursor"] = cursor
        if filters.get("name"):
            params["name"] = filters["name"]
        if filters.get("userId") or filters.get("user_id"):
            params["userId"] = filters.get("userId") or filters.get("user_id")
        if filters.get("environment"):
            params["environment"] = filters["environment"]

        url = urljoin(creds.host + "/", "api/public/v2/observations")
        data = request_json(
            "GET",
            url,
            auth=(creds.public_key, creds.secret_key),
            params=params,
        )
        items = data.get("data") or []
        if not isinstance(items, list):
            raise ConnectorError("Langfuse v2 observations response missing data list")

        gens = [
            o
            for o in items
            if isinstance(o, dict) and str(o.get("type", "GENERATION")).upper() == "GENERATION"
        ]
        yield gens, page
        collected += len(gens)

        meta = data.get("meta") or {}
        cursor = meta.get("cursor")
        if not cursor:
            break
        if not items:
            break
