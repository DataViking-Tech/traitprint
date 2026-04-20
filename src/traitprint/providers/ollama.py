"""Ollama adapter (local LLM via /api/chat)."""

from __future__ import annotations

import httpx

from traitprint.providers.base import LLMError, LLMResponse

DEFAULT_MODEL = "llama3.1"


class OllamaProvider:
    """Ollama provider — talks to a locally-running Ollama server."""

    name = "ollama"

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str | None = None,
        *,
        timeout: float = 300.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Ollama request failed: {exc}. Is the server running at {self.host}?"
            ) from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"Ollama returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        message = data.get("message") or {}
        text = message.get("content") or ""

        return LLMResponse(
            content=text,
            # Ollama reports eval counts; map them to input/output for parity.
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            model=data.get("model") or self.model,
            provider=self.name,
        )
