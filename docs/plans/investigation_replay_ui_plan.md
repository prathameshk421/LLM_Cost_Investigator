# Investigation Replay UI + Backend Plan

> **Path note (repo restructure):** artifact paths in this historical plan used `reports/`, `api/`, and `frontend/` at the repo root. Current layout is `data/reports/{incidents,transcripts}/`, `data/replay/`, `web/api/`, and `web/frontend/`. See `docs/plans/repo_structure_plan.md`.

Project goal: ship a stage-by-stage investigation replay view that makes deterministic DECISION-injection and tool-use the visual centerpiece of the LLM Cost Investigator demo.

Architecture choice (locked):

```text
reports/*.json + reports/live_*.txt
  -> one-time normalize export (Python)
  -> FastAPI read-only REST API (in-memory catalog)
  -> React frontend (stage-driven replay UI)
```

This is a presentation layer over already-produced artifacts. It does not change the core pipeline in `llm_cost_investigator/`.

## Background

The system detects LLM cost anomalies, routes them to diagnostic agents, and (for tool-enabled agents) may require a tool call when a deterministic DECISION-injection gate says so. That gate is the differentiator:

```text
Math decides when something is wrong.
Rules decide which agents should investigate.
Python pre-evaluates MUST CALL vs DO NOT.
Agents explain why — and only fetch detail when the gate says so.
```

### What already exists

| Artifact | Path | What it gives the UI |
| --- | --- | --- |
| Full incident reports | `reports/{scenario}_incident.json` | `IncidentReport`: signals, sample calls, agent evidence, root cause, recommendations |
| Live tool transcripts | `reports/live_token_context_tool_use.txt`, `reports/live_model_routing_tool_use.txt` | Telemetry → DECISION → tool call/result → final explanation |
| Gate logic (source of truth) | `llm_cost_investigator/agents.py` | `_token_context_tool_required`, `_model_routing_tool_required`, retry DECISION in `build_retry_loop_prompt_v2` |
| Router | `llm_cost_investigator/router.py` | `route_agents(anomaly)` — max 2 agents, priority order |
| Schemas | `llm_cost_investigator/schemas.py` | `IncidentReport`, `AnomalySignals`, `AgentEvidence`, etc. |

### Scenario inventory (demo catalog)

| ID | Kind | Tool gate | Durable source today |
| --- | --- | --- | --- |
| `retry_loop` | Main CLI | DO NOT (retry_z=3.5 ≥ 2.5) | `reports/retry_loop_incident.json` |
| `context_bloat` | Main CLI | DO NOT (input_z ≫ 3.0) | `reports/context_bloat_incident.json` |
| `model_misroute` | Main CLI | DO NOT (cost_z ≫ 3.0 despite model_changed) | `reports/model_misroute_incident.json` |
| `token_context_thin` | Synthetic MUST CALL | MUST CALL `get_call_chain` | `reports/live_token_context_tool_use.txt` |
| `model_routing_thin` | Synthetic MUST CALL | MUST CALL `get_window_calls` | `reports/live_model_routing_tool_use.txt` |
| `retry_loop_thin` | Synthetic MUST CALL | MUST CALL `get_call_chain` | Script only (`scripts/run_tool_use_live.py`) — no durable transcript yet |

Main scenarios show strong-signal “answer from aggregates.” Thin scenarios show the gate forcing tool use when aggregates alone are inconclusive. Both are needed for the story.

### Gap this plan closes

- No frontend, no FastAPI, no structured step timeline.
- `IncidentReport` has final answers but no DECISION line or tool trace.
- Live `.txt` has DECISION/tool stages but is multi-provider tee text, not API-ready JSON.
- Cited numbers in explanations are not linked back to tool-result fields.

## Proposed Changes

### 1. Project structure (presentation layer only)

