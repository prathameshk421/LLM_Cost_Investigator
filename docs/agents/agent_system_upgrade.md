# LLM Cost Investigator — Agent System Upgrade

> **Source:** Design drafted with Claude (website), adapted for this repo.  
> **Status:** Spec / ready to implement — not yet wired into code.  
> **Related:** `Agents.md`, `llm_cost_investigator/agents.py`, `aggregator.py`, `fallback.py`  
> **Principle (load-bearing):** every numeric threshold decision is resolved in **Python** before the LLM ever sees it. The LLM does differential diagnosis and narrative — not judging whether `retry_z_score >= 5`.

Pipeline addition:

```text
skills.py  → hard_skill_results per agent
knowledge_base.py → pattern matches
evidence_graph.py → compact graph summary
        ↓
injected into agent prompts + raw telemetry
        ↓
LLM → hypothesis / confidence / explanation
        ↓
aggregator caps confidence by skill band (does not fully trust LLM)
```

---

## Adaptation notes (repo-specific)

These map Claude’s design onto what we already have.

| Design piece | Repo reality | v1 approach |
| --- | --- | --- |
| Module layout `agents/skills.py` | Flat package today (`agents.py`) | Prefer `llm_cost_investigator/agent_skills.py` (or package `agent_reasoning/`) to avoid clashing with `agents.py` |
| `latency_growth_confirmation` | We have `latency_z_score` + sample `latency_ms`/`retry_count` | Approximate trend from samples; soft if samples thin |
| `cost_attribution_check` | No full-window cost breakdown in agent slice | Approximate from `sample_calls` (cost where `retry_count>0` / sum cost); mark as estimate in metrics |
| `chain_depth_recency` | Samples capped at 10 | Soft-pass when unclear; fail only if samples clearly predate window |
| `price_tier_consistency` | `ingest/pricing.py` rates exist | Use `lookup_rates` / known models; unknown models → soft_pass |
| `self_call_pattern_check` | Samples have `parent_call_id` | Detect repeated parent / same feature recursion from samples |
| Fallback agents | `fallback_*` already use thresholds | Reuse skills for fallbacks so offline path matches hard gates |
| Tool-use agents | Already have `get_call_chain` / `get_window_calls` | Keep tools; skills run **before** tool loop as ground truth |
| Schema | `AgentEvidence` extra=forbid | Put skills/KB/graph under `supporting_metrics`; don’t break schema |
| Demos | `tests/test_replay.py` PASS thresholds | Skills must not make demos fail; tune soft bands if needed |

**Defer if signals missing (don’t invent):** perfect cost attribution %, full-window latency series, chain recency without store access. Prefer soft_pass + explanation over hard fail when data is thin.

---

## 1. Hard Skills (per agent)

Each skill returns `pass | soft_pass | fail`. A `fail` on a skill marked **(falsifier)** forces `no_strong_signal` for that agent regardless of everything else.

### Retry Loop Agent

| # | Skill | Inputs | Pass | Soft-pass | Fail | Effect |
|---|-------|--------|------|-----------|------|--------|
| 1 | `retry_zscore_threshold` | `retry_z_score` | ≥5 | 3–5 | <3 | Gates 0.90+ band |
| 2 | `max_retry_floor` | `max_retry_count` | ≥5 | 2–4 | <2 | Gates 0.90+ band |
| 3 | `repeated_parent_call_check` | `repeated_parent_call_count` | ≥3 same parent | 1–2 | 0 | Confirms loop vs. one-off |
| 4 | `latency_growth_confirmation` | latency trend across retries | increasing trend | flat but plausible | flat + high count | Flat latency despite "high retries" smells like a counting bug |
| 5 | `sample_call_consistency` **(falsifier)** | sample calls vs. aggregate | samples show `retry_count>0` matching aggregate | minor mismatch | samples contradict aggregate (e.g. mostly `retry_count=0`) | Fail ⇒ force `no_strong_signal` |
| 6 | `cost_attribution_check` **(falsifier)** | retried-call cost ÷ total anomalous cost | ≥50% | 20–50% | <20% | Fail ⇒ force `no_strong_signal` |
| 7 | `non_retry_alternative_scan` | are token_context/model_routing signals also strong? | none strong | one moderate | another agent's hard-skill passes at 0.90+ | Doesn't fail this agent, but creates a `contradicts` edge in the evidence graph |

### Token Context Agent

