"""One-shot investigate and preview endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.models.investigate import InvestigateResponse, PreviewResponse, PricingEntry
from api.services.live_investigate import investigate_bytes, preview_bytes
from llm_cost_investigator.ingest import known_models

router = APIRouter(prefix="/api/investigate", tags=["investigate"])


@router.get("/pricing", response_model=list[PricingEntry])
def list_pricing() -> list[PricingEntry]:
    return [PricingEntry(**row) for row in known_models()]


@router.post("/preview", response_model=PreviewResponse)
async def preview_ingest(
    file: UploadFile = File(...),
    adapter: str = Form("auto"),
    feature_default: str | None = Form(None),
    column_mapping: str | None = Form(None),
    csv_delimiter: str | None = Form(None),
) -> PreviewResponse:
    data = await file.read()
    filename = file.filename or "upload.bin"
    try:
        return preview_bytes(
            data,
            filename=filename,
            adapter=adapter,  # type: ignore[arg-type]
            feature_default=feature_default or None,
            column_mapping=column_mapping,
            csv_delimiter=csv_delimiter or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=InvestigateResponse)
async def run_investigate(
    file: UploadFile = File(...),
    adapter: str = Form("auto"),
    feature_default: str | None = Form(None),
    column_mapping: str | None = Form(None),
    csv_delimiter: str | None = Form(None),
    label: str | None = Form(None),
    provider: str | None = Form(None),
    model: str | None = Form(None),
    force_fallback: bool = Form(True),
    max_anomalies: int = Form(5),
) -> InvestigateResponse:
    data = await file.read()
    filename = file.filename or "upload.bin"
    try:
        return investigate_bytes(
            data,
            filename=filename,
            adapter=adapter,  # type: ignore[arg-type]
            feature_default=feature_default or None,
            column_mapping=column_mapping,
            csv_delimiter=csv_delimiter or None,
            label=label or None,
            provider=provider or None,
            model=model or None,
            force_fallback=force_fallback,
            max_anomalies=max_anomalies,
            write_reports=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