```text
api/                              # FastAPI backend
  app.py                          # app factory, CORS, lifespan
  routes/
    incidents.py                  # list + detail endpoints
  models/
    replay.py                     # Pydantic response models (API-facing)
  services/
    catalog.py                    # load normalized JSON into memory
    citations.py                  # tool-result → explanation span matching
  data/                           # committed normalized replay payloads
    retry_loop.json
    context_bloat.json
    model_misroute.json
    token_context_thin.json
    model_routing_thin.json
    retry_loop_thin.json          # after export from script/transcript
  requirements.txt                # fastapi, uvicorn, pydantic (or pin in root later)

frontend/                         # React (Vite + TypeScript)
  package.json
  src/
    main.tsx
    App.tsx
    api/client.ts
    types/replay.ts
    components/
      ScenarioSwitcher.tsx
      StageRail.tsx
      StageContainer.tsx
      SignalsPanel.tsx
      RouterPanel.tsx
      DecisionBadge.tsx
      ToolTrace.tsx
      ExplanationPanel.tsx
      CitationHighlight.tsx
      RootCausePanel.tsx
      ReplayControls.tsx
    hooks/
      useIncident.ts
      useStageProgression.ts
    styles/
      tokens.css
      glass.css

scripts/
  export_replay_catalog.py        # one-time (or re-runnable) normalize → api/data/

plans/
  investigation_replay_ui_plan.md # this plan, committed for the repo
```

Do **not** put API/UI code inside `llm_cost_investigator/`. The core package stays the diagnostic pipeline; `api/` and `frontend/` only read artifacts.

Optional later: thin re-exports of gate helpers from core if useful, but the API may also recompute gates by importing:

```text
from llm_cost_investigator.agents import (
    _token_context_tool_required,
    _model_routing_tool_required,
)
from llm_cost_investigator.router import route_agents
```

Keep imports one-way: presentation → core. Never core → api.

---

### 2. Backend (FastAPI)

#### 2.1 Data layer — simplest clean approach

**Recommendation: load normalized JSON once at startup into an in-memory dict.**

| Approach | Verdict |
| --- | --- |
| Read raw `reports/*.json` + parse `live_*.txt` on every request | Reject — fragile, slow to reason about, frontend-shaped parsing in the hot path |
| SQLite/DB | Reject for this demo — static catalog, no writes |
| **One-time export → `api/data/*.json`, load at startup** | **Choose this** |

Startup (`lifespan`):

1. Glob `api/data/*.json`.
2. Validate each file with `ReplayIncident` (API Pydantic model).
3. Index by `id` in `dict[str, ReplayIncident]`.
4. On missing/invalid file: log and skip (or fail startup if catalog empty).

No file I/O on request path after boot. Restart reloads the catalog (fine for local demo).

#### 2.2 Parsing / export — normalize `live_*.txt` once

**Recommendation: do not parse `.txt` at request time.** Run a small export script that produces the same response shape as the incident detail endpoint.

Why: section headers are consistent (`[Telemetry sent to agent]`, `[Tool calls log]`, etc.) but multi-provider blocks, free-text spacing, and footer tables make live parsing brittle. Normalization is a one-shot offline step; the API stays boring.

**`scripts/export_replay_catalog.py` responsibilities:**

1. **Main incidents** — read `reports/{scenario}_incident.json` via `IncidentReport` validation.
2. **Recompute router** — `route_agents(anomaly_window)` → `routed_agents` + short rule reasons (mirror `router.py` conditions as structured `reason` strings).
3. **Recompute DECISION** per winning/relevant agent from gate helpers (not from free text).
4. **Thin live transcripts** — parse known section blocks for preferred provider (default: `groq` first block; configurable flag `--provider groq|cerebras`).
5. **Extract from each live block:**
   - telemetry JSON
   - DECISION line (full string)
   - tool call name + args
   - tool result payload (JSON array)
   - final hypothesis, confidence, explanation, metrics
6. **Build `tool_trace`** when MUST CALL and a result exists; empty when DO NOT.
7. **Run citation matching** (see §2.4) and embed `citations[]` on the agent stage.
8. Write `api/data/{id}.json`.

**For `retry_loop_thin`:** either re-run `scripts/run_tool_use_live.py` with durable tee (small script tweak) or hand-build one normalized JSON from the synthetic anomaly the script already constructs. Prefer generating a durable transcript once so all three thin scenarios share the same export path.

**Parser rules for live txt (export-time only):**

```text
Split on "SCENARIO: <id>  |  PROVIDER: <p>"
Keep first matching provider (default groq)
Between markers, capture:
  [Telemetry sent to agent]      → JSON object
  DECISION: ...                  → single line (must start with DECISION:)
  [Tool calls log]               → lines until next section; "(none)" => []
  [Tool result returned to model]→ JSON array (if tool called)
  [Final agent output]           → key: value fields (hypothesis, confidence, explanation, metrics)
```

