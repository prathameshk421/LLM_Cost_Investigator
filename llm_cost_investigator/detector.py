"""Deterministic anomaly detection over fixed telemetry windows."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from typing import Iterable

from llm_cost_investigator.schemas import AnomalySignals, AnomalyWindow, LLMCall

WINDOW_SIZE = timedelta(minutes=5)
MIN_BASELINE_WINDOWS = 3
COST_Z_THRESHOLD = 3.0
MIN_CURRENT_CALL_COUNT = 3
MIN_TOTAL_COST_USD = 0.01


@dataclass(frozen=True)
class WindowStats:
    """Numeric signal set for one feature/window.

    The detector is intentionally a deterministic signal computer. It does not
    use LLM judgment and it does not decide root cause.
    """

    call_count: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    avg_retry_count: float
    max_retry_count: int
    avg_latency_ms: float
    repeated_parent_call_count: int
    max_call_chain_depth: int
    models_seen: list[str]
    dominant_model: str | None


def detect_anomalies(calls: list[LLMCall]) -> list[AnomalyWindow]:
    """Detect anomalous cost windows from telemetry.

    Detection is deterministic:
    same calls + same window size + same thresholds = same anomaly windows.
    """
    if not calls:
        return []

    anomalies: list[AnomalyWindow] = []
    calls_by_feature: dict[str, list[LLMCall]] = defaultdict(list)
    for call in calls:
        calls_by_feature[call.feature_tag].append(call)

    for feature_tag, feature_calls in sorted(calls_by_feature.items()):
        sorted_feature_calls = sorted(feature_calls, key=lambda c: (c.timestamp, c.call_id))
        windows = _build_windows(sorted_feature_calls)
        previous_stats: list[WindowStats] = []

        for window_start in sorted(windows):
            window_calls = windows[window_start]
            stats = _compute_window_stats(window_calls, sorted_feature_calls)

            if len(previous_stats) >= MIN_BASELINE_WINDOWS:
                signals = _build_signals(stats, previous_stats)
                if _is_anomalous(stats, signals):
                    anomalies.append(
                        AnomalyWindow(
                            feature_tag=feature_tag,
                            start_time=window_start,
                            end_time=window_start + WINDOW_SIZE,
                            signals=signals,
                            sample_call_ids=[call.call_id for call in window_calls],
                            sample_calls=_sample_calls(window_calls),
                        )
                    )

            previous_stats.append(stats)

    return sorted(
        anomalies,
        key=lambda anomaly: (
            -anomaly.signals.cost_z_score,
            -_window_total_cost(anomaly.sample_calls),
            -_sortable_growth(anomaly.signals.cost_growth_pct),
            anomaly.start_time,
            anomaly.feature_tag,
        ),
    )


def _build_windows(calls: list[LLMCall]) -> dict[datetime, list[LLMCall]]:
    windows: dict[datetime, list[LLMCall]] = defaultdict(list)
    for call in calls:
        windows[_floor_to_window(call.timestamp)].append(call)
    return {
        start: sorted(window_calls, key=lambda c: (c.timestamp, c.call_id))
        for start, window_calls in windows.items()
    }


def _floor_to_window(timestamp: datetime) -> datetime:
    minute = timestamp.minute - (timestamp.minute % int(WINDOW_SIZE.total_seconds() // 60))
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def _compute_window_stats(
    window_calls: list[LLMCall],
    feature_calls: list[LLMCall],
) -> WindowStats:
    call_count = len(window_calls)
    total_cost_usd = sum(call.cost_usd for call in window_calls)
    total_input_tokens = sum(call.input_tokens for call in window_calls)
    total_output_tokens = sum(call.output_tokens for call in window_calls)
    retry_counts = [call.retry_count for call in window_calls]
    latencies = [call.latency_ms for call in window_calls]
    parent_counts = Counter(
        call.parent_call_id for call in window_calls if call.parent_call_id is not None
    )
    models_seen = sorted({call.model for call in window_calls})

    return WindowStats(
        call_count=call_count,
        total_cost_usd=total_cost_usd,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        avg_retry_count=_mean(retry_counts),
        max_retry_count=max(retry_counts, default=0),
        avg_latency_ms=_mean(latencies),
        repeated_parent_call_count=sum(1 for count in parent_counts.values() if count >= 2),
        max_call_chain_depth=_max_call_chain_depth(window_calls, feature_calls),
        models_seen=models_seen,
        dominant_model=_dominant_model(window_calls),
    )


def _build_signals(stats: WindowStats, baseline_stats: list[WindowStats]) -> AnomalySignals:
    baseline_models: set[str] = set()
    for bs in baseline_stats:
        baseline_models.update(bs.models_seen)
    current_models = set(stats.models_seen)
    model_changed = bool(current_models - baseline_models)
    baseline_model = _dominant_model_from_stats(baseline_stats)

    cost_mean = _baseline_mean(baseline_stats, "total_cost_usd")
    input_mean = _baseline_mean(baseline_stats, "total_input_tokens")
    output_mean = _baseline_mean(baseline_stats, "total_output_tokens")

    input_growth = _growth_pct(stats.total_input_tokens, input_mean)
    output_growth = _growth_pct(stats.total_output_tokens, output_mean)

    return AnomalySignals(
        cost_z_score=_z_score(stats.total_cost_usd, _values(baseline_stats, "total_cost_usd")),
        input_tokens_z_score=_z_score(
            stats.total_input_tokens,
            _values(baseline_stats, "total_input_tokens"),
        ),
        output_tokens_z_score=_z_score(
            stats.total_output_tokens,
            _values(baseline_stats, "total_output_tokens"),
        ),
        retry_z_score=_z_score(stats.avg_retry_count, _values(baseline_stats, "avg_retry_count")),
        calls_z_score=_z_score(stats.call_count, _values(baseline_stats, "call_count")),
        latency_z_score=_z_score(stats.avg_latency_ms, _values(baseline_stats, "avg_latency_ms")),
        input_token_growth_pct=input_growth,
        output_token_growth_pct=output_growth,
        cost_growth_pct=_growth_pct(stats.total_cost_usd, cost_mean),
        token_growth_pct=input_growth,
        max_retry_count=stats.max_retry_count,
        avg_retry_count=stats.avg_retry_count,
        max_call_chain_depth=stats.max_call_chain_depth,
        repeated_parent_call_count=stats.repeated_parent_call_count,
        model_changed=model_changed,
        models_seen=stats.models_seen,
        model_before=baseline_model,
        model_during=stats.dominant_model,
    )


def _is_anomalous(stats: WindowStats, signals: AnomalySignals) -> bool:
    enough_activity = (
        stats.call_count >= MIN_CURRENT_CALL_COUNT
        or stats.total_cost_usd >= MIN_TOTAL_COST_USD
    )
    if not enough_activity:
        return False
    return (
        signals.cost_z_score >= COST_Z_THRESHOLD
        or signals.input_tokens_z_score >= 3.0
        or signals.retry_z_score >= 3.0
        or signals.calls_z_score >= 3.0
        or (
            signals.model_changed is True
            and signals.cost_growth_pct is not None
            and signals.cost_growth_pct > 0
        )
    )


def _values(stats: Iterable[WindowStats], field_name: str) -> list[float]:
    return [float(getattr(stat, field_name)) for stat in stats]


def _baseline_mean(stats: Iterable[WindowStats], field_name: str) -> float:
    return _mean(_values(stats, field_name))


def _mean(values: Iterable[float | int]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(values_list) / len(values_list)


def _sample_std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0

    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return sqrt(variance)


def _z_score(current: float, baseline_values: list[float]) -> float:
    baseline_mean = _mean(baseline_values)
    baseline_std = _sample_std(baseline_values)

    if baseline_std == 0:
        if current == baseline_mean:
            return 0.0
        if current > baseline_mean:
            return 10.0
        return -10.0

    return (current - baseline_mean) / baseline_std


def _growth_pct(current: float, baseline_mean: float) -> float | None:
    if baseline_mean == 0:
        if current == 0:
            return 0.0
        return None
    return ((current - baseline_mean) / baseline_mean) * 100


def _dominant_model(calls: list[LLMCall]) -> str | None:
    if not calls:
        return None
    counts = Counter(call.model for call in calls)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _dominant_model_from_stats(stats: list[WindowStats]) -> str | None:
    models = [stat.dominant_model for stat in stats if stat.dominant_model is not None]
    if not models:
        return None
    counts = Counter(models)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _max_call_chain_depth(
    window_calls: list[LLMCall],
    feature_calls: list[LLMCall],
) -> int:
    calls_by_id = {call.call_id: call for call in feature_calls}
    memo: dict[str, int] = {}

    def depth(call: LLMCall, seen: set[str]) -> int:
        if call.call_id in memo:
            return memo[call.call_id]
        if call.call_id in seen:
            return 0
        if call.parent_call_id is None:
            memo[call.call_id] = 0
            return 0

        parent = calls_by_id.get(call.parent_call_id)
        if parent is None:
            memo[call.call_id] = 1
            return 1

        result = 1 + depth(parent, seen | {call.call_id})
        memo[call.call_id] = result
        return result

    return max((depth(call, set()) for call in window_calls), default=0)


def _sample_calls(window_calls: list[LLMCall]) -> list[LLMCall]:
    return sorted(window_calls, key=lambda c: (c.timestamp, c.call_id))[:10]


def _window_total_cost(calls: list[LLMCall]) -> float:
    return sum(call.cost_usd for call in calls)


def _sortable_growth(value: float | None) -> float:
    return value if value is not None else float("-inf")
