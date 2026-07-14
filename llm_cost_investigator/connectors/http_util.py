"""Shared HTTP helpers for connectors (httpx + retries)."""

from __future__ import annotations

import time
from typing import Any

from llm_cost_investigator.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorRateLimitError,
)

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "httpx is required for remote connectors. Install with: pip install httpx"
    ) from exc


DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RETRIES = 2


def request_json(
    method: str,
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> dict[str, Any]:
    """GET/POST JSON with retries on 429/502/503."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout or DEFAULT_TIMEOUT) as client:
                response = client.request(
                    method,
                    url,
                    auth=auth,
                    params=params,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            last_error = ConnectorError(f"Request timed out: {url}")
            if attempt >= MAX_RETRIES:
                raise last_error from exc
            time.sleep(0.5 * (2**attempt))
            continue
        except httpx.HTTPError as exc:
            last_error = ConnectorError(f"HTTP error contacting {url}: {exc}")
            if attempt >= MAX_RETRIES:
                raise last_error from exc
            time.sleep(0.5 * (2**attempt))
            continue

        if response.status_code in (401, 403):
            raise ConnectorAuthError(
                f"Langfuse auth failed (HTTP {response.status_code}). "
                "Check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
            )
        if response.status_code == 429:
            if attempt >= MAX_RETRIES:
                raise ConnectorRateLimitError(
                    "Langfuse rate limit exceeded after retries (HTTP 429)."
                )
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            time.sleep(min(delay, 30.0))
            continue
        if response.status_code in (502, 503):
            if attempt >= MAX_RETRIES:
                raise ConnectorError(
                    f"Langfuse temporary error HTTP {response.status_code}"
                )
            time.sleep(0.5 * (2**attempt))
            continue
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ConnectorError(
                f"Langfuse API error HTTP {response.status_code}: {detail}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise ConnectorError(f"Invalid JSON from Langfuse: {exc}") from exc
        if not isinstance(data, dict):
            raise ConnectorError("Langfuse response must be a JSON object")
        return data

    raise last_error or ConnectorError("Request failed")
