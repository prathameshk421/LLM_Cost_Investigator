"""Deterministic anomaly detection stubs."""

from __future__ import annotations

from llm_cost_investigator.schemas import AnomalyWindow, LLMCall


def detect_anomalies(calls: list[LLMCall]) -> list[AnomalyWindow]:
    """Detect anomalous windows from telemetry."""
    raise NotImplementedError("Anomaly detection is not implemented yet.")