#### 2.3 API surface

Read-only REST. No auth for local demo.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness |
| `GET` | `/api/incidents` | List catalog entries for scenario switcher |
| `GET` | `/api/incidents/{id}` | Full stage-ready incident payload |

**List item shape (`IncidentSummary`):**

```text
id: string                     # e.g. "token_context_thin"
title: string                  # human label
kind: "main" | "thin_must_call"
feature_tag: string
root_cause_hypothesis: string
has_tool_use: bool
winning_agent: string | null
```

**Detail shape (`ReplayIncident`):** single response containing everything the UI needs for stage-by-stage reveal — **no extra client-side parsing of transcripts.**

Optional convenience (not required for v1):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/incidents/{id}/stages/{stage_id}` | Slice of one stage only |

Prefer one detail fetch; stage reveal is frontend state over that payload.

#### 2.4 Response schema (Pydantic, stage-oriented)

```text
ReplayIncident
  id: str
  title: str
  kind: Literal["main", "thin_must_call"]
  scenario_label: str
  feature_tag: str
  window: { start_time, end_time }
  stages: list[ReplayStage]          # ordered reveal sequence
  root_cause: {
    hypothesis, confidence, winning_agent
  }
  recommendations: list[str]
  meta: {
    provider, model, generated_at, source_files: list[str]
  }

ReplayStage
  id: Literal[
    "signals",
    "router",
    "decision",
    "tool_trace",      # present only when tool_required; still emit with empty calls for DO NOT? 
    "explanation",
    "root_cause"
  ]
  title: str
  kind: Literal[
    "deterministic",   # cool accent — math/rules
    "agentic"          # warm accent — LLM judgment
  ]
  payload: ...         # discriminated by stage.id
