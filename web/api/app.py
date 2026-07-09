"""FastAPI application for investigation replay."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.incidents import router as incidents_router
from api.services.catalog import catalog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    count = catalog.load()
    logger.info("Replay catalog loaded: %d incident(s)", count)
    if count == 0:
        logger.warning(
            "Catalog is empty — run: python3 scripts/export_replay_catalog.py"
        )
    yield


app = FastAPI(
    title="LLM Cost Investigator — Replay API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(incidents_router)


@app.get("/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "incidents": len(catalog)}
