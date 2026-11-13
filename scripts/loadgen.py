#!/usr/bin/env python3
"""Send synthetic LLM call events to POST /v1/ingest.

Example:
    python scripts/loadgen.py --rate 50 --duration 120 --incident model_misroute
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

FEATURES = {  # feature: (model, input, output, latency_ms, weight)
    "summarizer": ("gpt-4o-mini", 900, 180, 700, 2),
    "classifier": ("gpt-4o-mini", 350, 40, 350, 3),
    "agent_reflection": ("gpt-4o-mini", 700, 160, 650, 1),
    "support_reply": ("gpt-4o-mini", 1100, 260, 850, 2),
    "search_rerank": ("gpt-4.1-mini", 1500, 60, 500, 2),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def jit(v: float) -> int:
    return max(1, int(v * random.gauss(1.0, 0.08)))


def event(feature: str, model: str, tin: int, tout: int, lat: int, **extra) -> dict:
    return {"call_id": uuid.uuid4().hex, "timestamp": now_iso(), "feature_tag": feature, "model": model,
            "input_tokens": tin, "output_tokens": tout, "latency_ms": lat, **extra}


def make_events(n: int, incident: str | None, incident_active: bool) -> list[dict]:
    names = list(FEATURES)
    weights = [FEATURES[f][4] for f in names]
    out = []
    for f in random.choices(names, weights, k=n):
        model, tin, tout, lat, _ = FEATURES[f]
        if incident_active and incident == "model_misroute" and f == "summarizer":
            model = "gpt-4.1"
        out.append(event(f, model, jit(tin), jit(tout), jit(lat)))
    if incident_active and incident == "retry_loop":
        parent = uuid.uuid4().hex
        for a in range(random.randint(6, 10)):
            out.append(event("support_reply", "gpt-4o-mini", jit(1100), jit(260), 850 + a * 400,
                             retry_count=a, parent_call_id=parent))
    if incident_active and incident == "context_bloat":
        prev = None
        for hop in range(random.randint(6, 8)):
            e = event("agent_reflection", "gpt-4o-mini", int(700 * (1 + hop)), jit(160), 650 + hop * 250)
            if prev:
                e["parent_call_id"] = prev
            prev = e["call_id"]
            out.append(e)
    return out


def post(url: str, events: list[dict], token: str | None) -> int:
    req = urllib.request.Request(url, data=json.dumps({"events": events}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/ingest")
    ap.add_argument("--rate", type=float, default=20, help="events per second")
    ap.add_argument("--duration", type=float, default=60, help="seconds (0 = until Ctrl+C)")
    ap.add_argument("--incident", choices=["model_misroute", "retry_loop", "context_bloat"])
    ap.add_argument("--incident-after", type=float, default=45, help="seconds before the incident starts")
    ap.add_argument("--token", default=None, help="Bearer token (LLMCI_API_TOKEN)")
    args = ap.parse_args()

    start = time.time()
    sent = 0
    try:
        while args.duration <= 0 or time.time() - start < args.duration:
            tick = time.time()
            active = args.incident is not None and tick - start >= args.incident_after
            n = max(1, round(args.rate))
            status = post(args.url, make_events(n, args.incident, active), args.token)
            sent += n
            if status == 429:
                print("429 queue full, backing off")
                time.sleep(1)
            elif status != 202:
                print(f"HTTP {status}")
            time.sleep(max(0.0, 1.0 - (time.time() - tick)))
            if int(time.time() - start) % 10 == 0:
                print(f"sent {sent} events, incident active: {active}")
    except KeyboardInterrupt:
        pass
    print(f"done, {sent} events in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
