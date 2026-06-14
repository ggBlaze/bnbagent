"""Unit tests for agents/base.py — focused on llm_complete's cross-provider
compatibility + the think-block strip that makes reasoning models (M3,
o1, R1) work with the bnbagent.

The strip is the one piece of plumbing that every LLM call goes through;
if it regresses, every reviewer call breaks. Locking its behavior down
with unit tests is cheap insurance."""
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.base import llm_complete  # noqa: E402
from agents.providers import (  # noqa: E402
    AgentRouting,
    GenericOAICompatClient,
    LLMRouter,
    load_providers_config,
)


class FakeLLMClient:
    """A fake LLMClient that returns a configurable string. Implements the
    LLMClient Protocol (only .complete() is needed for llm_complete)."""

    def __init__(self, name: str, response: str | Exception, call_log: list | None = None):
        self.name = name
        self._response = response
        self._call_log = call_log or []
        self.default_model = "fake-model"

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.2,
                        response_format=None, timeout_s=8.0):
        self._call_log.append({
            "messages": messages, "model": model, "max_tokens": max_tokens,
            "temperature": temperature, "response_format": response_format,
            "timeout_s": timeout_s,
        })
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _routing(provider_name: str, client: Any, enabled: bool = True, **kwargs) -> AgentRouting:
    return AgentRouting(
        provider_name=provider_name,
        client=client if enabled else None,
        model=kwargs.get("model", "fake-model"),
        max_tokens=kwargs.get("max_tokens", 1024),
        temperature=kwargs.get("temperature", 0.2),
        enabled=enabled,
        reason=kwargs.get("reason", "ok"),
        timeout_s=kwargs.get("timeout_s", 5.0),
    )


# --- v2.1.5: think-block strip -------------------------------------------

@pytest.mark.asyncio
async def test_llm_complete_strips_leading_think_block():
    """MiniMax M3 (and other reasoning models) emit a <think>...</think>
    block before the actual answer. llm_complete must strip it so the
    reviewer's json.loads() sees clean content."""
    thinky_response = (
        "<think>\nLet me reason about this trade...\n"
        "If EWMA is healthy, this is fine. If not, veto.\n</think>\n\n"
        '{"allow": true, "confidence": 0.82, "reason": "healthy EWMA"}'
    )
    fake = FakeLLMClient("minimax", thinky_response)
    routing = _routing("minimax", fake)
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    assert out == '{"allow": true, "confidence": 0.82, "reason": "healthy EWMA"}', (
        f"think block was not stripped. Got: {out!r}"
    )


@pytest.mark.asyncio
async def test_llm_complete_preserves_clean_output():
    """A non-reasoning model (gpt-4o-mini, claude-3-5-haiku) returns
    content with no think block. llm_complete must pass it through
    unchanged."""
    clean = '{"allow": false, "confidence": 0.95, "reason": "DD breach"}'
    fake = FakeLLMClient("openai", clean)
    routing = _routing("openai", fake)
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    assert out == clean


@pytest.mark.asyncio
async def test_llm_complete_strips_multiple_think_blocks():
    """If a model emits multiple <think> blocks (chain-of-thought in
    pieces), llm_complete strips them all. re.sub replaces all non-
    overlapping matches by default."""
    multi = (
        "<think>first thought</think>"
        "<think>second thought</think>\n"
        '{"allow": true, "confidence": 0.7, "reason": "ok"}'
    )
    fake = FakeLLMClient("minimax", multi)
    routing = _routing("minimax", fake)
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    assert out == '{"allow": true, "confidence": 0.7, "reason": "ok"}'


@pytest.mark.asyncio
async def test_llm_complete_handles_unclosed_think_gracefully():
    """A malformed response (<think> without closing </think>) should
    NOT eat the actual content. Pass the original through."""
    broken = '<think>unclosed reasoning here\n{"allow": true}'
    fake = FakeLLMClient("minimax", broken)
    routing = _routing("minimax", fake)
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    # The regex requires a closing </think>; without one the match fails
    # and the original string is returned unchanged.
    assert "unclosed reasoning" in out
    assert '{"allow": true}' in out


@pytest.mark.asyncio
async def test_llm_complete_returns_empty_when_disabled():
    """When the routing is disabled (no key, no client), llm_complete
    returns "" without calling the LLM. This is the degraded-mode
    behavior the reviewer relies on."""
    fake = FakeLLMClient("minimax", "should not be called")
    routing = _routing("minimax", fake, enabled=False, reason="no api key")
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    assert out == ""
    assert fake._call_log == []  # not called


@pytest.mark.asyncio
async def test_llm_complete_returns_empty_on_exception():
    """If the underlying client throws (network error, 4xx, etc.),
    llm_complete catches it and returns "". The reviewer treats this
    as 'llm_error' and falls back to the heuristic."""
    fake = FakeLLMClient("minimax", ConnectionError("boom"))
    routing = _routing("minimax", fake)
    out = await llm_complete(routing, [{"role": "user", "content": "?"}])
    assert out == ""


# --- v2.1.5: per-routing timeout ----------------------------------------

def test_default_timeout_for_reasoning_model_is_10s():
    from agents.providers import _default_timeout_for_model
    assert _default_timeout_for_model("MiniMax-M3") == 10.0
    assert _default_timeout_for_model("minimax-m3") == 10.0
    assert _default_timeout_for_model("o1-preview") == 10.0
    assert _default_timeout_for_model("o3-mini") == 10.0
    assert _default_timeout_for_model("deepseek-r1") == 10.0


def test_default_timeout_for_fast_chat_model_is_2s():
    from agents.providers import _default_timeout_for_model
    assert _default_timeout_for_model("claude-3-5-haiku-latest") == 2.0
    assert _default_timeout_for_model("gpt-4o-mini") == 2.0
    assert _default_timeout_for_model("gemini-1.5-flash") == 2.0


def test_default_timeout_for_unknown_model_is_5s():
    from agents.providers import _default_timeout_for_model
    assert _default_timeout_for_model("claude-3-5-sonnet") == 5.0
    assert _default_timeout_for_model("gpt-4o") == 5.0
    assert _default_timeout_for_model("") == 5.0
    assert _default_timeout_for_model("random-llm") == 5.0


def test_explicit_timeout_s_in_yaml_overrides_default(tmp_path, monkeypatch):
    """If the operator sets agents.<name>.timeout_s in providers.yaml,
    that value wins over the model-name auto-default."""
    from agents import providers as providers_mod
    cfg_yaml = """
default: minimax
providers:
  minimax: { base: 'https://x', key: 'k', default_model: 'MiniMax-M3' }
agents:
  reviewer:
    provider: minimax
    model: MiniMax-M3
    timeout_s: 25.0
"""
    p = tmp_path / "providers.yaml"
    p.write_text(cfg_yaml)
    cfg = load_providers_config(p)
    router = LLMRouter(cfg)
    r = router.for_agent("reviewer")
    # The reason will be "no api key" or similar because the key
    # is fake, but the timeout_s must be 25.0.
    assert r.timeout_s == 25.0
