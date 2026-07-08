# Incident Report: retry_loop

Root cause: uncapped_retry_loop
Affected feature: support_reply
Confidence: 0.96
Winning agent: retry_loop_agent

Summary:
The telemetry shows a very high retry_z_score (10) and a max_retry_count of 7, both well above the thresholds for an uncapped retry loop. Latency and cost z-scores are extreme, and cost grew >600%, consistent with repeated attempts. The sample calls demonstrate a monotonic increase in latency and cost with each retry, confirming the loop behavior.

Supporting evidence:
- **retry_loop_agent** — provider: cerebras, model: gpt-oss-120b, fallback: False
  - Hypothesis: uncapped_retry_loop
  - Confidence: 0.96
  - Explanation: The telemetry shows a very high retry_z_score (10) and a max_retry_count of 7, both well above the thresholds for an uncapped retry loop. Latency and cost z-scores are extreme, and cost grew >600%, consistent with repeated attempts. The sample calls demonstrate a monotonic increase in latency and cost with each retry, confirming the loop behavior.
  - Supporting metrics:
    - retry_z_score: 10.0
    - max_retry_count: 7
    - avg_retry_count: 3.5
    - repeated_parent_call_count: 1
    - latency_z_score: 457.21072421965715
    - cost_z_score: 1882.0355656496997
    - cost_growth_pct: 609.3596059113302
- **token_context_agent** — provider: cerebras, model: gpt-oss-120b, fallback: False
  - Hypothesis: no_strong_signal
  - Confidence: 0.45
  - Explanation: The token growth is moderate (≈62%) while cost growth is very high, but the call chain depth is only 1, indicating no deep recursion. The high retry_z_score shows many retries, which explains the cost increase. There is no evidence of context bloat or self‑calling behavior, so the signal is weak.
  - Supporting metrics:
    - input_tokens_z_score: 481.3136321568297
    - input_token_growth_pct: 62.166064981949454
    - cost_growth_pct: 609.3596059113302
    - max_call_chain_depth: 1
    - retry_z_score: 10.0
    - model_changed: False

Recommendations:
- Implement exponential backoff with jitter.
- Cap the maximum retry count in the client configuration.
- Add a circuit breaker pattern to prevent continuous retries during outages.
