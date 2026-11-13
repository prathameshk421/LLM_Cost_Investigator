# Repo Structure Reorganization Plan

## Background

The project grew in layers (core pipeline → live harnesses → investigation replay UI) without a single layout pass. The code works, but the tree is hard to navigate and several concerns are mixed.

### Current layout (problems)

```text
LLM_Cost_Investigator/
  main.py                    # CLI entry at root
  replay_tests.py            # tests at root
  __pycache__/               # root-level noise
  llm_cost_investigator.egg-info/
  llm_cost_investigator/     # core package (good)
  api/                       # FastAPI + fixtures mixed with code
    data/*.json              # demo fixtures living under package code
  frontend/                  # React (sibling of api, no shared parent)
  reports/                   # incidents + live transcripts mixed
  data/                      # empty leftover
  scripts/                   # live harnesses + export + dev shell mixed
  plans/                     # design docs at root
  pyproject.toml             # core deps only
  api/requirements.txt       # web deps split off
```

**Concrete pain points**

| Issue | Why it matters |
| --- | --- |
| Root clutter | `main.py`, `replay_tests.py`, egg-info, pycache compete with the package |
| `api/data/` under code | Generated fixtures sit next to application modules |
| `reports/` is a dump | Incident JSON/MD and live tool transcripts are different artifact kinds |
| Empty `data/` | Dead directory; confuses “where does data live?” |
| Dual web roots | `api/` + `frontend/` not grouped as one presentation layer |
| Dual dependency files | `pyproject.toml` vs `api/requirements.txt`; easy to drift |
| `PYTHONPATH=.` hacks | `api` is not an installed package; scripts insert `sys.path` |
| Scripts bucket | Live experiments, catalog export, and dev orchestration all share one flat folder |

### What should stay true after the move

- Core diagnostic pipeline remains `llm_cost_investigator/` and does **not** import web code.
- Presentation layer (API + UI) only **reads** artifacts; it does not change detection/agents.
- `pip install -e .` still runs the CLI and tests.
- Existing demo commands remain discoverable (thin wrappers or console scripts).
- Replay catalog can be regenerated from reports/transcripts.

---

## Proposed structure (target)

```text
LLM_Cost_Investigator/
├── README.md
├── AGENTS.md
├── pyproject.toml                 # core + optional [web] extras; console scripts
├── .gitignore                     # egg-info, __pycache__, node_modules, .venv, data/runtime
├── .env.example                   # no secrets; documents GROQ/CEREBRAS keys
│
├── llm_cost_investigator/         # CORE PACKAGE (internals largely unchanged)
│   ├── __init__.py
│   ├── schemas.py
│   ├── telemetry_store.py
│   ├── simulate_telemetry.py
│   ├── detector.py
│   ├── router.py
│   ├── agents.py
│   ├── llm_client.py
│   ├── fallback.py
│   ├── aggregator.py
│   ├── reporter.py
│   └── cli.py                     # moved from root main.py
│
├── tests/                         # ALL automated tests
│   └── test_replay.py             # moved from root replay_tests.py
│
├── web/                           # PRESENTATION LAYER (replay demo)
│   ├── api/                       # FastAPI
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── models/
│   │   ├── routes/
│   │   └── services/
│   └── frontend/                  # React + Vite (current frontend/*)
│       ├── package.json
│       ├── vite.config.ts
│       ├── index.html
│       └── src/
│
├── data/                          # ALL durable demo artifacts + fixtures
│   ├── reports/                   # pipeline outputs (regenerable)
│   │   ├── incidents/             # *_incident.json + *_incident.md
│   │   └── transcripts/           # live_*.txt tool-use captures
│   └── replay/                    # normalized UI catalog (export output)
│       └── *.json
│
├── scripts/
│   ├── live/                      # live provider harnesses
│   │   ├── run_tool_use_live.py
│   │   ├── run_token_context_tool_use_live.py
│   │   ├── run_model_routing_tool_use_live.py
│   │   └── run_model_isolation_test.py
│   ├── export_replay_catalog.py   # reports/transcripts → data/replay/
│   └── dev_replay.sh              # start web/api + web/frontend
│
└── docs/
    └── plans/                     # historical + design plans
        ├── base_plan.md
        ├── implementation_plan.md
        ├── detector_router_plan.md
        ├── testing_demo_plan.md
        ├── investigation_replay_ui_plan.md
        └── repo_structure_plan.md  # this plan, committed
```

### Layer diagram

```text
                    ┌─────────────────────┐
                    │  web/frontend       │  React replay UI
                    └─────────┬───────────┘
                              │ HTTP
                    ┌─────────▼───────────┐
                    │  web/api            │  FastAPI (read-only)
                    └─────────┬───────────┘
                              │ reads
                    ┌─────────▼───────────┐
                    │  data/replay/*.json │  fixtures
                    └─────────▲───────────┘
                              │ export script
         ┌────────────────────┴────────────────────┐
         │ data/reports/incidents + transcripts    │
         └────────────────────▲────────────────────┘
                              │ write_report / live scripts
         ┌────────────────────┴────────────────────┐
         │ llm_cost_investigator + cli             │  core pipeline
         └─────────────────────────────────────────┘
```

