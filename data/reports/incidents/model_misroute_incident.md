# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.95
Winning agent: model_routing_agent

Summary:
Hard skills indicate expensive model misroute with stable tokens. Pattern: MR-01. (Fallback)

Supporting evidence:
- **model_routing_agent** — provider: fallback, model: n/a, fallback: True (fallback provider explicitly selected)
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.95
  - Explanation: Hard skills indicate expensive model misroute with stable tokens. Pattern: MR-01. (Fallback)
  - Supporting metrics:
    - model_changed: True
    - model_before: gpt-4o-mini
    - model_during: gpt-4.1
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - cost_z_score: 1882.0355656496642
    - hard_skills: {'model_changed_flag': {'verdict': 'pass', 'value': True, 'detail': '', 'falsifier': True}, 'cost_growth_threshold': {'verdict': 'pass', 'value': 609.35960591133, 'detail': '', 'falsifier': False}, 'token_stability_check': {'verdict': 'pass', 'value': 24.559471365638768, 'detail': 'tokens stable', 'falsifier': False}, 'cost_zscore_check': {'verdict': 'pass', 'value': 1882.0355656496642, 'detail': '', 'falsifier': False}, 'price_tier_consistency': {'verdict': 'pass', 'value': {'before_price': 0.75, 'after_price': 10.0}, 'detail': 'after model is pricier', 'falsifier': True}, 'sample_call_confirmation': {'verdict': 'pass', 'value': 1.0, 'detail': 'samples show during model', 'falsifier': False}, 'routing_scope_check': {'verdict': 'pass', 'value': ['gpt-4.1'], 'detail': 'models in samples', 'falsifier': False}}
    - skill_band_ceiling: 1.0
    - forced_no_strong_signal: False
    - kb_matches: [{'pattern_id': 'MR-01', 'story': 'Traffic shifted to a pricier model with stable tokens.'}]
- **token_context_agent** — provider: fallback, model: n/a, fallback: True (hard skill falsifier failed)
  - Hypothesis: no_strong_signal
  - Confidence: 0.28
  - Explanation: Hard skills: weak or falsified token-context evidence. (Fallback)
  - Supporting metrics:
    - input_token_growth_pct: 24.559471365638768
    - max_call_chain_depth: 0
    - input_tokens_z_score: 155.8259871820166
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - hard_skills: {'token_growth_threshold': {'verdict': 'fail', 'value': 24.559471365638768, 'detail': '', 'falsifier': False}, 'chain_depth_floor': {'verdict': 'fail', 'value': 0, 'detail': '', 'falsifier': False}, 'chain_depth_recency': {'verdict': 'soft_pass', 'value': None, 'detail': 'insufficient chain timing evidence', 'falsifier': True}, 'token_zscore_check': {'verdict': 'pass', 'value': 155.8259871820166, 'detail': '', 'falsifier': False}, 'prompt_content_growth_correlation': {'verdict': 'fail', 'value': {'cost_growth_pct': 609.35960591133, 'token_growth_pct': 24.559471365638768}, 'detail': 'cost growth far exceeds token growth', 'falsifier': True}, 'self_call_pattern_check': {'verdict': 'fail', 'value': 0, 'detail': 'no recursive signature', 'falsifier': False}, 'sample_consistency_check': {'verdict': 'pass', 'value': (930, 955), 'detail': 'samples show token variation', 'falsifier': False}}
    - skill_band_ceiling: 0.49
    - forced_no_strong_signal: True
    - kb_matches: []

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
