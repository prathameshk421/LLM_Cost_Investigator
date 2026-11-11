# Architecture (v2: from demo to product)

## Decisions

| Question | Decision | Why (measured or cited) |
|---|---|---|
| Storage | **DuckDB file** `data/warehouse.duckdb` (`llm_cost_investigator/warehouse.py`) | 1M calls: spend by feature x day 3 ms vs 678 ms on SQLite (213x); model mix 1.4 vs 360 ms; file 13 MB vs 118 MB. No rollup tables needed below ~50M rows. |
| DuckDB single-writer limit | One process (the API) owns the file. **Every writer** (HTTP ingest, upload, Kafka bridge, seeding) calls `Warehouse.insert_calls` in that process. Threads use `Warehouse.cursor()`. | DuckDB allows one writer process. |
| Kafka | **No broker.** Ingest is `POST /v1/ingest` into an in-process bounded queue with a batch writer. `scripts/kafka_bridge.py` is an optional adapter that consumes a topic and POSTs batches. | LLM traffic is tens of events/s. A broker adds ops and cost for no gain at this scale. |
| Live detection | Deterministic detector tick every `LLMCI_TICK_S` (default 30 s) over the last 6 h for features that got new rows. LLM agents never run automatically: they run when a user clicks "Diagnose with LLM". | Keeps LLM spend near zero (free HF tier). |
| Alerts | Optional webhook `LLMCI_ALERT_WEBHOOK` (Slack-compatible JSON `{"text": ...}`) on new incident and budget threshold crossing. | |
| Auth | Optional `LLMCI_API_TOKEN`. When set, every `/api/*` and `/v1/*` route except `/health` needs `Authorization: Bearer <token>`. | Open risk #1 in REVIEW.md. |

```
app / SDK / LiteLLM callback ──POST /v1/ingest──┐
Kafka topic ──scripts/kafka_bridge.py──POST────┤
file upload / Langfuse pull ────────────────────┤
                                                ▼
                     bounded queue (429 when full) ──► batch writer (≤500 rows / 1 s)
                                                              │
                                                              ▼
                                               DuckDB warehouse (llm_calls, budgets, incidents)
                                                │                  │
                         detector tick (30 s) ◄─┘                  └─► analytics SQL (spend, forecast, drivers, what-if)
                                │                                             │
                   incidents (deterministic diagnosis)                         │
                                │      ▲ "Diagnose with LLM" (budget-guarded)  │
                                ▼      │                                       ▼
                  SSE /api/stream ──► React: Live · Spend · Investigate · Replay · Benchmark
                                │
                         webhook alert
```

## Shared module contract

`llm_cost_investigator/warehouse.py` (exists, do not change the public API):

```python
w = get_warehouse()                     # process singleton; env LLMCI_WAREHOUSE (":memory:" in tests)
w.insert_calls(calls: list[LLMCall], source="api") -> int   # dedupe on call_id
w.query(sql, params) -> list[dict]      # read-only SQL, rows as dicts
w.calls_between(start, end, feature_tag=None) -> list[LLMCall]
w.count(); w.time_range(); w.cursor(); w.clear()
reset_warehouse(path)                   # tests
```

Tables: `llm_calls(call_id PK, parent_call_id, ts TIMESTAMP naive UTC, feature_tag, model,
input_tokens, output_tokens, cost_usd, latency_ms, retry_count, source, ingested_at)`,
`budgets(budget_id PK, scope 'global'|'feature'|'model', scope_value, monthly_usd, alert_pct, created_at)`,
`incidents(incident_id PK, feature_tag, start_ts, end_ts, hypothesis, confidence, cost_usd, status, report_json, created_at)`.

## HTTP contract

All times are ISO-8601 UTC strings ending in `Z`. All money is USD floats.
"Now" for analytics is `?as_of=` (ISO) when given, else the latest `ts` in the warehouse
(so seeded historical data looks live), else the wall clock.

