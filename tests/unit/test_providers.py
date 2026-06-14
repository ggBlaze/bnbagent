"""Unit tests for the LLM provider layer.

5 smoke tests — one per adapter — using respx to mock httpx. The adapters
all use httpx.AsyncClient under the hood, so respx intercepts cleanly.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

from agents.providers import (
    AnthropicClient, OpenAIClient, OpenRouterClient, GenericOAICompatClient,
    LocalLLMClient, PROVIDERS, build_provider, load_providers_config, LLMRouter,
)


# --- env substitution -------------------------------------------------------

def test_resolve_env_substitutes_vars(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    from agents.providers import _resolve_env
    assert _resolve_env("$FOO") == "bar"
    assert _resolve_env("${FOO}") == "bar"
    assert _resolve_env({"k": "$FOO"}) == {"k": "bar"}
    assert _resolve_env([1, "$FOO"]) == [1, "bar"]
    assert _resolve_env(42) == 42


def test_resolve_env_returns_empty_for_unset(monkeypatch):
    monkeypatch.delenv("NOT_SET_VAR_X", raising=False)
    from agents.providers import _resolve_env
    assert _resolve_env("$NOT_SET_VAR_X") == ""


# --- provider registry ------------------------------------------------------

def test_providers_registry_has_all_6():
    # v2.1.5: added 'minimax' (OpenAI-compatible endpoint at api.minimaxi.chat).
    assert set(PROVIDERS.keys()) == {"anthropic", "openai", "openrouter", "oai_compat", "minimax", "local"}


def test_build_provider_raises_on_unknown():
    with pytest.raises(ValueError, match="unknown provider"):
        build_provider("nope", {})


def test_build_provider_anthropic_requires_key():
    with pytest.raises(ValueError, match="requires an API key"):
        build_provider("anthropic", {"base": "https://x", "key": ""})


def test_build_provider_local_no_key_ok():
    c = build_provider("local", {"base": "http://127.0.0.1:8080", "key": ""})
    assert c.name == "local"


# --- AnthropicClient --------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_complete_200():
    body = {
        "model": "claude-3-5-haiku-latest", "max_tokens": 64, "temperature": 0.2,
        "messages": [{"role": "user", "content": "hi"}],
    }
    resp = {"content": [{"type": "text", "text": "hello world"}]}
    with respx.mock(assert_all_called=True) as m:
        m.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=resp))
        c = AnthropicClient(base="https://api.anthropic.com", api_key="sk-test")
        out = await c.complete([{"role": "user", "content": "hi"}], model="claude-3-5-haiku-latest")
        assert out == "hello world"


@pytest.mark.asyncio
async def test_anthropic_stream_yields_deltas():
    sse_lines = [
        "data: " + json.dumps({"type": "content_block_start"}),
        "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "foo "}}),
        "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "bar"}}),
    ]
    with respx.mock() as m:
        m.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content="\n".join(sse_lines),
                                        headers={"content-type": "text/event-stream"}))
        c = AnthropicClient(base="https://api.anthropic.com", api_key="sk-test")
        chunks = []
        async for ch in c.stream([{"role": "user", "content": "hi"}]):
            chunks.append(ch)
        assert "".join(chunks) == "foo bar"


# --- OpenAIClient -----------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_complete_200():
    resp = {"choices": [{"message": {"content": "hi from openai"}}]}
    with respx.mock() as m:
        m.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=resp))
        c = OpenAIClient(base="https://api.openai.com", api_key="sk-test")
        out = await c.complete([{"role": "user", "content": "hi"}])
        assert out == "hi from openai"


@pytest.mark.asyncio
async def test_openai_stream_yields_deltas():
    sse = "\n".join([
        "data: " + json.dumps({"choices": [{"delta": {"content": "a"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "b"}}]}),
        "data: [DONE]",
    ])
    with respx.mock() as m:
        m.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse,
                                        headers={"content-type": "text/event-stream"}))
        c = OpenAIClient(base="https://api.openai.com", api_key="sk-test")
        chunks = []
        async for ch in c.stream([{"role": "user", "content": "hi"}]):
            chunks.append(ch)
        assert "".join(chunks) == "ab"


# --- OpenRouterClient -------------------------------------------------------

@pytest.mark.asyncio
async def test_openrouter_sends_required_headers():
    resp = {"choices": [{"message": {"content": "ok"}}]}
    with respx.mock(assert_all_called=True) as m:
        route = m.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=resp))
        c = OpenRouterClient(base="https://openrouter.ai/api", api_key="sk-or-test")
        await c.complete([{"role": "user", "content": "hi"}])
        sent = route.calls[0].request
        assert sent.headers.get("Authorization") == "Bearer sk-or-test"
        assert sent.headers.get("HTTP-Referer")  # non-empty
        assert sent.headers.get("X-Title")       # non-empty


# --- GenericOAICompatClient -------------------------------------------------

@pytest.mark.asyncio
async def test_generic_oai_compat_smoke():
    resp = {"choices": [{"message": {"content": "mistral ok"}}]}
    with respx.mock() as m:
        m.post("https://mistral.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=resp))
        c = GenericOAICompatClient(base="https://mistral.example", api_key="k")
        out = await c.complete([{"role": "user", "content": "hi"}])
        assert out == "mistral ok"


# --- LocalLLMClient ---------------------------------------------------------

@pytest.mark.asyncio
async def test_local_smoke():
    resp = {"choices": [{"message": {"content": "local ok"}}]}
    with respx.mock() as m:
        m.post("http://127.0.0.1:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=resp))
        c = LocalLLMClient(base="http://127.0.0.1:8080", api_key="")
        out = await c.complete([{"role": "user", "content": "hi"}])
        assert out == "local ok"


# --- LLMRouter --------------------------------------------------------------

def test_router_status_no_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Build a self-contained config in tmp so we don't depend on the
    # shipping providers.yaml being on disk in CI
    cfg = {
        "default": "openrouter",
        "providers": {
            "openrouter": {"base": "https://openrouter.ai/api", "key": "$OPENROUTER_API_KEY"},
        },
        "agents": {"chat": {"provider": "openrouter", "model": "x"}},
    }
    r = LLMRouter(config=cfg)
    s = r.status()
    assert "providers" in s and "agents" in s
    assert s["agents"]["chat"]["enabled"] is False
    assert "no api key" in s["agents"]["chat"]["reason"]


def test_router_status_with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = {
        "default": "openrouter",
        "providers": {
            "openrouter": {"base": "https://openrouter.ai/api", "key": "$OPENROUTER_API_KEY"},
        },
        "agents": {
            "advisor": {"provider": "openrouter", "model": "x"},
            "chat":    {"provider": "openrouter", "model": "x"},
        },
    }
    r = LLMRouter(config=cfg)
    s = r.status()
    assert s["agents"]["advisor"]["enabled"] is True
    assert s["agents"]["chat"]["enabled"] is True


def test_router_for_agent_returns_disabled_when_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {
        "default": "anthropic",
        "providers": {"anthropic": {"base": "https://api.anthropic.com", "key": "$ANTHROPIC_API_KEY"}},
        "agents":   {"chat": {"provider": "anthropic", "model": "x"}},
    }
    r = LLMRouter(config=cfg)
    # _resolve_env first to substitute the env var
    from agents.providers import _resolve_env
    r.config = _resolve_env(cfg)
    ar = r.for_agent("chat")
    assert ar.enabled is False
    assert ar.client is None
