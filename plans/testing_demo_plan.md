# Testing And Demo Plan

## Replay Test Matrix

| Scenario | Expected Root Cause | Expected Agent |
| --- | --- | --- |
| `retry_loop` | `uncapped_retry_loop` | `retry_loop_agent` |
| `context_bloat` | `context_bloat_self_calling_agent` | `token_context_agent` |
| `model_misroute` | `expensive_model_misroute` | `model_routing_agent` |

## Test Requirements

Each replay test must verify:

- anomaly was detected
- expected agent was routed
- root cause matched ground truth
- confidence >= 0.70
- report file was created

## CLI Demo

Required commands:

```bash
python main.py --scenario retry_loop
python main.py --scenario context_bloat
python main.py --scenario model_misroute
python replay_tests.py
```

Nice-to-have command:

```bash
python main.py --scenario all
```

## Expected Terminal Output

Example:

```text
Scenario: model_misroute
Detected anomaly: summarizer cost spike
Routed agents: model_routing_agent
Root cause: expensive_model_misroute
Confidence: 0.94
Report: reports/model_misroute_incident.md
Result: PASS
```

## Report Format

Each Markdown report should include:

```text
# Incident Report: <scenario>

Root cause:
Affected feature:
Confidence:
Winning agent:

Summary:

Supporting evidence:

Recommendations:
```

## README Checklist

README should include:

- what the project does
- why LLM cost anomalies matter
- architecture diagram
- how agents work
- why detector/router are deterministic
- how to run the demo
- sample output
- resume bullet

## Resume Bullet

Use this draft:

```text
Built an agentic LLM cost anomaly investigator using deterministic z-score detection, cost-aware agent routing, Pydantic-validated diagnostic LLM agents, and replay tests against labeled retry-loop, context-bloat, and model-misrouting incidents.
```

