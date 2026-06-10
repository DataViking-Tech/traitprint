"""Tests for BYOK LLM provider adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from traitprint.providers import (
    LLMError,
    ProviderNotConfigured,
    detect_provider,
    load_credentials,
    provider_from_name,
)
from traitprint.providers.anthropic import AnthropicProvider
from traitprint.providers.base import LLMResponse
from traitprint.providers.ollama import OllamaProvider
from traitprint.providers.openai import OpenAIProvider
from traitprint.providers.openrouter import OpenRouterProvider
from traitprint.providers.pricing import estimate_cost

# ---- fake httpx transport -------------------------------------------------


def _mock_transport(expected_url: str, response_body: dict, status: int = 200):
    """Build an httpx MockTransport that asserts URL and returns a JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path in expected_url or str(request.url).startswith(
            expected_url
        ), f"Unexpected URL: {request.url}"
        return httpx.Response(status, json=response_body)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests don't pick up real API keys from the host env."""
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(key, raising=False)


# ---- credentials loader ---------------------------------------------------


class TestCredentials:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert load_credentials(tmp_path / "nope.json") == {}

    def test_malformed_file_returns_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / ".credentials"
        p.write_text("not json", encoding="utf-8")
        assert load_credentials(p) == {}

    def test_keys_are_lowercased(self, tmp_path: Path) -> None:
        p = tmp_path / ".credentials"
        p.write_text(json.dumps({"ANTHROPIC_API_KEY": "sk-test"}), encoding="utf-8")
        assert load_credentials(p) == {"anthropic_api_key": "sk-test"}


# ---- factory --------------------------------------------------------------


class TestProviderFactory:
    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(LLMError, match="Unknown provider"):
            provider_from_name("cohere")

    def test_anthropic_requires_key(self) -> None:
        with pytest.raises(ProviderNotConfigured, match="ANTHROPIC_API_KEY"):
            provider_from_name("anthropic", credentials={})

    def test_openai_requires_key(self) -> None:
        with pytest.raises(ProviderNotConfigured, match="OPENAI_API_KEY"):
            provider_from_name("openai", credentials={})

    def test_openrouter_requires_key(self) -> None:
        with pytest.raises(ProviderNotConfigured, match="OPENROUTER_API_KEY"):
            provider_from_name("openrouter", credentials={})

    def test_ollama_has_no_required_key(self) -> None:
        p = provider_from_name("ollama", credentials={})
        assert p.name == "ollama"
        assert p.host == "http://localhost:11434"  # type: ignore[attr-defined]

    def test_env_takes_precedence_over_creds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        p = provider_from_name(
            "anthropic", credentials={"anthropic_api_key": "sk-from-file"}
        )
        assert p.api_key == "sk-from-env"  # type: ignore[attr-defined]

    def test_creds_used_when_no_env(self) -> None:
        p = provider_from_name(
            "anthropic", credentials={"anthropic_api_key": "sk-from-file"}
        )
        assert p.api_key == "sk-from-file"  # type: ignore[attr-defined]

    def test_explicit_model_override(self) -> None:
        p = provider_from_name(
            "anthropic",
            model="claude-opus-4-7",
            credentials={"anthropic_api_key": "x"},
        )
        assert p.model == "claude-opus-4-7"


# ---- detect_provider -------------------------------------------------------


class TestDetectProvider:
    def test_preferred_respected(self) -> None:
        p = detect_provider(
            preferred="openai",
            credentials={"openai_api_key": "sk-x"},
        )
        assert p.name == "openai"

    def test_preferred_missing_raises(self) -> None:
        with pytest.raises(ProviderNotConfigured):
            detect_provider(preferred="anthropic", credentials={})

    def test_no_providers_raises(self) -> None:
        # Default-host Ollama is not a signal (D11): with no keys and no
        # OLLAMA_HOST, auto-detect raises instead of constructing a doomed
        # localhost Ollama provider.
        with pytest.raises(ProviderNotConfigured, match="No LLM provider"):
            detect_provider(credentials={})

    def test_anthropic_wins_when_ollama_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate Ollama being first in priority by just asking for anthropic directly.
        p = detect_provider(
            preferred="anthropic", credentials={"anthropic_api_key": "sk-a"}
        )
        assert p.name == "anthropic"

    def test_cloud_key_only_returns_cloud_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D11: with only ANTHROPIC_API_KEY set (no OLLAMA_HOST), auto-detect
        # must skip Ollama and return the configured cloud provider.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
        p = detect_provider(credentials={})
        assert p.name == "anthropic"

    def test_cloud_key_in_credentials_returns_cloud_provider(self) -> None:
        p = detect_provider(credentials={"anthropic_api_key": "sk-a"})
        assert p.name == "anthropic"

    def test_ollama_host_env_is_a_signal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
        p = detect_provider(credentials={})
        assert p.name == "ollama"
        assert p.host == "http://gpu-box:11434"  # type: ignore[attr-defined]

    def test_ollama_host_in_credentials_is_a_signal(self) -> None:
        p = detect_provider(credentials={"ollama_host": "http://gpu-box:11434"})
        assert p.name == "ollama"
        assert p.host == "http://gpu-box:11434"  # type: ignore[attr-defined]

    def test_explicit_ollama_signal_keeps_local_first_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Cheapest-local-first still applies when Ollama is explicit.
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
        p = detect_provider(credentials={"anthropic_api_key": "sk-a"})
        assert p.name == "ollama"

    def test_preferred_ollama_works_without_signal(self) -> None:
        # `--provider ollama` stays unchanged: no signal needed, default host.
        p = detect_provider(preferred="ollama", credentials={})
        assert p.name == "ollama"
        assert p.host == "http://localhost:11434"  # type: ignore[attr-defined]


