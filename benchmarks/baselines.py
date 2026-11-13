"""Two baseline detectors, as registered in benchmarks/PREREGISTRATION.md.

B1 cost alert: per feature, 5-minute windows (UTC floor, same as the product
detector). A window is an incident when its total cost has z >= 3 against the
trailing 72 windows of that feature (mean and sample std), after 12 warm-up
windows. Idle windows between the feature's first and last call count as 0
cost. Flagged windows ARE kept in the trailing baseline: a plain alert has no
exclusion logic. Every B1 incident is labelled `expensive_model_misroute`.

B2 three rules, on the same windows and the same warm-up: label
`uncapped_retry_loop` if max retry_count >= 4; else `expensive_model_misroute`
if a model appears that the feature did not use in any earlier window; else
`context_bloat_self_calling_agent` if max parent chain depth >= 4; else none.

Adjacent flagged windows of one feature with one label merge into one incident.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from llm_cost_investigator.schemas import LLMCall

WINDOW = timedelta(minutes=5)
WARMUP = 12
TRAIL = 72
Z_MIN = 3.0
RETRY_MIN = 4
DEPTH_MIN = 4

B1_LABEL = "expensive_model_misroute"


def _floor(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def _depths(calls: list[LLMCall]) -> dict[str, int]:
    """Ancestor hops per call, following parent_call_id within one feature.

    A parent id that is not a call of this feature ends the chain. A cycle is
    cut: the call where the walk re-enters the path gets depth 0 for the loop.
    """
    parent = {c.call_id: c.parent_call_id for c in calls}
    depth: dict[str, int] = {}
    for cid in parent:
        if cid in depth:
            continue
        path: list[str] = []
        on_path: set[str] = set()
        cur: str | None = cid
        while cur is not None and cur in parent and cur not in depth and cur not in on_path:
            path.append(cur)
            on_path.add(cur)
            cur = parent[cur]
        if cur is not None and cur in depth:
            nxt = depth[cur] + 1
        else:
            nxt = 0  # chain root, dangling parent, or cycle cut
        for node in reversed(path):
            depth[node] = nxt
            nxt += 1
    return depth


def _merge(items: list[tuple[str, str, datetime]]) -> list[dict]:
    """items: (feature, cause, window_start) sorted per feature by start."""
    out: list[dict] = []
    last: dict[str, dict] = {}
    for feature, cause, start in sorted(items, key=lambda x: (x[0], x[2])):
        prev = last.get(feature)
        if prev and prev["cause"] == cause and prev["end"] == start:
            prev["end"] = start + WINDOW
        else:
            prev = {"cause": cause, "feature": feature, "start": start, "end": start + WINDOW}
            out.append(prev)
            last[feature] = prev
    return out


def run_baselines(calls: list[LLMCall]) -> dict[str, list[dict]]:
    by_feature: dict[str, list[LLMCall]] = defaultdict(list)
    for c in calls:
        by_feature[c.feature_tag].append(c)

    b1: list[tuple[str, str, datetime]] = []
    b2: list[tuple[str, str, datetime]] = []

    for feature, fcalls in by_feature.items():
        wins: dict[datetime, list[LLMCall]] = defaultdict(list)
        for c in fcalls:
            wins[_floor(c.timestamp)].append(c)
        first, last = min(wins), max(wins)
        n = int((last - first) // WINDOW) + 1
        starts = [first + WINDOW * i for i in range(n)]
        costs = [sum(c.cost_usd for c in wins[s]) if s in wins else 0.0 for s in starts]

        # B1
        for i in range(WARMUP, n):
            trail = costs[max(0, i - TRAIL):i]
            m = len(trail)
            if m < 2:
                continue
            mean = sum(trail) / m
            var = sum((x - mean) ** 2 for x in trail) / (m - 1)
            std = math.sqrt(var)
            if std > 0:
                flagged = (costs[i] - mean) / std >= Z_MIN
            else:
                flagged = costs[i] > mean  # zero spread: z is +inf above the mean
            if flagged:
                b1.append((feature, B1_LABEL, starts[i]))

        # B2
        depth = _depths(fcalls)
        seen_models: set[str] = set()
        for i, s in enumerate(starts):
            wc = wins.get(s)
            if not wc:
                continue
            models = {c.model for c in wc}
            new_model = bool(models - seen_models)
            seen_models |= models
            if i < WARMUP:
                continue
            if max(c.retry_count for c in wc) >= RETRY_MIN:
                cause = "uncapped_retry_loop"
            elif new_model:
                cause = "expensive_model_misroute"
            elif max(depth[c.call_id] for c in wc) >= DEPTH_MIN:
                cause = "context_bloat_self_calling_agent"
            else:
                continue
            b2.append((feature, cause, s))

    return {"B1": _merge(b1), "B2": _merge(b2)}
