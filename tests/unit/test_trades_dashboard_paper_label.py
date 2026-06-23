"""Tests for v2.3.5b: /api/trades must show ALL trades the bot signed,
including real on-chain ones, with the correct is_paper label.

Why this exists:
  Before the fix, two bugs conspired to make /api/trades always empty
  on mainnet:
    1. Position.is_paper defaulted to True and submit_floor_trade()
       never overrode it — so a Position whose on-chain tx actually
       settled was mislabeled as paper.
    2. /api/trades then filtered `is_paper=False` only, so the
       (already-empty) real list was the only one returned.

  After the fix:
    1. submit_floor_trade() stamps is_paper=(status != "submitted")
       on the Position, so a settled on-chain trade is correctly
       marked is_paper=False.
    2. /api/trades returns all trades and surfaces real/paper counts.

This file covers:
  - Position.is_paper defaults to True (preserved for paper mode)
  - A Position created via submit_floor_trade with a settled tx is
    is_paper=False
  - A Position created via submit_floor_trade with a failed tx
    falls back to is_paper=True
  - The /api/trades endpoint returns all trades (real + paper)
  - /api/trades reports real_trades_count + paper_trades_count
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.portfolio import Position


def test_position_is_paper_defaults_true():
    """Backwards-compat: bare Position() is paper by default. Code
    that doesn't set is_paper gets the same behaviour as v2.2.0."""
    p = Position(
        sleeve="B", symbol="ETH", side="long",
        notional_usdc=Decimal("1"), risk_usdc=Decimal("0.01"),
        entry_ts=0, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
    )
    assert p.is_paper is True


def test_position_is_paper_overridable_to_false():
    """A Position whose on-chain tx settled is is_paper=False."""
    p = Position(
        sleeve="B", symbol="ETH", side="long",
        notional_usdc=Decimal("1"), risk_usdc=Decimal("0.01"),
        entry_ts=0, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
        is_paper=False,
    )
    assert p.is_paper is False


def _make_agent_with_floor(*, onchain_status: str):
    """Build an Agent-like object whose submit_floor_trade produces a
    Position with the given on-chain status. We patch _can_do_onchain
    + _submit_onchain_swap so the test doesn't touch a real RPC."""
    from core.tick import Agent
    from core.risk import ProposedTrade

    # Real Portfolio so add_position + close_position work normally.
    from core.portfolio import Portfolio
    portfolio = Portfolio(starting_equity=Decimal("100"))

    policy = {
        "global_risk": {"per_trade_risk_pct": 1.0, "max_daily_trades": 3,
                        "max_notional_usdc_per_trade": 1.0},
        "sleeves": {
            "A": {"enabled": False},
            "B": {"enabled": True},
            "C": {"enabled": False},
        },
    }
    agent = Agent(
        policy=policy, portfolio=portfolio, dashboard_state={},
        components={"bsc": MagicMock(), "pancake": MagicMock(),
                    "wallet": MagicMock()},
    )
    # Force the can-do-onchain gate to True so we exercise the
    # on-chain branch. Then return the requested onchain_status.
    agent._can_do_onchain = lambda: True
    if onchain_status == "submitted":
        agent._submit_onchain_swap = AsyncMock(return_value={
            "status": "submitted", "tx_hash": "0xDEAD",
            "bsctrace_url": "https://bsctrace.com/tx/0xDEAD",
            "block_number": 12345, "gas_used": 100000,
        })
    else:
        agent._submit_onchain_swap = AsyncMock(return_value={
            "status": "failed", "error": "reverted",
        })

    proposed = ProposedTrade(
        sleeve="B", symbol="USDT", side="buy",
        notional_usdc=Decimal("1"), risk_usdc=Decimal("0.01"),
    )
    return agent, proposed, portfolio


def test_floor_trade_settled_onchain_is_not_paper():
    """v2.3.5b fix: when the on-chain tx settles, the Position MUST
    be is_paper=False so the dashboard /api/trades shows it."""
    agent, proposed, portfolio = _make_agent_with_floor(onchain_status="submitted")
    asyncio.run(agent.submit_floor_trade(proposed, reason="daily_floor", hold_min=30))

    # The open should have left exactly one open position.
    assert len(portfolio.positions) == 1
    pos = list(portfolio.positions.values())[0]
    assert pos.is_paper is False, (
        "settled on-chain trade must be is_paper=False — this is the "
        "v2.3.5b regression that made /api/trades always empty on mainnet"
    )