| # | Skill | Inputs | Pass | Soft-pass | Fail | Effect |
|---|-------|--------|------|-----------|------|--------|
| 1 | `token_growth_threshold` | input token growth % | ≥300% | 100–299% | <100% | Gates 0.90+ band |
| 2 | `chain_depth_floor` | max parent call-chain depth | ≥5 | 3–4 | <3 | Gates 0.90+ band |
| 3 | `chain_depth_recency` **(falsifier)** | do the deep chains fall inside the anomaly window? | yes | mostly yes | deep chains predate the spike | Fail ⇒ force `no_strong_signal` |
| 4 | `token_zscore_check` | `input_token_zscore` | ≥3 | 2–3 | <2 | Supports band |
| 5 | `prompt_content_growth_correlation` **(falsifier)** | cost growth vs. token growth ratio | cost growth ≈ token growth × price/token | mild divergence | cost growth far exceeds token growth | Fail ⇒ this looks like model routing, not bloat — hand off, don't claim |
| 6 | `self_call_pattern_check` | parent-child call signatures | same-agent signature recursing | mixed | genuinely varied multi-step pipeline (not recursive) | Distinguishes real bloat from normal deep pipelines |
| 7 | `sample_consistency_check` | samples vs. aggregate depth/tokens | match | minor mismatch | contradict | Data-quality flag |

### Model Routing Agent

| # | Skill | Inputs | Pass | Soft-pass | Fail | Effect |
|---|-------|--------|------|-----------|------|--------|
| 1 | `model_changed_flag` **(falsifier)** | `model_before != model_after` | true | — | false | Fail ⇒ force `no_strong_signal` (nothing to route-blame) |
| 2 | `cost_growth_threshold` | cost growth % | ≥200% | 50–199% | <50% | Gates 0.90+ band |
| 3 | `token_stability_check` **(falsifier-ish)** | token growth % | <50% | 50–100% | >100% | Fail ⇒ cap confidence at 0.50–0.74, defer to token_context |
| 4 | `cost_zscore_check` | `cost_zscore` | ≥3 | 2–3 | <2 | Supports band |
| 5 | `price_tier_consistency` **(falsifier)** | is `model_after` actually pricier than `model_before`? | yes | — | no | Fail ⇒ force `no_strong_signal` (sanity check against known pricing) |
| 6 | `sample_call_confirmation` | do sample calls show the new model in the anomalous calls specifically | yes | mostly | new model absent from samples | Confirms scope |
| 7 | `routing_scope_check` | isolated call sites vs. global change | — | — | — | Informs explanation only, not pass/fail |

---

## 2. Knowledge Base of Patterns

| pattern_id | owner | required signals | contradictory signals | typical story | confidence band | supporting_metrics keys |
|---|---|---|---|---|---|---|
| **RL-01** | retry_loop | `retry_z≥5`, `max_retry≥5`, `cost_attribution≥50%` | token growth also high, model changed | Transient upstream 5xx with no backoff cap | 0.90–1.00 | `retry_z_score, max_retry_count, cost_attribution_pct` |
| **RL-02** | retry_loop | `retry_z 3–5`, `repeated_parent_call moderate` | samples show `retry_count=0` | Retry logic fires but isn't logged consistently | 0.75–0.89 | `retry_z_score, repeated_parent_call_count` |
| **RL-03** | retry_loop | retries present, `cost_attribution<20%` | — (self-contradicting) | Retries exist but aren't driving the spend | forces `no_strong_signal` | `cost_attribution_pct` |
| **TC-01** | token_context | `token_growth≥300%`, `chain_depth≥5`, `self_call_pattern=true` | cost growth ≫ token growth | Agent recursively re-embeds full history each call | 0.90–1.00 | `input_token_growth_pct, chain_depth, cost_token_growth_ratio` |
| **TC-02** | token_context | `token_z≥3`, `chain_depth 3–4` | chain depth predates window | Gradual prompt creep from accumulating context | 0.75–0.89 | `input_token_zscore, chain_depth` |
| **TC-03** | token_context | `chain_depth` high, `token_growth<100%` | — | Normal deep multi-step pipeline, not bloat (false-positive risk) | pushes to `no_strong_signal` or low | `chain_depth, input_token_growth_pct` |
| **MR-01** | model_routing | `model_changed=true`, `cost_growth≥200%`, `token_stability pass` | tokens also grew heavily | Router config regression sent traffic to a premium model | 0.90–1.00 | `model_before, model_after, cost_growth_pct` |
| **MR-02** | model_routing | `model_changed=true`, `cost_z≥3`, `token_growth 50–100%` | — | Partial misroute, some traffic shifted | 0.75–0.89 | `cost_zscore, token_growth_pct` |
| **MR-03** | model_routing | `model_changed=true`, tokens also grew a lot | self-contradicting | Model change confounded by real token growth — can't cleanly attribute | capped 0.50–0.74 | `token_growth_pct, cost_growth_pct` |
| **X-01** | cross-agent | two agents both clear pass-tier with comparable `cost_attribution` | mutual `contradicts` edges | Genuine co-occurring causes (rare) | per-agent bands, aggregator states both explicitly | `cost_attribution_pct` (both) |
| **X-02** | cross-agent | no agent reaches soft-pass on anything | — | Legitimate volume increase, not a bug | all → `no_strong_signal` | none |
| **X-03** | cross-agent | any `sample_consistency`-type skill fails | — | Telemetry/data-quality issue, not attributable | all → `no_strong_signal` | flag which sample check failed |