### Analytics (owner: analytics builder; files `llm_cost_investigator/analytics.py`, `llm_cost_investigator/budgets.py`, `web/api/routes/analytics.py`)

`GET /api/analytics/overview?days=30`
```json
{"as_of": "...Z", "days": 30,
 "kpis": {"spend_usd": 0, "calls": 0, "tokens": 0, "cost_per_1k_tokens": 0, "cost_per_call": 0,
          "spend_prev_period_usd": 0, "spend_change_pct": 0,
          "mtd_spend_usd": 0, "forecast_month_usd": 0, "forecast_p10_usd": 0, "forecast_p90_usd": 0,
          "burn_rate_usd_per_day": 0, "retry_waste_usd": 0, "p95_latency_ms": 0},
 "series": {"granularity": "day", "buckets": ["...Z"], "by_feature": {"summarizer": [0.1, ...]}},
 "by_feature": [{"key": "summarizer", "spend_usd": 0, "calls": 0, "tokens": 0, "cost_per_1k_tokens": 0, "share": 0.31, "change_pct": 12.5}],
 "by_model":   [{"key": "gpt-4o-mini", "spend_usd": 0, "calls": 0, "tokens": 0, "cost_per_1k_tokens": 0, "share": 0.5, "change_pct": 0}]}
```
`granularity` is `hour` when `days <= 2`, else `day`.

`GET /api/analytics/drivers?days=7`: period-over-period change per (feature, model), ranked by |delta|,
with the delta split into volume, tokens-per-call and price-per-token effects (sum of effects = delta):
```json
{"as_of": "...Z", "days": 7, "total_delta_usd": 0,
 "drivers": [{"feature_tag": "summarizer", "model": "gpt-4.1", "prev_usd": 0, "curr_usd": 0, "delta_usd": 0,
              "share_of_delta": 0.83, "volume_effect_usd": 0, "tokens_effect_usd": 0, "price_effect_usd": 0}]}
```

`GET /api/analytics/whatif?days=30&to_model=gpt-4o-mini[&feature_tag=x][&from_model=y]`
```json
{"to_model": "gpt-4o-mini", "days": 30, "actual_usd": 0, "projected_usd": 0, "savings_usd": 0, "savings_pct": 0,
 "monthly_savings_usd": 0, "rows": [{"feature_tag": "...", "model": "...", "actual_usd": 0, "projected_usd": 0, "savings_usd": 0}],
 "pricing_source": "static table llm_cost_investigator/ingest/pricing.py"}
```

`GET /api/analytics/waste?days=30`: retry waste and self-call chain cost:
```json
{"retry_waste_usd": 0, "retry_waste_by_feature": [{"feature_tag": "...", "usd": 0, "retried_calls": 0}],
 "deep_chain_cost_usd": 0, "top_chains": [{"root_call_id": "...", "feature_tag": "...", "depth": 6, "calls": 6, "usd": 0}]}
```

`GET /api/budgets` → `[{"budget_id","scope","scope_value","monthly_usd","alert_pct","mtd_spend_usd","forecast_month_usd","pct_used","forecast_pct","status": "ok"|"warning"|"over"|"forecast_over","days_left"}]`
`POST /api/budgets` body `{"scope","scope_value","monthly_usd","alert_pct"}` → the created row (same shape).
`DELETE /api/budgets/{budget_id}` → `{"deleted": true}`.

Forecast method: month-to-date spend + days left × median daily spend of the last 7 days;
P10/P90 from the 10th/90th percentile of those 7 daily spends.

`POST /api/warehouse/seed` body `{"days": 30, "seed": 1, "incidents": true, "reset": true}` → `{"inserted": n, "start": "...Z", "end": "...Z"}`.
Makes realistic history (daily + weekly seasonality, growth trend, and 3 injected incidents: a
model misroute, a retry storm and a context-bloat chain on different days) ending at the current hour.

`GET /api/warehouse/stats` → `{"calls": n, "start": "...Z"|null, "end": "...Z"|null, "path": "...", "size_mb": 0}`.