```

**Stage payloads:**

| Stage id | Payload fields |
| --- | --- |
| `signals` | `signals: dict[str, number\|bool\|string\|list]`, `highlights: list[{key, value, threshold, status: pass\|fail\|neutral}]`, `sample_calls: list[call summary]` |
| `router` | `candidates: list[{agent_name, selected: bool, reasons: list[str]}]`, `selected: list[str]`, `max_agents: 2` |
| `decision` | `agent_name`, `tool_name`, `tool_required: bool`, `decision: "MUST_CALL" \| "DO_NOT_CALL"`, `decision_line: str`, `comparisons: list[{metric, value, operator, threshold, passed}]` |
| `tool_trace` | `tool_required: bool`, `calls: list[{name, args}]`, `result: list[dict] \| null`, `result_summary: str \| null` |
| `explanation` | `agent_name`, `hypothesis`, `confidence`, `explanation`, `supporting_metrics`, `citations: list[Citation]` |
| `root_cause` | `hypothesis`, `confidence`, `winning_agent`, `all_evidence: list[{agent_name, hypothesis, confidence}]`, `recommendations: list[str]`, `tie_break_note: str \| null` |

**`Citation` (backend-computed):**

```text
value: str | number          # e.g. 1600 or "gpt-4.1"
display: str                 # "1600"
explanation_span: { start: int, end: int }  # char offsets into explanation
source: {
  tool_name: str,
  path: str,                 # e.g. "[1].input_tokens" or "[0].model"
  field: str,
  raw: Any
}
match_kind: "exact_number" | "approx_number" | "exact_string"
```

**Cited-value matching — recommendation: backend at export/response-build time.**

Reasons:

- Frontend stays a pure renderer (offsets + highlight classes).
- Matching logic is testable in Python without a browser.
- Payload is cacheable/static in `api/data/`.
- Ambiguous matches can be resolved once offline with manual overrides if needed.

Matching strategy (deterministic, ordered):

1. Flatten tool result values: numbers and short strings (`model` names, call_ids if cited).
2. Scan explanation left-to-right for:
   - exact integer/float tokens (normalize `~0.002`, `≈9×`, `800 → 1600`)
   - exact model name substrings
3. Prefer longer matches; skip overlapping spans.
4. Link each span to the first tool-result path that produced that value.
5. If no tool result (DO NOT CALL stages), `citations = []` and explanation still renders as narrative.

For main incidents without live tool results, still emit `decision` + empty `tool_trace` so the UI can show “gate said skip.”

#### 2.5 Multi-agent incidents

Main `retry_loop` and `model_misroute` often route **two** agents. Replay stages should:

1. Show one shared `signals` + `router` stage.
2. Then, for **each selected agent** in priority order: `decision` → optional `tool_trace` → `explanation`.
3. Finish with single `root_cause` aggregator stage.

Practical encoding: either nested stages under `agent_runs[]` or flat stage ids with suffixes (`decision:retry_loop_agent`). Prefer **flat ordered list** with `agent_name` on relevant stages — simpler for `StageRail` and next-button progression.

#### 2.6 CORS / local dev

```text
FastAPI:  http://127.0.0.1:8000
Vite:     http://127.0.0.1:5173
```

CORS middleware:

```text
allow_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
allow_methods = ["GET"]
allow_headers = ["*"]
```

No cookies/auth. No wildcard origin in any “prod” note — local-only is enough.

---

### 3. Stage breakdown (frontend, data-driven)

Default progression for a **thin MUST CALL** incident:

| # | Stage | Data needed | Visual change |
| --- | ---: | --- | --- |
| 1 | **Raw signals** | `stages[signals].highlights` + key z-scores/growth % | Metric cards fade/slide in; numbers count up; threshold chips show cool “math” treatment |
| 2 | **Router decision** | selected agents + reasons | Agent chips light up; non-selected dim; short reason lines appear |
| 3 | **DECISION line** | `decision_line`, comparisons, MUST/DO NOT | **Hero moment**: monospace DECISION string types or snaps in; badge MUST CALL (amber pulse) or DO NOT (cool teal); comparison table shows each threshold check |
| 4a | **Tool request** | tool name + args | Request card appears under decision; cool accent border |
| 4b | **Tool result** | result JSON / table | Result panel expands; chain or per-call rows cascade in |
| 4c | **Citation link** | `citations[]` | Draw connection (line or dual highlight) from result cells to explanation spans; then show full narrative with highlights |
| 5 | **Root cause** | hypothesis, confidence, recommendations | Confidence ring/bar animates; recommendations list; warm “LLM judgment locked by aggregator” framing |

For **DO NOT CALL** main scenarios: stage 3 shows DO NOT; stage 4 collapses to a single “Tool skipped — aggregate signals sufficient” state (still a stage, so the absence of tool use is visible, not missing).

**Per-agent loop:** if two agents, stages 3–4c repeat before stage 5.

---

### 4. Visual design spec

#### 4.1 Typography

| Role | Font | Size / weight |
| --- | --- | --- |
| App chrome / stage titles | **Geist Sans** (fallback Inter) | 20–24px / 600 stage title; 13px / 500 labels |
| UI body / buttons | Geist Sans | 14px / 400–500 |
| Telemetry keys, JSON, tool payloads, DECISION line | **Geist Mono** (fallback JetBrains Mono) | 12–13px / 400; DECISION line 14px / 500 |
| LLM narrative explanation only | **Newsreader** or **Fraunces** | 16–17px / 400, line-height 1.55 |
| Big metric values | Geist Mono | 28–32px / 600 tabular-nums |
| Confidence % | Geist Mono | 40px / 600 |

Load via `fontsource` or system CDN in Vite; set CSS variables on `:root`.

#### 4.2 Color system (dark observability)

Base:

| Token | Hex | Use |
| --- | --- | --- |
| `--bg-0` | `#07090C` | Page background |
| `--bg-1` | `#0C1016` | Deep panels |
| `--glass` | `rgba(18, 24, 34, 0.55)` | Frosted panels |
| `--glass-border` | `rgba(255, 255, 255, 0.08)` | Panel edges |
| `--text-0` | `#E8EDF5` | Primary text |
| `--text-1` | `#9AA6B8` | Secondary labels |
| `--text-2` | `#5C6B80` | Muted / inactive |

Glass recipe:

```text
background: var(--glass);
backdrop-filter: blur(16px) saturate(1.2);
border: 1px solid var(--glass-border);
border-radius: 12–16px;
box-shadow: 0 8px 32px rgba(0,0,0,0.35);
```

Two-accent system (core story: math vs judgment):