---

## 3. Lightweight Evidence Graph

**Node types**

- `AnomalyNode` — the cost spike itself (window id, total excess cost, duration)
- `SignalNode` — one per observed metric, tagged with its hard-skill verdict (`pass/soft_pass/fail`)
- `HypothesisNode` — one per agent's candidate hypothesis

**Edge types**

- `explains` (SignalNode/HypothesisNode → AnomalyNode) — this evidence accounts for the spike
- `contradicts` (SignalNode → HypothesisNode, or HypothesisNode → HypothesisNode) — evidence undermining a candidate
- `co_occurs` (SignalNode → SignalNode) — present together, no causal claim, flags confounds

**Summarizing into ~20–40 lines for the prompt** — one line per hypothesis, listing its explains/contradicts edges only (not the full graph):

```
H:uncapped_retry_loop — explains: retry_z=6.2(pass), cost_attribution=71%(pass) — contradicts: none
H:context_bloat_self_calling_agent — explains: token_growth=140%(soft) — contradicts: chain_depth=2(fail, recency)
H:expensive_model_misroute — explains: model_changed=true, cost_growth=340% — contradicts: token_growth=180%(fail token_stability)
```

**Rules forcing `no_strong_signal`**

- A hypothesis with any **falsifier**-tagged `contradicts` edge is dropped before the LLM even reasons about confidence.
- If two hypotheses have roughly equal `explains` weight and both carry unresolved `contradicts` edges, that agent returns `no_strong_signal` — cross-agent tie-breaking is the aggregator's job, not the agent's.

---

## 4. Upgraded System Prompts

### Shared Preamble (prepend to all three)

```
You are a principal SRE performing differential diagnosis on an LLM cost anomaly.
You receive telemetry for one anomaly window, plus HARD_SKILL_RESULTS that have
already been computed deterministically — you must NOT recompute or override them,
only reason from them.

Rules:
- Use only the telemetry and HARD_SKILL_RESULTS provided. Never invent metrics.
- If HARD_SKILL_RESULTS marks a falsifier as "fail" for your hypothesis, you MUST
  return "no_strong_signal" — no exceptions.
- Your confidence may not exceed the band implied by HARD_SKILL_RESULTS (see rubric).
  Prefer under-confident over wrong.
- Output ONLY valid JSON. No markdown, no prose outside the JSON object.
- Weak or contradicted evidence -> hypothesis "no_strong_signal".
```

### Retry Loop Agent — Full Prompt

```
[SHARED PREAMBLE]

ROLE: Retry Loop Agent. You test whether an uncapped/silent retry loop explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS — do not recompute):
1. retry_zscore_threshold
2. max_retry_floor
3. repeated_parent_call_check
4. latency_growth_confirmation
5. sample_call_consistency (FALSIFIER)
6. cost_attribution_check (FALSIFIER)
7. non_retry_alternative_scan

If skill 5 or 6 = "fail" -> hypothesis MUST be "no_strong_signal".

KNOWLEDGE BASE PATTERNS TO MATCH: RL-01, RL-02, RL-03 (see KB_MATCHES input).
Use the matched pattern's confidence band as your ceiling, not a target you must hit.

CONFIDENCE RUBRIC:
- 0.90-1.00: skills 1 AND 2 pass, skill 6 pass (>=50%)
- 0.75-0.89: skill 1 or 2 soft_pass/pass, skill 3 supports, skill 6 >=20%
- 0.50-0.74: some retry evidence but skill 7 flags a stronger competing signal
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY: {evidence_graph_summary}
HARD_SKILL_RESULTS: {hard_skill_results_json}
KB_MATCHES: {kb_matches_json}
Telemetry: {anomaly_window_json}

OUTPUT SCHEMA (JSON only):
{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {},
  "explanation": "..."
}
```

