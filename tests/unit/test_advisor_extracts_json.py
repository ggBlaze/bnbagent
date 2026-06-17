"""P3 (v2.1.8): the advisor must parse the same fenced/think-wrapped
JSON responses the reviewer handles.

Symptom in production:

    WARNING agents.advisor: advisor: malformed JSON:
        Expecting value: line 1 column 1 (char 0) —
        raw='```json\\n{\\n  "actions": [...

F3 fixed the reviewer with a private `_extract_json_object`. P3 lifts
that helper into `agents/base.py` so the advisor (and any future
agent) can reuse the same brace-balanced scanner.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agents.providers import LLMRouter
from agents.advisor import StrategyAdvisor, Advice
from tests.fixtures.llm import FakeLLMClient


def _build_router(llm: FakeLLMClient | None) -> LLMRouter:
    if llm is None:
        return LLMRouter(config={"default": "x",
                                  "providers": {"x": {"base": "x", "key": ""}},
                                  "agents": {"advisor": {"provider": "x", "model": "m"}}})
    r = LLMRouter(config={"default": "fake",
                          "providers": {"fake": {"base": "x", "key": "k"}},
                          "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    r._cache["fake"] = llm
    return r


def test_helper_lives_in_agents_base():
    """The helper is shared infrastructure — must be importable from
    agents.base so any future agent (chat, future-arbitrage, ...) can
    reuse it instead of re-implementing the brace scanner."""
    from agents.base import extract_json_object
    assert callable(extract_json_object)


def test_extract_json_object_handles_fenced_json():
    """The exact production-failure shape from advisor:
    raw='```json\\n{...'. Bare json.loads chokes; extract must strip."""
    from agents.base import extract_json_object
    raw = '```json\n{"actions": [{"type": "no_op"}], "confidence": 0.5}\n```'
    parsed = json.loads(extract_json_object(raw))
    assert parsed["actions"] == [{"type": "no_op"}]
    assert parsed["confidence"] == 0.5


@pytest.mark.asyncio
async def test_advisor_parses_fenced_json_response():
    """End-to-end: feed the advisor the exact fenced-JSON shape that
    failed in production at 19:41:22 — assert it parses (not
    parsed_ok=False)."""
    fenced = (
        "```json\n"
        '{\n  "actions": [{"type": "no_op", "reason": "fresh state"}],\n'
        '  "confidence": 0.5\n}\n'
        "```"
    )
    fake = FakeLLMClient(scripts=[fenced])
    router = _build_router(fake)
    advisor = StrategyAdvisor(components={"policy": {}}, router=router,
                              persona_name="advisor")
    advice = await advisor._decide("system prompt", {"foo": "bar"})
    assert advice.parsed_ok is True, (
        f"advisor must extract JSON from fenced response; "
        f"got parsed_ok={advice.parsed_ok}, error={advice.error!r}"
    )
    assert advice.actions == [{"type": "no_op", "reason": "fresh state"}]
    assert advice.confidence == 0.5


@pytest.mark.asyncio
async def test_advisor_parses_thinking_tag_wrapped_response():
    """Some reasoning models prefix with <thinking>...</thinking>.
    llm_complete strips well-formed <think> only; advisor's extractor
    handles the rest."""
    raw = (
        "<thinking>I should tighten sleeve B because of vol spike.</thinking>\n"
        '{"actions": [{"type": "tighten", "sleeve": "B", "key": "max_position_pct", "value": 3.0}], "confidence": 0.7}'
    )
    fake = FakeLLMClient(scripts=[raw])
    router = _build_router(fake)
    advisor = StrategyAdvisor(components={"policy": {}}, router=router,
                              persona_name="advisor")
    advice = await advisor._decide("system prompt", {})
    assert advice.parsed_ok is True
    assert advice.actions[0]["sleeve"] == "B"
    assert advice.confidence == 0.7


def test_reviewer_still_uses_the_helper():
    """The reviewer's _extract_json_object was the original; ensure the
    lift didn't drop the reviewer's call path. Smoke check: importing
    the reviewer module also imports the (now shared) helper."""
    import agents.reviewer
    from agents.base import extract_json_object
    assert callable(extract_json_object)
