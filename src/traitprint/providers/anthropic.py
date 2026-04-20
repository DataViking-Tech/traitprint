"""Anthropic Messages API adapter."""

from __future__ import annotations

import httpx

from traitprint.providers.base import LLMError, LLMResponse

DEFAULT_MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider:
    """Anthropic Messages API provider."""

    name = "anthropic"

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
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        try:
            resp = httpx.post(
                self.base_url, json=payload, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"Anthropic returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        # Messages API: content is a list of blocks; pull text from the first
        # text block we find.
        content_blocks = data.get("content") or []
        text = ""
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break

        usage = data.get("usage") or {}
        return LLMResponse(
            content=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=data.get("model") or self.model,
            provider=self.name,
        )
