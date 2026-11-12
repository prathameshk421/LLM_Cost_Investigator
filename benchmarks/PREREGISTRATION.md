# Pre-registration: proof tiers A to E

Committed **before** any run of these tiers. The git commit that adds this
file is the timestamp. Thresholds, metrics and seed ranges below must not be
changed after results are seen. Any later change gets a new section with its
own date and commit, and both the old and the new results are published.

Plan: [docs/proof/PLAN.md](../docs/proof/PLAN.md).

## Code under test

The pipeline at the commit that adds this file. If a rule changes later
(for example the known night-time new-model weakness below), the change is
tuned on **dev seeds 300-399** only and then reported on **fresh seeds
400-499**, next to the original result. No rule is ever tuned on a seed range
that is reported as a result.

Known weakness found before this registration (on reported seeds 124-127, so
not fixed there): in sparse night traffic, "new model" is decided from the
cost per token of 1-3 calls. This gave one missed 2x misroute and one false
misroute on an A/B minority model.

## Seed ranges

| Range | Use |
|---|---|
| 0-29 | earlier dev and test halves (used for tuning) |
| 30-127 | earlier reported runs |
| **200-299** | **Tier A, reported once** |
| 300-399 | dev seeds for any future fix |
| 400-499 | fresh seeds to report a future fix |

## Tier A: synthetic held-out (generator `benchmarks/heldout/gen.py`)

Data: 28 cases × seeds 200-299 = 2,800 datasets (1,800 positive or mixed,
1,000 negative). Deterministic agents.

Metrics:
- **Hit (time-tolerant):** every truth (cause, feature) is reported by an
  incident whose window starts at or after `t_inc - 5 min`. A correct label
  on a window that starts before the incident does not count.
- **False alarm (negatives):** any non-`no_strong_signal` incident on a
  negative dataset.
- **Wrong extra report (positives):** a reported (cause, feature) that is not
  in the truth.
- **Detection delay:** end of the first correctly labelled window minus
  `t_inc`, per cause. Report the median and p95.
- **Dollars before detection:** the incident feature's spend between `t_inc`
  and the end of that first window.
- **Intervals:** Wilson 95% intervals for every rate.

Pass, all required:
1. Hit-rate Wilson lower bound ≥ 97%.
2. False-alarm Wilson upper bound ≤ 1%.
3. Median delay ≤ 10 min and p95 ≤ 20 min, for each cause.
4. On negatives, the false-alarm rate is at least 20 points below each baseline.

Falsified if:
- the 3-rule baseline hit rate is within 2 points of the product with a false-alarm rate no worse by more than 2 points; or
- the time-tolerant hit rate is more than 3 points below the any-time hit rate.

### Baselines (`benchmarks/baselines.py`)

- **B1 cost alert:** per feature, a 5-minute window is an incident when total
  cost z ≥ 3 against the trailing 72 windows (warm-up 12). The label is the
  most common cause in the training mix, `expensive_model_misroute`.
- **B2 three rules:** on the same windows, label `uncapped_retry_loop` if max
  `retry_count` ≥ 4; else `expensive_model_misroute` if a model not seen in
  the feature's earlier windows appears; else `context_bloat_self_calling_agent`
  if chain depth ≥ 4; else no incident.

### Ablation (live LLM, DeepSeek V4.1 Flash via Deploy)

Same 112 datasets (28 cases × seeds 200-203) for three arms:
deterministic, LLM with the disagreement guard, LLM without it
(`LLMCI_LLM_GUARD=0`). Paired McNemar test on exact match.

Claim C5 passes if: LLM-with-guard exact match is not significantly lower
than deterministic (McNemar p ≥ 0.05) and the cost is < $0.002 per dataset.
A provider outage run is reported, not discarded.

## Tier B: real traffic background (BurstGPT)

Pass: hit rate ≥ 90% on 200 injected slices, and ≤ 1 false incident per
feature-week on 100 clean slices. Falsified if raw real traffic gives more
than 1 false incident per feature-day. The injector is committed before its
first run. The detector is not tuned on these slices.

## Tier C: red team

Pass: no crash, and a confident wrong-cause rate ≤ 10% (confident = root
cause confidence ≥ 0.60). Misses are allowed and are documented.

## Tier D: live end to end

Pass: three real induced bugs (uncapped retry loop, switch to a pricier
model, self-calling agent) are each labelled correctly within 2 windows, with
0 alarms during the 60-minute baseline. The recorded take is published
whatever the result.

## Tier E: independent check

Pass: human labels vs truth ≥ 85% agreement, and tool vs human ≥ 85%.

## Addendum 1 (2026-11-12, before any reported Tier B run)

- **Clean-slice cap.** `investigate_calls(max_anomalies=5)` caps how many
  incidents one slice can report. That would understate false incidents on
  clean slices. The reported Tier B clean run uses
  `TIERB_CLEAN_MAX_ANOMALIES=50`, and the output records how many slices still
  hit the cap.
- **Disclosure.** While building Tier B, 4 clean slices from the reserved
  fresh range (Tier B slice seeds 400-403) were run by mistake. They gave 0
  false incidents. Any future fix reported on seeds 400-499 will exclude
  400-403 and say so.
- **Trace mapping.** BurstGPT names only "ChatGPT" and "GPT-4". They are
  priced as `gpt-3.5-turbo` and `gpt-4-turbo`. The trace has no parent id,
  retry or latency fields, so those only exist on injected rows.

## Addendum 2 (2026-11-12, after the first Tier B run, before the second)

The first reported Tier B run used 100 injected slices (seeds 200-299), not
the registered 200. To meet the registration, 100 more injected slices are run
on the unused seed range 600-699 with the same code, and both runs are
reported together. The first run's result (98/100 hit, 0 false incidents on
100 clean slices) is published as is.

## Addendum 3 (2026-11-12, before the reported Tier C run)

Building Tier C on dev seeds 300-319 found a crash: duplicate `call_id`s
made `investigate_calls` fail with `sqlite3.IntegrityError`. It is fixed
before the reported Tier C run (`TelemetryStore.insert_calls` now keeps the
first copy, like the warehouse). This is a crash fix, not a threshold or
detection-rule change. The code under test for Tier C is the commit that adds
this addendum. Second Tier B batch (seeds 600-699): 95/100 hit; combined
193/200.

## Addendum 4 (2026-11-12): Tier E human labelling dropped

The owner decided not to run the human half of Tier E (blind human labels
and friend-written incident scripts). Tier E is reported with the blind
model labeller only (gpt-oss-20b, a different model family from the
DeepSeek agents). The proof pack therefore makes **no claim of independent
human verification**. The blind slices and answer sheet stay in
`docs/proof/tier_e/` so anyone can do it later.

## Addendum 5 (2026-11-12): second blind labeller added after the first failed

The registered model labeller (gpt-oss-20b) could not do the task: 10/40
agreement with truth (12/40 answers truncated even at 16k tokens; 6/28 on
finished answers). At the owner's request a second blind labeller was added
**after** that result: Fable (a much stronger model), reading only the brief
and the 40 slice CSVs. Result: 40/40 vs truth and 40/40 vs the tool.
Caveats: Fable wrote the held-out generator earlier in the project, so it is
independent of the detection rules but not of the incident design; and the
labeller was chosen after the first one failed. Both runs are published.
