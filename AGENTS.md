# Agents Plan

The agents are visibly agentic but constrained:

- Each agent has one diagnostic role.
- Each receives a narrow telemetry slice.
- Each must return structured JSON.
- Pydantic validates every response.
- The pipeline retries once on malformed JSON.
- Deterministic fallback evidence keeps the demo from failing.

## Shared Evidence Schema

```python
class AgentEvidence(BaseModel):
    agent_name: Literal[
        "retry_loop_agent",
        "token_context_agent",
        "model_routing_agent"
    ]
    hypothesis: Literal[
        "uncapped_retry_loop",
        "context_bloat_self_calling_agent",
        "expensive_model_misroute",
        "no_strong_signal"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_metrics: dict[str, Any]
    explanation: str
```

## Shared Prompt Rules

Every agent prompt should include:

```text
Use only the telemetry provided.
Do not invent missing metrics.
Return only valid JSON.
Do not include markdown.
If the evidence is weak, return hypothesis "no_strong_signal".
Confidence must be between 0 and 1.
```

## Retry Loop Agent

Purpose: diagnose uncapped retries or repeated failing call patterns.

Input signals:

- retry z-score
- max retry count
- average retry count
- repeated parent call count
- latency growth
- sample calls from anomaly window

Prompt:

```text
You are the Retry Loop Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by an uncapped retry loop or repeated failed calls.

Look for:
- high retry_count
- repeated child calls from the same parent_call_id
- latency growth consistent with retries
- cost growth caused by repeated attempts

Use only the telemetry provided. Do not invent data.

Return only valid JSON matching this shape:
{
  "agent_name": "retry_loop_agent",
  "hypothesis": "uncapped_retry_loop" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}

Telemetry:
{anomaly_window_json}
```

Confidence guide:

- 0.90-1.00: retry z-score >= 5 and max retry count >= 5
- 0.75-0.89: retry z-score >= 3 or repeated parent calls are obvious
- 0.50-0.74: retry evidence exists but another cause may explain cost
- below 0.50: return `no_strong_signal`

## Token Context Agent

Purpose: diagnose context bloat, recursive/self-calling agents, or expanding prompts.

Input signals:

- input token z-score
- input token growth percentage
- max parent call-chain depth
- parent-child call samples
- cost growth from larger prompts

Prompt:

```text
You are the Token Context Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by context bloat, recursive self-calling behavior, or growing prompts.

Look for:
- input_tokens increasing over time
- deep parent_call_id chains
- agent_reflection or similar features calling themselves
- cost growth explained by larger context, not retries or model changes

Use only the telemetry provided. Do not invent data.

Return only valid JSON matching this shape:
{
  "agent_name": "token_context_agent",
  "hypothesis": "context_bloat_self_calling_agent" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}

Telemetry:
{anomaly_window_json}
```

Confidence guide:

- 0.90-1.00: input tokens grow by >= 300% and chain depth >= 5
- 0.75-0.89: input token z-score >= 3 and chain depth >= 4
- 0.50-0.74: token growth exists but chain evidence is weak
- below 0.50: return `no_strong_signal`

## Model Routing Agent

Purpose: diagnose accidental routing to a pricier model.

Input signals:

- model before/after
- model_changed
- cost z-score
- token growth percentage
- cost growth percentage
- sample calls before and during anomaly

Prompt:

```text
You are the Model Routing Diagnostic Agent.

Your only job is to decide whether this anomaly was caused by a feature being routed to a more expensive model.

Look for:
- same feature_tag using a different model during the anomaly
- cost_usd increasing sharply
- input/output tokens staying roughly stable
- no major retry spike

Use only the telemetry provided. Do not invent data.

Return only valid JSON matching this shape:
{
  "agent_name": "model_routing_agent",
  "hypothesis": "expensive_model_misroute" | "no_strong_signal",
  "confidence": number,
  "supporting_metrics": object,
  "explanation": string
}

Telemetry:
{anomaly_window_json}
```

Confidence guide:

- 0.90-1.00: model changed, cost grew >= 200%, token growth < 50%
- 0.75-0.89: model changed and cost z-score >= 3
- 0.50-0.74: cost grew without token growth, but model evidence is partial
- below 0.50: return `no_strong_signal`

## Agent Wrapper

Expected behavior:

```python
def call_agent_with_validation(llm_client, prompt: str, schema: type[BaseModel]):
    raw = llm_client(prompt)
    try:
        return schema.model_validate_json(raw)
    except Exception:
        repair_prompt = f"""
        Your previous response was not valid JSON for the required schema.
        Return only corrected JSON. No markdown. No prose.

        Invalid response:
        {raw}
        """
        repaired = llm_client(repair_prompt)
        return schema.model_validate_json(repaired)
```

If the second parse fails, call the deterministic fallback for that agent.

## Aggregator Contract

Agents do not make the final decision alone.

The aggregator:

- receives all evidence objects
- discards `no_strong_signal`
- selects the highest confidence root cause
- applies deterministic tie-breaks
- passes the selected result to the report writer

Tie-break order:

1. `expensive_model_misroute`
2. `uncapped_retry_loop`
3. `context_bloat_self_calling_agent`