### Live ingest and stream (owner: streaming builder; files `llm_cost_investigator/ingest_service.py`, `llm_cost_investigator/otel_map.py`, `web/api/routes/stream.py`, `scripts/loadgen.py`, `scripts/kafka_bridge.py`)

`POST /v1/ingest` body `{"events": [ ... ]}` (max 5000 per request). Each event is either a canonical
`LLMCall` dict (missing `call_id` → generated; missing `cost_usd` → priced) or an OTel GenAI-style dict
using `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
plus our extension keys `llmci.feature_tag`, `llmci.parent_call_id`, `llmci.retry_count`, `duration_ms`, `timestamp`.
→ `202 {"accepted": n, "rejected": m, "errors": ["..."], "queue_depth": d}`; `429` when the queue is full.

`GET /api/stream`: Server-Sent Events. Event types (`event:` line = type, `data:` = JSON):
- `stats` every 2 s: `{"events_per_sec", "queue_depth", "total_calls", "spend_last_5m_usd", "ts"}`
- `ingest` per flushed batch: `{"count", "spend_usd", "by_feature": {"x": usd}, "ts"}`
- `incident` when the tick opens one: `{"incident_id","feature_tag","start","end","hypothesis","confidence","cost_usd"}`
- `budget` when a budget crosses its alert or 100 %: `{"budget_id","scope","scope_value","pct_used","status"}`

`POST /api/stream/demo` body `{"action": "start"|"stop", "rate": 20, "incident": "model_misroute"|"retry_loop"|"context_bloat"|null}`
→ `{"running": bool, "rate": n, "incident": ...}`. Runs an in-process generator that POSTs nothing:
it pushes events straight into the ingest queue at `rate`/s with live timestamps (now). The incident starts 45 s after start.

`GET /api/incidents/live?limit=50` → `[{incident_id, feature_tag, start, end, hypothesis, confidence, cost_usd, status}]`.
`POST /api/incidents/live/{incident_id}/diagnose` body `{"provider": "hf"|"fallback"}` → re-runs the
agents on that window (budget-guarded) and returns the same `LiveIncident` shape as `/api/investigate`.

### Frontend (owner: UI builder; new files only, the integrator wires `App.tsx`)

- `web/frontend/src/types/analytics.ts`, `web/frontend/src/api/analytics.ts`
- `web/frontend/src/components/SpendView.tsx` (Spend tab: KPI tiles, stacked spend chart, budget bars + forecast, drivers table, what-if card, waste card, seed button when empty)
- `web/frontend/src/components/LiveView.tsx` (Live tab: SSE connection state, events/s, rolling 5-min spend chart, incident feed with "Diagnose with LLM", demo traffic start/stop with incident picker)
- Reuse tokens/classes from `styles/glass.css`; add new CSS to `web/frontend/src/styles/spend.css`.
- Chart colors: categorical slots `#3987e5, #d95926, #199e70, #c98500, #d55181, #9085e9, #e66767, #008300` in fixed order, color follows the entity.

## Research map

What the literature suggests, where it lands in this codebase, and what we measured.
Citations were checked by the research pass on 2026-11-11.

