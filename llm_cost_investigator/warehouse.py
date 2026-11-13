"""Persistent telemetry warehouse (DuckDB).

DuckDB is a columnar, in-process database. On 1M calls the spend rollups this
product needs run 65x to 260x faster than SQLite (3 ms vs 678 ms for spend by
feature x day), and the file is 9x smaller. See docs/ARCHITECTURE.md.

One process owns the database file. Every writer (HTTP ingest, file upload,
Kafka bridge) goes through ``Warehouse.insert_calls`` in that process, so the
single-writer rule of DuckDB is never broken. Each thread uses its own cursor.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from llm_cost_investigator.schemas import LLMCall

__all__ = ["Warehouse", "get_warehouse", "reset_warehouse"]

DEFAULT_PATH = "data/warehouse.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
  call_id        VARCHAR PRIMARY KEY,
  parent_call_id VARCHAR,
  ts             TIMESTAMP NOT NULL,             -- naive UTC
  feature_tag    VARCHAR NOT NULL,
  model          VARCHAR NOT NULL,
  input_tokens   INTEGER NOT NULL,
  output_tokens  INTEGER NOT NULL,
  cost_usd       DOUBLE NOT NULL,
  latency_ms     INTEGER NOT NULL,
  retry_count    INTEGER NOT NULL,
  source         VARCHAR NOT NULL DEFAULT 'api',
  ingested_at    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS budgets (
  budget_id      VARCHAR PRIMARY KEY,
  scope          VARCHAR NOT NULL,           -- 'global' | 'feature' | 'model'
  scope_value    VARCHAR,                    -- feature tag / model, NULL for global
  monthly_usd    DOUBLE NOT NULL,
  alert_pct      DOUBLE NOT NULL DEFAULT 0.8,
  created_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS incidents (
  incident_id    VARCHAR PRIMARY KEY,
  feature_tag    VARCHAR NOT NULL,
  start_ts       TIMESTAMP NOT NULL,
  end_ts         TIMESTAMP NOT NULL,
  hypothesis     VARCHAR NOT NULL,
  confidence     DOUBLE NOT NULL,
  cost_usd       DOUBLE NOT NULL DEFAULT 0,
  status         VARCHAR NOT NULL DEFAULT 'open',   -- open | resolved
  report_json    VARCHAR,                           -- IncidentReport JSON
  created_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
"""

_COLUMNS = (
    "call_id, parent_call_id, ts, feature_tag, model, input_tokens, "
    "output_tokens, cost_usd, latency_ms, retry_count, source"
)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _naive(ts: datetime) -> datetime:
    """Stored form: naive UTC (DuckDB TIMESTAMPTZ reads need pytz)."""
    return _utc(ts).replace(tzinfo=None)


class Warehouse:
    """Thread-safe facade over one DuckDB database."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(self.path)
        self._write_lock = threading.Lock()
        self._conn.execute(SCHEMA)

    # -- connections -------------------------------------------------------

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A cursor for the calling thread (DuckDB cursors are not shared)."""
        return self._conn.cursor()

    def close(self) -> None:
        self._conn.close()

    # -- writes ------------------------------------------------------------

    def insert_calls(self, calls: Iterable[LLMCall], source: str = "api") -> int:
        """Insert calls, skip duplicate call_ids. Returns the number inserted."""
        rows = [
            (
                c.call_id,
                c.parent_call_id,
                _naive(c.timestamp),
                c.feature_tag,
                c.model,
                c.input_tokens,
                c.output_tokens,
                c.cost_usd,
                c.latency_ms,
                c.retry_count,
                source,
            )
            for c in calls
        ]
        if not rows:
            return 0
        # Columnar bulk insert: 11 lists, one statement. executemany is
        # row-at-a-time in DuckDB (about 12 s for 48k rows); UNNEST of
        # list parameters loads the same rows in milliseconds.
        cols = list(zip(*rows))
        names = [c.strip() for c in _COLUMNS.split(",")]
        select = ", ".join(f"UNNEST(?) AS {n}" for n in names)
        with self._write_lock:
            cur = self._conn.cursor()
            before = cur.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
            cur.execute(
                f"INSERT INTO llm_calls ({_COLUMNS}) "
                f"SELECT DISTINCT ON (call_id) * FROM (SELECT {select}) "
                "ON CONFLICT (call_id) DO NOTHING",
                [list(c) for c in cols],
            )
            after = cur.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
            return int(after - before)

    def clear(self) -> None:
        with self._write_lock:
            self._conn.execute("DELETE FROM llm_calls")

    # -- reads -------------------------------------------------------------

    def count(self) -> int:
        return int(self.cursor().execute("SELECT count(*) FROM llm_calls").fetchone()[0])

    def query(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Run a read query and return rows as dicts."""
        cur = self.cursor()
        res = cur.execute(sql, params or [])
        cols = [d[0] for d in res.description]
        return [dict(zip(cols, row)) for row in res.fetchall()]

    def calls_between(
        self,
        start: datetime,
        end: datetime,
        feature_tag: str | None = None,
    ) -> list[LLMCall]:
        """Hydrate LLMCall objects for the detector / agents."""
        sql = (
            "SELECT call_id, parent_call_id, ts, feature_tag, model, input_tokens, "
            "output_tokens, cost_usd, latency_ms, retry_count FROM llm_calls "
            "WHERE ts >= ? AND ts < ?"
        )
        params: list[Any] = [_naive(start), _naive(end)]
        if feature_tag:
            sql += " AND feature_tag = ?"
            params.append(feature_tag)
        sql += " ORDER BY ts, call_id"
        out = []
        for r in self.cursor().execute(sql, params).fetchall():
            out.append(
                LLMCall(
                    call_id=r[0],
                    parent_call_id=r[1],
                    timestamp=_utc(r[2]),
                    feature_tag=r[3],
                    model=r[4],
                    input_tokens=r[5],
                    output_tokens=r[6],
                    cost_usd=r[7],
                    latency_ms=r[8],
                    retry_count=r[9],
                )
            )
        return out

    def time_range(self) -> tuple[datetime | None, datetime | None]:
        lo, hi = self.cursor().execute("SELECT min(ts), max(ts) FROM llm_calls").fetchone()
        return (_utc(lo) if lo else None, _utc(hi) if hi else None)


_singleton: Warehouse | None = None
_singleton_lock = threading.Lock()


def get_warehouse() -> Warehouse:
    """Process-wide warehouse. Path from ``LLMCI_WAREHOUSE`` (``:memory:`` in tests)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = Warehouse(os.environ.get("LLMCI_WAREHOUSE", DEFAULT_PATH))
        return _singleton


def reset_warehouse(path: str | None = None) -> Warehouse:
    """Replace the singleton (tests)."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = Warehouse(path or os.environ.get("LLMCI_WAREHOUSE", DEFAULT_PATH))
        return _singleton
