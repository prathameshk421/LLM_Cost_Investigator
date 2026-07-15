"""Simple static pricing for missing cost_usd fields."""

from __future__ import annotations

# Prices are USD per 1M tokens (input, output). Approximate mid-2025 public rates.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "llama-3.3-70b": (0.59, 0.79),
    "llama-3.1-8b": (0.05, 0.08),
    "openai/gpt-oss-120b": (0.15, 0.60),
}

# Used when model is unknown — intentionally cheap-ish so estimates don't invent huge spikes alone.
FALLBACK_INPUT_PER_M = 1.00
FALLBACK_OUTPUT_PER_M = 3.00


def _normalize_model_key(model: str) -> str:
    return model.strip().lower()


def lookup_rates(model: str) -> tuple[float, float, str]:
    """Return (input_per_m, output_per_m, pricing_key)."""
    key = _normalize_model_key(model)
    if key in _MODEL_PRICING:
        inp, out = _MODEL_PRICING[key]
        return inp, out, key

    # Prefix / contains match for versioned names — prefer longest key so
    # "gpt-4o-mini-2024…" maps to gpt-4o-mini, not gpt-4o.
    candidates = [
        known
        for known in _MODEL_PRICING
        if key.startswith(known) or known in key
    ]
    if candidates:
        best = max(candidates, key=len)
        rates = _MODEL_PRICING[best]
        return rates[0], rates[1], best

    return FALLBACK_INPUT_PER_M, FALLBACK_OUTPUT_PER_M, "fallback_default"


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[float, str]:
    """Estimate cost. Returns (cost_usd, pricing_key)."""
    inp_rate, out_rate, pricing_key = lookup_rates(model)
    cost = (input_tokens / 1_000_000.0) * inp_rate + (
        output_tokens / 1_000_000.0
    ) * out_rate
    return round(max(cost, 0.0), 8), pricing_key


def known_models() -> list[dict[str, float | str]]:
    """List known pricing entries for API/docs."""
    rows: list[dict[str, float | str]] = [
        {
            "model": name,
            "input_per_million_usd": rates[0],
            "output_per_million_usd": rates[1],
        }
        for name, rates in sorted(_MODEL_PRICING.items())
    ]
    rows.append(
        {
            "model": "fallback_default",
            "input_per_million_usd": FALLBACK_INPUT_PER_M,
            "output_per_million_usd": FALLBACK_OUTPUT_PER_M,
        }
    )
    return rows