# ---- provider HTTP mocking -----------------------------------------------


def _install_mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Patch httpx.post to use a MockTransport via a temporary client."""
    transport = httpx.MockTransport(handler)

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr("httpx.post", fake_post)


class TestAnthropicProvider:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                    "usage": {"input_tokens": 42, "output_tokens": 7},
                },
            )

        _install_mock(monkeypatch, handler)

        provider = AnthropicProvider(api_key="sk-test")
        resp = provider.complete(system="sys", user="hi")

        assert resp.content == '{"ok": true}'
        assert resp.input_tokens == 42
        assert resp.output_tokens == 7
        assert resp.model == "claude-sonnet-4-6"
        assert resp.provider == "anthropic"
        assert captured["headers"].get("x-api-key") == "sk-test"
        assert captured["headers"].get("anthropic-version") == "2023-06-01"
        assert captured["body"]["system"] == "sys"
        assert captured["body"]["messages"][0]["content"] == "hi"

    def test_http_error_raises_llm_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text='{"error":"invalid key"}')

        _install_mock(monkeypatch, handler)

        with pytest.raises(LLMError, match="401"):
            AnthropicProvider(api_key="bad").complete(system="", user="")


class TestOpenAIProvider:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            assert body["response_format"]["type"] == "json_object"
            return httpx.Response(
                200,
                json={
                    "model": "gpt-4o-mini",
                    "choices": [
                        {"message": {"content": '{"ok":true}', "role": "assistant"}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            )

        _install_mock(monkeypatch, handler)
        resp = OpenAIProvider(api_key="sk-x").complete(system="", user="hi")
        assert resp.content == '{"ok":true}'
        assert resp.input_tokens == 10
        assert resp.output_tokens == 3
        assert resp.provider == "openai"


class TestOpenRouterProvider:
    def test_sends_identification_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={
                    "model": "anthropic/claude-sonnet-4",
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        _install_mock(monkeypatch, handler)
        OpenRouterProvider(api_key="sk-or").complete(system="", user="")
        assert captured["headers"]["authorization"] == "Bearer sk-or"
        assert captured["headers"]["x-title"] == "Traitprint"


class TestOllamaProvider:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            body = json.loads(request.content.decode("utf-8"))
            assert body["format"] == "json"
            return httpx.Response(
                200,
                json={
                    "model": "llama3.1",
                    "message": {"role": "assistant", "content": '{"ok":1}'},
                    "prompt_eval_count": 50,
                    "eval_count": 12,
                },
            )

        _install_mock(monkeypatch, handler)
        resp = OllamaProvider().complete(system="", user="")
        assert resp.content == '{"ok":1}'
        assert resp.input_tokens == 50
        assert resp.output_tokens == 12

    def test_connect_error_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        _install_mock(monkeypatch, handler)

        with pytest.raises(LLMError, match="Is the server running"):
            OllamaProvider(host="http://localhost:9999").complete(system="", user="")


# ---- pricing --------------------------------------------------------------


class TestPricing:
    def test_known_model(self) -> None:
        cost = estimate_cost("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(3.00 + 15.00)

    def test_unknown_model_uses_default(self) -> None:
        cost = estimate_cost("anthropic", "claude-new-model", 1_000_000, 0)
        assert cost == pytest.approx(3.00)

    def test_ollama_is_free(self) -> None:
        assert estimate_cost("ollama", "llama3.1", 999_999, 999_999) == 0.0


# ---- LLMResponse ----------------------------------------------------------


class TestLLMResponse:
    def test_cost_estimate_reads_pricing(self) -> None:
        r = LLMResponse(
            content="x",
            input_tokens=2_000_000,
            output_tokens=0,
            model="gpt-4o-mini",
            provider="openai",
        )
        assert r.cost_usd == pytest.approx(0.30)
