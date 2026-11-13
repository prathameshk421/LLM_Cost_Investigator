"""Live ingest: bounded queue, batch writer, SSE pub/sub, detector tick, demo generator.

Threads and locks
-----------------
- ``_q`` is a bounded ``queue.Queue`` (max ``MAX_QUEUE`` events). ``_enqueue_lock`` makes
  "check space, then put" atomic, so a request is accepted whole or rejected whole (429).
- The writer thread drains ``_q`` in batches (<= 500 rows or 1 s) and calls
  ``Warehouse.insert_calls``. ``_flush_lock`` serialises writers (thread or ``flush()``).
- The tick thread runs ``tick_once()`` every ``LLMCI_TICK_S`` seconds. ``_dirty`` (feature ->
  newest ts) is guarded by ``_dirty_lock``; ``_tick_lock`` serialises ticks.
- SSE subscribers each own an ``asyncio.Queue``. ``publish`` runs on any thread and hands the
  message over with ``loop.call_soon_threadsafe``; a full queue drops its oldest message.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import queue
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from llm_cost_investigator.ingest.pricing import estimate_cost_usd
from llm_cost_investigator.schemas import AnomalyWindow, IncidentReport, LLMCall
from llm_cost_investigator.warehouse import get_warehouse

logger = logging.getLogger(__name__)

__all__ = [
    "IngestService",
    "DemoGenerator",
    "QueueFull",
    "get_service",
    "reset_service",
    "start_background",
    "stop_background",
]

MAX_QUEUE = 50_000
BATCH_ROWS = 500
BATCH_SECONDS = 1.0
LOOKBACK = timedelta(hours=6)
SUBSCRIBER_MAX = 256
INCIDENT_TYPES = ("model_misroute", "retry_loop", "context_bloat")


class QueueFull(Exception):
    """The ingest queue cannot take the whole batch."""


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _naive_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def _aware_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# SSE pub/sub
# ---------------------------------------------------------------------------


class Subscriber:
    """One SSE client. Messages are ``(event_type, data_dict)`` tuples."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = SUBSCRIBER_MAX) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def _offer(self, msg: tuple[str, dict[str, Any]]) -> None:
        # Runs on the loop thread.
        while True:
            try:
                self.queue.put_nowait(msg)
                return
            except asyncio.QueueFull:
                try:
                    self.queue.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:  # pragma: no cover
                    return


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class IngestService:
    def __init__(self, max_queue: int = MAX_QUEUE) -> None:
        self.max_queue = max_queue
        self._q: queue.Queue[tuple[LLMCall, str]] = queue.Queue(maxsize=max_queue)
        self._enqueue_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._dirty: dict[str, datetime] = {}
        self._dirty_lock = threading.Lock()
        self._subs: list[Subscriber] = []
        self._subs_lock = threading.Lock()
        self._rate: collections.deque[tuple[int, int]] = collections.deque()
        self._rate_lock = threading.Lock()
        self._first_event_t: float | None = None
        self._budget_seen: set[tuple[str, str]] = set()
        self._stats_cache: tuple[float, dict[str, Any]] | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._thread_lock = threading.Lock()
        self.demo: DemoGenerator | None = None
        self.written = 0
        self.write_errors = 0

    # -- enqueue -----------------------------------------------------------

    def enqueue(self, calls: list[LLMCall], source: str = "api") -> int:
        """Queue calls all-or-nothing. Raises ``QueueFull`` (HTTP 429)."""
        if not calls:
            return 0
        with self._enqueue_lock:
            if self._q.qsize() + len(calls) > self.max_queue:
                raise QueueFull(f"queue holds {self._q.qsize()} of {self.max_queue}")
            for call in calls:
                self._q.put_nowait((call, source))
        self._count_events(len(calls))
        return len(calls)

    def queue_depth(self) -> int:
        return self._q.qsize()

    def _count_events(self, n: int) -> None:
        now = time.time()
        sec = int(now)
        with self._rate_lock:
            if self._first_event_t is None:
                self._first_event_t = now
            if self._rate and self._rate[-1][0] == sec:
                self._rate[-1] = (sec, self._rate[-1][1] + n)
            else:
                self._rate.append((sec, n))
            while self._rate and self._rate[0][0] < sec - 10:
                self._rate.popleft()

    def events_per_sec(self) -> float:
        now = time.time()
        sec = int(now)
        with self._rate_lock:
            total = sum(n for s, n in self._rate if s >= sec - 9)
            first = self._first_event_t
        if not total or first is None:
            return 0.0
        span = min(10.0, max(1.0, now - first))
        return round(total / span, 2)

    # -- writer ------------------------------------------------------------

    def _drain(self, max_rows: int | None, deadline: float | None) -> list[tuple[LLMCall, str]]:
        batch: list[tuple[LLMCall, str]] = []
        while max_rows is None or len(batch) < max_rows:
            try:
                if deadline is None:
                    batch.append(self._q.get_nowait())
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    batch.append(self._q.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    def flush(self) -> int:
        """Write everything queued right now, in the calling thread. Returns rows inserted."""
        inserted = 0
        while True:
            with self._flush_lock:
                batch = self._drain(BATCH_ROWS, None)
                if not batch:
                    return inserted
                inserted += self._write(batch)

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._flush_lock:
                batch = [first] + self._drain(BATCH_ROWS - 1, time.monotonic() + BATCH_SECONDS)
                try:
                    self._write(batch)
                except Exception:  # pragma: no cover - _write already guards
                    logger.exception("writer failed")
        self.flush()

    def _write(self, batch: list[tuple[LLMCall, str]]) -> int:
        by_source: dict[str, list[LLMCall]] = {}
        for call, source in batch:
            by_source.setdefault(source, []).append(call)
        w = get_warehouse()
        inserted = 0
        for source, calls in by_source.items():
            try:
                inserted += w.insert_calls(calls, source=source)
            except Exception:
                self.write_errors += len(calls)
                logger.exception("insert_calls failed for %d rows (source=%s)", len(calls), source)
        calls_all = [c for c, _ in batch]
        self.written += inserted
        self._mark_dirty(calls_all)
        by_feature: dict[str, float] = {}
        for c in calls_all:
            by_feature[c.feature_tag] = by_feature.get(c.feature_tag, 0.0) + c.cost_usd
        self.publish(
            "ingest",
            {
                "count": inserted,
                "spend_usd": round(sum(by_feature.values()), 6),
                "by_feature": {k: round(v, 6) for k, v in by_feature.items()},
                "ts": _iso(datetime.now(timezone.utc)),
            },
        )
        as_of = max(_aware_utc(c.timestamp) for c in calls_all)
        self._check_budgets(w, as_of)
        return inserted

    def write_direct(self, calls: list[LLMCall], source: str = "demo", chunk: int = 5000) -> int:
        """Bulk write bypassing the queue (demo backfill). Marks features dirty."""
        w = get_warehouse()
        inserted = 0
        for i in range(0, len(calls), chunk):
            part = calls[i : i + chunk]
            inserted += w.insert_calls(part, source=source)
            self._mark_dirty(part)
        return inserted

    def _mark_dirty(self, calls: Iterable[LLMCall]) -> None:
        with self._dirty_lock:
            for c in calls:
                ts = _aware_utc(c.timestamp)
                cur = self._dirty.get(c.feature_tag)
                if cur is None or ts > cur:
                    self._dirty[c.feature_tag] = ts

    # -- budgets -----------------------------------------------------------

    def _check_budgets(self, w: Any, as_of: datetime) -> None:
        try:
            from llm_cost_investigator import budgets  # type: ignore[attr-defined]
        except Exception:
            return
        try:
            rows = budgets.check_thresholds(w, as_of)
        except Exception:
            logger.exception("budgets.check_thresholds failed")
            return
        for row in rows or []:
            if hasattr(row, "model_dump"):
                row = row.model_dump()
            elif not isinstance(row, dict) and hasattr(row, "__dict__"):
                row = dict(vars(row))
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", ""))
            bid = str(row.get("budget_id", ""))
            if status in ("", "ok"):
                continue  # check_thresholds already returns only worsened rows
            event = {
                "budget_id": bid,
                "scope": row.get("scope"),
                "scope_value": row.get("scope_value"),
                "pct_used": row.get("pct_used"),
                "status": status,
            }
            self.publish("budget", event)
            pct = event["pct_used"]
            label = event["scope_value"] or event["scope"]
            self.notify(f"LLM budget {label}: {status}" + (f" ({float(pct):.0%} used)" if pct is not None else ""))

    # -- pub/sub -----------------------------------------------------------

    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None) -> Subscriber:
        sub = Subscriber(loop or asyncio.get_running_loop())
        with self._subs_lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._subs_lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub._offer, (event_type, data))
            except RuntimeError:  # loop closed
                self.unsubscribe(sub)

    # -- alerts ------------------------------------------------------------

    def notify(self, text: str) -> None:
        """Fire-and-forget webhook (LLMCI_ALERT_WEBHOOK). Never raises."""
        url = os.environ.get("LLMCI_ALERT_WEBHOOK")
        if not url:
            return
        threading.Thread(target=self._post_webhook, args=(url, text), daemon=True).start()

    def _post_webhook(self, url: str, text: str) -> None:
        try:
            import httpx

            httpx.post(url, json={"text": text}, timeout=3.0)
        except Exception:
            logger.warning("alert webhook failed", exc_info=False)

    # -- stats -------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._stats_cache
        if cached and now - cached[0] < 1.0:
            data = dict(cached[1])
        else:
            w = get_warehouse()
            total = w.count()
            spend = 0.0
            try:
                _, hi = w.time_range()
                if hi is not None:
                    row = w.query(
                        "SELECT coalesce(sum(cost_usd), 0) AS s FROM llm_calls WHERE ts > ?",
                        [_naive_utc(hi - timedelta(minutes=5))],
                    )
                    spend = float(row[0]["s"])
            except Exception:
                logger.exception("stats spend query failed")
            data = {"total_calls": total, "spend_last_5m_usd": round(spend, 6)}
            self._stats_cache = (now, data)
        return {
            "events_per_sec": self.events_per_sec(),
            "queue_depth": self.queue_depth(),
            **data,
            "ts": _iso(datetime.now(timezone.utc)),
        }

    # -- detector tick -----------------------------------------------------

    def tick_once(self) -> list[dict[str, Any]]:
        """Detect + diagnose (deterministic) for features with new rows. Returns new incidents."""
        from llm_cost_investigator.detector import detect_anomalies
        from llm_cost_investigator.investigate import group_adjacent_windows

        with self._tick_lock:
            with self._dirty_lock:
                dirty, self._dirty = self._dirty, {}
            w = get_warehouse()
            opened: list[dict[str, Any]] = []
            for feature, newest in sorted(dirty.items()):
                try:
                    end = newest + timedelta(seconds=1)
                    calls = w.calls_between(end - LOOKBACK, end, feature_tag=feature)
                    anomalies = detect_anomalies(calls) if calls else []
                    for group in group_adjacent_windows(anomalies):
                        inc = self._handle_group(w, feature, group, calls)
                        if inc:
                            opened.append(inc)
                except Exception:
                    logger.exception("tick failed for feature %s", feature)
            return opened

    def _handle_group(
        self, w: Any, feature: str, group: list[AnomalyWindow], calls: list[LLMCall]
    ) -> dict[str, Any] | None:
        g_start = min(_aware_utc(x.start_time) for x in group)
        g_end = max(_aware_utc(x.end_time) for x in group)
        slack = timedelta(minutes=5)
        existing = w.query(
            "SELECT incident_id, start_ts, end_ts FROM incidents WHERE feature_tag = ? "
            "AND start_ts <= ? AND end_ts >= ? ORDER BY start_ts LIMIT 1",
            [feature, _naive_utc(g_end + slack), _naive_utc(g_start - slack)],
        )
        cost = sum(c.cost_usd for c in calls if g_start <= _aware_utc(c.timestamp) < g_end)
        if existing:
            row = existing[0]
            new_start = min(_aware_utc(row["start_ts"]), g_start)
            new_end = max(_aware_utc(row["end_ts"]), g_end)
            w.cursor().execute(
                "UPDATE incidents SET start_ts = ?, end_ts = ?, cost_usd = greatest(cost_usd, ?) "
                "WHERE incident_id = ?",
                [_naive_utc(new_start), _naive_utc(new_end), cost, row["incident_id"]],
            )
            return None

        report = self.diagnose_window(group[0])
        cause = report.root_cause
        if cause.hypothesis == "no_strong_signal":
            return None
        incident_id = "inc_" + uuid.uuid4().hex[:12]
        w.cursor().execute(
            "INSERT INTO incidents (incident_id, feature_tag, start_ts, end_ts, hypothesis, "
            "confidence, cost_usd, status, report_json) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                incident_id, feature, _naive_utc(g_start), _naive_utc(g_end),
                cause.hypothesis, float(cause.confidence), float(cost), "open",
                report.model_dump_json(),
            ],
        )
        event = {
            "incident_id": incident_id,
            "feature_tag": feature,
            "start": _iso(g_start),
            "end": _iso(g_end),
            "hypothesis": cause.hypothesis,
            "confidence": round(float(cause.confidence), 4),
            "cost_usd": round(float(cost), 6),
        }
        self.publish("incident", event)
        self.notify(
            f"LLM cost incident on {feature}: {cause.hypothesis} "
            f"(confidence {cause.confidence:.0%}, ${cost:.2f})"
        )
        return event

    @staticmethod
    def diagnose_window(window: AnomalyWindow) -> IncidentReport:
        """Deterministic diagnosis (provider=fallback): router + agents + aggregator."""
        from llm_cost_investigator.agent_skills import score_all_agents
        from llm_cost_investigator.agents import run_agents_detailed
        from llm_cost_investigator.aggregator import select_root_cause
        from llm_cost_investigator.investigate import RECOMMENDATIONS
        from llm_cost_investigator.router import route_agents

        runs = run_agents_detailed(route_agents(window), window, provider="fallback")
        packs = score_all_agents(window)
        cause = select_root_cause([r.evidence for r in runs], skill_packs=packs)
        return IncidentReport(
            scenario=f"live_{window.feature_tag}",
            root_cause=cause,
            anomaly_window=window,
            agent_runs=runs,
            recommendations=list(RECOMMENDATIONS.get(cause.hypothesis, ["Monitor baseline metrics."])),
        )

    # -- incidents API -----------------------------------------------------

    @staticmethod
    def _incident_row(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "incident_id": r["incident_id"],
            "feature_tag": r["feature_tag"],
            "start": _iso(r["start_ts"]),
            "end": _iso(r["end_ts"]),
            "hypothesis": r["hypothesis"],
            "confidence": round(float(r["confidence"]), 4),
            "cost_usd": round(float(r["cost_usd"]), 6),
            "status": r["status"],
        }

    def list_incidents(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = get_warehouse().query(
            "SELECT incident_id, feature_tag, start_ts, end_ts, hypothesis, confidence, cost_usd, status "
            "FROM incidents ORDER BY start_ts DESC, incident_id LIMIT ?",
            [int(limit)],
        )
        return [self._incident_row(r) for r in rows]

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        rows = get_warehouse().query(
            "SELECT incident_id, feature_tag, start_ts, end_ts, hypothesis, confidence, cost_usd, status "
            "FROM incidents WHERE incident_id = ?",
            [incident_id],
        )
        return self._incident_row(rows[0]) if rows else None

    def update_incident(self, incident_id: str, hypothesis: str, confidence: float, report_json: str) -> None:
        get_warehouse().cursor().execute(
            "UPDATE incidents SET hypothesis = ?, confidence = ?, report_json = ? WHERE incident_id = ?",
            [hypothesis, float(confidence), report_json, incident_id],
        )

    # -- lifecycle ---------------------------------------------------------

    def start_background(self) -> None:
        with self._thread_lock:
            if any(t.is_alive() for t in self._threads):
                return
            self._stop.clear()
            tick_s = max(0.1, float(os.environ.get("LLMCI_TICK_S", "30") or 30))
            self._threads = [
                threading.Thread(target=self._writer_loop, name="llmci-writer", daemon=True),
                threading.Thread(target=self._tick_loop, args=(tick_s,), name="llmci-tick", daemon=True),
            ]
            for t in self._threads:
                t.start()

    def _tick_loop(self, tick_s: float) -> None:
        while not self._stop.wait(tick_s):
            try:
                self.tick_once()
            except Exception:
                logger.exception("tick loop error")

    def stop_background(self) -> None:
        with self._thread_lock:
            demo, self.demo = self.demo, None
            threads, self._threads = self._threads, []
        if demo:
            demo.stop()
        self._stop.set()
        for t in threads:
            t.join(timeout=5)
        try:
            self.flush()
        except Exception:
            logger.exception("final flush failed")

    # -- demo --------------------------------------------------------------

    def demo_start(self, rate: int = 20, incident: str | None = None) -> dict[str, Any]:
        self.demo_stop()
        self.start_background()
        demo = DemoGenerator(self, rate=rate, incident=incident)
        demo.start()
        self.demo = demo
        return demo.state()

    def demo_stop(self) -> dict[str, Any]:
        demo, self.demo = self.demo, None
        if demo:
            demo.stop()
        return {"running": False, "rate": demo.rate if demo else 0, "incident": demo.incident if demo else None}

    def demo_state(self) -> dict[str, Any]:
        return self.demo.state() if self.demo and self.demo.running else {"running": False, "rate": 0, "incident": None}


# ---------------------------------------------------------------------------
# Demo traffic generator
# ---------------------------------------------------------------------------

# feature -> (model, input_tokens, output_tokens, latency_ms, relative weight)
# Demo traffic uses its own feature names. Sharing names with real or
# seeded history would make every demo window look like a model change and
# a 1000x volume jump on that feature, and bury the injected incident.
DEMO_PREFIX = "demo_"

_FEATURES: dict[str, tuple[str, int, int, int, float]] = {
    "summarizer": ("gpt-4o-mini", 900, 180, 700, 2.0),
    "classifier": ("gpt-4o-mini", 350, 40, 350, 3.0),
    "agent_reflection": ("gpt-4o-mini", 700, 160, 650, 1.0),
    "support_reply": ("gpt-4o-mini", 1100, 260, 850, 2.0),
    "search_rerank": ("gpt-4.1-mini", 1500, 60, 500, 2.0),
}
_WEIGHT_SUM = sum(v[4] for v in _FEATURES.values())


class DemoGenerator:
    """Makes realistic traffic with live (or injected) timestamps.

    ``advance(now)`` generates one-second steps up to ``now`` and queues them, so tests drive a
    virtual clock. ``start()`` runs the same code on a thread with the wall clock.
    """

    def __init__(
        self,
        service: IngestService,
        rate: int = 20,
        incident: str | None = None,
        *,
        incident_after_s: float = 45.0,
        backfill_minutes: int = 75,  # > 1 h: detector warm-up is 12 windows
        seed: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if incident is not None and incident not in INCIDENT_TYPES:
            raise ValueError(f"incident must be one of {INCIDENT_TYPES} or None")
        self.service = service
        self.rate = max(1, min(int(rate), 500))
        self.incident = incident
        self.incident_after_s = incident_after_s
        self.backfill_minutes = backfill_minutes
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = random.Random(seed)
        self.started_at: datetime | None = None
        self.incident_start: datetime | None = None
        self._cursor: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False

    # -- event synthesis ---------------------------------------------------

    def _cid(self) -> str:
        return f"{self.rng.getrandbits(128):032x}"

    def _jit(self, v: float, noise: float = 0.08) -> int:
        return max(1, int(v * (1.0 + self.rng.gauss(0.0, noise))))

    def _call(
        self, ts: datetime, feature: str, model: str, tin: int, tout: int, lat: int,
        retry: int = 0, parent: str | None = None, call_id: str | None = None,
    ) -> LLMCall:
        cost = estimate_cost_usd(model, tin, tout)[0] * (1 + retry)
        return LLMCall(
            timestamp=ts, call_id=call_id or self._cid(), parent_call_id=parent,
            feature_tag=DEMO_PREFIX + feature, model=model, input_tokens=tin, output_tokens=tout,
            cost_usd=round(cost, 8), latency_ms=lat, retry_count=retry,
        )

    def _poisson_ish(self, mean: float) -> int:
        base = int(mean)
        return base + (1 if self.rng.random() < mean - base else 0)

    def generate_step(self, t: datetime, incident_active: bool) -> list[LLMCall]:
        """Events for the one-second slot starting at ``t``."""
        out: list[LLMCall] = []
        for feature, (model, tin, tout, lat, weight) in _FEATURES.items():
            use_model = model
            if incident_active and self.incident == "model_misroute" and feature == "summarizer":
                use_model = "gpt-4.1"
            n = self._poisson_ish(self.rate * weight / _WEIGHT_SUM)
            for _ in range(n):
                ts = t + timedelta(milliseconds=self.rng.randrange(0, 1000))
                out.append(self._call(ts, feature, use_model, self._jit(tin), self._jit(tout), self._jit(lat)))
        if incident_active and self.incident == "retry_loop":
            model, tin, tout, lat, _ = _FEATURES["support_reply"]
            parent = self._cid()
            for attempt in range(self.rng.randint(6, 10)):
                ts = t + timedelta(milliseconds=min(999, attempt * 90))
                out.append(self._call(ts, "support_reply", model, self._jit(tin, 0.04), self._jit(tout, 0.04),
                                      lat + attempt * self.rng.randint(300, 600), retry=attempt, parent=parent))
        elif incident_active and self.incident == "context_bloat":
            model, tin, tout, lat, _ = _FEATURES["agent_reflection"]
            prev: str | None = None
            growth = self.rng.uniform(0.7, 1.2)
            for hop in range(self.rng.randint(6, 8)):
                cid = self._cid()
                ts = t + timedelta(milliseconds=min(999, hop * 100))
                out.append(self._call(ts, "agent_reflection", model, int(tin * (1 + growth * hop)),
                                      self._jit(tout), lat + hop * self.rng.randint(150, 350),
                                      parent=prev, call_id=cid))
                prev = cid
        return out

    def backfill(self, now: datetime) -> int:
        """Write ``backfill_minutes`` of baseline history ending at ``now`` (past timestamps)."""
        begin = now - timedelta(minutes=self.backfill_minutes)
        calls: list[LLMCall] = []
        t = begin
        while t < now:
            calls.extend(self.generate_step(t, False))
            t += timedelta(seconds=1)
        return self.service.write_direct(calls, source="demo")

    def begin(self, now: datetime | None = None) -> None:
        """Backfill history and set the cursor. Called by ``start()``; tests call it directly."""
        now = now or self.clock()
        self.backfill(now)
        self.started_at = now
        self.incident_start = now + timedelta(seconds=self.incident_after_s)
        self._cursor = now
        self.running = True

    def advance(self, now: datetime) -> int:
        """Generate and queue events for every full second up to ``now``."""
        if self._cursor is None:
            raise RuntimeError("call begin() first")
        n = 0
        batch: list[LLMCall] = []
        while self._cursor + timedelta(seconds=1) <= now:
            active = bool(self.incident) and self.incident_start is not None and self._cursor >= self.incident_start
            batch.extend(self.generate_step(self._cursor, active))
            self._cursor += timedelta(seconds=1)
            if len(batch) >= 2000:
                n += self._push(batch)
                batch = []
        n += self._push(batch)
        return n

    def _push(self, batch: list[LLMCall]) -> int:
        if not batch:
            return 0
        try:
            return self.service.enqueue(batch, source="demo")
        except QueueFull:
            return 0

    # -- thread ------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.begin()
        self._thread = threading.Thread(target=self._run, name="llmci-demo", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            try:
                self.advance(self.clock())
            except Exception:
                logger.exception("demo generator error")
        self.running = False

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=3)

    def state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "rate": self.rate,
            "incident": self.incident,
            "started_at": _iso(self.started_at) if self.started_at else None,
            "incident_at": _iso(self.incident_start) if self.incident and self.incident_start else None,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: IngestService | None = None
_service_lock = threading.Lock()


def get_service() -> IngestService:
    global _service
    with _service_lock:
        if _service is None:
            _service = IngestService()
        return _service


def reset_service() -> IngestService:
    """Stop and replace the singleton (tests)."""
    global _service
    with _service_lock:
        old, _service = _service, None
    if old:
        old.stop_background()
    return get_service()


def start_background() -> None:
    get_service().start_background()


def stop_background() -> None:
    get_service().stop_background()
