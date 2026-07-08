# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.96
Winning agent: model_routing_agent

Summary:
The summarizer feature was routed from the low‑cost gpt-4o-mini to the much more expensive gpt-4.1. Cost increased by >600% while input/output token counts grew only ~25%, well under the 50% threshold. No retry spikes are observed. This pattern strongly indicates a misrouting to a pricier model.

Supporting evidence:
- **model_routing_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.96
  - Explanation: The summarizer feature was routed from the low‑cost gpt-4o-mini to the much more expensive gpt-4.1. Cost increased by >600% while input/output token counts grew only ~25%, well under the 50% threshold. No retry spikes are observed. This pattern strongly indicates a misrouting to a pricier model.
  - Supporting metrics:
    - model_before: gpt-4o-mini
    - model_during: gpt-4.1
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - cost_z_score: 1882.0355656496642
    - retry_z_score: 0.0
    - max_retry_count: 0
- **token_context_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: no_strong_signal
  - Confidence: 0.42
  - Explanation: Input token count shows a modest 24.6% increase and a very high z-score, but there is no call chain depth (max depth 0), indicating no recursive self-calling behavior. The massive cost growth aligns with a model change flag, suggesting the cost increase is due to the new model rather than context bloat. Therefore, there is no strong signal pointing to context bloat or self-calling anomalies.
  - Supporting metrics:
    - input_tokens_z_score: 155.8259871820166
    - input_token_growth_pct: 24.559471365638768
    - max_call_chain_depth: 0
    - model_changed: True
    - cost_growth_pct: 609.35960591133

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
