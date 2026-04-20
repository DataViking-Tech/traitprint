"""Rough BYOK cost estimation.

Prices are per 1M tokens, USD. Pulled from public pricing pages; stale by
design — the CLI shows these as *estimates*, not invoices. Unknown models
return 0.0 so the cost line still prints.
"""

from __future__ import annotations

# (input_per_mtok, output_per_mtok)
_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    # Anthropic
    ("anthropic", "claude-opus-4-7"): (15.00, 75.00),
    ("anthropic", "claude-sonnet-4-6"): (3.00, 15.00),
    ("anthropic", "claude-sonnet-4-20250514"): (3.00, 15.00),
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
    # OpenAI
    ("openai", "gpt-4o"): (2.50, 10.00),
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("openai", "gpt-4.1"): (2.00, 8.00),
    ("openai", "gpt-4.1-mini"): (0.40, 1.60),
    # OpenRouter models vary wildly — treat as 0 unless user overrides.
}

# Provider-level fallback if a model isn't in the table. Useful so the number
# isn't wildly wrong when new models ship.
_DEFAULTS: dict[str, tuple[float, float]] = {
    "anthropic": (3.00, 15.00),
    "openai": (2.50, 10.00),
    "openrouter": (0.0, 0.0),
    "ollama": (0.0, 0.0),
}


def estimate_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate USD cost for the given token counts."""
    key = (provider.lower(), model)
    prices = _PRICES.get(key)
    if prices is None:
        prices = _DEFAULTS.get(provider.lower(), (0.0, 0.0))
    in_rate, out_rate = prices
    return (input_tokens / 1_000_000) * in_rate + (
        output_tokens / 1_000_000
    ) * out_rate
