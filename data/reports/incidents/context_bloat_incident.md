# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.84
Winning agent: token_context_agent

Summary:
Token metrics crossed the context-bloat thresholds. (Fallback)

Supporting evidence:
- **token_context_agent** — provider: fallback, model: n/a, fallback: True (fallback provider explicitly selected)
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.84
  - Explanation: Token metrics crossed the context-bloat thresholds. (Fallback)
  - Supporting metrics:
    - input_token_growth_pct: 294.0677966101695
    - max_call_chain_depth: 5
    - input_tokens_z_score: 1454.8417278608008

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
