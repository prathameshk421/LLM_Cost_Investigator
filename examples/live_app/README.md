# Live app for Tier D

A small app with three features. It sends every LLM call to the investigator
through `llmci_wrapper.py`. After a clean baseline, `--bug` turns on a real fault.

| Feature | Baseline | Bug |
|---|---|---|
| `summarize` | `openai/gpt-oss-20b` on Hugging Face | `misroute`: `openai/gpt-oss-120b` on Hugging Face (about 2.5 times the price) |
| `classify` | `deepseek-ai/deepseek-v4.1-flash-1017` on Deploy | `retry_loop`: strict JSON, validator always fails, up to 15 attempts |
| `reflect` | one critique call on Deploy | `bloat`: full history re-sent, self-call chain of depth 8 |

Retry attempt n has `retry_count=n` and `parent_call_id` set to the first attempt.
Bloat calls chain through `parent_call_id`. Logs go to stdout, one line per call.

## Setup

- Keys: `DEPLOY_API_KEY` (classify, reflect) and `HF_TOKEN` or a cached `hf auth login` (summarize).
- Start the API and UI: `PYTHONPATH=. .venv/bin/uvicorn api.app:app --app-dir web --port 8000`.
  Set `LLMCI_API_TOKEN` in both shells if you use a token.
- The investigator prices events itself, so the app sends no `cost_usd`.

## Rehearsal (no LLM, no cost)

```
PYTHONPATH=. .venv/bin/python examples/live_app/app.py --fake --time-scale 60 \
  --bug retry_loop,misroute,bloat --minutes 20 --gap-minutes 10
```

`--time-scale 60` runs 165 simulated minutes in about 165 s. Event timestamps follow
the simulated clock and end at the current time. This checks the plumbing only.
Fake token counts are not evidence for the proof.

## Recorded take

```
PYTHONPATH=. .venv/bin/python examples/live_app/app.py \
  --bug retry_loop,misroute,bloat --minutes 20 --gap-minutes 10 | tee tierd_take.log
```

Run time: 75 min baseline, then 20 min per bug with 10 min clean gaps, about 2 h 45 min.
Expect about 1,800 calls. Cost is about $0.5 (DeepSeek about $0.0003 per call).
To run one bug alone, pass one name, for example `--bug misroute`.

Film two panes: the terminal log, and the UI Live tab (events per second, spend
chart, incident feed). Mark the start of each bug window on camera. Publish the take
whatever the result.

## Pass rule

Each bug is labelled correctly within 2 windows, and there are 0 alarms during
the baseline. See `benchmarks/PREREGISTRATION.md`.

## Caveats

- **Cold start.** The detector needs history. `--baseline-minutes` must be at least 60
  and the default is 75. Do not start a bug earlier.
- **Misroute needs two prices.** The Deploy gateway serves only DeepSeek and
  `zai-org/glm-5.3-flash`, with no listed prices. So the baseline `summarize` runs on
  Hugging Face. Change the targets with `--summarize-provider/--summarize-model` and
  `--pricy-provider/--pricy-model`. A real misroute needs two models with different prices.
- **Reasoning tokens.** `gpt-oss` models spend output tokens on reasoning. Output
  tokens can differ between the two sizes, so read the price ratio from the report.
- **Free HF plan.** Check the credit limit before a 2 h 45 min run.
- **Retry loop size.** One failing classify makes 15 calls. This adds about 840 calls
  over a 30 min bug window. Use `--rate` to scale volume.
- **Duplicate runs.** Each run sends new call ids. A second take on the same
  warehouse mixes with the first. Use a fresh `LLMCI_WAREHOUSE`.
