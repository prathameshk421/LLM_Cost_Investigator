# Base Build Plan

Project goal: build an agentic LLM cost anomaly investigator before the BNY internship deadline on July 9, 2026 at 10:00 AM.

Core architecture:

```text
Telemetry harness/simulator
  -> SQLite telemetry store
  -> deterministic anomaly detector
  -> rule-based agent router
  -> specialized LLM diagnostic agents
  -> deterministic aggregator
  -> incident report generator
  -> replay tests against labeled bugs
```

## Hard Done Definition

The project is done enough when these commands work:

```bash
python main.py --scenario retry_loop
python main.py --scenario context_bloat
python main.py --scenario model_misroute
python replay_tests.py
```

`replay_tests.py` must show all three scenarios as `PASS`.

## Scope Lock

Build only these three diagnosis agents first:

- Retry Loop Agent
- Token Context Agent
- Model Routing Agent

Skip Traffic/Deployment Agent unless everything else is already working.

No frontend until the CLI demo and tests pass.

## Planned Project Structure

```text
llm_cost_investigator/
  __init__.py
  schemas.py
  telemetry_store.py
  simulate_telemetry.py
  detector.py
  router.py
  agents.py
  aggregator.py
  reporter.py
main.py
replay_tests.py
reports/
data/
```

## Build Order

1. Create schemas and SQLite table.
2. Generate baseline telemetry plus injected labeled bugs.
3. Detect anomalous windows with deterministic z-scores.
4. Route anomaly windows to relevant agents.
5. Add fake agent outputs to prove the end-to-end pipeline.
6. Replace fake outputs with LLM agent calls using Pydantic validation.
7. Aggregate evidence deterministically.
8. Generate Markdown and JSON incident reports.
9. Add replay tests for the three known bug scenarios.
10. Add README, architecture diagram, and resume bullet.

## Time Budget

| Phase | Target Time | Output |
| --- | ---: | --- |
| Skeleton + telemetry | 45 min | SQLite DB and simulated rows |
| Detector + router | 45 min | anomaly windows routed to agents |
| Agents + validation | 75 min | structured JSON agent evidence |
| Aggregator + reports | 45 min | incident report output |
| Replay tests | 45 min | all scenarios pass |
| README/demo polish | 30 min | submission-ready repo |

## Design Rule

Keep the control plane deterministic and make only diagnosis/reporting agentic:

```text
Math decides when something is wrong.
Rules decide which agents should investigate.
Agents explain why it likely happened.
Aggregator locks the final root cause.
```

