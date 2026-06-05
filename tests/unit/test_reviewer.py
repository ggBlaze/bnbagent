"""Unit tests for the TradeReviewer (Layer 2)."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agents.providers import LLMRouter
from agents.reviewer import TradeReviewer, ReviewVerdict
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


@pytest.mark.asyncio
async def test_disabled_llm_passes_trade(tmp_path):
    router = _build_router(None)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5})
    assert v.allow is True
    assert v.source == "no_reviewer"


@pytest.mark.asyncio
async def test_low_confidence_veto(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": True, "confidence": 0.5, "reason": "meh"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5})
    assert v.allow is False
    assert v.source == "low_confidence"


@pytest.mark.asyncio
async def test_llm_says_no_respected(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": False, "confidence": 0.3, "reason": "looks bad"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5})
    assert v.allow is False
    # explicit veto, not the low_confidence downgrade
    assert v.source == "llm"


@pytest.mark.asyncio
async def test_heuristic_overrides_llm_allow(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": True, "confidence": 0.99, "reason": "looks great"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    # win_rate below the 0.20 threshold → heuristic veto must fire
    v = await rev.review(_prop(), {"win_rate_ewma": 0.15, "loss_cooldown_active": False,
                                    "policy_max_dd_pct": 100, "sleeve_dd_pct": 0})
    assert v.allow is False
    assert v.source == "heuristic_veto"


@pytest.mark.asyncio
async def test_loss_cooldown_veto(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": True, "confidence": 0.9, "reason": "ok"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5, "loss_cooldown_active": True,
                                    "policy_max_dd_pct": 100, "sleeve_dd_pct": 0})
    assert v.allow is False
    assert "cooldown" in v.reason


@pytest.mark.asyncio
async def test_recent_5_loss_streak_veto(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": True, "confidence": 0.9, "reason": "ok"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    recent = [{"pnl_pct": -1.0} for _ in range(5)]
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5, "loss_cooldown_active": False,
                                    "policy_max_dd_pct": 100, "sleeve_dd_pct": 0,
                                    "recent_trades": recent})
    assert v.allow is False
    assert "4/5" in v.reason or "5/5" in v.reason


@pytest.mark.asyncio
async def test_llm_timeout_falls_back_to_heuristic(tmp_path):
    class SlowFake(FakeLLMClient):
        async def complete(self, *a, **kw):
            self.calls.append({"a": a, "kw": kw})
            await asyncio.sleep(2.0)
            return json.dumps({"allow": True, "confidence": 0.9, "reason": "slow"})
    slow = SlowFake()
    router = _build_router(slow)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.5, "loss_cooldown_active": False,
                                    "policy_max_dd_pct": 100, "sleeve_dd_pct": 0})
    assert v.source == "llm_timeout"
    assert v.allow is True  # heuristic allows when no signals fire


@pytest.mark.asyncio
async def test_high_confidence_passes(tmp_path):
    fake = FakeLLMClient(scripts=[json.dumps({"allow": True, "confidence": 0.95, "reason": "good"})])
    router = _build_router(fake)
    rev = TradeReviewer(sleeve="B", components={}, router=router, decision_log=tmp_path / "d.jsonl")
    v = await rev.review(_prop(), {"win_rate_ewma": 0.55, "loss_cooldown_active": False,
                                    "policy_max_dd_pct": 100, "sleeve_dd_pct": 0})
    assert v.allow is True
    assert v.source == "llm"


def test_recent_returns_list(tmp_path):
    rev = TradeReviewer(sleeve="B", components={}, router=_build_router(None),
                        decision_log=tmp_path / "d.jsonl")
    assert rev.recent(5) == []
