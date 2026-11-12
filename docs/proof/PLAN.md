# Proof plan: show outsiders that LLM Cost Investigator works

Status: plan only, nothing below is run yet. Written by an independent
evaluator from the repo state on 2026-11-12. Inputs from the code: the
detector needs 12 baseline windows (60 min), and a live-LLM dataset costs
about $0.0017.

## 1. Claims

| Claim to make | Evidence needed |
|---|---|
| C1. On blind synthetic incidents it catches ≥ 97% with ≤ 1 false alarm per 100 clean datasets | Tier A confidence intervals, time-tolerant scoring |
| C2. It beats a plain cost alert and a 3-rule classifier | Tier A baselines |
| C3. Detection takes 1 to 2 windows (5 to 10 min) after onset | Tier A delay distribution |
| C4. It holds on real traffic shape (BurstGPT background) | Tier B |
| C5. A live LLM does not lower accuracy and costs < $0.002 per dataset | Tier A ablation |
| C6. It works end to end on real API calls with real induced bugs | Tier D recording |

Do **not** claim:
- "99.6% accurate" without the word *synthetic*.
- "The LLM improves accuracy". The data so far shows 99.1% (LLM) against 100% (deterministic).
- "Production proven" or "validated on customer data".
- "Invoice-accurate cost". The price table is static.
- Cold-start coverage (the first hour of a feature).
- Viral agent-loop cost stories as fact. Secondary sources disagree.

## 2. Tiers

Write every pass/fail threshold into `benchmarks/PREREGISTRATION.md` and
commit it **before** any run. The commit hash proves the thresholds came first.

### Tier A: rigor on the synthetic set (4 h, $0.40)

Add `benchmarks/heldout/stats.py` (reads the `--out` JSON of `run.py` and `live_llm.py`).

