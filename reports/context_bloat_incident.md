# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.82
Winning agent: token_context_agent

Summary:
The telemetry shows an extreme input token z-score far above the threshold and a ~294% token growth, indicating severe context bloat. Additionally, the call chain depth of 5 and the presence of the 'agent_reflection' feature suggest recursive self‑calling behavior. Together these signals strongly point to a combined context bloat and self‑calling anomaly.

Supporting evidence:
- **token_context_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.82
  - Explanation: The telemetry shows an extreme input token z-score far above the threshold and a ~294% token growth, indicating severe context bloat. Additionally, the call chain depth of 5 and the presence of the 'agent_reflection' feature suggest recursive self‑calling behavior. Together these signals strongly point to a combined context bloat and self‑calling anomaly.
  - Supporting metrics:
    - feature_tag: agent_reflection
    - input_tokens_z_score: 1454.8417278608008
    - input_token_growth_pct: 294.0677966101695
    - cost_growth_pct: 313.79310344827593
    - max_call_chain_depth: 5
    - retry_z_score: 0.0
    - model_changed: False

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