### Token Context Agent — Full Prompt

```
[SHARED PREAMBLE]

ROLE: Token Context Agent. You test whether context bloat / a recursive self-calling
agent explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS):
1. token_growth_threshold
2. chain_depth_floor
3. chain_depth_recency (FALSIFIER)
4. token_zscore_check
5. prompt_content_growth_correlation (FALSIFIER)
6. self_call_pattern_check
7. sample_consistency_check

If skill 3 or 5 = "fail" -> hypothesis MUST be "no_strong_signal".
If skill 5 fails specifically because cost growth exceeds token growth, note in your
explanation that this looks more like a model routing issue -- do not claim it yourself.

KNOWLEDGE BASE PATTERNS TO MATCH: TC-01, TC-02, TC-03.

CONFIDENCE RUBRIC:
- 0.90-1.00: skill 1 pass (>=300%) AND skill 2 pass (>=5)
- 0.75-0.89: skill 4 pass (z>=3) AND skill 2 pass (>=4)
- 0.50-0.74: token growth exists but skill 2/6 evidence is weak
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY: {evidence_graph_summary}
HARD_SKILL_RESULTS: {hard_skill_results_json}
KB_MATCHES: {kb_matches_json}
Telemetry: {anomaly_window_json}

OUTPUT SCHEMA (JSON only):
{
  "agent_name": "token_context_agent",
  "hypothesis": "context_bloat_self_calling_agent" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {},
  "explanation": "..."
}
```

### Model Routing Agent — Full Prompt

```
[SHARED PREAMBLE]

ROLE: Model Routing Agent. You test whether accidental routing to a pricier model
(with roughly stable tokens) explains the cost spike.

HARD SKILL CHECKLIST (already resolved in HARD_SKILL_RESULTS):
1. model_changed_flag (FALSIFIER)
2. cost_growth_threshold
3. token_stability_check (caps confidence if failed)
4. cost_zscore_check
5. price_tier_consistency (FALSIFIER)
6. sample_call_confirmation
7. routing_scope_check

If skill 1 or 5 = "fail" -> hypothesis MUST be "no_strong_signal".
If skill 3 = "fail" (tokens also grew a lot), cap confidence at 0.50-0.74 and say
plainly in your explanation that token growth confounds attribution to routing alone.

KNOWLEDGE BASE PATTERNS TO MATCH: MR-01, MR-02, MR-03.

CONFIDENCE RUBRIC:
- 0.90-1.00: skill 1 pass, skill 2 pass (>=200%), skill 3 pass (tokens stable)
- 0.75-0.89: skill 1 pass, skill 4 pass (z>=3)
- 0.50-0.74: cost grew but skill 3 failed (token growth confounds) or evidence partial
- below 0.50 / falsifier fail: "no_strong_signal"

EVIDENCE GRAPH SUMMARY: {evidence_graph_summary}
HARD_SKILL_RESULTS: {hard_skill_results_json}
KB_MATCHES: {kb_matches_json}
Telemetry: {anomaly_window_json}

OUTPUT SCHEMA (JSON only):
{
  "agent_name": "model_routing_agent",
  "hypothesis": "expensive_model_misroute" | "no_strong_signal",
  "confidence": 0.0-1.0,
  "supporting_metrics": {},
  "explanation": "..."
}
```

---

## 5. Aggregator Upgrade Rules

