# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.82
Winning agent: token_context_agent

Summary:
The telemetry shows a massive increase in input tokens (≈294% growth) with an extremely high z-score, and a deep call chain of depth 5 where each call references the previous one, indicating the agent is recursively calling itself and accumulating context. Cost growth aligns with the token increase, and there are no retries or model changes. This pattern matches the context bloat self-calling scenario.

Supporting evidence:
- **token_context_agent** — provider: cerebras, model: gpt-oss-120b, fallback: False
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.82
  - Explanation: The telemetry shows a massive increase in input tokens (≈294% growth) with an extremely high z-score, and a deep call chain of depth 5 where each call references the previous one, indicating the agent is recursively calling itself and accumulating context. Cost growth aligns with the token increase, and there are no retries or model changes. This pattern matches the context bloat self-calling scenario.
  - Supporting metrics:
    - input_token_growth_pct: 294.0677966101695
    - max_call_chain_depth: 5
    - input_tokens_z_score: 1454.8417278608008
    - cost_growth_pct: 313.79310344827593
    - retry_z_score: 0.0
    - model_changed: False

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
