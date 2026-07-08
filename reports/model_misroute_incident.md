# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.95
Winning agent: model_routing_agent

Summary:
During the 5‑minute window the feature switched from the cheap gpt-4o-mini to the much pricier gpt-4.1. Cost surged by ~609% while token usage grew only ~25%, well below the 50% threshold. No retry activity was observed. These signals strongly indicate the anomaly is due to routing to a more expensive model.

Supporting evidence:
- **model_routing_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.95
  - Explanation: During the 5‑minute window the feature switched from the cheap gpt-4o-mini to the much pricier gpt-4.1. Cost surged by ~609% while token usage grew only ~25%, well below the 50% threshold. No retry activity was observed. These signals strongly indicate the anomaly is due to routing to a more expensive model.
  - Supporting metrics:
    - model_before: gpt-4o-mini
    - model_during: gpt-4.1
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - input_token_growth_pct: 24.559471365638768
    - output_token_growth_pct: 24.93150684931507
    - retry_z_score: 0.0
    - max_retry_count: 0
    - model_changed: True
    - cost_z_score: 1882.0355656496642
- **token_context_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.62
  - Explanation: The anomaly shows an extreme input token z-score indicating unusually large context size, while the call chain depth is zero, ruling out recursive self-calls. The modest token growth (≈24.6%) combined with a massive cost increase suggests the primary driver is context bloat, possibly amplified by a model change.
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
