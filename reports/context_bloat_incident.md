# Incident Report: context_bloat

Root cause: context_bloat_self_calling_agent
Affected feature: agent_reflection
Confidence: 0.84
Winning agent: token_context_agent

## Agent Execution

- Agent: token_context_agent
- Provider: fallback
- Model: n/a
- Fallback used: True
- Fallback reason: fallback provider explicitly selected

## Supporting Evidence

- Agent: token_context_agent
- Hypothesis: context_bloat_self_calling_agent
- Confidence: 0.84
- Explanation: Token metrics crossed the context-bloat thresholds. (Fallback)
- Metrics: {'input_token_growth_pct': 294.0677966101695, 'max_call_chain_depth': 5, 'input_tokens_z_score': 1454.8417278608008}

## Recommendations
- Limit maximum reflection / chain depth to a safe threshold (e.g., 3).
- Implement prompt summarization or token truncation for history.
- Add a fail-safe budget check on the chain to terminate expanding contexts.
