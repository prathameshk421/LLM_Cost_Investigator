# Held-out telemetry spec (written BEFORE the rule files were read)

Source of truth: how a real LLM app behaves. No threshold of the pipeline is used here.

## World
- Horizon: 3 days of history plus the incident day. Start on a random weekday and random hour.
- 5 features with different volumes (peak calls/min):
  chat_assistant 6, rag_search 3 (pipeline), doc_summarizer 1.2, code_review 0.4, nightly_digest 0.15 (sparse).
- Arrivals: inhomogeneous Poisson. Rate = base * diurnal(hour) * weekly(dow) * slow lognormal burst factor.
  Diurnal: peak near 14:00 UTC, night trough about 15% of peak. Weekend 55%.
- Prompt size: lognormal per feature (sigma 0.5 to 0.9). Output: lognormal, smaller.
- Models: each feature has a baseline mix (A/B). Example: chat 85% cheap, 15% mid.
- Latency: 300 ms + per-output-token time, lognormal noise.
- Legit retries: about 2% of calls have retry_count 1 or 2.
- Legit chains: rag_search is a 2 to 3 step chain (rewrite -> retrieve-answer -> optional cite). Depth 2 to 3. Normal.
- Cost: pricing.estimate_cost_usd(model, in, out). Retried calls bill tokens once per attempt.

## Positive cases (ground truth = hypothesis + feature)
- retry storm: N parents (3 to 12), each with 4 to 15 retries. Each attempt is a child call row with
  parent_call_id = parent and retry_count = attempt index. Magnitudes: small/medium/large. Day and night.
- context bloat: self-calling chain, depth 4 to 10, prompt grows 1.3x to 1.8x per step. Several chains
  (2 to 15). Subtle = depth 4, few chains. Day and night.
- misroute: the feature switches to a pricier model for 30 to 120 min. Ratios: ~2x (subtle), ~5x, >=10x.
  Partial (50% of traffic) and full. Day and night.

## Hard negatives (ground truth = none)
- surge 2x to 6x on all features (same shape, more calls).
- marketing burst: one feature 4x to 8x for 20 to 40 min.
- cheaper model: feature moves to a cheaper model.
- A/B ratio shift: baseline already used both models (e.g. 85/15); ratio moves to 50/50.
  (Debatable. Scored as negative, reported apart.)
- product change: prompts +40% from the start of the last day, no chains.
- normal RAG chains only (plain baseline).
- sparse feature with random large prompts (heavy tail).
- clock gap: 20 min with no data, then normal traffic (with and without a catch-up).

## Mixed
- two causes on two features (retry storm + misroute; bloat + misroute).
- misroute + 3x surge on the same feature.

## Score
- positive: hit if an incident has the right hypothesis on the right feature (any rank).
  Also record wrong-hypothesis alarms on any feature.
- negative: false alarm if any incident has a hypothesis other than no_strong_signal.
- >= 30 seeds per case. Seeds split even = dev, odd = test for any fix.