---

## Design decisions (locked recommendations)

### 1. Keep the core package name and location

**Keep** `llm_cost_investigator/` at the repo root (not `src/` layout).

- Already installed via setuptools `packages = ["llm_cost_investigator"]`.
- `src/` would force more import/path churn for little gain on this repo size.

### 2. CLI: package entrypoint + thin root compatibility (optional)

**Move** `main.py` → `llm_cost_investigator/cli.py`.

**Register** in `pyproject.toml`:

```toml
[project.scripts]
llm-cost-investigator = "llm_cost_investigator.cli:main"
```

**Optional:** leave a 3-line root `main.py` that calls `cli.main()` so existing README commands (`python3 main.py --scenario …`) keep working during the transition. Prefer deleting it once README is updated, if you want a clean root.

### 3. Tests under `tests/`

**Move** `replay_tests.py` → `tests/test_replay.py`.

Run with:

```bash
python3 -m pytest tests/          # if pytest added later
# or keep current style:
python3 tests/test_replay.py
```

Do **not** invent a large test framework in this move unless already using one.

### 4. Group presentation under `web/`

| From | To |
| --- | --- |
| `api/` (code only) | `web/api/` |
| `frontend/` | `web/frontend/` |
| `api/data/*.json` | `data/replay/*.json` |

Update:

- `catalog.py` default data dir → `data/replay` (repo-relative, not under package).
- `scripts/dev_replay.sh` uvicorn module path → `web.api.app:app` **or** keep package name `api` by placing it as `web/api` on `PYTHONPATH=web`.

**Preferred install shape for the API package name:**

Option A (recommended): treat `web` as path root so imports stay `from api...`:

```bash
PYTHONPATH=web uvicorn api.app:app
```

Option B: rename package to `web.api` and update all imports to `from web.api...`.

**Choose Option A** — less rename noise; `web/` is only a folder boundary.

### 5. Unify artifact storage under `data/`

| Kind | Path | Writer | Reader |
| --- | --- | --- | --- |
| Incident reports | `data/reports/incidents/` | `reporter.write_report` | export script, humans |
| Live transcripts | `data/reports/transcripts/` | live scripts | export script |
| Replay fixtures | `data/replay/` | `export_replay_catalog.py` | FastAPI catalog |

Default `write_report(..., output_dir="data/reports/incidents")`.

Live scripts write `data/reports/transcripts/live_*.txt`.

Remove the empty root `data/` confusion by giving it real subfolders.

### 6. Scripts layout

| Path | Role |
| --- | --- |
| `scripts/live/*` | Provider harnesses (Groq/Cerebras) |
| `scripts/export_replay_catalog.py` | Build `data/replay/` |
| `scripts/dev_replay.sh` | Local web demo |

No business logic moves into scripts beyond what already lives there.

### 7. Docs / plans

**Move** `plans/` → `docs/plans/`.

Keep `AGENTS.md` and `README.md` at root (standard convention).

### 8. Dependencies in one place

Update `pyproject.toml`:

```toml
[project]
dependencies = [
  "openai>=1.0,<2.0",
  "pydantic>=2.8,<3.0",
]

[project.optional-dependencies]
web = [
  "fastapi>=0.115,<1.0",
  "uvicorn[standard]>=0.30,<1.0",
]
dev = [
  # optional later: pytest
]

[project.scripts]
llm-cost-investigator = "llm_cost_investigator.cli:main"

[tool.setuptools]
packages = ["llm_cost_investigator"]
```

Install:

```bash
pip install -e ".[web]"
```

**Delete** `api/requirements.txt` after extras land (or leave a one-line pointer file during migration).

### 9. Root cleanliness

Add/update `.gitignore`:

```text
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
node_modules/
dist/
web/frontend/dist/
.env
data/runtime/          # if any ephemeral SQLite DBs appear later
*.db
```

Do not commit `node_modules/` or egg-info (if currently tracked, untrack in this PR).

---

## Migration steps (execution order)

Do this as one cohesive change set (single PR/branch). Prefer `git mv` to preserve history.

1. **Create target directories**
   - `tests/`, `web/`, `data/reports/incidents/`, `data/reports/transcripts/`, `data/replay/`, `scripts/live/`, `docs/plans/`

2. **Move core entry + tests**
   - `main.py` → `llm_cost_investigator/cli.py` (adjust `if __name__`)
   - Optional thin root `main.py` shim
   - `replay_tests.py` → `tests/test_replay.py`
   - Fix any hard-coded `reports/` paths inside tests

3. **Move artifacts**
   - `reports/*_incident.*` → `data/reports/incidents/`
   - `reports/live_*.txt` → `data/reports/transcripts/`
   - Remove empty `reports/` and empty old `data/` if leftover

