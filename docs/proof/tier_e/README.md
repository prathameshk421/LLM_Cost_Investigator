# Tier E: label these slices blind

Each CSV in `slices/` is 4 hours of LLM API telemetry from one app with 5
features, as 5-minute aggregates per feature. Some slices contain an incident,
some do not. Do **not** open `answer_key.json`.

For each slice, write in `human_answer_sheet.csv` every incident you see as
(cause, feature), one row per finding (add rows for a second finding), or
`none`. Causes:

- `uncapped_retry_loop`: a feature retries failing calls many times (retry counts well above 2).
- `context_bloat_self_calling_agent`: a feature calls itself in a chain and resends a growing history (parent chain deeper than normal, prompts growing).
- `expensive_model_misroute`: a feature's traffic moves to a pricier model it did not use before (a cheaper model is not this).
- `none`: normal traffic, more traffic, A/B shifts, cheaper models, longer prompts without a chain.
