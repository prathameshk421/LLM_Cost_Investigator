"""FastAPI application for investigation replay."""

from __future__ import annotations

import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import TokenAuthMiddleware, api_token
from api.routes.connectors import router as connectors_router
from api.routes.incidents import router as incidents_router
from api.routes.investigate import router as investigate_router
from api.routes.lab import router as lab_router
from api.routes.analytics import router as analytics_router
from api.routes.stream import router as stream_router
from llm_cost_investigator.ingest_service import start_background, stop_background
from api.services.catalog import catalog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not api_token():
        logger.warning(
            "LLMCI_API_TOKEN is not set: the API is open. Set it before exposing this server."
        )
    # Open the warehouse now so a lock conflict fails start-up with a clear
    # message, not a 500 on the first request. DuckDB allows one writer
    # process per file (see docs/ARCHITECTURE.md).
    from llm_cost_investigator.warehouse import get_warehouse

    try:
        wh = get_warehouse()
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open the warehouse ({exc}). Another API process probably "
            "holds the DuckDB file. Stop it, or set LLMCI_WAREHOUSE to another path."
        ) from exc
    logger.info("Warehouse %s: %d calls", wh.path, wh.count())
    start_background()  # ingest writer + detector tick threads
    count = catalog.load()
    logger.info("Replay catalog loaded: %d incident(s)", count)
    if count == 0:
        logger.warning(
            "Catalog is empty — run: python3 scripts/export_replay_catalog.py"
        )
    yield
    stop_background()


app = FastAPI(
    title="LLM Cost Investigator API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(TokenAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Stream first: its /api/incidents/live must win over the replay
# catalog's /api/incidents/{incident_id} catch-all.
app.include_router(stream_router)
app.include_router(incidents_router)
app.include_router(investigate_router)
app.include_router(connectors_router)
app.include_router(lab_router)
app.include_router(analytics_router)


@app.get("/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "incidents": len(catalog)}


# Serve the built UI from the same port when it exists (Docker / `make serve`).
# Mounted last, so every API route above wins over the static catch-all.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="ui")
