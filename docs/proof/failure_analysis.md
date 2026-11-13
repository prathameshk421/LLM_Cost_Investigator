# What breaks it

Red team (Tier C): 12 adversarial cases × 20 seeds (200-219), deterministic agents.
Raw table: [failure_analysis_draft.md](failure_analysis_draft.md), data: [data/tier_c.json](data/tier_c.json).
Result: 0 crashes and 0% confident wrong labels [0-2%], so Tier C passes. The misses below are documented, not hidden.

| Case | Result | Why | What would fix it |
|---|---|---|---|
| c02 incident in the first hour of a feature | 0/20 caught | No verdict before 12 windows (60 min) of history. The incident is learned as normal. | A cold-start rule against a global per-call cost prior. |
| c03 retries sent as new call ids with no `retry_count` and no parent | 0/20 caught | Without the fields, a retry storm looks like more traffic, and traffic alone is not an incident by design. | Ask SDKs to send `retry_count` / parent ids (the example wrapper does), or detect near-identical bursts. |
| c04 context bloat with no parent ids | 0/20 caught (all flagged, then labelled no_strong_signal) | Self-calling bloat needs a call chain deeper than history. Bigger prompts alone count as a product change. | Send `parent_call_id`. |
| c09 planned upgrade to a pricier model | 20/20 reported as a misroute | Telemetry cannot tell a planned change from a mistake. Scored as ambiguous. | A "planned change" annotation (deploy marker) that suppresses the label. |
| c01 slow misroute ramp over 6 h | 19/20 | One ramp stayed under the thresholds. | - |
| c11 10x surge plus a small retry storm | 18/20 | The surge dilutes the storm's share of cost. | - |
| c05 unknown model, c06 three causes at once, c07 duplicates and shuffled events, c08 new feature, c10 cache-miss price rise, c12 one call per hour | all correct | - | - |

Found and fixed during Tier C development (dev seeds only, before the reported run):
duplicate `call_id`s crashed the investigation with `sqlite3.IntegrityError`.
The first copy is now kept. See [PREREGISTRATION.md](../../benchmarks/PREREGISTRATION.md) addendum 3.

Other known weaknesses from Tier A and the live runs:
- Subtle depth-4 self-calling chains: 10% missed.
- Sparse night traffic: "new model" is judged from 1-3 calls, which gave 2-4 wrong extra
  labels per 100 night cases and a few missed 2x misroutes. Fix proposed: compare the new
  model's list price with the feature's known models. It must be tuned on dev seeds 300-399
  and reported on fresh seeds, per the pre-registration.
- Detection delay: 11 to 22 minutes median, above the 10-minute target.