1. **Larger N:** `python -m benchmarks.heldout.run --seed-from 200 --seed-to 300 --out data/reports/heldout_200_299.json`. About 1,800 positive or mixed datasets and 1,000 negatives.
2. **Intervals:** Wilson 95% intervals per case and per cause. 0/1,000 false alarms gives an upper bound of 0.3%. Today's 0/300 only supports 1.0%.
3. **Time-tolerant hit:** the incident window must overlap `[t_inc, end + 15 min]`. `make_case` already returns `t_inc`. This follows range-based precision and recall ([Tatbul et al. 2018, arXiv 1803.03639](https://arxiv.org/abs/1803.03639)).
4. **Detection delay:** first flagged window end minus `t_inc`. Report the median and p95 per cause, and the dollars spent before detection.
5. **Baselines** (`benchmarks/baselines.py`):
   - a cost z-score > 3 alert;
   - three rules: retry count ≥ 4, model changed, chain depth ≥ 4.
6. **Ablation:** deterministic vs LLM with guard vs LLM without guard, on the same 112 datasets. This needs an env flag for the disagreement guard in `agents._apply_skill_pack_to_evidence`. Use a paired McNemar test.

**Deliverable:** `docs/proof/results.md` with a results table and a delay histogram.

**Pass:**
- Hit lower bound ≥ 97%.
- False-alarm upper bound ≤ 1%.
- Median delay ≤ 10 min and p95 ≤ 20 min.
- Beats both baselines on negatives by ≥ 20 points.

**Falsified if:**
- The 3-rule baseline is within 2 points. Then the skills add nothing.
- The time-tolerant hit rate drops more than 3 points. Then earlier hits were luck.

### Tier B: real traffic shape (5 h, $0.25)

**Data:**
- [BurstGPT](https://github.com/HPMLL/BurstGPT): 5.29M real Azure OpenAI requests over 121 days, CC-BY-4.0.
- [Azure LLM inference trace 2023](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2023.md): a second background.

**Steps:**
1. `benchmarks/real/burstgpt_adapter.py`: feature_tag = Log Type + Model, latency = Elapsed time, cost from the price table. Keep failed rows.
2. Run the raw trace with no injection. Hand-review every alarm and publish them in `docs/proof/burstgpt_raw_alarms.md`.
3. `benchmarks/real/inject.py`: reuse the SPEC parameter ranges for the three incident types.
4. Draw start times and features from a seed, over 200 random 3-day slices. Leave 100 slices clean.

**Honesty rules:**
- Commit the injector before the first run.
- Do not tune the detector on these slices.
- If anything is fixed, re-run on new slices and report both runs.

**Pass:**
- Hit rate ≥ 90%.
- ≤ 1 false incident per feature-week on clean slices.

**Falsified if:** more than 1 false incident per feature-day on raw real traffic. Then the baseline model fits only the generator.

**Limit to state:** the incidents are still synthetic, and BurstGPT has no parent IDs or retry field.

### Tier C: red team (3 h, $0)

`benchmarks/redteam/cases.py`, 12 cases × 20 seeds:
- slow misroute ramp from 10% to 100% over 6 h;
- incident in the first hour;
- retries as new call IDs with no `retry_count`;
- bloat with no parent IDs;
- a model missing from the price table;
- three causes at once;
- duplicate and out-of-order events;
- new feature launch (negative);
- planned upgrade to a pricier model (negative, and not separable from a misroute);
- cache-miss price rise;
- 10x surge plus a small retry storm;
- a 1-call-per-hour feature.

**Deliverable:** `docs/proof/failure_analysis.md`, one row per case with the hit rate and the cause of each miss.

**Pass:** no crash, and a confident wrong-cause rate ≤ 10%. Documented misses are acceptable.

**Falsified if:** confident wrong labels exceed 10%. A wrong label is worse than a miss.

### Tier D: live end to end (5 h, $0.70)

`examples/live_app/`: a 3-feature app with a 30-line wrapper that posts provider `usage` to `/v1/ingest`.

1. Run 75 min of real baseline traffic at about 6 calls/min.
2. Flip one real bug per flag:
   - a JSON-validation retry loop with no cap (hard stop at 15);
   - a config change to a pricier model;
   - a reflection agent that appends its full history (depth 8).
3. Record the SSE UI and the terminal.

Do one rehearsal and one recorded take, about 800 calls each.

**Pass:** all 3 bugs labelled correctly within 2 windows, with 0 alarms in the baseline hour.

**Falsified if:** any miss or any baseline alarm. Publish the take anyway.

### Tier E: independent check (3 h, $0)

1. Export 40 incident slices from Tiers A to C (12 negatives) as CSV and chart only, with no tool output.
2. One engineer friend and one other model (free gpt-oss-20b) label them blind.
3. The friend writes 5 new incident scripts from a one-paragraph brief. Run them unseen.

**Deliverable:** a confusion matrix and Cohen's kappa.

**Pass:** human vs truth ≥ 85%, and tool vs human ≥ 85%.

**Falsified if:** the human disagrees with the generator labels. Then the ground truth is the problem.

## 3. Proof pack (`docs/proof/`)

- **`results.md`:** one page. Rows are Tiers A to E. Columns are N, metric, 95% CI, threshold, pass/fail, and command.
- **Make targets:** `make proof-synth`, `make proof-real`, `make proof-redteam`, `make proof-live`, and `make proof` (all free tiers).
- **Limitations:** no customer data, injected incidents only, one author, 60 min cold start, estimated cost, three causes only.
- **2-minute video:**
  - 0:00 App runs, flat spend.
  - 0:20 Flip the retry bug; show the timer until the incident card.
  - 0:50 Open the evidence: skills, numbers, dollars burned.
  - 1:10 Misroute, then bloat.
  - 1:40 Results table.
  - 1:50 Limitations.

**What a senior engineer asks first, and where the pack answers:**

| Question | Answer in the pack |
|---|---|
| "You graded your own homework." | Pre-registration hash, BurstGPT background, friend-written cases, blind labels |
| "Is this not just a threshold?" | Baseline table |
| "Why an LLM?" | Ablation, stated plainly: it adds narrative, not accuracy |
| "What breaks it?" | `failure_analysis.md` |

## 4. Order and budget

| # | Work | Time | Spend |
|---|---|---|---|
| 1 | Pre-registration, then Tier A items 1-5 | 3 h | $0 |
| 2 | Tier D rehearsal (in the background during 3) | 1.5 h | $0.30 |
| 3 | Tier B | 5 h | $0.25 |
| 4 | Tier A ablation | 1 h | $0.40 |
| 5 | Tier C | 3 h | $0 |
| 6 | Tier D recorded take | 2 h | $0.40 |
| 7 | Tier E (send slices to the friend at step 3) | 1 h | $0 |
| 8 | Write the pack | 2 h | $0 |

Total: about $1.35 of live spend, $1.15 in reserve. If time runs out, cut
Tier C to 6 cases. Do not cut Tier B or the baselines.
