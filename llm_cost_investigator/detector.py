"""Deterministic anomaly detection over fixed telemetry windows."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Iterable

from llm_cost_investigator.schemas import AnomalySignals, AnomalyWindow, LLMCall

WINDOW_SIZE = timedelta(minutes=5)
# Baseline: the trailing BASELINE_MAX_WINDOWS accepted windows (6 h), and no
# verdict before MIN_BASELINE_WINDOWS. A flagged run stays out of the
# baseline for at most MAX_EXCLUDED_WINDOWS (1 h); after that it is a level
# shift and is learned, so the baseline cannot freeze for a whole day.
# Chosen on the held-out dev seeds only (docs/ARCHITECTURE.md).
MIN_BASELINE_WINDOWS = int(os.environ.get("LLMCI_MIN_BASELINE_WINDOWS", "12"))
# Below this much history a rare A/B model may simply not have shown up
# yet, so only a change of the *dominant* model counts as a new model.
MODEL_HISTORY_MIN_WINDOWS = 12
BASELINE_MAX_WINDOWS = 72
MAX_EXCLUDED_WINDOWS = 12
# Most SDKs retry up to 2 times by default. Retries at or below that cap
# are normal; a retry loop needs more than the cap and more than history.
NORMAL_RETRY_CAP = 2
COST_Z_THRESHOLD = 3.0
MIN_CURRENT_CALL_COUNT = 3
MIN_TOTAL_COST_USD = 0.01
# Structural early-warning thresholds (see _structural_break).
STRUCTURAL_RETRY_MIN = 4
STRUCTURAL_DEPTH_MIN = 4
STRUCTURAL_MARGIN = 3
# Minimum calls in a window before per-call averages count as evidence.
MIN_PER_CALL_SAMPLES = 5
# Robust (median/MAD) z-scores need a few baseline windows to be stable.
# Off by default: on the lab benchmark at 35% noise it cut accuracy from
# 97.0% to 93.0% with a fixed |z| >= 3 threshold (see docs/ARCHITECTURE.md).
ROBUST_Z = os.environ.get("LLMCI_ROBUST_Z", "0") == "1"
ROBUST_MIN_POINTS = 5


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
    avg_input_tokens: float
    unit_cost_per_1k: float
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

    # Treat naive timestamps as UTC so mixed inputs cannot crash sorting.
    calls = [_as_utc(call) for call in calls]

    anomalies: list[AnomalyWindow] = []
    calls_by_feature: dict[str, list[LLMCall]] = defaultdict(list)
    for call in calls:
        calls_by_feature[call.feature_tag].append(call)

    for feature_tag, feature_calls in sorted(calls_by_feature.items()):
        sorted_feature_calls = sorted(feature_calls, key=lambda c: (c.timestamp, c.call_id))
        windows = _build_windows(sorted_feature_calls)
        chain_index = {call.call_id: call for call in sorted_feature_calls}
        accepted: list[WindowStats] = []
        history = _History()
        flagged_run = 0

        for window_start in sorted(windows):
            window_calls = windows[window_start]
            stats = _compute_window_stats(window_calls, sorted_feature_calls, chain_index)
            baseline = accepted[-BASELINE_MAX_WINDOWS:]

            if len(baseline) >= MIN_BASELINE_WINDOWS:
                signals = _build_signals(stats, baseline, history)
                if _is_anomalous(stats, signals, baseline):
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
                    flagged_run += 1
                    # Keep a fresh incident out of the baseline, so it cannot
                    # raise its own baseline. A run longer than 1 h is a level
                    # shift: learn its level, but not its models or shape.
                    if flagged_run > MAX_EXCLUDED_WINDOWS:
                        accepted.append(stats)
                    continue
                flagged_run = 0

            accepted.append(stats)
            history.learn(stats)

    # Triage order: windows with causal evidence first, then cost z. Ranking
    # by cost z alone pushed real but small incidents below big noisy
    # windows and out of the investigated top N.
    return sorted(
        anomalies,
        key=lambda anomaly: (
            -int(anomaly.signals.causal_evidence),
            -anomaly.signals.cost_z_score,
            -_window_total_cost(anomaly.sample_calls),
            -_sortable_growth(anomaly.signals.cost_growth_pct),
            anomaly.start_time,
            anomaly.feature_tag,
        ),
    )


class _History:
    """What a feature normally does, from accepted (non-flagged) windows."""

    def __init__(self) -> None:
        self.models: set[str] = set()
        self.max_depth = 0
        self.max_retry = 0
        self.windows = 0

    def learn(self, stats: WindowStats) -> None:
        self.windows += 1
        self.models.update(stats.models_seen)
        self.max_depth = max(self.max_depth, stats.max_call_chain_depth)
        self.max_retry = max(self.max_retry, stats.max_retry_count)


def _as_utc(call: LLMCall) -> LLMCall:
    if call.timestamp.tzinfo is None:
        return call.model_copy(update={"timestamp": call.timestamp.replace(tzinfo=timezone.utc)})
    return call


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
    chain_index: dict[str, LLMCall] | None = None,
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
        avg_input_tokens=_mean(call.input_tokens for call in window_calls),
        unit_cost_per_1k=_unit_cost(total_cost_usd, total_input_tokens + total_output_tokens),
        avg_retry_count=_mean(retry_counts),
        max_retry_count=max(retry_counts, default=0),
        avg_latency_ms=_mean(latencies),
        repeated_parent_call_count=sum(1 for count in parent_counts.values() if count >= 2),
        max_call_chain_depth=_max_call_chain_depth(window_calls, feature_calls, chain_index),
        models_seen=models_seen,
        dominant_model=_dominant_model(window_calls),
    )


def _build_signals(
    stats: WindowStats,
    baseline_stats: list[WindowStats],
    history: _History | None = None,
) -> AnomalySignals:
    current_models = set(stats.models_seen)
    if history is not None:
        # New = never seen in the feature's accepted history (an A/B
        # minority model that appears now and then is not a change).
        known_models = history.models
    else:
        known_models = set()
        for bs in baseline_stats:
            known_models.update(bs.models_seen)
    if history is not None and history.windows < MODEL_HISTORY_MIN_WINDOWS:
        model_changed = stats.dominant_model is not None and stats.dominant_model not in known_models
    else:
        model_changed = bool(current_models - known_models)
    hist_depth = history.max_depth if history is not None else 0
    hist_retry = history.max_retry if history is not None else 0
    causal = (
        model_changed
        or stats.max_retry_count > max(NORMAL_RETRY_CAP, hist_retry)
        or (stats.max_call_chain_depth >= 2 and stats.max_call_chain_depth > hist_depth)
    )
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
        unit_cost_growth_pct=_growth_pct(
            stats.unit_cost_per_1k, _baseline_mean(baseline_stats, "unit_cost_per_1k")
        ),
        # Per-call means from 1-4 calls are mostly noise (one long prompt
        # moves the mean 50%+). Below the floor, report "unknown" (None).
        avg_input_token_growth_pct=(
            _growth_pct(stats.avg_input_tokens, _baseline_mean(baseline_stats, "avg_input_tokens"))
            if stats.call_count >= MIN_PER_CALL_SAMPLES
            else None
        ),
        max_retry_count=stats.max_retry_count,
        avg_retry_count=stats.avg_retry_count,
        max_call_chain_depth=stats.max_call_chain_depth,
        repeated_parent_call_count=stats.repeated_parent_call_count,
        model_changed=model_changed,
        history_max_chain_depth=hist_depth,
        history_max_retry_count=hist_retry,
        causal_evidence=causal,
        models_seen=stats.models_seen,
        model_before=baseline_model,
        model_during=stats.dominant_model,
    )


def _is_anomalous(
    stats: WindowStats,
    signals: AnomalySignals,
    baseline_stats: list[WindowStats] | None = None,
) -> bool:
    # A model never seen for this feature, at a higher price per token, is
    # evidence at any volume. A quiet-hours switch has too few calls for the
    # volume gate below, and would then be learned into the baseline.
    if (
        signals.model_changed
        and signals.unit_cost_growth_pct is not None
        and signals.unit_cost_growth_pct > 0
    ):
        return True
    enough_activity = (
        stats.call_count >= MIN_CURRENT_CALL_COUNT
        or stats.total_cost_usd >= MIN_TOTAL_COST_USD
    )
    if not enough_activity:
        return False
    if baseline_stats and _structural_break(stats, baseline_stats):
        return True
    return (
        signals.cost_z_score >= COST_Z_THRESHOLD
        or signals.input_tokens_z_score >= 3.0
        # Retry z on a near-zero series is huge for one normal retry, so the
        # retry trigger also needs retries above the client cap and history.
        or (
            signals.retry_z_score >= 3.0
            and stats.max_retry_count
            > max(NORMAL_RETRY_CAP, signals.history_max_retry_count)
        )
        or signals.calls_z_score >= 3.0
        or (
            signals.model_changed is True
            and signals.cost_growth_pct is not None
            and signals.cost_growth_pct > 0
        )
    )


def _structural_break(stats: WindowStats, baseline_stats: list[WindowStats]) -> bool:
    """Early warning: call-graph shape changed before spend did.

    A retry storm or a deep self-call chain is a bug even while cost is
    still inside normal noise. Both are measured against the feature's own
    baseline, so a feature that always chains or retries does not trigger.
    """
    base_retry = max(b.max_retry_count for b in baseline_stats)
    base_depth = max(b.max_call_chain_depth for b in baseline_stats)
    retry_storm = (
        stats.max_retry_count >= STRUCTURAL_RETRY_MIN
        and stats.max_retry_count >= base_retry + STRUCTURAL_MARGIN
        and stats.repeated_parent_call_count >= 1
    )
    deep_chain = (
        stats.max_call_chain_depth >= STRUCTURAL_DEPTH_MIN
        and stats.max_call_chain_depth >= base_depth + STRUCTURAL_MARGIN
    )
    return retry_storm or deep_chain


def _unit_cost(cost_usd: float, tokens: int) -> float:
    return cost_usd / tokens * 1000 if tokens > 0 else 0.0


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


def _robust_z(current: float, baseline_values: list[float]) -> float | None:
    """Median/MAD z-score (S-H-ESD, Hochenbaum et al. 2017, arXiv 1704.07706).

    One past spike moves the mean and standard deviation a lot; it barely
    moves the median and MAD. Returns None when the robust scale is not
    usable (too few points or MAD of 0), so the caller can fall back.
    """
    if len(baseline_values) < ROBUST_MIN_POINTS:
        return None
    ordered = sorted(baseline_values)
    median = _median(ordered)
    mad = _median(sorted(abs(v - median) for v in ordered))
    if mad == 0:
        return None
    # 0.6745 makes MAD-based z comparable to a normal z (MAD ≈ 0.6745 σ).
    return 0.6745 * (current - median) / mad


def _median(ordered: list[float]) -> float:
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _z_score(current: float, baseline_values: list[float]) -> float:
    if ROBUST_Z:
        robust = _robust_z(current, baseline_values)
        if robust is not None:
            return robust
    return _classic_z(current, baseline_values)


def _classic_z(current: float, baseline_values: list[float]) -> float:
    baseline_mean = _mean(baseline_values)
    baseline_std = _sample_std(baseline_values)

    if baseline_std == 0:
        if current == baseline_mean:
            return 0.0
        # Zero variance in the baseline: use a floor of 10% of the mean.
        # The floor must be relative. An absolute floor of 1.0 is one whole
        # USD for cost, so a flat $0.01 baseline could grow 100x unseen.
        # Only a zero mean (e.g. no retries) uses the unit floor.
        min_std = abs(baseline_mean) * 0.1 if baseline_mean != 0 else 1.0
        return (current - baseline_mean) / min_std

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
    chain_index: dict[str, LLMCall] | None = None,
) -> int:
    # Build the id index once per feature, not once per window (it made
    # detection O(windows x calls): 1-4 s on an 11k-call dataset).
    calls_by_id = chain_index if chain_index is not None else {c.call_id: c for c in feature_calls}
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
    """Pick a representative slice (cap 10): early + high-signal + most recent.

    Chronological first-10 alone misses late retry storms / late model shifts
    and can false-trigger sample consistency falsifiers.
    """
    if len(window_calls) <= 10:
        return sorted(window_calls, key=lambda c: (c.timestamp, c.call_id))

    ordered = sorted(window_calls, key=lambda c: (c.timestamp, c.call_id))
    by_id: dict[str, LLMCall] = {}

    def _add(call: LLMCall) -> None:
        if call.call_id not in by_id and len(by_id) < 10:
            by_id[call.call_id] = call

    # 1) Earliest calls (window onset)
    for call in ordered[:2]:
        _add(call)

    # 2) Highest-signal calls (retry, then cost)
    signal_ranked = sorted(
        ordered,
        key=lambda c: (c.retry_count, c.cost_usd, c.timestamp, c.call_id),
        reverse=True,
    )
    for call in signal_ranked[:3]:
        _add(call)

    # 3) Most recent calls
    for call in reversed(ordered):
        _add(call)
        if len(by_id) >= 8:
            break

    # 4) Fill remaining slots evenly across the window
    if len(by_id) < 10 and len(ordered) > 0:
        step = max(1, len(ordered) // (10 - len(by_id) + 1))
        for i in range(0, len(ordered), step):
            _add(ordered[i])
            if len(by_id) >= 10:
                break

    return sorted(by_id.values(), key=lambda c: (c.timestamp, c.call_id))


def _window_total_cost(calls: list[LLMCall]) -> float:
    return sum(call.cost_usd for call in calls)


def _sortable_growth(value: float | None) -> float:
    return value if value is not None else float("-inf")
