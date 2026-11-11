"""Budget guard and call log for live LLM calls.

Every live chat completion goes through ``BudgetedOpenAI``. Before each call
the ledger checks two hard limits. After each call it records the token
usage, an estimated USD cost, and one JSON line in the call log.

Limits (environment variables, per process):

- ``LLM_MAX_CALLS``   maximum number of live calls (default 60)
- ``LLM_BUDGET_USD``  maximum estimated spend in USD (default 0.05)
- ``LLM_CALL_LOG``    path of the JSONL call log
                      (default ``data/logs/llm_calls.jsonl``)

When a limit is reached the wrapper raises ``BudgetExceeded``. The agent
runner catches every exception and uses the deterministic fallback, so a
run never fails because of the budget. It only stops spending.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "BudgetExceeded",
    "BudgetedOpenAI",
    "UsageLedger",
    "ledger",
    "estimate_usd",
]

# USD per 1M tokens (input, output). Conservative upper estimates for the
# hosted open models we call. Unknown models use DEFAULT_RATE.
MODEL_RATES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-20b": (0.10, 0.50),
    "openai/gpt-oss-120b": (0.25, 0.75),
    "gpt-oss-120b": (0.25, 0.75),
    "meta-llama/llama-3.1-8b-instruct": (0.10, 0.10),
    "qwen/qwen3-32b": (0.30, 0.60),
}
DEFAULT_RATE: tuple[float, float] = (1.00, 3.00)


class BudgetExceeded(RuntimeError):
    """Raised before a live call when a budget limit is already reached."""


def _rate_for(model: str | None) -> tuple[float, float]:
    if not model:
        return DEFAULT_RATE
    key = model.strip().lower()
    # Drop a router suffix such as ":cerebras" or ":fastest".
    key = key.split(":", 1)[0]
    return MODEL_RATES.get(key, DEFAULT_RATE)


def estimate_usd(model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate the USD cost of one call from token counts."""
    rate_in, rate_out = _rate_for(model)
    return (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class UsageLedger:
    """Thread-safe running totals for live LLM calls in this process."""

    max_calls: int = field(default_factory=lambda: _env_int("LLM_MAX_CALLS", 60))
    budget_usd: float = field(default_factory=lambda: _env_float("LLM_BUDGET_USD", 0.05))
    log_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("LLM_CALL_LOG", "data/logs/llm_calls.jsonl")
        )
    )
    calls: int = 0
    errors: int = 0
    blocked: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    spent_usd: float = 0.0
    recent: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    RECENT_MAX = 50

    def check(self) -> None:
        """Raise BudgetExceeded if a further call would break a limit."""
        with self._lock:
            reason = None
            if self.calls >= self.max_calls:
                reason = f"call cap reached ({self.calls}/{self.max_calls} calls)"
            elif self.spent_usd >= self.budget_usd:
                reason = (
                    f"budget reached (${self.spent_usd:.4f} of ${self.budget_usd:.4f})"
                )
            if reason:
                self.blocked += 1
                raise BudgetExceeded(reason)

    def record(self, entry: dict[str, Any]) -> None:
        """Add one finished call to the totals and append it to the log."""
        with self._lock:
            self.calls += 1
            if entry.get("error"):
                self.errors += 1
            self.prompt_tokens += int(entry.get("prompt_tokens") or 0)
            self.completion_tokens += int(entry.get("completion_tokens") or 0)
            self.spent_usd += float(entry.get("est_usd") or 0.0)
            self.recent.append(entry)
            del self.recent[: -self.RECENT_MAX]
        self._append_log(entry)

    def _append_log(self, entry: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            # Logs must never break a diagnosis.
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "errors": self.errors,
                "blocked": self.blocked,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "spent_usd": round(self.spent_usd, 6),
                "max_calls": self.max_calls,
                "budget_usd": self.budget_usd,
                "remaining_usd": round(max(self.budget_usd - self.spent_usd, 0.0), 6),
                "log_path": str(self.log_path),
                "recent": list(reversed(self.recent)),
            }

    def reset(self) -> None:
        with self._lock:
            self.calls = self.errors = self.blocked = 0
            self.prompt_tokens = self.completion_tokens = 0
            self.spent_usd = 0.0
            self.recent.clear()


ledger = UsageLedger()


class _Completions:
    def __init__(self, inner: Any, provider: str, usage: UsageLedger) -> None:
        self._inner = inner
        self._provider = provider
        self._usage = usage

    def create(self, **kwargs: Any) -> Any:
        self._usage.check()
        model = kwargs.get("model")
        started = time.perf_counter()
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": self._provider,
            "model": model,
            "tools": bool(kwargs.get("tools")),
            "messages": len(kwargs.get("messages") or []),
        }
        try:
            response = self._inner.create(**kwargs)
        except Exception as exc:
            entry.update(
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                prompt_tokens=0,
                completion_tokens=0,
                est_usd=0.0,
            )
            self._usage.record(entry)
            raise
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        choice = response.choices[0] if getattr(response, "choices", None) else None
        msg = getattr(choice, "message", None)
        entry.update(
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            est_usd=round(estimate_usd(model, prompt_tokens, completion_tokens), 8),
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=len(getattr(msg, "tool_calls", None) or []),
        )
        self._usage.record(entry)
        return response


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class BudgetedOpenAI:
    """Proxy for an ``openai.OpenAI`` client that enforces the ledger.

    Only ``chat.completions.create`` is used by this project, so only that
    path is wrapped. Other attributes pass through to the real client.
    """

    def __init__(self, inner: Any, provider: str, usage: UsageLedger | None = None) -> None:
        self._inner = inner
        self.chat = _Chat(_Completions(inner.chat.completions, provider, usage or ledger))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
