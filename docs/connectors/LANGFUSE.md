# Langfuse connector

Pull **GENERATION** observations from Langfuse for a time window, normalize them into `LLMCall`, and run the same investigation pipeline as file ingest.

## Setup

1. Install deps (includes `httpx` and `pyyaml`):

   ```bash
   pip install -e .
   ```

2. Set credentials in `.env` (or the process environment):

   ```bash
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   # optional
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```

   | Host | When |
   | --- | --- |
   | `https://cloud.langfuse.com` | Langfuse Cloud (EU, default) |
   | `https://us.cloud.langfuse.com` | Langfuse Cloud (US) |
   | `https://your-host` | Self-hosted |

3. Optional named profile — copy `docs/connectors/connections.example.yaml` to `.llmci/connections.yaml`.

**Secrets never go in the profile file or the UI.** Profiles only store host, defaults, and filters.

## CLI

```bash
# Ad-hoc (env keys + optional LANGFUSE_HOST)
python -m llm_cost_investigator.cli investigate \
  --connector langfuse \
  --since-hours 24 \
  --force-fallback

# Named profile
python -m llm_cost_investigator.cli investigate \
  --connection prod-langfuse \
  --since-hours 6 \
  --label langfuse_prod \
  --force-fallback

# Pull only → JSONL
python -m llm_cost_investigator.cli pull \
  --connector langfuse \
  --since-hours 24 \
  --out data/samples/langfuse_pull.jsonl

# Profiles
python -m llm_cost_investigator.cli connections list
python -m llm_cost_investigator.cli connections test --connector langfuse
```

## Field mapping

| LLMCall | Langfuse sources |
| --- | --- |
| `timestamp` | `startTime` |
| `call_id` | observation `id` |
| `parent_call_id` | `parentObservationId` (may point at SPANs not pulled) |
| `feature_tag` | `metadata.feature_tag` → `name` → `traceName` → default |
| `model` | `providedModelName` / `model` |
| `input_tokens` / `output_tokens` | `inputUsage`/`outputUsage`, `usageDetails`, or `usage` |
| `cost_usd` | `totalCost` / cost details / `calculatedTotalCost` (else estimated) |
| `latency_ms` | `latency` (seconds) or end−start |
| `retry_count` | `metadata.retry_count` / `metadata.retries` (else **0**) |

Prompts (`input`/`output`) are **not** forwarded into `LLMCall` or agent context.

### Instrumentation tips

- Set `metadata.feature_tag` on generations so cost is grouped by product feature.
- Set `metadata.retry_count` if your client retries — Langfuse does not invent retries.
- Parent chains for context-bloat detection are strongest when parents are other GENERATIONs (not only SPANs).

## API (web)

| Method | Path |
| --- | --- |
| GET | `/api/connectors` |
| GET | `/api/connectors/profiles` |
| POST | `/api/connectors/test` |
| POST | `/api/connectors/preview` |
| POST | `/api/connectors/investigate` |

UI: **Investigate → Remote (Langfuse)**. Keys remain on the API process only.

## Limits

- Default window: last **24 hours**
- Default max rows: **10_000** (hard cap 100_000)
- API version default: **v1** (self-host + cloud); set `api_version: v2` for Cloud-only Observations API v2
- Empty windows exit CLI with code **1** / API HTTP 400

## Security

- Env-only secrets
- No secret fields allowed in `connections.yaml`
- Report slugs use `label_feature_index` so demo incidents are not overwritten
