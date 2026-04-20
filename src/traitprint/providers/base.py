"""Provider base protocol + factory + credentials resolver."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

AVAILABLE_PROVIDERS = ("anthropic", "openai", "ollama", "openrouter")

# Priority order when auto-detecting (cheapest-local-first: Ollama > Anthropic
# > OpenAI > OpenRouter). The ordering matters only when multiple keys are
# present and the user hasn't picked one.
_DETECT_ORDER = ("ollama", "anthropic", "openai", "openrouter")


class LLMError(RuntimeError):
    """Raised when a provider call fails."""


class ProviderNotConfigured(LLMError):  # noqa: N818  (public name, stable API)
    """Raised when a provider has no credentials available."""


@dataclass
class LLMResponse:
    """Normalized response from any provider."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str

    @property
    def cost_usd(self) -> float:
        """Estimated USD cost for this call.

        Uses a small built-in price table. Unknown models cost $0.00 so the
        CLI can still show a usage line without raising.
        """
        from traitprint.providers.pricing import estimate_cost

        return estimate_cost(
            self.provider, self.model, self.input_tokens, self.output_tokens
        )


class LLMProvider(Protocol):
    """Minimal provider interface."""

    name: str
    model: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Send a single-turn chat completion and return normalized response."""
        ...


def _credentials_path() -> Path:
    """Return the path to the BYOK credentials file (~/.traitprint/.credentials)."""
    return Path.home() / ".traitprint" / ".credentials"


def load_credentials(path: Path | None = None) -> dict[str, str]:
    """Load BYOK credentials from a JSON file.

    The file is optional. If missing or malformed, returns an empty dict.
    Values in this file are *supplements* to environment variables — env
    always wins so that CI and one-off runs work without editing the file.
    """
    p = path or _credentials_path()
    if not p.is_file():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Normalize keys to lower-case for case-insensitive lookup.
    return {str(k).lower(): str(v) for k, v in data.items() if v}


def _resolve_key(env_name: str, cred_key: str, creds: dict[str, str]) -> str | None:
    """Look up a credential first in env, then in the credentials file."""
    return os.environ.get(env_name) or creds.get(cred_key)


def provider_from_name(
    name: str,
    *,
    model: str | None = None,
    credentials: dict[str, str] | None = None,
) -> LLMProvider:
    """Return a configured provider by name.

    Raises ``ProviderNotConfigured`` when required credentials are missing.
    """
    name = name.lower().strip()
    if name not in AVAILABLE_PROVIDERS:
        raise LLMError(
            f"Unknown provider '{name}'. Supported: {', '.join(AVAILABLE_PROVIDERS)}"
        )

    creds = credentials if credentials is not None else load_credentials()

    if name == "anthropic":
        from traitprint.providers.anthropic import AnthropicProvider

        api_key = _resolve_key("ANTHROPIC_API_KEY", "anthropic_api_key", creds)
        if not api_key:
            raise ProviderNotConfigured(
                "Anthropic requires ANTHROPIC_API_KEY (env) or "
                "anthropic_api_key in .credentials"
            )
        return AnthropicProvider(api_key=api_key, model=model)

    if name == "openai":
        from traitprint.providers.openai import OpenAIProvider

        api_key = _resolve_key("OPENAI_API_KEY", "openai_api_key", creds)
        if not api_key:
            raise ProviderNotConfigured(
                "OpenAI requires OPENAI_API_KEY (env) or "
                "openai_api_key in .credentials"
            )
        return OpenAIProvider(api_key=api_key, model=model)

    if name == "openrouter":
        from traitprint.providers.openrouter import OpenRouterProvider

        api_key = _resolve_key("OPENROUTER_API_KEY", "openrouter_api_key", creds)
        if not api_key:
            raise ProviderNotConfigured(
                "OpenRouter requires OPENROUTER_API_KEY (env) or "
                "openrouter_api_key in .credentials"
            )
        return OpenRouterProvider(api_key=api_key, model=model)

    if name == "ollama":
        from traitprint.providers.ollama import OllamaProvider

        # Ollama has no key — host defaults to http://localhost:11434.
        host = (
            os.environ.get("OLLAMA_HOST")
            or creds.get("ollama_host")
            or "http://localhost:11434"
        )
        return OllamaProvider(host=host, model=model)

    raise LLMError(f"Provider '{name}' not implemented")  # pragma: no cover


def detect_provider(
    preferred: str | None = None,
    *,
    model: str | None = None,
    credentials: dict[str, str] | None = None,
) -> LLMProvider:
    """Return the first configured provider.

    If ``preferred`` is passed, it's tried first (and raises if it isn't
    configured, so users get a helpful error when they asked for a
    specific provider). Otherwise, providers are tried in ``_DETECT_ORDER``.
    """
    creds = credentials if credentials is not None else load_credentials()

    if preferred:
        return provider_from_name(preferred, model=model, credentials=creds)

    last_error: LLMError | None = None
    for name in _DETECT_ORDER:
        try:
            return provider_from_name(name, model=model, credentials=creds)
        except ProviderNotConfigured as exc:
            last_error = exc
            continue

    hint = (
        "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY — "
        "or run Ollama locally (http://localhost:11434)."
    )
    raise ProviderNotConfigured(
        f"No LLM provider configured. {hint}"
        + (f" Last error: {last_error}" if last_error else "")
    )
