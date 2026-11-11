# Project review: failures, fixes and open risks

Review date: 2026-11-11. Base: branch `mvp` at `d806b92`.

The 43 existing tests passed before this review. They did not catch the
problems below, because the simulator makes one fixed dataset per scenario.
The review added a seeded scenario lab and ran the pipeline on hundreds of new
datasets, plus an independent probe set written by a separate audit.

## Fixed

| # | Severity | Problem | Fix | Proof |
|---|---|---|---|---|
| 1 | High | Real traffic growth (more calls, same shape) was diagnosed as context bloat in 20 of 20 runs. Token signals used window totals, which grow with call volume. | New per-call signal `avg_input_token_growth_pct`. The router and a new falsifier skill `per_call_growth_check` need per-call growth or a call chain. | `traffic_surge` 0% → 100% correct. False alarm rate 52.5% → 0%. |
| 2 | High | The zero-variance z-score floor was 1.0 in absolute units. For cost that is $1, so a 100x spike on a $0.01 baseline had z = 0.99 and was not flagged. | Floor is 10% of the baseline mean (unit floor only when the mean is 0). | 100x spike: z 0.99 → 990. |
| 3 | High | `--scenario` PASS did not check the root cause. Any confident answer passed. | PASS needs the expected root cause. | `cli.py` `EXPECTED_ROOT_CAUSE` |
| 4 | Medium | Misroutes were missed when traffic also changed: model skills used total token growth. | Model skills use per-call growth. New signal `unit_cost_growth_pct` (USD per 1k tokens) and skill `unit_price_jump`. | Bursty probe misroute 25/30 → 29/30. |
| 5 | Medium | Early retry storms and deep self-call chains were not flagged until cost left the noise band. | Structural early warning in the detector, measured against the feature's own baseline. | Bursty probe retry 26/30 → 30/30, bloat 20/30 → 27/30. |
| 6 | Medium | Flagged windows stayed in the baseline, so a sustained incident hid its own later windows. | Flagged windows are kept out of the baseline. | `detector.py` |
| 7 | Medium | A sustained incident spans many windows, and each one paid for its own LLM investigation. | Adjacent flagged windows of one feature merge into one incident. | Live misroute run: 4 calls → 1, $0.00103 → $0.00027. |
| 8 | Medium | LLM answers in markdown code fences always fell back to the deterministic path. | Fence and prose stripping before schema validation. | `test_markdown_fenced_json_is_accepted` |
| 9 | Medium | An agent could return another agent's hypothesis and win. | `check_agent_scope` rejects it and the agent falls back. | `test_agent_cannot_return_another_agents_hypothesis` |
| 10 | Medium | Upload routes were `async` but did blocking work, which froze the event loop (22 s on a 21 MB file). The size check ran after the full read. | Sync routes (run in a worker thread). Bounded read and a 413 error. | `test_oversized_upload_is_413` |
| 11 | Medium | 3 test files (including the 724-line replay suite) had no `test_` functions, so pytest never ran them. | Added pytest entry points. | 43 → 46 collected before new tests. |
| 12 | Low | Mixed naive and aware timestamps crashed the detector. | Naive times are read as UTC. | `test_mixed_naive_and_aware_timestamps_do_not_crash` |
| 13 | Low | Pricing: "o1" matched any model name that contains those letters. Newer models were priced at a $1/$3 default. | Short keys match as a prefix only. Added GPT-5, o3, o4-mini, Claude 4 and Gemini 2.5 rates. | `test_pricing_short_keys_need_prefix` |
| 14 | Low | UTF-16 and non-UTF-8 files raised a raw `UnicodeDecodeError`. Unknown adapter gave the detail `'bogus'`. | Clear 400 errors. | `test_utf16_and_bad_bytes`, `test_bad_adapter_is_clear_400` |
| 15 | Low | Frontend error handler read the response body twice, so API error details were lost. | Read once, then parse. FastAPI 422 lists are formatted. | `web/frontend/src/api/client.ts` |

All 15 fixes have regression tests in `tests/test_hardening.py`. Fixes 1, 2,
8, 12 and 13 were also run against the original `mvp` code to confirm that
the tests fail there.

## Added

- `hf` provider: Hugging Face Inference Providers router, default `openai/gpt-oss-20b`.
- Budget guard and call log (`usage.py`): hard per-process limits on calls and
  USD, one JSON line per live call.
- Scenario lab and benchmark with a CI accuracy gate.
- API: `/api/simulate`, `/api/usage`, `/api/benchmark`. Responses carry a cost
  timeline and per-run LLM usage.
- UI: one-click Simulate source, cost timeline chart with flagged bands,
  correct/wrong verdict, LLM budget badge with the recent call table, and a
  Benchmark tab.
- GitHub Actions CI: pytest, scenario CLI, accuracy gate, frontend build.

## Open risks

| Risk | Impact | Suggested next step |
|---|---|---|
| The API has no authentication. With a provider key in `.env`, any caller can spend it, up to the budget guard limit. Langfuse routes also use server-side keys. | Spend up to `LLM_BUDGET_USD` per process; data pull from Langfuse. | Add an API token or bind to localhost only for demos. Keep the budget low on shared hosts. |
| The budget guard is per process and in memory. A restart resets it. | A restart loop can spend more than one budget. | Persist the ledger (SQLite) or use the provider's own spend limit. |
| Cost estimates use a static price table. | Spend numbers are estimates, not invoices. | Read `usage.cost` where a provider returns it. |
| Accuracy numbers come from synthetic data. | Real telemetry has shapes the lab does not make. | Replay a real Langfuse export and label a sample by hand. |
| The detector needs 3 baseline windows (15 minutes) per feature. | A new feature is not checked in its first 15 minutes. | Add a cold-start rule against a global per-call cost prior. |
| The router calls at most 2 agents. When all three signals fire, one agent only contributes through skill scores. | A mixed incident can lose its third cause. | Allow 3 agents when the budget allows. |
| `print()` calls in library code for fallback notes. | Noisy stdout when used as a library. | Move to `logging`. |
| Some tests write reports into tracked `data/reports/`. | Dirty working tree after a test run. | Point tests at a temp `output_dir`. |