1. **Cap, don't trust.** For each agent's returned confidence, recompute `combined_confidence = min(agent_confidence, skill_band_ceiling)`, where `skill_band_ceiling` comes from the KB pattern match — never let the LLM's stated confidence exceed what the hard skills actually support.
2. **Drop falsified agents first**, same as today — anything forced to `no_strong_signal` (by a failed falsifier) is removed before comparison.
3. **Cross-agent contradiction check.** If Agent A's hypothesis has a `contradicts` edge sourced from Agent B's passing hard skill (e.g., model_routing's `token_stability_check` fails because token_context independently found real growth), demote A's `combined_confidence` into the 0.50–0.74 band regardless of what A itself reported.
4. **Tie-break only within epsilon.** Apply the `expensive_model_misroute > uncapped_retry_loop > context_bloat_self_calling_agent` order only when two `combined_confidence` values are within 0.05 of each other — not as a blanket override of a clearly higher-confidence competitor.
5. **Never fabricate a merged cause.** Pick the single highest `combined_confidence` hypothesis. Only report "multiple contributing causes" when two hypotheses both independently clear all their falsifiers with no unresolved `contradicts` edges between them (matches KB pattern X-01) — and even then, list both explicitly rather than blending into new narrative. *(v1: keep single winner; multi-cause is optional polish.)*
6. **All-`no_strong_signal` case:** report "no attributable root cause" and surface whichever KB cross-agent pattern matched (X-02 volume increase vs. X-03 data quality) so the incident report explains *why* nothing was attributed, not just that nothing was.

---

## 6. Worked Examples

### Example A — Strong retry loop (RL-01)

Telemetry sketch: `retry_z_score=6.4, max_retry_count=9, avg_retry_count=4.2, repeated_parent_call_count=14, cost_attribution=68%`, latency increasing across retries, samples confirm retries with visible backoff.

- Skills fired: `retry_zscore_threshold`=pass, `max_retry_floor`=pass, `cost_attribution_check`=pass (68%≥50%), `sample_call_consistency`=pass.
- Expected: `uncapped_retry_loop`, confidence **0.90–0.97**, KB match RL-01.

### Example B — Strong context bloat (TC-01)

Telemetry sketch: `input_token_growth=420%, chain_depth=7, token_zscore=5.1`, self-call pattern confirmed (same agent signature recursing), cost growth tracks token growth closely (no independent model change).

- Skills fired: `token_growth_threshold`=pass (420%≥300%), `chain_depth_floor`=pass (7≥5), `prompt_content_growth_correlation`=pass.
- Expected: `context_bloat_self_calling_agent`, confidence **0.90–0.98**, KB match TC-01.

### Example C — Strong model misroute (MR-01)

Telemetry sketch: `model_before=gpt-4o-mini, model_after=gpt-4.1, model_changed=true, cost_growth=310%, token_growth=22%, cost_zscore=6.8`.

- Skills fired: `model_changed_flag`=pass, `cost_growth_threshold`=pass (310%≥200%), `token_stability_check`=pass (22%<50%).
- Expected: `expensive_model_misroute`, confidence **0.92–0.99**, KB match MR-01.

### Example D — Ambiguous → `no_strong_signal` (X-03)

Telemetry sketch: `retry_z_score=3.4` (soft), `max_retry_count=3` (soft), but 4 of 5 sampled calls show `retry_count=0` (contradicts aggregate); `token_growth=140%` (soft) but `chain_depth=2` (fail); model unchanged.

- `sample_call_consistency` fails for retry_loop (falsifier) → forced `no_strong_signal`.
- `chain_depth_recency`/`chain_depth_floor` fail for token_context → `no_strong_signal`.
- `model_changed_flag` fails for model_routing → `no_strong_signal`.
- All three agents return `no_strong_signal`; aggregator reports KB pattern **X-03** — data-quality mismatch, not attributable — rather than forcing a guess.

---

## 7. Implementation Sketch (adapted to this repo)

Prefer **flat modules under `llm_cost_investigator/`** (matches current layout):

```text
llm_cost_investigator/
  agent_skills.py       # pure functions → hard_skill_results
  knowledge_base.py     # PATTERNS + match_patterns(...)
  evidence_graph.py     # build_graph + summarize_graph (~20-40 lines)
  agents.py             # wire skills/KB/graph into prompts; keep tools + validation
  aggregator.py         # cap confidence, epsilon tie-break, cross-agent demote
  fallback.py           # optional: reuse skill ceilings for offline path
  schemas.py            # no AgentEvidence shape change; extras in supporting_metrics

tests/
  test_agent_skills.py
  test_knowledge_base.py
  test_evidence_graph.py
  test_aggregator_caps.py
  test_replay.py        # must stay green
```

Prompt text can live as Python f-strings in `agents.py` first (current style); extract to `prompts/*.txt` only if it gets noisy.

No graph DB, no orchestration platform — in-memory dicts/lists only.

---

## 8. Execution checklist (for implementer)

See also the short guide in the same folder: `docs/agents/EXECUTION_STEPS.md`.
