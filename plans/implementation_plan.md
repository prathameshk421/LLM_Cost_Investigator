# Implementation Plan

## 1. Schemas

Create `llm_cost_investigator/schemas.py`.

Required models:

- `LLMCall`
- `AnomalyWindow`
- `AgentEvidence`
- `RootCauseResult`
- `IncidentReport`

Use Pydantic for validation.

Important fields:

```text
timestamp
call_id
parent_call_id
feature_tag
model
input_tokens
output_tokens
cost_usd
latency_ms
retry_count
scenario_label
```

`scenario_label` is only for replay tests and demos. The detector and agents should not use it as evidence.

## 2. Telemetry Store

Create `telemetry_store.py`.

Use SQLite with one main table:

```sql
CREATE TABLE llm_calls (
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
```

Functions:

- `init_db(path)`
- `insert_call(conn, call)`
- `insert_calls(conn, calls)`
- `fetch_calls(conn, scenario=None)`
- `fetch_window(conn, feature_tag, start, end)`

## 3. Simulator

Create `simulate_telemetry.py`.

Generate four fake features:

- `summarizer`
- `classifier`
- `agent_reflection`
- `support_reply`

Generate baseline traffic, then inject one bug scenario per run:

- `retry_loop`
- `context_bloat`
- `model_misroute`

Bug patterns:

- Retry loop: high `retry_count`, repeated parent-child calls, latency spike.
- Context bloat: parent chains deepen, `input_tokens` grow each call.
- Model misroute: model switches from cheap to expensive, cost jumps while tokens stay similar.

## 4. Detector

Create `detector.py`.

Bucket calls into 1-minute or 5-minute windows.

Compute feature-level metrics:

- `cost_per_call`
- `input_tokens_per_call`
- `output_tokens_per_call`
- `avg_retry_count`
- `calls_per_window`
- `max_call_chain_depth`
- `model_changed`

Compute z-scores against earlier baseline windows.

Flag anomaly if any of these hold:

- cost z-score >= 3
- input token z-score >= 3
- retry z-score >= 3
- calls z-score >= 3
- model changed and cost increased sharply

Return `AnomalyWindow`.

## 5. Router

Create `router.py`.

Routing rules:

```text
retry_z_score >= 3 or max_retry_count >= 3
  -> retry_loop_agent

input_tokens_z_score >= 3 or input_token_growth_pct >= 100 or max_call_chain_depth >= 4
  -> token_context_agent

cost_z_score >= 3 and token_growth_pct < 50, or model_changed == true
  -> model_routing_agent
```

Only call agents whose preconditions are met.

## 6. Agent Execution

Create `agents.py`.

Each agent is a real LLM call when an API key is available.

Add a deterministic fallback mode for development:

- if no API key exists, return evidence from local rules
- make this explicit in terminal output

Agent wrapper requirements:

- strict prompt
- JSON-only response
- Pydantic validation
- retry once on parse failure
- fallback to deterministic evidence if the second parse fails

## 7. Aggregator

Create `aggregator.py`.

First version should be deterministic:

- ignore `no_strong_signal`
- pick highest confidence
- tie-break: model misroute > retry loop > context bloat

Optional LLM lead investigator:

- may write summary text
- must not override root cause selected by deterministic aggregator

## 8. Reporter

Create `reporter.py`.

Outputs:

- terminal summary
- `reports/<scenario>_incident.md`
- `reports/<scenario>_incident.json`

Report sections:

- Root cause
- Affected feature
- Confidence
- Winning agent
- Supporting evidence
- Recommendations

## 9. Replay Tests

Create `replay_tests.py`.

Test cases:

- `retry_loop` expects `uncapped_retry_loop`
- `context_bloat` expects `context_bloat_self_calling_agent`
- `model_misroute` expects `expensive_model_misroute`

Each test should:

1. create fresh DB
2. generate scenario
3. run detector
4. route agents
5. aggregate evidence
6. compare result to expected root cause

