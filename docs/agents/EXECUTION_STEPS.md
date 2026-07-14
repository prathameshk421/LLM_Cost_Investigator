# How to execute the Agent System Upgrade

Spec: [`agent_system_upgrade.md`](./agent_system_upgrade.md)  
Contract (keep compatible): root [`Agents.md`](../../Agents.md)

## Decision summary (read the Claude design and chose this)

| Choice | Decision | Why |
| --- | --- | --- |
| Architecture | **Keep** Claude’s principle: skills → KB → graph → prompt → capped aggregator | Matches our “DECISION injection / deterministic first” style |
| Layout | Flat modules (`agent_skills.py`, …), **not** nested `agents/` package | Avoids clash with existing `agents.py` |
| Schema | **No** new top-level AgentEvidence fields in v1 | Put `hard_skills`, `kb_matches`, `skill_band_ceiling` inside `supporting_metrics` |
| Prompts | Replace the three `build_*_prompt` bodies with Section 4 prompts | Inject `HARD_SKILL_RESULTS`, `KB_MATCHES`, `EVIDENCE_GRAPH` |
| Multi-cause (X-01) | **Defer** dual-winner reporting | Keep single root cause for reporter/UI; optional later |
| Cost attribution | Approximate from **sample_calls** | Full-window attribution needs store queries; soft when samples sparse |
| Price tier | Use `ingest/pricing.py` | Fail falsifier only when both models have known rates and after ≤ before |
| Fallbacks | Run same skills offline | Offline demos stay aligned with hard gates |
| Tools | Keep tool-use path | Skills still run first as ground truth |

---

## Phase order (do in order)

### Phase 0 — Safety baseline (5 min)

```bash
cd /Volumes/SSD/Projects/Personal/LLM_Cost_Investigator
python3 tests/test_replay.py
python3 -m llm_cost_investigator.cli --scenario all --force-fallback
```

Record that demos PASS before you touch agent logic.

---

### Phase 1 — Hard skills only (highest value)

**Create** `llm_cost_investigator/agent_skills.py`:

```python
# sketch
SkillVerdict = Literal["pass", "soft_pass", "fail"]

def score_retry_loop(anomaly: AnomalyWindow) -> dict[str, Any]:
    """Return {skill_name: {verdict, detail, falsifier?}, ...} + band_ceiling."""

def score_token_context(anomaly: AnomalyWindow) -> dict[str, Any]: ...
def score_model_routing(anomaly: AnomalyWindow) -> dict[str, Any]: ...

def skill_band_ceiling(skill_results: dict) -> float:
    """Map pass/soft/fail combo → 1.0 | 0.89 | 0.74 | 0.49"""

def forced_no_strong_signal(skill_results: dict) -> bool:
    """True if any falsifier skill is fail."""
```

Implement skills using **existing** `anomaly.signals` + `anomaly.sample_calls` (see adaptation table in the spec).

**Wire into fallbacks first** (`fallback_retry_loop` etc. or shared helper):

1. Compute skills  
2. If falsifier fail → `no_strong_signal`  
3. Else pick hypothesis + confidence ≤ `skill_band_ceiling`

**Test:**

```bash
python3 tests/test_agent_skills.py   # new: pure unit tests on synthetic AnomalyWindows
python3 tests/test_replay.py         # demos still PASS
```

**Exit criteria:** offline demos still green; skills alone improve fallback explanations (`supporting_metrics` includes skill dict).

---

### Phase 2 — Knowledge base

**Create** `llm_cost_investigator/knowledge_base.py`:

- Encode patterns RL-01…X-03 as structured dicts (from Section 2)
- `match_patterns(agent_name, telemetry_slice, skill_results) -> list[matched]`
- Each match: `pattern_id`, `band_high`, `band_low`, `story`

**Test:** synthetic skill_results → expected pattern IDs (Examples A–D).

---

### Phase 3 — Evidence graph (compact summary only)

**Create** `llm_cost_investigator/evidence_graph.py`:

- `build_graph(anomaly, per_agent_skills) -> graph`
- `summarize_graph(graph) -> str` in the 3-line hypothesis format from Section 3

Do **not** persist the graph. It’s prompt fodder only.

**Test:** string contains `H:uncapped_retry_loop` style lines; falsifier shows under `contradicts`.

---

### Phase 4 — Upgrade prompts (Claude Section 4 → our code)

**Edit** `llm_cost_investigator/agents.py`:

1. Change signatures:

```python
def build_retry_loop_prompt(
    telemetry_json: str,
    *,
    hard_skill_results_json: str,
    kb_matches_json: str,
    evidence_graph_summary: str,
) -> str: ...
```

2. Paste shared preamble + agent body from Section 4 of the spec (fill placeholders).
3. In `run_agents` / `run_agents_detailed` (and tool-use path if separate):

```text
for each agent:
  skills = score_*(anomaly)
  if forced_no_strong_signal(skills):
      return AgentEvidence(..., no_strong_signal, supporting_metrics={skills...})
      # optional: still call LLM for narrative-only — v1 skip LLM to save cost
  kb = match_patterns(...)
  graph_summary = summarize_graph(...)  # can be shared across agents
  prompt = build_*_prompt(telemetry, skills, kb, graph_summary)
  evidence = call LLM + validate
  evidence.supporting_metrics["hard_skills"] = ...
  evidence.supporting_metrics["kb_matches"] = ...
  evidence.supporting_metrics["skill_band_ceiling"] = ...
```

**Recommendation for v1:** if falsifier fails, **skip LLM** and return deterministic `no_strong_signal` (faster, cheaper, consistent).

---

### Phase 5 — Aggregator caps

**Edit** `llm_cost_investigator/aggregator.py`:

```python
def select_root_cause(evidence: list[AgentEvidence]) -> RootCauseResult:
    # 1. drop no_strong_signal
    # 2. combined = min(confidence, supporting_metrics.get("skill_band_ceiling", confidence))
    # 3. optional: demote if supporting_metrics flags cross_contradict
    # 4. winner = max by (combined, TIE_BREAK) but tie-break only if |c1-c2| <= 0.05
```

If all dropped, keep today’s `no_strong_signal` result; optionally set explanation via report path from X-02/X-03 later.

**Test:** LLM returns confidence 0.99 but ceiling 0.74 → root cause confidence ≤ 0.74.

---

### Phase 6 — Docs + green suite

1. Update root `Agents.md` with short “Hard skills + KB” section (link to this doc).  
2. Run full suite:

```bash
python3 tests/test_agent_skills.py
python3 tests/test_knowledge_base.py
python3 tests/test_evidence_graph.py
python3 tests/test_aggregator_caps.py
python3 tests/test_replay.py
python3 -m llm_cost_investigator.cli --scenario all --force-fallback
# optional live:
# python -m llm_cost_investigator.cli --scenario all --provider groq
```

3. Spot-check one live investigate still returns JSON + stages in UI.

---

## Suggested file touch list

| File | Action |
| --- | --- |
| `docs/agents/agent_system_upgrade.md` | Spec (done) |
| `llm_cost_investigator/agent_skills.py` | **Create** |
| `llm_cost_investigator/knowledge_base.py` | **Create** |
| `llm_cost_investigator/evidence_graph.py` | **Create** |
| `llm_cost_investigator/agents.py` | Wire + new prompts |
| `llm_cost_investigator/aggregator.py` | Cap + epsilon ties |
| `llm_cost_investigator/fallback.py` | Reuse skills if separate |
| `Agents.md` | Brief contract update |
| `tests/test_agent_*.py` | **Create** |

Do **not** touch for this upgrade: connectors, ingest, web UI stage schema (unless supporting_metrics appear in raw_report only).

---

## Risk controls

| Risk | Mitigation |
| --- | --- |
| Demo scenarios drop below PASS (0.70) | Soften sample_consistency / cost_attribution when samples empty; never fail falsifier on missing data — use soft_pass |
| Over-strict falsifiers | Missing metric → soft_pass, not fail |
| Prompt bloat | Graph summary max ~40 lines; skills JSON compact |
| Double-counting thresholds | LLM never recomputes; aggregator caps |

---

## Definition of done

- [ ] Skills computed in Python for all 3 agents  
- [ ] Falsifier fail → forced `no_strong_signal` without LLM  
- [ ] Prompts include HARD_SKILL_RESULTS + KB_MATCHES + graph summary  
- [ ] Aggregator never exceeds skill_band_ceiling  
- [ ] Tie-break only within 0.05  
- [ ] `tests/test_replay.py` and `--scenario all --force-fallback` PASS  
- [ ] Worked examples A–D covered by unit tests  

---

## Optional later (not v1)

- Multi-cause X-01 dual listing in incident report  
- Full-window cost attribution via TelemetryStore queries  
- Extract prompts to `.txt` files  
- UI surface for skill/KB cards in stage panels  
