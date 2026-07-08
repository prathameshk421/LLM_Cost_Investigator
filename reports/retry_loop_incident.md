# Incident Report: retry_loop

Generated: 2026-07-08T16:10:03.713513+00:00

## Summary

Root cause: Retry metrics crossed the high-confidence retry-loop thresholds. (Fallback)

## Root Cause

| Field | Value |
| :--- | :--- |
| Hypothesis | uncapped_retry_loop |
| Affected feature | support_reply |
| Confidence | 0.94 |
| Winning agent | retry_loop_agent |

## Agent Execution
- **retry_loop_agent** — provider: fallback, model: n/a, fallback: True, reason: fallback provider explicitly selected

## Supporting Evidence

### retry_loop_agent
- **Hypothesis**: uncapped_retry_loop
- **Confidence**: 0.94
- **Explanation**: Retry metrics crossed the high-confidence retry-loop thresholds. (Fallback)
- **Key metrics**:
  - retry_z_score: 10.0
  - max_retry_count: 7
  - avg_retry_count: 3.5
  - repeated_parent_call_count: 1

## Recommendations
- Implement exponential backoff with jitter.
- Cap the maximum retry count in the client configuration.
- Add a circuit breaker pattern to prevent continuous retries during outages.
