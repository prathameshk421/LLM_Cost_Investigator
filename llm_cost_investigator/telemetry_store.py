"""SQLite storage helpers for simulated telemetry."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from llm_cost_investigator.schemas import LLMCall

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_calls (
  timestamp TEXT NOT NULL,
  call_id TEXT PRIMARY KEY,
  parent_call_id TEXT,
  feature_tag TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  latency_ms INTEGER NOT NULL,
  retry_count INTEGER NOT NULL,
  scenario_label TEXT
);
"""


def init_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn


def insert_call(conn: sqlite3.Connection, call: LLMCall) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls (
            timestamp,
            call_id,
            parent_call_id,
            feature_tag,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            retry_count,
            scenario_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call.timestamp.isoformat(),
            call.call_id,
            call.parent_call_id,
            call.feature_tag,
            call.model,
            call.input_tokens,
            call.output_tokens,
            call.cost_usd,
            call.latency_ms,
            call.retry_count,
            call.scenario_label,
        ),
    )
    conn.commit()


def insert_calls(conn: sqlite3.Connection, calls: Iterable[LLMCall]) -> None:
    rows = [
        (
            call.timestamp.isoformat(),
            call.call_id,
            call.parent_call_id,
            call.feature_tag,
            call.model,
            call.input_tokens,
            call.output_tokens,
            call.cost_usd,
            call.latency_ms,
            call.retry_count,
            call.scenario_label,
        )
        for call in calls
    ]
    conn.executemany(
        """
        INSERT INTO llm_calls (
            timestamp,
            call_id,
            parent_call_id,
            feature_tag,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            retry_count,
            scenario_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def fetch_calls(conn: sqlite3.Connection, scenario: str | None = None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if scenario is None:
        cursor = conn.execute("SELECT * FROM llm_calls ORDER BY timestamp, call_id")
    else:
        cursor = conn.execute(
            "SELECT * FROM llm_calls WHERE scenario_label = ? ORDER BY timestamp, call_id",
            (scenario,),
        )
    return list(cursor.fetchall())


def fetch_window(
    conn: sqlite3.Connection,
    feature_tag: str,
    start: str,
    end: str,
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT * FROM llm_calls
        WHERE feature_tag = ?
          AND timestamp >= ?
          AND timestamp <= ?
        ORDER BY timestamp, call_id
        """,
        (feature_tag, start, end),
    )
    return list(cursor.fetchall())
