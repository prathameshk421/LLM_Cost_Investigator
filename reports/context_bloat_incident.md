# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.95
Winning agent: token_context_agent

Summary:
The input token growth percentage is 294.07%, the maximum call chain depth is 5, and the input tokens z-score is 1454.84, indicating a strong signal of context bloat and recursive self-calling behavior.

Supporting evidence:
- **token_context_agent** — provider: groq, model: llama-3.3-70b-versatile, fallback: False
  - Hypothesis: context_bloat_self_calling_agent
  - Confidence: 0.95
  - Explanation: The input token growth percentage is 294.07%, the maximum call chain depth is 5, and the input tokens z-score is 1454.84, indicating a strong signal of context bloat and recursive self-calling behavior.
  - Supporting metrics:
    - input_token_growth_pct: 294.0677966101695
    - max_call_chain_depth: 5
    - input_tokens_z_score: 1454.8417278608008

Recommendations:
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
