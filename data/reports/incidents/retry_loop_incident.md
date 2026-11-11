# Incident Report: retry_loop

Root cause: uncapped_retry_loop
Affected feature: support_reply
Confidence: 0.82
Winning agent: retry_loop_agent

Summary:
Hard skills indicate uncapped retry loop / repeated failed calls. Pattern: RL-02. (Fallback)

Supporting evidence:
- **retry_loop_agent** — provider: fallback, model: n/a, fallback: True (fallback provider explicitly selected)
  - Hypothesis: uncapped_retry_loop
  - Confidence: 0.82
  - Explanation: Hard skills indicate uncapped retry loop / repeated failed calls. Pattern: RL-02. (Fallback)
  - Supporting metrics:
    - retry_z_score: 3.5
    - max_retry_count: 7
    - avg_retry_count: 3.5
    - repeated_parent_call_count: 1
    - cost_attribution_pct: 97.22222222222223
    - hard_skills: {'retry_zscore_threshold': {'verdict': 'soft_pass', 'value': 3.5, 'detail': 'retry_z in [3,5)', 'falsifier': False}, 'max_retry_floor': {'verdict': 'pass', 'value': 7, 'detail': 'max_retry >= 5', 'falsifier': False}, 'repeated_parent_call_check': {'verdict': 'soft_pass', 'value': 1, 'detail': '', 'falsifier': False}, 'latency_growth_confirmation': {'verdict': 'pass', 'value': 464.3228837785705, 'detail': 'sample latency vs retry_count', 'falsifier': False}, 'sample_call_consistency': {'verdict': 'pass', 'value': 0.875, 'detail': 'samples consistent with retries', 'falsifier': True}, 'cost_attribution_check': {'verdict': 'pass', 'value': 97.22222222222223, 'detail': 'retried_cost=0.105000 total=0.108000', 'falsifier': True}, 'non_retry_alternative_scan': {'verdict': 'pass', 'value': 'none', 'detail': 'no strong alternative', 'falsifier': False}}
    - skill_band_ceiling: 0.89
    - forced_no_strong_signal: False
    - kb_matches: [{'pattern_id': 'RL-02', 'story': 'Elevated retries with moderate parent fan-out.'}]

Recommendations:
- Implement exponential backoff with jitter.
- Cap the maximum retry count in the client configuration.
- Add a circuit breaker pattern to prevent continuous retries during outages.
