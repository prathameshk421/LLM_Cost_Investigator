# LLM Cost Investigator

Agentic LLM cost anomaly investigator for diagnosing sudden spend spikes in
LLM-backed features.

The control plane is deterministic:

```text
simulated telemetry -> z-score detector -> rule router -> diagnostic LLM agents
-> deterministic aggregator -> incident reports
```

Only the diagnostic layer is agentic. The detector decides that a feature is
anomalous, the router picks which narrow agent should investigate, and the
aggregator locks the final root cause from validated JSON evidence.

## Diagnostic Agents

The project has three diagnostic agents:

- `retry_loop_agent` diagnoses uncapped retries or repeated failed calls.
- `token_context_agent` diagnoses context bloat and recursive call chains.
- `model_routing_agent` diagnoses accidental routing to a pricier model.

Each agent receives only its own telemetry slice, calls a real LLM when Groq or
Cerebras is configured, returns JSON, and is validated with Pydantic. Malformed
JSON gets one repair retry. Deterministic fallback evidence is used only when no
API key exists, the live call fails, or validation fails twice.

## Provider Setup

Groq and Cerebras are called through their OpenAI-compatible APIs.

```bash
export GROQ_API_KEY="..."
export LLM_PROVIDER="groq"
export LLM_MODEL="llama-3.3-70b-versatile"
```

```bash
export CEREBRAS_API_KEY="..."
export LLM_PROVIDER="cerebras"
export LLM_MODEL="llama3.1-8b"
```

Provider-specific model variables also work:

```bash
export GROQ_MODEL="llama-3.3-70b-versatile"
export CEREBRAS_MODEL="llama3.1-8b"
```

If `LLM_PROVIDER` is not set, the CLI auto-selects Groq when `GROQ_API_KEY` is
present, then Cerebras when `CEREBRAS_API_KEY` is present. Use
`--force-fallback` for a fully deterministic run with no live API calls.

## Run

```bash
python3 main.py --scenario retry_loop
python3 main.py --scenario context_bloat
python3 main.py --scenario model_misroute
python3 main.py --scenario all
```

Force deterministic fallback:

```bash
python3 main.py --scenario all --force-fallback
```

Run replay tests:

```bash
python3 replay_tests.py
```

Replay tests explicitly use fallback mode so they remain stable and do not spend
API credits.

## Reports

Each scenario writes:

- `reports/<scenario>_report.json`
- `reports/<scenario>_incident.md`

Reports include the winning root cause, supporting agent evidence,
recommendations, and execution metadata showing provider/model or fallback
reason.

## Resume Bullet

Built an agentic LLM cost anomaly investigator using deterministic z-score
detection, cost-aware agent routing, Pydantic-validated diagnostic LLM agents,
and replay tests against labeled retry-loop, context-bloat, and model-misrouting
incidents.