| Token | Hex | Use |
| --- | --- | --- |
| `--accent-cool` | `#3DDCFF` | Deterministic: z-scores, thresholds, router rules, DECISION comparisons, tool request/result chrome |
| `--accent-cool-dim` | `rgba(61, 220, 255, 0.12)` | Cool panel fills |
| `--accent-warm` | `#FF8A4C` | Agentic: explanation narrative, confidence, hypothesis chips, recommendations |
| `--accent-warm-dim` | `rgba(255, 138, 76, 0.12)` | Warm panel fills |

Status:

| Token | Hex | Use |
| --- | --- | --- |
| `--status-pass` / MUST energy | `#34D399` | Optional “tool called successfully” |
| `--status-must` | `#FBBF24` | MUST CALL badge (high attention) |
| `--status-skip` | `#64748B` | DO NOT CALL / skipped tool |
| `--status-fail` | `#F87171` | Parse/export errors only (not root-cause “bad”) |
| `--status-ambiguous` | `#A78BFA` | Thin-signal / medium confidence band |

Citation highlight:

```text
tool result cell: background rgba(61,220,255,0.18); outline 1px cool
explanation span: background rgba(255,138,76,0.18); underline cool→warm gradient optional
paired state: both elevated simultaneously on hover/reveal
```

#### 4.3 Motion

**Recommendation: Framer Motion for stage enter/exit and DECISION hero; CSS transitions for hover and number ticks.**

| Motion | Approach | Duration |
| --- | --- | --- |
| Stage panel enter | `opacity` + `y: 12→0` (framer) | 280–350ms ease-out |
| Stage exit | fade only | 150–200ms |
| Metric number reveal | count-up from 0 (or previous) | 500–700ms ease-out |
| DECISION line | mono typewriter or hard cut + scale 0.98→1 + cool glow pulse | 400ms enter; pulse 600ms |
| MUST CALL badge | amber box-shadow pulse ×2 | 800ms total |
| Tool result rows | stagger children 40–60ms | 200ms each |
| Citation link | draw SVG path or dual-highlight flash | 400–600ms after result settles |
| Confidence fill | width/stroke-dashoffset | 600ms |

Respect `prefers-reduced-motion: reduce` → instant opacity only, no count-up.

---

### 5. Frontend component architecture

```text
App
├── ScenarioSwitcher          # list from GET /api/incidents
├── StageRail                 # vertical/horizontal step list; click to jump when unlocked
├── StageContainer            # framer AnimatePresence for active stage(s)
│   ├── SignalsPanel
│   ├── RouterPanel
│   ├── DecisionBadge         # MUST / DO NOT + decision_line + comparisons
│   ├── ToolTrace             # request → result (skipped state if empty)
│   ├── ExplanationPanel      # serif narrative + CitationHighlight spans
│   └── RootCausePanel
└── ReplayControls            # Next / Prev / Auto / Reset
```

**Data fetching:**

- On mount: `GET /api/incidents` → switcher.
- On selection: `GET /api/incidents/{id}` → full `ReplayIncident`; show loading skeleton; reset stage index to 0.
- No global store required. Local React state + one context optional (`ReplayContext`) for active incident + stage index.

**Stage progression — recommendation: click-to-advance default; optional auto-advance.**

| Mode | Behavior |
| --- | --- |
| **Manual (default)** | Next reveals next stage; Prev allowed; StageRail jump only to already-revealed or any stage after first full playthrough |
| **Auto** | Timer advances every ~2.5–3.5s (longer on tool_trace/explanation); pause on hover; spacebar toggles |
| **Both** | Toggle in ReplayControls; auto is off by default for interview demos |

Why manual default: live interview pacing control is more important than cinematic autoplay. Auto is low-cost once stages are ordered.

**Scenario switcher:**

- Entire list from backend `GET /api/incidents`.
- Group UI sections: **Main scenarios** vs **Thin MUST-CALL demos**.
- Selecting a new id refetches detail and resets progression.
- Do not hardcode the three scenarios in the frontend beyond presentation copy.

---

### 6. Run / dev instructions

```bash
# 0) (once, or after regenerating reports)
python3 scripts/export_replay_catalog.py

# 1) Backend
cd api && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000

# 2) Frontend
cd frontend && npm install
npm run dev   # http://127.0.0.1:5173
```

Optional convenience:

```bash
# scripts/dev_replay.sh — starts both (backend bg + frontend fg)
```

Demo path for interview:

1. Open UI → select `model_misroute` (strong signal, DO NOT tool) — show router + decision skip.
2. Switch to `model_routing_thin` — same agent family, MUST CALL, tool result, citations.
3. Optionally `token_context_thin` for chain doubling narrative.

