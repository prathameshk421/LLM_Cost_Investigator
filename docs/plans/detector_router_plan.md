# Detector And Router Plan

## Detector Goal

The detector answers:

```text
Is a feature behaving abnormally compared with its own baseline?
```

It should not decide the final root cause. It only creates a structured anomaly window.

## Window Metrics

For each `feature_tag` and time window, compute:

- `call_count`
- `cost_per_call`
- `input_tokens_per_call`
- `output_tokens_per_call`
- `avg_retry_count`
- `max_retry_count`
- `avg_latency_ms`
- `models_seen`
- `model_changed`
- `max_call_chain_depth`

## Baseline

Use earlier windows for the same `feature_tag`.

Minimum baseline:

- at least 3 previous windows if possible
- otherwise use all earlier calls for that feature

Compute:

```text
z = (current_value - baseline_mean) / baseline_std
```

If baseline standard deviation is zero, use a small epsilon.

## Anomaly Signals

Populate `AnomalyWindow.signals` with:

```text
cost_z_score
input_tokens_z_score
output_tokens_z_score
retry_z_score
calls_z_score
latency_z_score
input_token_growth_pct
output_token_growth_pct
cost_growth_pct
token_growth_pct
max_retry_count
max_call_chain_depth
model_changed
```

## Anomaly Thresholds

Flag an anomaly when:

- any major z-score >= 3
- or `model_changed == true` and `cost_growth_pct >= 100`
- or `max_call_chain_depth >= 4` and `input_token_growth_pct >= 100`

## Router Goal

The router answers:

```text
Which diagnostic agents are worth paying for?
```

It should call 1-2 agents per anomaly, not all agents.

## Routing Rules

Retry Loop Agent:

```python
retry_z_score >= 3 or max_retry_count >= 3
```

Token Context Agent:

```python
input_tokens_z_score >= 3
or input_token_growth_pct >= 100
or max_call_chain_depth >= 4
```

Model Routing Agent:

```python
(cost_z_score >= 3 and token_growth_pct < 50)
or model_changed is True
```

Fallback:

```python
if no route matches:
    choose the agent that best matches the largest z-score
```

## Cost-Aware Story

The router is important for the project narrative:

```text
The system uses deterministic signals to avoid calling every agent on every anomaly.
This keeps diagnosis cost-aware, which matches the product's purpose.
```

