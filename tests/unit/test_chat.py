"""Unit tests for the ChatAgent (Layer 3)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from agents.chat import ChatAgent, TOOL_SPECS
from agents.providers import LLMRouter
from core.portfolio import Portfolio
from tests.fixtures.llm import FakeLLMClient


def _build_components(policy=None):
    from core.portfolio import Position
    pf = Portfolio(starting_equity=Decimal("100"))
    pf.positions["B:BTC"] = Position(
        sleeve="B", symbol="BTC", side="long",
        notional_usdc=Decimal("10"), risk_usdc=Decimal("0.5"),
        entry_ts=0, entry_price=Decimal("50000"),
        stop_price=Decimal("49000"), tp_price=Decimal("51500"),
    )
    return {
        "portfolio": pf,
        "policy": policy or {"version": "1.0.0", "global_risk": {"per_trade_risk_pct": 1.0},
                              "evaluator_address": "0x" + "a" * 40},
        "cmc": type("C", (), {"_x402_spend_today_usdc": Decimal("0.10")})(),
    }


def _build_router(llm: FakeLLMClient | None) -> LLMRouter:
    if llm is None:
        return LLMRouter(config={"default": "anthropic",
                                  "providers": {"anthropic": {"base": "https://x", "key": ""}},
                                  "agents": {"chat": {"provider": "anthropic", "model": "m"}}})
    r = LLMRouter(config={"default": "fake",
                          "providers": {"fake": {"base": "x", "key": "k"}},
                          "agents": {"chat": {"provider": "fake", "model": "m"}}})
    r._cache["fake"] = llm
    return r


def test_tool_specs_has_9_tools():
    names = [s.name for s in TOOL_SPECS]
    assert "get_pnl_summary" in names
    assert "list_recent_trades" in names
    assert "list_open_positions" in names
    assert "recommend_risk_change" in names
    assert "create_token" in names
    assert "list_skills" in names
    assert "enable_skill" in names
    assert "disable_skill" in names
    assert "sign_new_policy" in names


def test_tool_dispatcher_dispatches_known_tool(tmp_path):
    comp = _build_components()
    ca = ChatAgent(components=comp, router=_build_router(None), decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("get_pnl_summary", {}))
    assert isinstance(r, dict)
    assert "equity" in r


def test_tool_dispatcher_rejects_unknown(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("definitely_not_a_tool", {}))
    assert "error" in r


def test_recommend_risk_change_does_not_write(tmp_path):
    comp = _build_components()
    ca = ChatAgent(components=comp, router=_build_router(None), decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("recommend_risk_change", {"key": "per_trade_risk_pct", "value": 0.5,
                                                                 "reason": "wants tighter"}))
    assert r["recommendation"]["key"] == "per_trade_risk_pct"
    assert r["recommendation"]["tightening"] is True
    assert "Setup" in r["apply_via"]
    assert comp["policy"]["global_risk"]["per_trade_risk_pct"] == 1.0


def test_create_token_routes_through_dashboard(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("create_token", {"name": "Mooncoin", "symbol": "MOON", "supply": 1_000_000}))
    assert r["action"] == "create_token"
    assert "/api/tokens/deploy" in r["dispatch_via"]


def test_list_open_positions_returns_list(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("list_open_positions", {}))
    assert isinstance(r, list)
    assert any(p["symbol"] == "BTC" for p in r)


def test_list_recent_trades_empty(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    r = asyncio_run(ca.dispatch_tool("list_recent_trades", {"n": 5}))
    assert r == []


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_chat_disabled_emits_banner(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    events = []
    async for ev in ca.chat("hi"):
        events.append(ev)
    assert any(e.type == "banner" for e in events)
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_chat_enabled_streams_text(tmp_path):
    fake = FakeLLMClient(stream_chunks=["Hello, ", "BNB ", "Agent."])
    ca = ChatAgent(components=_build_components(), router=_build_router(fake),
                   decision_log=tmp_path / "d.jsonl")
    events = []
    async for ev in ca.chat("hi"):
        events.append(ev)
    text = "".join(e.text for e in events if e.type == "delta")
    assert text == "Hello, BNB Agent."


def test_recent_returns_list(tmp_path):
    ca = ChatAgent(components=_build_components(), router=_build_router(None),
                   decision_log=tmp_path / "d.jsonl")
    assert ca.recent(5) == []