4. **Move web**
   - `api/` code → `web/api/` (without `api/data`)
   - `api/data/*` → `data/replay/`
   - `frontend/` → `web/frontend/`
   - Point `catalog.DEFAULT_DATA_DIR` at repo `data/replay`
   - Update `dev_replay.sh` paths and `PYTHONPATH=web`
   - Update Vite proxy (unchanged URLs; only folder path for `npm run dev`)

5. **Move scripts + docs**
   - Live harnesses → `scripts/live/`
   - Update transcript output paths in those scripts
   - `plans/*` → `docs/plans/`
   - Update `export_replay_catalog.py` input/output roots

6. **Wire packaging**
   - `pyproject.toml`: scripts + optional `web` extras
   - Remove or deprecate `web/api/requirements.txt`
   - Reinstall: `pip install -e ".[web]"`

7. **Update docs**
   - `README.md` run commands, paths, replay UI section
   - `AGENTS.md` only if it references paths (likely minimal)

8. **Delete dead weight**
   - Empty dirs, obsolete path comments, root `__pycache__`

9. **Verify** (see Verification Plan)

---

## Path rewrite checklist (files that must change)

| File | Change |
| --- | --- |
| `llm_cost_investigator/reporter.py` | default `output_dir` → `data/reports/incidents` |
| `llm_cost_investigator/cli.py` (from main) | no logic change beyond move |
| `tests/test_replay.py` | report path expectations if any |
| `scripts/export_replay_catalog.py` | `REPORTS`, `OUT_DIR`, imports via `PYTHONPATH` |
| `scripts/live/run_*_live.py` | transcript write paths |
| `scripts/dev_replay.sh` | `web/api`, `web/frontend`, `data/replay` |
| `web/api/services/catalog.py` | `DEFAULT_DATA_DIR` → repo `data/replay` |
| `web/frontend/package.json` | name only if desired; scripts same |
| `pyproject.toml` | extras + console_scripts |
| `README.md` | all commands and tree description |
| Replay fixture `meta.source_files` | re-export after path move |

**Do not rewrite** historical plan docs’ internal old paths except adding a short note at the top of `docs/plans/investigation_replay_ui_plan.md` that paths were superceded by this layout.

---

## What we are deliberately not doing

| Idea | Why skip |
| --- | --- |
| Full monorepo (`packages/*`) | Overkill for one demo product |
| `src/llm_cost_investigator` | Extra packaging churn |
| Merging React into Python package | Wrong language boundary |
| Database for fixtures | Static replay catalog is enough |
| Renaming core package | Breaks mental model and imports |
| Moving SQLite runtime DBs into package | Keep ephemeral under gitignored runtime path if added later |

---

## Open Questions

| Question | Recommendation |
| --- | --- |
| Keep root `main.py` shim? | **Yes for one transition**, then remove after README uses `llm-cost-investigator` or `python -m llm_cost_investigator.cli`. |
| Keep name `reports/` at root? | **No** — nest under `data/reports/{incidents,transcripts}` so all data has one home. |
| Rename Python package `api` → `web.api`? | **No** — use `web/` as filesystem parent and `PYTHONPATH=web` so `import api` still works. |
| Commit `data/replay/*.json`? | **Yes** — demo fixtures (same as committing reports). |
| Commit `data/reports/*`? | **Yes** for demo evidence already in repo; regenerable but useful checked-in. |
| Add pytest now? | **Optional** — moving the file is enough; pytest is not required for this restructure. |

---

## Verification Plan

After the move, from a clean shell:

```bash
# 1. Install
pip install -e ".[web]"

# 2. Core CLI
python -m llm_cost_investigator.cli --scenario retry_loop --force-fallback
# or: llm-cost-investigator --scenario retry_loop --force-fallback
# expect write under data/reports/incidents/

# 3. Tests
python3 tests/test_replay.py
# all PASS

# 4. Export catalog
python3 scripts/export_replay_catalog.py
# writes data/replay/*.json (6 files)

# 5. API
PYTHONPATH=web uvicorn api.app:app --port 8000
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/incidents | python3 -c "import sys,json; assert len(json.load(sys.stdin))>=5"

# 6. Frontend
cd web/frontend && npm install && npm run dev
# UI loads catalog via proxy

# 7. Hygiene
test ! -d reports          # old path gone (or only if intentionally kept)
test -d data/replay
test -d web/api
test -d web/frontend
test -d docs/plans
```

**Regression boundary**

- No changes to agent prompts, gate thresholds, aggregator tie-breaks, or detector math.
- Restructure is path/packaging only plus default output directories.

**Done definition**

```text
Root contains only: README, AGENTS, pyproject, package, tests/, web/, data/, scripts/, docs/
Core CLI + tests pass
Replay export + API + UI work from new paths
README documents the new tree and commands
```
