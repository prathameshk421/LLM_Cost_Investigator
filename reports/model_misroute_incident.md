# Incident Report: model_misroute

Root cause: expensive_model_misroute
Affected feature: summarizer
Confidence: 0.95
Winning agent: model_routing_agent

## Agent Execution

- Agent: model_routing_agent
- Provider: fallback
- Model: n/a
- Fallback used: True
- Fallback reason: fallback provider explicitly selected

## Supporting Evidence

- Agent: model_routing_agent
- Hypothesis: expensive_model_misroute
- Confidence: 0.95
- Explanation: Model routing metrics crossed the expensive model misroute thresholds. (Fallback)
- Metrics: {'model_changed': True, 'cost_growth_pct': 609.35960591133, 'token_growth_pct': 24.559471365638768, 'cost_z_score': 1882.0355656496642}

## Recommendations
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