def test_floor_trade_failed_onchain_falls_back_to_paper():
    """When the on-chain tx fails, the floor still opens a position
    (paper fallback) so the contest 1-trade/day rule is satisfied
    without risking real funds. That position is correctly paper."""
    agent, proposed, portfolio = _make_agent_with_floor(onchain_status="failed")
    asyncio.run(agent.submit_floor_trade(proposed, reason="daily_floor", hold_min=30))

    assert len(portfolio.positions) == 1
    pos = list(portfolio.positions.values())[0]
    assert pos.is_paper is True


# ------------------------------------------------------------------
# /api/trades endpoint — must return ALL trades, not just real ones
# ------------------------------------------------------------------

def test_api_trades_returns_all_with_paper_label(tmp_path, monkeypatch):
    """v2.3.5b: the panel shows every trade, not just real ones.
    The frontend can label paper vs real; the operator wants to
    see the agent's full activity (including paper fallbacks)."""
    from fastapi.testclient import TestClient
    from core.portfolio import Portfolio
    import dashboard.backend.main as dash_mod
    import core.dashboard_state as _ds_file

    # Redirect the IPC state file to a tmp path so this test
    # doesn't see the live agent's on-disk state. Then write a
    # controlled snapshot via DASHBOARD_STATE.
    monkeypatch.setattr(_ds_file, "DEFAULT_PATH", tmp_path / "dashboard_state.json")

    portfolio = Portfolio(
        starting_equity=Decimal("100"),
        trades_persistence_path=None,  # don't touch the real on-disk file
    )
    # Open + close a real on-chain trade
    real_pos = Position(
        sleeve="B", symbol="USDT", side="buy",
        notional_usdc=Decimal("1"), risk_usdc=Decimal("0.01"),
        entry_ts=1000, entry_price=Decimal("1"),
        stop_price=Decimal("0.99"), tp_price=Decimal("1.01"),
        is_paper=False,
    )
    portfolio.add_position("real-1", real_pos)
    portfolio.close_position("real-1", exit_price=Decimal("1"), reason="tp")
    # Open + close a paper fallback trade
    paper_pos = Position(
        sleeve="B", symbol="ETH", side="long",
        notional_usdc=Decimal("1"), risk_usdc=Decimal("0.01"),
        entry_ts=2000, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
        is_paper=True,
    )
    portfolio.add_position("paper-1", paper_pos)
    portfolio.close_position("paper-1", exit_price=Decimal("101"), reason="tp")

    # Mutate the in-process DASHBOARD_STATE that the build_app
    # factory reads. The IPC file path is empty by default so this
    # becomes the authoritative state.
    dash_mod.DASHBOARD_STATE.clear()
    dash_mod.DASHBOARD_STATE.update({
        "trades_view": list(portfolio.closed_trades),
        "config": {"mode": "mainnet"},
    })
    try:
        client = TestClient(dash_mod.app)
        r = client.get("/api/trades")
        assert r.status_code == 200
        body = r.json()
        # BOTH trades are surfaced (not just the real one)
        assert len(body["trades"]) == 2, (
            f"expected 2 trades (1 real + 1 paper), got {len(body['trades'])}: "
            f"{body['trades']}"
        )
        assert body["real_trades_count"] == 1
        assert body["paper_trades_count"] == 1
        # The is_paper flag is preserved on each trade record
        flags = {t["id"]: t["is_paper"] for t in body["trades"]}
        assert flags["real-1"] is False
        assert flags["paper-1"] is True
    finally:
        dash_mod.DASHBOARD_STATE.clear()


def test_api_trades_empty_state_returns_zero_counts(tmp_path, monkeypatch):
    """When the agent has no closed trades yet, the endpoint returns
    200 with empty trades + zero counts (NOT 404 / 'Not Found')."""
    from fastapi.testclient import TestClient
    import dashboard.backend.main as dash_mod
    import core.dashboard_state as _ds_file

    monkeypatch.setattr(_ds_file, "DEFAULT_PATH", tmp_path / "dashboard_state.json")
    dash_mod.DASHBOARD_STATE.clear()
    dash_mod.DASHBOARD_STATE.update({
        "trades_view": [],
        "config": {"mode": "mainnet"},
    })
    try:
        client = TestClient(dash_mod.app)
        r = client.get("/api/trades")
        assert r.status_code == 200
        body = r.json()
        assert body["trades"] == []
        assert body["real_trades_count"] == 0
        assert body["paper_trades_count"] == 0
        assert body["is_mainnet"] is True
    finally:
        dash_mod.DASHBOARD_STATE.clear()
