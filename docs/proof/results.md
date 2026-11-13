# Proof results

All thresholds were committed before any run: [benchmarks/PREREGISTRATION.md](../../benchmarks/PREREGISTRATION.md)
(first commit `b2b3a79`, addenda 1 to 6 dated and committed; each says whether it came before or after the runs it covers).
Plan: [PLAN.md](PLAN.md). Raw outputs: [data/](data/). Intervals are Wilson 95%.

## One-page summary

| Tier | What was tested | N | Result | Pre-registered verdict |
|---|---|---|---|---|
| A | Blind synthetic held-out set, seeds 200-299 | 2,800 datasets | 99.1% of incidents caught [98.5-99.4]; 0.5% false alarms on clean data [0.2-1.2]; 0.7% wrong extra labels | **1 of 4 criteria pass** (see below). Not falsified. |
| A | vs plain cost alert (B1) | same | B1: 36.9% caught, 100% false alarms | product far ahead |
| A | vs three hand rules (B2) | same | B2: 100% caught, **16.4% false alarms**, 10.7% wrong extra labels | product has **33x fewer false alarms**; B2 catches more and faster |
| A | Live LLM ablation (DeepSeek V4.1 Flash), seeds 200-203 | 112 paired | exact match: deterministic 110, LLM + guard 110, LLM without guard 111; McNemar p = 1.0 | **C5 pass**: the LLM does not lower accuracy; $0.0018 per dataset |
| B | Real traffic background: BurstGPT (real Azure OpenAI requests), injected incidents | 200 injected + 100 clean 72 h slices | 96.5% caught (193/200); 0 false incidents over 171 feature-weeks | **pass** (≥ 90%, ≤ 1 per feature-week) |
| B | Raw real trace, no injection | 240 feature-days, 1.4M requests | 0 incidents reported. The detector flagged 178 windows (heavy-tailed real prompt sizes); diagnosis labelled all 178 as volume (X-02). | not falsified (> 1 per feature-day) |
| C | Red team: 12 adversarial cases × 20 seeds | 240 datasets | 0 crashes; 0% confident wrong labels [0-2] | **pass** (after one crash fix found on dev seeds, see addendum 3) |
| D | Live app with real LLM calls and three real induced bugs ([tier_d.md](tier_d.md)) | 2 takes (1,942 real call attempts, 1,214 succeeded) | 0 alarms in both 75-min baselines. Retry loop caught in 58 s, misroute in 3 min 58 s, bloat in 59 s, all with the right label on the right feature. 0 wrong labels. | **pass**, over two takes: a network outage voided the misroute and bloat parts of take 4 (addendum 6) |
| E | Blind labels of 40 slices on fresh seeds (CSV only, no tool output) | 40 | Fable: 40/40 vs truth, 40/40 vs tool (κ = 1.0). gpt-oss-20b: 10/40 (could not do the task). Tool vs truth: 40/40. | **pass** with caveats: Fable wrote the generator; it was added after gpt-oss failed; no human labels (addenda 4-5) |

## Tier A detail

| Criterion (pre-registered) | Threshold | Measured | Verdict |
|---|---|---|---|
| 1. Hit-rate lower bound | ≥ 97% | 98.5% | PASS |
| 2. False-alarm upper bound | ≤ 1% | 1.2% (5/1,000) | FAIL, narrowly |
| 3. Detection delay, median / p95 per cause | ≤ 10 / ≤ 20 min | bloat 11 / 26, misroute 22 / 61, retry 11 / 28 | FAIL |
| 4. False alarms 20 points below each baseline | ≥ 20 pts | B1: 99.5 pts, B2: 15.9 pts | FAIL against B2 |
| Falsifier: B2 within 2 points | - | B2 false alarms 16.4% vs 0.5% | not falsified |
| Falsifier: time-tolerant hit 3 points below any-time hit | - | 99.1% vs 99.1% | not falsified |

Plain reading:
- The product's advantage is **precision**. It almost never raises a false or wrong
  incident: 0.5% false alarms vs 16.4% for the three-rule baseline and 100% for a cost alert.
- It is **not fast**. A misroute is confirmed after a median of 22 minutes; the three
  rules flag a new model in 4. The 10-minute target was missed for every cause.
- Weakest cases: subtle depth-4 bloat (10/100 missed), and sparse night traffic
  (2-4 wrong extra labels per 100). Full per-case table: [data/tier_a_200_299.json](data/tier_a_200_299.json).

## Code after the pinned proof runs

Tiers A, B, C and E ran on the commits named in the pre-registration. Two agent
changes came later: the tool-call loop became an explicit state machine
(`c338e10`), and an injected LLM client is now called even without a provider
name (`e619576`). A live check of the new loop on seeds 900-901 matched the
deterministic result on 16 of 16 datasets. Tier D ran on the later code.

## Live LLM evidence (DeepSeek V4.1 Flash via the Deploy gateway)

| Run | Datasets | Calls | Errors | Spend | Accuracy vs deterministic |
|---|---|---|---|---|---|
| seeds 120-123 | 112 | 563 | 0 | $0.19 | 99.1% vs 100% exact (1 miss, root cause fixed: RL-03 veto) |
| seeds 124-127, 8 workers | 112 | 451 | 266 (provider outage) | $0.06 | 98.2% vs 98.2%: every failed call fell back, no crash, no wrong answer |
| seeds 124-127, 4 workers | 112 | 572 | 0 | $0.19 | 98.2% vs 98.2% |
| ablation, seeds 200-203 | 2 × 112 | 1,173 | 0 | $0.40 | see Tier A row |

The LLM adds a readable explanation and tool-checked evidence. It does not add measurable accuracy.

## What we claim, and what we do not

We claim:
- On blind synthetic incidents, at least 98.5% are caught (95% lower bound) with a
  false-alarm rate between 0.2% and 1.2%.
- On a real-traffic background (BurstGPT), 96.5% of injected incidents are caught with
  0 false incidents in 171 feature-weeks of clean real traffic.
- It is far more precise than a cost alert or three hand rules.
- On a live app with real API calls, it caught three real bugs (retry loop, pricier model, self-calling agent) within 1 to 4 minutes, with 0 baseline alarms.
- A live LLM (DeepSeek) does not lower accuracy and costs about $0.002 per dataset.

We do not claim:
- Accuracy on customer data. All incidents are injected or synthetic.
- Fast detection. The median delay is 11 to 22 minutes.
- That the LLM improves accuracy.
- Invoice-accurate costs. Prices come from a static table.
- Coverage of a feature's first hour (cold start), or of retries and agent chains that
  the telemetry does not mark (no `retry_count`, no `parent_call_id`). See [failure_analysis.md](failure_analysis.md).
- That a planned upgrade to a pricier model is told apart from a mistaken one. It is not.

## Reproduce

```bash
make install
PYTHONPATH=. .venv/bin/python -m benchmarks.heldout.tier_a --seed-from 200 --seed-to 300 --baselines --out /tmp/tier_a.json
PYTHONPATH=. .venv/bin/python -m benchmarks.real.tier_b --slices-seed-from 200 --slices-seed-to 300 --clean-from 500 --clean-to 600 --out /tmp/tier_b.json   # needs BurstGPT_1.csv
PYTHONPATH=. .venv/bin/python -m benchmarks.redteam.run --seed-from 200 --seed-to 220 --out /tmp/tier_c.json
DEPLOY_API_KEY=... PYTHONPATH=. .venv/bin/python -m benchmarks.heldout.live_llm --provider deploy --seed-from 200 --seed-to 204 --workers 4
```
