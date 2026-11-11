# Incident Report: canonical_sample_summarizer_0

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
    - token_growth_pct: 3.7995594713656384
    - cost_z_score: 1882.0355656496642
    - unit_cost_growth_pct: 469.21002632038295
    - hard_skills: {'model_changed_flag': {'verdict': 'pass', 'value': True, 'detail': '', 'falsifier': True}, 'cost_growth_threshold': {'verdict': 'pass', 'value': 609.35960591133, 'detail': '', 'falsifier': False}, 'token_stability_check': {'verdict': 'pass', 'value': 3.7995594713656384, 'detail': 'tokens stable', 'falsifier': False}, 'cost_zscore_check': {'verdict': 'pass', 'value': 1882.0355656496642, 'detail': '', 'falsifier': False}, 'price_tier_consistency': {'verdict': 'pass', 'value': {'before_price': 0.75, 'after_price': 10.0}, 'detail': 'after model is pricier', 'falsifier': True}, 'sample_call_confirmation': {'verdict': 'pass', 'value': 1.0, 'detail': 'samples show during model', 'falsifier': False}, 'routing_scope_check': {'verdict': 'pass', 'value': ['gpt-4.1'], 'detail': 'models in samples', 'falsifier': False}, 'unit_price_jump': {'verdict': 'pass', 'value': 469.21002632038295, 'detail': 'price per token at least doubled', 'falsifier': False}}
    - skill_band_ceiling: 1.0
    - forced_no_strong_signal: False
    - kb_matches: [{'pattern_id': 'MR-01', 'story': 'Traffic shifted to a pricier model with stable tokens.'}]

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
