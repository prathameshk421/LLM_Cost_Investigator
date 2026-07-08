# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.95
Winning agent: model_routing_agent

Summary:
Model routing metrics crossed the expensive model misroute thresholds. (Fallback)

Supporting evidence:
- **model_routing_agent** — provider: fallback, model: n/a, fallback: True (no GROQ_API_KEY or CEREBRAS_API_KEY configured)
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.95
  - Explanation: Model routing metrics crossed the expensive model misroute thresholds. (Fallback)
  - Supporting metrics:
    - model_changed: True
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - cost_z_score: 1882.0355656496642
- **token_context_agent** — provider: fallback, model: n/a, fallback: True (no GROQ_API_KEY or CEREBRAS_API_KEY configured)
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.62
  - Explanation: Token metrics crossed the context-bloat thresholds. (Fallback)
  - Supporting metrics:
    - input_token_growth_pct: 24.559471365638768
    - max_call_chain_depth: 0
    - input_tokens_z_score: 155.8259871820166

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
