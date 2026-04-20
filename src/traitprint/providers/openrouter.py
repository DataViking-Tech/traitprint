"""OpenRouter adapter (OpenAI-compatible chat completions)."""

from __future__ import annotations

import httpx

from traitprint.providers.base import LLMError, LLMResponse

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    """OpenRouter provider — proxies many vendors via an OpenAI-style API."""

    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or API_URL
        self.timeout = timeout

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://traitprint.com",
            "X-Title": "Traitprint",
        }

        try:
            resp = httpx.post(
                self.base_url, json=payload, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"OpenRouter returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        choices = data.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            text = message.get("content") or ""

        usage = data.get("usage") or {}
        return LLMResponse(
            content=text,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=data.get("model") or self.model,
            provider=self.name,
        )