---

### 7. Explicit answers to open questions

| Question | Recommendation |
| --- | --- |
| Cited-value matching backend vs frontend? | **Backend (export/build time).** Embed `citations[]` with char spans. Frontend only highlights. |
| Parse `live_*.txt` live vs normalize first? | **One-time Python export** to `api/data/*.json`. API never parses tee text. |
| Auto vs click-to-advance? | **Click-to-advance default**; optional auto toggle with ~3s delay. |
| Live run endpoint now? | **Read-only replay only.** Later: add `POST /api/runs` that calls `main.process_scenario` / tool harness and streams events — would need new event log persistence, not a frontend rewrite if response shape stays stage-compatible. |

#### Future live-run seam (note only, out of scope)

```text
Today:   static ReplayIncident JSON
Later:   same ReplayIncident built incrementally from run events
         WebSocket or SSE: stage.complete events
         Storage: reports/runs/{run_id}.json
Change:  catalog service gains a RunStore; frontend StageContainer already event-shaped
```

Do not implement `POST /run` in this plan’s delivery scope.

---

### 8. Implementation order

1. **Export script + sample `api/data/*.json`** for all 5–6 scenarios (blocking for UI).
2. **FastAPI** list + detail + CORS + in-memory catalog.
3. **Frontend scaffold** (Vite React TS) + design tokens + layout shell.
4. **Stage components** in order: Signals → Router → Decision → ToolTrace → Explanation/Citations → RootCause.
5. **Replay controls** (manual + auto).
6. **Polish motion** and reduced-motion.
7. **Smoke test** against real catalog; fix citation gaps with export overrides if needed.

## Open Questions

Resolved in this plan (see §7). Remaining small decisions that can be made at implement time without redesign:

1. **Provider preference for multi-provider live transcripts** — default `groq`; document flag on export script.
2. **Font hosting** — self-hosted `@fontsource` packages vs system fallbacks if offline demo required.
3. **Whether main-scenario `tool_trace` stage is omitted or shown as explicit skip** — recommend always show skip so gate behavior is never invisible.
4. **Committing `api/data/*.json` to git** — yes; treat as demo fixtures like `reports/`.

## Verification Plan

### Backend

```bash
python3 scripts/export_replay_catalog.py
# assert api/data has expected ids

uvicorn app:app --port 8000
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/incidents | jq 'length >= 5'
curl -s http://127.0.0.1:8000/api/incidents/token_context_thin | jq '.stages[].id'
curl -s http://127.0.0.1:8000/api/incidents/model_routing_thin | jq '.stages[] | select(.id=="decision")'
```

Checks:

- List returns both `main` and `thin_must_call`.
- Thin incidents have `decision.tool_required == true` and non-empty `tool_trace.calls`.
- Main incidents with strong signals have `tool_required == false` and empty calls.
- Explanation stages for thin incidents include ≥1 citation when numbers appear in both result and text.
- Invalid id → 404 JSON.

### Frontend (manual demo script)

1. Load app → see grouped scenario switcher populated from API (not hardcoded-only).
2. Select `retry_loop` → advance stages → DO NOT / skip tool is visible → root cause `uncapped_retry_loop`.
3. Select `token_context_thin` → DECISION MUST CALL → tool result chain 800→6400 → explanation highlights match.
4. Select `model_routing_thin` → MUST CALL `get_window_calls` → mini/4.1 costs linked in narrative.
5. Toggle Auto → stages advance without crash; toggle off mid-run.
6. Reduced-motion OS setting → no count-up / no typewriter.

### Regression boundary

- `python3 replay_tests.py` still PASSes (core pipeline untouched).
- No new dependencies inside `llm_cost_investigator/` package required for the pipeline itself.

### Done definition

```text
Two terminals (or one script) start API + UI.
Operator can replay main + thin scenarios stage-by-stage.
DECISION line and tool use/skip are visually obvious.
Citations connect tool results to explanation text for at least the two existing live thin transcripts.
Core package and replay_tests remain green without API/UI.
```

## Design Rule (unchanged)

```text
Math decides when something is wrong.
Rules decide which agents should investigate.
Python injects DECISION so the model never interprets the gate.
The UI makes that injection and any tool use impossible to miss.
```
