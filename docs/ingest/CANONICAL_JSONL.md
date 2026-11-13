# Canonical telemetry JSONL

One JSON object per line. Field names match `LLMCall` in the core schema.

## Required fields

| Field | Type | Notes |
| --- | --- | --- |
| `timestamp` | ISO-8601 or unix seconds/ms | Normalized to UTC |
| `call_id` | string | Unique per file |
| `feature_tag` | string | Feature / product surface name |
| `model` | string | Model id |
| `input_tokens` | int ≥ 0 | |
| `output_tokens` | int ≥ 0 | |

## Optional fields

| Field | Default if missing |
| --- | --- |
| `cost_usd` | Estimated from pricing table |
| `latency_ms` | `0` |
| `retry_count` | `0` |
| `parent_call_id` | `null` |
| `scenario_label` | `null` (demo only; never used as evidence) |

## Example

```json
{"timestamp":"2026-07-01T12:00:00Z","call_id":"c1","feature_tag":"summarizer","model":"gpt-4o-mini","input_tokens":1200,"output_tokens":300,"cost_usd":0.002,"latency_ms":400,"retry_count":0,"parent_call_id":null}
```

Also accepted: a single JSON **array** of the same objects (`.json` file).

## Other adapters

- **OpenAI-style:** nested `usage.prompt_tokens` / `usage.completion_tokens`, `id`, `created`, optional `metadata.feature_tag`
- **CSV:** supply column mapping (`timestamp=ts,call_id=id,...`)

## CLI

```bash
python -m llm_cost_investigator.cli investigate \
  --input data/samples/canonical_sample.jsonl \
  --adapter jsonl \
  --force-fallback
```

## Limitations

- Sparse `parent_call_id` weakens retry / context-chain diagnoses.
- Estimated costs may mis-rank models if your rates differ; prefer real `cost_usd`.
