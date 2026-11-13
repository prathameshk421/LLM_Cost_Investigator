"""Cost timeline per feature, in the same windows the detector uses."""

from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from typing import Any

from llm_cost_investigator.detector import WINDOW_SIZE, _floor_to_window

MAX_POINTS = 400


def _iso(ts) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_timeline(
    calls: list,
    incidents: list | None = None,
    flagged_windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return {window_minutes, windows, series, flagged} for a cost chart.

    ``series`` maps feature -> list of cost per window (0 when idle), aligned
    with ``windows``. ``flagged`` lists the investigated anomaly windows with
    their root cause, so the chart can mark them.
    """
    cost: dict[str, dict[Any, float]] = defaultdict(lambda: defaultdict(float))
    count: dict[Any, int] = defaultdict(int)
    for call in calls:
        start = _floor_to_window(call.timestamp)
        cost[call.feature_tag][start] += call.cost_usd
        count[start] += 1

    windows = sorted(count)
    if len(windows) > MAX_POINTS:
        windows = windows[-MAX_POINTS:]

    series = {
        feature: [round(per_window.get(w, 0.0), 6) for w in windows]
        for feature, per_window in sorted(cost.items())
    }
    flagged = []
    reports = list(incidents or [])
    if flagged_windows:
        # Each merged window inherits the diagnosis of its incident group.
        group_cause: dict[int, Any] = {}
        for g, report in enumerate(reports):
            group_cause[g] = report.root_cause
        for fw in flagged_windows:
            cause = group_cause.get(fw["group"])
            flagged.append(
                {
                    "feature_tag": fw["feature_tag"],
                    "start": _iso(fw["start_time"]),
                    "end": _iso(fw["end_time"]),
                    "hypothesis": cause.hypothesis if cause else "not_investigated",
                    "confidence": round(cause.confidence, 3) if cause else 0.0,
                    "group": fw["group"],
                }
            )
    else:
        for report in reports:
            window = report.anomaly_window
            flagged.append(
                {
                    "feature_tag": window.feature_tag,
                    "start": _iso(window.start_time),
                    "end": _iso(window.end_time),
                    "hypothesis": report.root_cause.hypothesis,
                    "confidence": round(report.root_cause.confidence, 3),
                }
            )
    return {
        "window_minutes": int(WINDOW_SIZE.total_seconds() // 60),
        "windows": [_iso(w) for w in windows],
        "calls_per_window": [count[w] for w in windows],
        "series": series,
        "flagged": flagged,
    }
