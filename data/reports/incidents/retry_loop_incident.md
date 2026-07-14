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
    - hard_skills: {'retry_zscore_threshold': {'verdict': 'soft_pass', 'value': 3.5, 'detail': 'retry_z in [3,5)', 'falsifier': False}, 'max_retry_floor': {'verdict': 'pass', 'value': 7, 'detail': 'max_retry >= 5', 'falsifier': False}, 'repeated_parent_call_check': {'verdict': 'soft_pass', 'value': 1, 'detail': '', 'falsifier': False}, 'latency_growth_confirmation': {'verdict': 'pass', 'value': 457.21072421965715, 'detail': 'sample latency vs retry_count', 'falsifier': False}, 'sample_call_consistency': {'verdict': 'pass', 'value': 0.875, 'detail': 'samples consistent with retries', 'falsifier': True}, 'cost_attribution_check': {'verdict': 'pass', 'value': 97.22222222222223, 'detail': 'retried_cost=0.105000 total=0.108000', 'falsifier': True}, 'non_retry_alternative_scan': {'verdict': 'pass', 'value': 'none', 'detail': 'no strong alternative', 'falsifier': False}}
    - skill_band_ceiling: 0.89
    - forced_no_strong_signal: False
    - kb_matches: [{'pattern_id': 'RL-02', 'story': 'Elevated retries with moderate parent fan-out.'}]
- **token_context_agent** — provider: fallback, model: n/a, fallback: True (hard skill falsifier failed)
  - Hypothesis: no_strong_signal
  - Confidence: 0.28
  - Explanation: Hard skills: weak or falsified token-context evidence. (Fallback)
  - Supporting metrics:
    - input_token_growth_pct: 62.166064981949454
    - max_call_chain_depth: 1
    - input_tokens_z_score: 481.3136321568297
    - cost_growth_pct: 609.3596059113302
    - token_growth_pct: 62.166064981949454
    - hard_skills: {'token_growth_threshold': {'verdict': 'fail', 'value': 62.166064981949454, 'detail': '', 'falsifier': False}, 'chain_depth_floor': {'verdict': 'fail', 'value': 1, 'detail': '', 'falsifier': False}, 'chain_depth_recency': {'verdict': 'soft_pass', 'value': None, 'detail': 'insufficient chain timing evidence', 'falsifier': True}, 'token_zscore_check': {'verdict': 'pass', 'value': 481.3136321568297, 'detail': '', 'falsifier': False}, 'prompt_content_growth_correlation': {'verdict': 'fail', 'value': {'cost_growth_pct': 609.3596059113302, 'token_growth_pct': 62.166064981949454}, 'detail': 'cost growth far exceeds token growth', 'falsifier': True}, 'self_call_pattern_check': {'verdict': 'soft_pass', 'value': 8, 'detail': 'weak chain signature', 'falsifier': False}, 'sample_consistency_check': {'verdict': 'pass', 'value': (1120, 1126), 'detail': 'samples show token variation', 'falsifier': False}}
    - skill_band_ceiling: 0.49
    - forced_no_strong_signal: True
    - kb_matches: []

Recommendations:
- Implement exponential backoff with jitter.
- Cap the maximum retry count in the client configuration.
- Add a circuit breaker pattern to prevent continuous retries during outages.
