"""F3: TradeReviewer must parse JSON even when the model wraps it in
think tags, code fences, or surrounding prose.

The production failure was:

    WARN agents.reviewer: reviewer[A] bad JSON:
        Expecting value: line 1 column 1 (char 0)

`Expecting value: line 1 column 1 (char 0)` means raw[0] is not a JSON
opening character. agents/base.py:llm_complete already strips
<think>...</think> blocks (well-formed, lowercase `think`), but the
response can still arrive with:

  - an unclosed <think> block (no </think>),
  - a different tag (<thinking>, <reasoning>, <reason>),
  - a markdown fence (```json ... ``` or just ``` ... ```),
  - prose before the JSON ("Sure, here's my verdict: {...}"),
  - multiple JSON objects (only the first is the verdict).

The reviewer's bare json.loads(raw) chokes on any of these, falls into
the "llm_error" branch, and the trade gets a heuristic verdict. With a
healthy EWMA the heuristic allows, which silently green-lights trades the
LLM intended to veto.

The fix is a robust private extractor: look for the first balanced JSON
object in the cleaned string and parse it. If none is found, fall back
to the heuristic as before (no behavior change for the truly broken
case).
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agents.providers import LLMRouter
from agents.reviewer import TradeReviewer
from core.risk import ProposedTrade
from tests.fixtures.llm import FakeLLMClient


def _build_router(llm: FakeLLMClient | None) -> LLMRouter:
    if llm is None:
        return LLMRouter(config={"default": "anthropic",
                                  "providers": {"anthropic": {"base": "https://x", "key": ""}},
                                  "agents": {"reviewer": {"provider": "anthropic", "model": "m"}}})
    r = LLMRouter(config={"default": "fake",
                          "providers": {"fake": {"base": "x", "key": "k"}},
                          "agents": {"reviewer": {"provider": "fake", "model": "m"}}})
    r._cache["fake"] = llm
    return r


def _prop() -> ProposedTrade:
    return ProposedTrade(sleeve="B", symbol="BTC", side="long",
                         notional_usdc=Decimal("10"), risk_usdc=Decimal("0.5"))


# Sleeve state that DOES NOT trigger any heuristic veto. If the JSON
# parse succeeds and the LLM said allow, the reviewer should return
# source="llm". If the parse fails and falls to heuristic, source will
# be "llm_error". This is the discriminator across the tests below.
_SAFE_STATE = {
    "win_rate_ewma": 0.55,
    "loss_cooldown_active": False,
    "policy_max_dd_pct": 100,
    "sleeve_dd_pct": 0,
}


@pytest.mark.asyncio
async def test_parses_with_unclosed_think_tag(tmp_path):
    """MiniMax M3 can truncate the <think>...</think> close tag when
    max_tokens is reached mid-thought. llm_complete's regex requires the
    closing tag, so the unclosed block leaks through. The reviewer must
    still find the JSON tail."""
    raw = (
        '<think>Let me weigh the risk-reward...\n'
        'The EWMA looks healthy, so this should be fine.\n'
        '{"allow": true, "confidence": 0.85, "reason": "healthy EWMA"}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    assert v.source == "llm", f"reviewer fell to {v.source!r} — JSON tail was not extracted"
    assert v.allow is True
    assert v.confidence == pytest.approx(0.85)
    assert "EWMA" in v.reason


@pytest.mark.asyncio
async def test_parses_with_thinking_tag_alt_name(tmp_path):
    """Some reasoning models use <thinking>...</thinking> instead of
    <think>...</think>. llm_complete's regex only matches the latter."""
    raw = (
        '<thinking>Considering position size and recent vol...</thinking>\n'
        '{"allow": false, "confidence": 0.91, "reason": "vol spike — wait"}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    # explicit "allow: false" should be respected
    assert v.allow is False
    assert v.source == "llm"
    assert "vol" in v.reason


@pytest.mark.asyncio
async def test_parses_with_markdown_fence(tmp_path):
    """Some models wrap JSON in ```json ... ``` fences when
    response_format isn't honored."""
    raw = (
        'Here is my decision:\n'
        '```json\n'
        '{"allow": true, "confidence": 0.92, "reason": "all clear"}\n'
        '```'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    assert v.source == "llm"
    assert v.allow is True
    assert v.confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_parses_with_prose_prefix(tmp_path):
    """Model may include a sentence before the JSON object."""
    raw = (
        'Sure — given the sleeve state, my verdict is: '
        '{"allow": true, "confidence": 0.80, "reason": "ok"}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    assert v.source == "llm"
    assert v.allow is True


@pytest.mark.asyncio
async def test_takes_first_json_object_when_multiple(tmp_path):
    """If the model emits the verdict twice (e.g. once in a non-standard
    <thinking> block, once after), the extractor takes the first balanced
    object encountered. We use <thinking> (not <think>) so llm_complete's
    strip doesn't pre-eat the first one, making the two-object scenario
    actually reach the reviewer's extractor."""
    raw = (
        '<thinking>I think the answer is '
        '{"allow": false, "confidence": 0.95, "reason": "veto"}\n'
        'wait, maybe reconsider.</thinking>\n'
        '{"allow": true, "confidence": 0.5, "reason": "reconsidered"}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    # First balanced object is the veto inside <thinking>. allow=False
    # is respected, confidence=0.95 is well above the threshold so the
    # source stays "llm" (not "low_confidence").
    assert v.source == "llm"
    assert v.allow is False
    assert v.confidence == pytest.approx(0.95)
    assert v.reason.startswith("veto")


@pytest.mark.asyncio
async def test_unparseable_falls_back_to_heuristic(tmp_path):
    """If no JSON object can be extracted (model rambled, returned XML,
    etc.), the reviewer falls back to the heuristic.

    With a safe sleeve state the heuristic returns allow=True with
    confidence=0.5; review() then wraps that low-confidence allow as
    a low_confidence veto. Both signals — confidence==0.5 (the heuristic
    default) and the eventual veto — confirm the heuristic ran, not the
    LLM."""
    raw = "Sorry I cannot respond in JSON. The market is too unpredictable."
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    # Heuristic confidence (0.5) bubbles through the low-confidence wrap.
    assert v.confidence == pytest.approx(0.5)
    assert v.allow is False
    assert v.source == "low_confidence"


@pytest.mark.asyncio
async def test_handles_nested_json_object(tmp_path):
    """The reason field could itself contain {} characters; the extractor
    must find the outer balanced object, not stop at the first }."""
    raw = (
        '<thinking>complex reasoning</thinking>\n'
        '{"allow": true, "confidence": 0.88, '
        '"reason": "RSI {oversold} bounce expected"}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    assert v.source == "llm"
    assert v.allow is True
    assert "RSI" in v.reason


@pytest.mark.asyncio
async def test_handles_braces_inside_strings(tmp_path):
    """The extractor must track string boundaries; a } inside a string
    literal is not a closing brace."""
    raw = (
        '{"allow": false, "confidence": 0.95, '
        '"reason": "matched pattern \\"} fake close\\""}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router,
                        decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), _SAFE_STATE)
    assert v.source == "llm"
    assert v.allow is False
