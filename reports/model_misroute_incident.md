# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.95
Winning agent: model_routing_agent

Summary:
The cost grew by 609% while the token growth was only 24%, and the model was changed from gpt-4o-mini to gpt-4.1, indicating a likely misroute to a more expensive model.

Supporting evidence:
- **model_routing_agent** — provider: groq, model: llama-3.3-70b-versatile, fallback: False
  - Hypothesis: expensive_model_misroute
  - Confidence: 0.95
  - Explanation: The cost grew by 609% while the token growth was only 24%, and the model was changed from gpt-4o-mini to gpt-4.1, indicating a likely misroute to a more expensive model.
  - Supporting metrics:
    - cost_growth_pct: 609.35960591133
    - token_growth_pct: 24.559471365638768
    - cost_z_score: 1882.0355656496642
    - model_changed: True
    - model_before: gpt-4o-mini
    - model_during: gpt-4.1
- **token_context_agent** — provider: groq, model: llama-3.3-70b-versatile, fallback: False
  - Hypothesis: no_strong_signal
  - Confidence: 0.00
  - Explanation: The input token growth percentage is 24.56%, which is not sufficient to indicate context bloat. The maximum call chain depth is 0, indicating no recursive self-calling behavior. The input tokens z-score is high, but without a significant increase in input tokens or chain depth, it does not provide strong evidence for context bloat or recursive self-calling behavior.
  - Supporting metrics:
    - input_token_growth_pct: 24.559471365638768
    - max_call_chain_depth: 0
    - input_tokens_z_score: 155.8259871820166

Recommendations:
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
