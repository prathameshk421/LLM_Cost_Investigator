# Incident Report: model_misroute

Generated: 2026-07-08T16:10:42.136868+00:00

## Summary

Root cause: Model routing metrics crossed the expensive model misroute thresholds. (Fallback)

## Root Cause

| Field | Value |
| :--- | :--- |
| Hypothesis | expensive_model_misroute |
| Affected feature | summarizer |
| Confidence | 0.95 |
| Winning agent | model_routing_agent |

## Agent Execution
- **model_routing_agent** — provider: fallback, model: n/a, fallback: True, reason: fallback provider explicitly selected

## Supporting Evidence

### model_routing_agent
- **Hypothesis**: expensive_model_misroute
- **Confidence**: 0.95
- **Explanation**: Model routing metrics crossed the expensive model misroute thresholds. (Fallback)
- **Key metrics**:
  - model_changed: True
  - cost_growth_pct: 609.35960591133
  - token_growth_pct: 24.559471365638768
  - cost_z_score: 1882.0355656496642

## Recommendations
- Revert default model routing configurations to more cost-effective models.
- Add CI/CD checks to prevent unintentional model upgrades.
- Implement rate limiting or budget alerts for premium model endpoints.
