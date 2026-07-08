# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.82
Winning agent: token_context_agent

Summary:
The telemetry shows a massive increase in input tokens (≈294% growth, z‑score > 1400) and a deep call chain of depth 5 where each call is an agent_reflection invoking itself. No retries or model changes are present, and cost growth aligns with the token increase. These patterns strongly indicate context bloat combined with recursive self‑calling behavior.

Supporting evidence:
- **token_context_agent** — provider: groq, model: openai/gpt-oss-120b, fallback: False
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.82
  - Explanation: The telemetry shows a massive increase in input tokens (≈294% growth, z‑score > 1400) and a deep call chain of depth 5 where each call is an agent_reflection invoking itself. No retries or model changes are present, and cost growth aligns with the token increase. These patterns strongly indicate context bloat combined with recursive self‑calling behavior.
  - Supporting metrics:
    - input_tokens_z_score: 1454.8417278608008
    - input_token_growth_pct: 294.0677966101695
    - token_growth_pct: 294.0677966101695
    - cost_growth_pct: 313.79310344827593
    - max_call_chain_depth: 5
    - retry_z_score: 0.0
    - model_changed: False

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
