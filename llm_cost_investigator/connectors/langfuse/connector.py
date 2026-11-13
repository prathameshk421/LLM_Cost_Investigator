"""Langfuse LogConnector implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from llm_cost_investigator.connectors.base import PullRequest
from llm_cost_investigator.connectors.langfuse.client import list_observations_pages
from llm_cost_investigator.connectors.langfuse.map import observation_to_row
from llm_cost_investigator.connectors.secrets import (
    LangfuseCredentials,
    resolve_langfuse_credentials,
)
from llm_cost_investigator.ingest.base import materialize_rows
from llm_cost_investigator.ingest.types import IngestOptions, IngestReport
from llm_cost_investigator.schemas import LLMCall


class LangfuseConnector:
    """Pull GENERATION observations and normalize to LLMCall."""

    name = "langfuse"

    def __init__(
        self,
        *,
        host: str | None = None,
        api_version: str = "v1",
        connection_name: str | None = None,
        creds: LangfuseCredentials | None = None,
    ) -> None:
        self.api_version = api_version if api_version in ("v1", "v2") else "v1"
        self.connection_name = connection_name
        self._creds = creds or resolve_langfuse_credentials(host_override=host)

    def pull(
        self, request: PullRequest
    ) -> tuple[list[LLMCall], IngestReport, dict[str, Any]]:
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(hours=float(request.since_hours))
        max_rows = int(request.max_rows)

        raw_rows: list[dict[str, Any]] = []
        map_warnings: list[str] = []
        map_dropped = 0
        sample_dropped: list[dict[str, Any]] = []
        pages = 0
        truncated = False

        for observations, page_idx in list_observations_pages(
            self._creds,
            from_time=from_time,
            to_time=to_time,
            max_rows=max_rows,
            api_version=self.api_version,
            filters=request.filters,
        ):
            pages = page_idx
            for obs in observations:
                if len(raw_rows) >= max_rows:
                    truncated = True
                    break
                try:
                    row = observation_to_row(
                        obs, feature_default=request.feature_default
                    )
                    raw_rows.append(row)
                except Exception as exc:
                    map_dropped += 1
                    if len(sample_dropped) < 10:
                        sample_dropped.append(
                            {
                                "row_index": map_dropped + len(raw_rows) - 1,
                                "reason": str(exc),
                                "raw": {
                                    k: obs.get(k)
                                    for k in (
                                        "id",
                                        "name",
                                        "type",
                                        "model",
                                        "providedModelName",
                                        "startTime",
                                    )
                                    if isinstance(obs, dict) and k in obs
                                },
                            }
                        )
            if len(raw_rows) >= max_rows:
                truncated = True
                break

        if truncated:
            map_warnings.append(
                f"Pull truncated at max_rows={max_rows}; more observations may exist"
            )

        options = IngestOptions(
            feature_default=request.feature_default,
            max_rows=max_rows,
            source_name=f"langfuse:{urlparse(self._creds.host).netloc or 'host'}",
        )
        calls, report = materialize_rows(
            raw_rows,
            adapter_name="langfuse",
            options=options,
            extra_warnings=map_warnings,
        )
        # Fold pre-normalize drop counts into report
        if map_dropped:
            report = IngestReport(
                adapter=report.adapter,
                accepted=report.accepted,
                dropped=report.dropped + map_dropped,
                warnings=list(report.warnings)
                + [f"langfuse map dropped {map_dropped} observation(s)"],
                cost_estimated_count=report.cost_estimated_count,
                sample_dropped=list(sample_dropped) + list(report.sample_dropped),
                detected_headers=report.detected_headers,
            )

        host_display = urlparse(self._creds.host).netloc or self._creds.host
        meta = {
            "connector": self.name,
            "host": host_display,
            "from_time": from_time.isoformat(),
            "to_time": to_time.isoformat(),
            "pages_fetched": pages,
            "raw_observations": len(raw_rows) + map_dropped,
            "api_version": self.api_version,
            "connection": self.connection_name,
            "truncated": truncated,
        }
        return calls, report, meta
