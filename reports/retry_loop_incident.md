# Incident Report: retry_loop

Root cause: uncapped_retry_loop
Affected feature: support_reply
Confidence: 0.81
Winning agent: retry_loop_agent

Summary:
The retry Z-score of 3.5 exceeds the threshold for concern, and the maximum retry count of 7 indicates multiple attempts per request. Latency and cost Z-scores are extremely high, with cost growth over 600%, consistent with repeated retries inflating both latency and expense. These combined signals strongly point to an uncapped retry loop.

Supporting evidence:
- **retry_loop_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: uncapped_retry_loop
  - Confidence: 0.81
  - Explanation: The retry Z-score of 3.5 exceeds the threshold for concern, and the maximum retry count of 7 indicates multiple attempts per request. Latency and cost Z-scores are extremely high, with cost growth over 600%, consistent with repeated retries inflating both latency and expense. These combined signals strongly point to an uncapped retry loop.
  - Supporting metrics:
    - retry_z_score: 3.5
    - max_retry_count: 7
    - avg_retry_count: 3.5
    - repeated_parent_call_count: 1
    - latency_z_score: 457.21072421965715
    - cost_z_score: 1882.0355656496997
    - cost_growth_pct: 609.3596059113302
- **token_context_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: no_strong_signal
  - Confidence: 0.42
  - Explanation: While input token z-score and cost growth are extremely high, the call chain depth never exceeds 1 and all calls share the same parent, indicating no recursive self‑calling or deep context accumulation. The dominant factor appears to be a series of retries, not context bloat. Therefore there is no strong signal of context bloat or self‑calling behavior.
  - Supporting metrics:
    - input_tokens_z_score: 481.3136321568297
    - input_token_growth_pct: 62.166064981949454
    - cost_growth_pct: 609.3596059113302
    - max_call_chain_depth: 1
    - retry_z_score: 3.5
    - model_changed: False

Recommendations:
- Implement exponential backoff with jitter.
- Cap the maximum retry count in the client configuration.
- Add a circuit breaker pattern to prevent continuous retries during outages.
