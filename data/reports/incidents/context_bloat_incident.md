# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.84
Winning agent: token_context_agent

Summary:
Hard skills indicate context bloat / recursive self-calling growth. Pattern: TC-02. (Fallback)

Supporting evidence:
- **token_context_agent** — provider: fallback, model: n/a, fallback: True (fallback provider explicitly selected)
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.84
  - Explanation: Hard skills indicate context bloat / recursive self-calling growth. Pattern: TC-02. (Fallback)
  - Supporting metrics:
    - input_token_growth_pct: 294.0677966101695
    - avg_input_token_growth_pct: 228.38983050847457
    - max_call_chain_depth: 5
    - input_tokens_z_score: 1454.8417278608008
    - cost_growth_pct: 313.79310344827593
    - token_growth_pct: 294.0677966101695
    - hard_skills: {'token_growth_threshold': {'verdict': 'soft_pass', 'value': 294.0677966101695, 'detail': '', 'falsifier': False}, 'chain_depth_floor': {'verdict': 'pass', 'value': 5, 'detail': '', 'falsifier': False}, 'chain_depth_recency': {'verdict': 'pass', 'value': 1.0, 'detail': 'deep chain samples in window', 'falsifier': True}, 'token_zscore_check': {'verdict': 'pass', 'value': 1454.8417278608008, 'detail': '', 'falsifier': False}, 'prompt_content_growth_correlation': {'verdict': 'pass', 'value': {'cost_growth_pct': 313.79310344827593, 'token_growth_pct': 294.0677966101695}, 'detail': 'cost tracks tokens', 'falsifier': True}, 'self_call_pattern_check': {'verdict': 'pass', 'value': 5, 'detail': 'parent chain present', 'falsifier': False}, 'sample_consistency_check': {'verdict': 'pass', 'value': (700, 3950), 'detail': 'samples show token variation', 'falsifier': False}, 'per_call_growth_check': {'verdict': 'pass', 'value': 228.38983050847457, 'detail': 'prompts per call grew', 'falsifier': True}}
    - skill_band_ceiling: 0.89
    - forced_no_strong_signal: False
    - kb_matches: [{'pattern_id': 'TC-02', 'story': 'Gradual prompt/context creep with elevated token z-score.'}]

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
