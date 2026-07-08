# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.96
Winning agent: model_routing_agent

Summary:
The telemetry shows a model change from the cheaper gpt-4o-mini to the more expensive gpt-4.1 during the anomaly window. Cost grew by over 600% while token usage increased only ~25%, well below the 50% threshold. No retry spikes are present. These patterns strongly indicate the feature was misrouted to a higher‑cost model.

Supporting evidence:
- **model_routing_agent** — provider: cerebras, model: gpt-oss-120b, fallback: False
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.96
  - Explanation: The telemetry shows a model change from the cheaper gpt-4o-mini to the more expensive gpt-4.1 during the anomaly window. Cost grew by over 600% while token usage increased only ~25%, well below the 50% threshold. No retry spikes are present. These patterns strongly indicate the feature was misrouted to a higher‑cost model.
  - Supporting metrics:
    - model_changed: True
    - model_before: gpt-4o-mini
    - model_during: gpt-4.1
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - cost_z_score: 1882.0355656496642
    - retry_z_score: 0.0
    - max_retry_count: 0
- **token_context_agent** — provider: cerebras, model: gpt-oss-120b, fallback: False
  - Hypothesis: no_strong_signal
  - Confidence: 0.32
  - Explanation: While the input token z-score is extremely high, the actual input token growth is only ~24.6% and there is no call chain depth (max depth = 0). The cost surge aligns with a model change rather than increased context or recursive calls. Therefore there is no strong signal of context bloat or self‑calling behavior.
  - Supporting metrics:
    - input_tokens_z_score: 155.8259871820166
    - input_token_growth_pct: 24.559471365638768
    - cost_growth_pct: 609.35960591133
    - max_call_chain_depth: 0
    - model_changed: True

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
