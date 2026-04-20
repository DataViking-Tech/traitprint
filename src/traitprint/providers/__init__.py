"""BYOK LLM provider adapters for traitprint.

A provider is a small adapter around a chat/completion endpoint that returns
structured JSON. Providers are deliberately thin — they exist so that
``traitprint vault import-resume`` can call whichever backend the user has
credentials for.

Usage::

    from traitprint.providers import detect_provider
    provider = detect_provider()
    response = provider.complete(system=..., user=...)
    print(response.content, response.cost_usd)

Supported providers: ``anthropic``, ``openai``, ``ollama``, ``openrouter``.
"""

from __future__ import annotations

from traitprint.providers.base import (
    AVAILABLE_PROVIDERS,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderNotConfigured,
    detect_provider,
    load_credentials,
    provider_from_name,
)

__all__ = [
    "AVAILABLE_PROVIDERS",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "ProviderNotConfigured",
    "detect_provider",
    "load_credentials",
    "provider_from_name",
]
