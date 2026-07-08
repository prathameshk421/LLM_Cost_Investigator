"""SQLite storage helpers for simulated telemetry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from llm_cost_investigator.schemas import LLMCall

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_calls (
  call_id TEXT PRIMARY KEY,
  parent_call_id TEXT,
  timestamp TEXT NOT NULL,
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

INDEX_FEATURE_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_feature_time ON llm_calls (feature_tag, timestamp);
"""

INDEX_PARENT_SQL = """
CREATE INDEX IF NOT EXISTS idx_parent ON llm_calls (parent_call_id);
"""


class TelemetryStore:
    """SQLite-backed database connection and query manager for LLM telemetry."""

    def __init__(self, db_path: str | Path = "telemetry.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

        # Performance and integrity tuning
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self.init_schema()

    def init_schema(self) -> None:
        """Initialize table and indexes if they do not exist."""
        self._conn.execute(SCHEMA_SQL)
        self._conn.execute(INDEX_FEATURE_TIME_SQL)
        self._conn.execute(INDEX_PARENT_SQL)
        self._conn.commit()

    def __enter__(self) -> TelemetryStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @classmethod
    def from_connection(cls, conn: sqlite3.Connection) -> TelemetryStore:
        """Wrap an existing sqlite3.Connection in a TelemetryStore interface."""
        store = cls.__new__(cls)
        store.db_path = getattr(conn, "db_path", ":memory:")
        store._conn = conn
        return store

    def close(self) -> None:
        """Close the SQLite database connection."""
        self._conn.close()

    def _row_to_call(self, row: sqlite3.Row) -> LLMCall:
        """Convert a sqlite3.Row record to a validated LLMCall model."""
        return LLMCall(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            call_id=row["call_id"],
            parent_call_id=row["parent_call_id"],
            feature_tag=row["feature_tag"],
            model=row["model"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            latency_ms=row["latency_ms"],
            retry_count=row["retry_count"],
            scenario_label=row["scenario_label"],
        )

    def insert_call(self, call: LLMCall) -> None:
        """Insert a single LLMCall record.

        Let IntegrityError propagate to catch simulator or caller bugs.
        """
        self._conn.execute(
            """
            INSERT INTO llm_calls (
                call_id,
                parent_call_id,
                timestamp,
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
                call.call_id,
                call.parent_call_id,
                call.timestamp.isoformat(),
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
        self._conn.commit()

    def insert_calls(self, calls: Iterable[LLMCall]) -> None:
        """Insert multiple LLMCall records inside a single transaction."""
        rows = [
            (
                call.call_id,
                call.parent_call_id,
                call.timestamp.isoformat(),
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
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO llm_calls (
                    call_id,
                    parent_call_id,
                    timestamp,
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

    def get_calls_for_feature(
        self,
        feature_tag: str,
        start: datetime,
        end: datetime,
    ) -> list[LLMCall]:
        """Fetch all calls for a feature within a datetime range, sorted chronologically."""
        cursor = self._conn.execute(
            """
            SELECT * FROM llm_calls
            WHERE feature_tag = ?
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC, call_id ASC
            """,
            (feature_tag, start.isoformat(), end.isoformat()),
        )
        return [self._row_to_call(row) for row in cursor]

    def get_baseline_calls(
        self,
        feature_tag: str,
        before: datetime,
        lookback: timedelta,
    ) -> list[LLMCall]:
        """Fetch historical baseline calls prior to a given datetime."""
        start = before - lookback
        return self.get_calls_for_feature(feature_tag, start, before)

    def get_call_chain(self, call_id: str) -> list[LLMCall]:
        """Walk the call chain upwards to root, returning it root-first.

        Protects against cycles with a depth threshold.
        """
        MAX_DEPTH = 50
        chain: list[LLMCall] = []
        current_id: str | None = call_id

        # To avoid infinite loop on cycle, track seen IDs
        seen: set[str] = set()

        while current_id and len(chain) < MAX_DEPTH:
            if current_id in seen:
                break
            seen.add(current_id)

            cursor = self._conn.execute(
                "SELECT * FROM llm_calls WHERE call_id = ?",
                (current_id,),
            )
            row = cursor.fetchone()
            if not row:
                break

            call = self._row_to_call(row)
            chain.append(call)
            current_id = call.parent_call_id

        # Order root-first (meaning oldest parent first)
        return list(reversed(chain))

    def get_children(self, parent_call_id: str) -> list[LLMCall]:
        """Fetch direct child calls for a given parent_call_id."""
        cursor = self._conn.execute(
            """
            SELECT * FROM llm_calls
            WHERE parent_call_id = ?
            ORDER BY timestamp ASC, call_id ASC
            """,
            (parent_call_id,),
        )
        return [self._row_to_call(row) for row in cursor]

    def get_distinct_feature_tags(self) -> list[str]:
        """Fetch all unique feature_tag values present in the database."""
        cursor = self._conn.execute(
            "SELECT DISTINCT feature_tag FROM llm_calls ORDER BY feature_tag ASC"
        )
        return [row[0] for row in cursor]

    # --- replay/test helpers only ---

    def get_calls_by_scenario(self, scenario_label: str) -> list[LLMCall]:
        """Fetch all calls for a specific scenario (used only for replay/demo)."""
        cursor = self._conn.execute(
            """
            SELECT * FROM llm_calls
            WHERE scenario_label = ?
            ORDER BY timestamp ASC, call_id ASC
            """,
            (scenario_label,),
        )
        return [self._row_to_call(row) for row in cursor]


# ---------------------------------------------------------------------------
# Module-level convenience API matching implementation_plan.md signatures
# ---------------------------------------------------------------------------

def _store_from_conn(conn: sqlite3.Connection) -> TelemetryStore:
    """Wrap a raw connection in a TelemetryStore without re-initialising."""
    return TelemetryStore.from_connection(conn)

def init_db(path: str | Path = "telemetry.db") -> sqlite3.Connection:
    """Create or open a SQLite database and initialise the schema.

    Returns the raw connection so callers can pass it to the other
    module-level helpers.
    """
    raw_conn = sqlite3.connect(str(path))
    raw_conn.row_factory = sqlite3.Row
    store = _store_from_conn(raw_conn)
    store.init_schema()
    return raw_conn

def insert_call(conn: sqlite3.Connection, call: LLMCall) -> None:
    """Insert a single LLMCall record through an existing connection."""
    _store_from_conn(conn).insert_call(call)

def insert_calls(conn: sqlite3.Connection, calls: Iterable[LLMCall]) -> None:
    """Insert multiple LLMCall records through an existing connection."""
    _store_from_conn(conn).insert_calls(calls)

def fetch_calls(
    conn: sqlite3.Connection,
    scenario: str | None = None,
) -> list[LLMCall]:
    """Fetch all calls, optionally filtered by scenario_label."""
    store = _store_from_conn(conn)
    if scenario is not None:
        return store.get_calls_by_scenario(scenario)
    cursor = conn.execute(
        "SELECT * FROM llm_calls ORDER BY timestamp ASC, call_id ASC"
    )
    return [store._row_to_call(row) for row in cursor]

def fetch_window(
    conn: sqlite3.Connection,
    feature_tag: str,
    start: datetime,
    end: datetime,
) -> list[LLMCall]:
    """Fetch calls for a feature within a datetime range."""
    return _store_from_conn(conn).get_calls_for_feature(feature_tag, start, end)
