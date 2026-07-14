"""Glue: upload bytes → ingest → investigate → stage-shaped incidents."""

from __future__ import annotations

import json
from typing import Any

from api.models.investigate import (
    IngestSummary,
    InvestigateResponse,
    InvestigationSummary,
    LiveIncident,
    PreviewResponse,
)
from api.services.stage_builder import incident_report_to_replay
from llm_cost_investigator.ingest import (
    IngestOptions,
    guess_column_mapping,
    load_telemetry,
)
from llm_cost_investigator.investigate import investigate_calls


MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _parse_column_mapping(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("column_mapping JSON must be an object")
        return {str(k): str(v) for k, v in data.items()}
    # CSV-style key=col pairs
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid mapping entry {part!r}")
        k, v = part.split("=", 1)
        mapping[k.strip()] = v.strip()
    return mapping or None


def _ingest_summary(report) -> IngestSummary:
    return IngestSummary(
        adapter=report.adapter,
        accepted=report.accepted,
        dropped=report.dropped,
        warnings=list(report.warnings),
        cost_estimated_count=report.cost_estimated_count,
        sample_dropped=list(report.sample_dropped),
        detected_headers=report.detected_headers,
    )


def preview_bytes(
    data: bytes,
    *,
    filename: str,
    adapter: str = "auto",
    feature_default: str | None = None,
    column_mapping: str | None = None,
    csv_delimiter: str | None = None,
) -> PreviewResponse:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES} bytes")

    mapping = _parse_column_mapping(column_mapping)
    options = IngestOptions(
        feature_default=feature_default,
        column_mapping=mapping,
        csv_delimiter=csv_delimiter,
        source_name=filename,
    )
    calls, report = load_telemetry(
        data, adapter=adapter, options=options, filename=filename  # type: ignore[arg-type]
    )
    features = sorted({c.feature_tag for c in calls})
    samples = [
        c.model_dump(mode="json")
        for c in calls[:5]
    ]
    guessed = None
    if report.detected_headers:
        guessed = guess_column_mapping(report.detected_headers)
    return PreviewResponse(
        ingest=_ingest_summary(report),
        feature_tags=features,
        sample_calls=samples,
        guessed_mapping=guessed,
    )


def investigate_bytes(
    data: bytes,
    *,
    filename: str,
    adapter: str = "auto",
    feature_default: str | None = None,
    column_mapping: str | None = None,
    csv_delimiter: str | None = None,
    label: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    force_fallback: bool = False,
    max_anomalies: int = 5,
    write_reports: bool = True,
) -> InvestigateResponse:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES} bytes")

    mapping = _parse_column_mapping(column_mapping)
    options = IngestOptions(
        feature_default=feature_default,
        column_mapping=mapping,
        csv_delimiter=csv_delimiter,
        source_name=filename,
    )
    calls, report = load_telemetry(
        data, adapter=adapter, options=options, filename=filename  # type: ignore[arg-type]
    )
    if report.accepted == 0:
        raise ValueError("No accepted rows after ingest")

    resolved_provider = "fallback" if force_fallback else provider
    stem = label or (filename.rsplit(".", 1)[0] if filename else "upload")
    result = investigate_calls(
        calls,
        label=stem,
        provider=resolved_provider,
        model=model,
        max_anomalies=max(1, min(max_anomalies, 20)),
        write_reports=write_reports,
        report_naming="label_feature_index",
    )

    live_incidents: list[LiveIncident] = []
    for i, incident_report in enumerate(result.incidents):
        rid = f"live_{incident_report.scenario}"
        replay = incident_report_to_replay(
            incident_report,
            incident_id=rid,
            title=(
                f"{incident_report.anomaly_window.feature_tag} — "
                f"{incident_report.root_cause.hypothesis}"
            ),
            source_files=[filename],
        )
        live_incidents.append(
            LiveIncident.from_replay(
                replay,
                raw_report=incident_report.model_dump(mode="json"),
            )
        )

    return InvestigateResponse(
        ingest=_ingest_summary(report),
        summary=InvestigationSummary(
            label=result.label,
            call_count=result.call_count,
            feature_tags=result.feature_tags,
            anomaly_count=result.anomaly_count,
            no_anomaly_reason=result.no_anomaly_reason,
        ),
        incidents=live_incidents,
    )
