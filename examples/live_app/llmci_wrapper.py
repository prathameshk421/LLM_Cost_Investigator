"""Copy this file into your app. It reports every LLM call to Tokenhound (LLM Cost Investigator).

    client = instrument(OpenAI(...), "http://127.0.0.1:8000")
    with llmci_context(feature_tag="summarize"):
        client.chat.completions.create(model=..., messages=...)

Events are posted from a background thread. A failed post never reaches your app.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone

_ctx: contextvars.ContextVar = contextvars.ContextVar("llmci_ctx", default={})


@contextlib.contextmanager
def llmci_context(**fields):
    """Set feature_tag, parent_call_id, retry_count or call_id for calls inside the block."""
    token = _ctx.set({**_ctx.get(), **fields})
    try:
        yield
    finally:
        _ctx.reset(token)


def instrument(client, investigator_url="http://127.0.0.1:8000", token=None, timeout=2.0,
               price=None, now=None, send=None):
    """Wrap client.chat.completions.create. price(model, in_tok, out_tok) -> usd is optional."""
    def default_send(event):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps({"events": [event]}).encode()
        req = urllib.request.Request(investigator_url.rstrip("/") + "/v1/ingest", body, headers)
        urllib.request.urlopen(req, timeout=timeout).read()

    send = send or default_send
    original = client.chat.completions.create
    threads: list[threading.Thread] = []

    def emit(event):
        def run():
            try:
                send(event)
            except Exception:
                pass  # investigator down or slow: the app must not care

        threads[:] = [t for t in threads if t.is_alive()]
        t = threading.Thread(target=run, daemon=True)
        t.start()
        threads.append(t)

    def create(*args, **kwargs):
        ctx = _ctx.get()
        # Stamp the call when it starts (as OpenTelemetry does), so a late answer
        # keeps the time it was made.
        ts = (now() if now else datetime.now(timezone.utc)).isoformat()
        start, usage = time.perf_counter(), None
        try:
            response = original(*args, **kwargs)
            usage = getattr(response, "usage", None)
            return response
        finally:
            try:
                tin = int(getattr(usage, "prompt_tokens", 0) or 0)
                tout = int(getattr(usage, "completion_tokens", 0) or 0)
                model = kwargs.get("model", "unknown")
                event = {
                    "timestamp": ts,
                    "call_id": ctx.get("call_id") or str(uuid.uuid4()),
                    "parent_call_id": ctx.get("parent_call_id"),
                    "feature_tag": ctx.get("feature_tag", "unknown"),
                    "model": model,
                    "input_tokens": tin,
                    "output_tokens": tout,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "retry_count": int(ctx.get("retry_count", 0)),
                }
                if price:  # otherwise the server prices the call
                    event["cost_usd"] = float(price(model, tin, tout))
                emit(event)
            except Exception:
                pass

    client.chat.completions.create = create
    client.llmci_flush = lambda: [t.join(timeout + 1) for t in list(threads)] and None
    return client