| Idea | Source | Where | Status |
|---|---|---|---|
| Robust median/MAD z-score | S-H-ESD, Hochenbaum, Vallis, Kejariwal 2017, arXiv 1704.07706 | `detector._robust_z`, flag `LLMCI_ROBUST_Z=1` | **Tried, off by default.** With a fixed \|z\| ≥ 3 threshold it cut lab accuracy at 35% noise from 97.0% to 93.0% and raised flagged windows on clean data from 68 to 117. S-H-ESD pairs MAD with a generalized ESD test; that is the next step to try. |
| Delta decomposition into volume, tokens per call and price per token; ranked drivers | Adtributor, Bhagwan et al., NSDI 2014 | `analytics.drivers`, `/api/analytics/drivers` | Built (v2). |
| Per-call vs total token growth, unit price signal | Causal structure retries → calls → tokens → cost, model → unit price → cost (CIRCA, Li et al. KDD 2022, arXiv 2206.05871; RCD, Ikram et al. NeurIPS 2022) | `detector`, `agent_skills` | Built (v1 review fixes 1 and 4) as hand-coded rules. Deriving demotes from an explicit graph is future work. |
| One incident per change, not per window | BARO, Pham, Ha, Zhang FSE 2024, arXiv 2405.09330; BOCPD, Adams and MacKay 2007, arXiv 0710.3742 | `investigate.group_adjacent_windows` | Adjacent-window merge built; change-point onset is future work. |
| Past incidents as LLM examples; self-consistency when ambiguous | RCACopilot, EuroSys 2024, arXiv 2305.15778; RCAgent, CIKM 2024, arXiv 2310.16340 | `agents.py` prompts, `incidents` table | Incidents are now persisted, which this needs. Retrieval is future work. |
| Seasonal decomposition | MSTL, Bandara, Hyndman, Bergmeir 2021, arXiv 2107.13462 | detector baseline | Needs 2+ weeks of real data. Future work. |
| Standard event names | OpenTelemetry GenAI semantic conventions (still "Development" status) | `otel_map.py` only | Built (v2), isolated in one file so renames stay cheap. |
| Billing export | FinOps FOCUS spec | analytics export view | Future work. |

Skipped on purpose: SR-CNN (needs labelled training data), Kafka and ClickHouse (scale we do not have), deep-learning detectors.

## Overfitting check and held-out benchmark (2026-11-12)

The first v2 rules scored 100% on the scenario lab, but the lab was used while
tuning them. An independent evaluator wrote `benchmarks/heldout/gen.py` from
`benchmarks/heldout/SPEC.md` before it read the rule files, then scored the
pipeline. Result on the old rules: **66% of incidents caught, 85% false alarms.**

Causes it found (file: function):

| Cause | Fix now in the code |
|---|---|
| Baseline froze: flagged windows stayed out forever, no time limit (`detector.detect_anomalies`) | Trailing 72-window baseline; a flagged run stays out for at most 12 windows, then it is a level shift and is learned |
| Ranking by cost z only pushed real incidents out of the top 5 | Windows with causal evidence (new model, retries above cap, new deeper chain) rank first |
| Normal RAG chains counted as bloat (absolute depth) | `agent_skills.effective_chain_depth`: only a chain deeper than the feature's own history counts; self-calling bloat needs it |
| One normal retry gave retry z ≥ 20 | Retry evidence needs `retry_count` above the SDK cap (2) and above the feature's history |
| Low cost share vetoed small retry storms | It caps confidence at 0.74 instead |
| Night-time model switch was not flagged | A never-seen model with a higher unit price flags at any volume |
| Rare A/B model looked new during cold start | With under 12 windows of history only a *dominant* model change counts |
| Cheaper model called an "expensive" misroute | Falling unit price is a falsifier |
| `_max_call_chain_depth` rebuilt an index per window | Built once per feature (1-4 s to 0.42 s per 11k calls) |

Warm-up (`MIN_BASELINE_WINDOWS`, 6 vs 12) was chosen on the dev half only (12 won: 0.0% vs 6.7% false alarms).
Demo generators got 70-75 minute baselines to match; 30 minutes had been a lab artifact.

| Set | Incidents caught | False alarms |
|---|---|---|
| Held-out test half, scored once | 99.6% | 2.0% (3/150, all cheaper-model) |
| Fresh seeds 30-59 after the cheaper-model falsifier | 99.4% | 0/300 |

Rule for future changes: tune on `--half dev`, report fresh seeds. CI runs
`python -m benchmarks.heldout.run --seed-from 60 --seed-to 64 --min-hit 0.95 --max-false-alarm 0.05`.

Still open: the detector flags about 200 window groups per 53-hour dataset (diagnosis filters them);
a seasonal or volume-normalised baseline should cut that. Incidents that start in a feature's first
hour of history are learned as normal.
