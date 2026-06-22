"""Tests for v2.2.0 (live-only): the dashboard on mainnet shows real
wallet data + paper sim as a secondary view, not the paper book as
the hero.

Covers:
  - /api/stats exposes paper_sim_equity + paper_sim_pnl on mainnet
  - /api/trades returns only real trades on mainnet, all on other modes
  - /api/equity-series returns live wallet on mainnet, paper on others
  - /api/competition/register refuses with 409 when already registered
  - TokenModule.is_deploy_unlocked() is False during the contest window
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# /api/stats: paper_sim_* on mainnet
# ------------------------------------------------------------------

def test_stats_exposes_paper_sim_fields_on_mainnet(monkeypatch):
    """v2.2.0: on mainnet, /api/stats must include paper_sim_equity,
    paper_sim_pnl, paper_sim_peak, paper_sim_trades — the contest
    strategy simulation values, clearly separated from the real
    wallet. The frontend renders these in the 'Strategy Simulation'
    panel."""
    from dashboard.backend.main import app

    # Force mainnet mode in the in-process state
    state = {
        "config": {"mode": "mainnet", "chain_id": 56, "rpcs": []},
        "policy": {},
        "stats": {
            "equity": 100.0,
            "starting": 100.0,
            "peak": 100.0,
            "drawdown_pct": 0.0,
            "day_pnl_pct": 0.0,
            "open_positions": 0,
            "closed_trades": 0,
            "gross_exposure": 0.0,
            "sleeve_exposure": {},
            "paper_pnl_usdc": 0.0,
            "real_pnl_usdc": 0.0,
            "paper_trades": 0,
            "real_trades": 0,
        },
    }
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    fake_summary = Path("/tmp/setup.json")
    fake_summary.write_text(json.dumps({"usdc_balance": 80.0, "bnb_balance": 0.05, "live_balance_ts": int(time.time())}))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    client = TestClient(app)
    r = client.get("/api/stats")
    body = r.json()

    assert body["mode"] == "mainnet"
    assert body["paper_sim_equity"] == 100.0
    assert body["paper_sim_pnl"] == 0.0
    assert body["paper_sim_peak"] == 100.0
    assert body["paper_sim_trades"] == 0
    # The hero is the real wallet, not the paper book
    assert body["primary_equity_usdc"] == 80.0
    assert body["wallet_usdc_balance"] == 80.0


def test_stats_omits_paper_sim_fields_off_mainnet(monkeypatch, tmp_path):
    """v2.2.0: on non-mainnet modes, the paper_sim_* fields are
    omitted (the hero IS the paper sim, no need for a separate
    panel)."""
    from dashboard.backend.main import app

    state = {
        "config": {"mode": "testnet", "chain_id": 97, "rpcs": []},
        "policy": {},
        "stats": {
            "equity": 100.0, "starting": 100.0, "peak": 100.0,
            "drawdown_pct": 0.0, "day_pnl_pct": 0.0,
            "open_positions": 0, "closed_trades": 0,
            "gross_exposure": 0.0, "sleeve_exposure": {},
            "paper_pnl_usdc": 0.0, "real_pnl_usdc": 0.0,
            "paper_trades": 0, "real_trades": 0,
        },
    }
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({}))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    client = TestClient(app)
    r = client.get("/api/stats")
    body = r.json()
    assert "paper_sim_equity" not in body
    assert "primary_equity_usdc" not in body or body.get("primary_equity_usdc") is None


# ------------------------------------------------------------------
# /api/trades: paper only on non-mainnet, real only on mainnet
# ------------------------------------------------------------------

def test_trades_filters_to_real_on_mainnet(monkeypatch):
    """v2.2.0: on mainnet, the trades endpoint must return only
    is_paper=False trades. The agent's paper book is the contest
    strategy sim and must not pollute the operator's view of
    real money."""
    from dashboard.backend.main import app

    trades_mixed = [
        {"id": "T-1", "sleeve": "A", "symbol": "BTC", "pnl_usdc": "1.5",
         "ts_close": 100, "is_paper": False},
        {"id": "T-2", "sleeve": "B", "symbol": "ETH", "pnl_usdc": "0.8",
         "ts_close": 101, "is_paper": True},
        {"id": "T-3", "sleeve": "C", "symbol": "SOL", "pnl_usdc": "-0.3",
         "ts_close": 102, "is_paper": False},
    ]
    state = {
        "config": {"mode": "mainnet", "chain_id": 56},
        "policy": {},
        "trades_view": trades_mixed,
        "stats": {},
    }
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    client = TestClient(app)
    r = client.get("/api/trades")
    body = r.json()
    assert body["is_mainnet"] is True
    assert body["source"] == "live_onchain"
    assert len(body["trades"]) == 2
    assert all(not t["is_paper"] for t in body["trades"])
    # The paper trades count is reported for the operator's awareness
    assert body["paper_trades_count"] == 1


def test_trades_returns_all_on_testnet(monkeypatch):
    """v2.2.0: on testnet/mock/replay, all trades are returned
    (the strategy sim IS the trading, no filter)."""
    from dashboard.backend.main import app

    trades = [
        {"id": "T-1", "sleeve": "A", "symbol": "BTC", "pnl_usdc": "1.5",
         "ts_close": 100, "is_paper": True},
        {"id": "T-2", "sleeve": "B", "symbol": "ETH", "pnl_usdc": "0.8",
         "ts_close": 101, "is_paper": True},
    ]
    state = {
        "config": {"mode": "testnet", "chain_id": 97},
        "policy": {},
        "trades_view": trades,
        "stats": {},
    }
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    client = TestClient(app)
    r = client.get("/api/trades")
    body = r.json()
    assert body["is_mainnet"] is False
    assert body["source"] == "paper_book"
    assert len(body["trades"]) == 2


# ------------------------------------------------------------------
# /api/equity-series: live wallet on mainnet, paper elsewhere
# ------------------------------------------------------------------

def test_equity_series_live_wallet_on_mainnet(monkeypatch, tmp_path):
    """v2.2.0: on mainnet, the equity series must come from the
    live wallet USDC, not the paper book. The chart should show
    the real money curve."""
    from dashboard.backend.main import app

    state = {
        "config": {"mode": "mainnet", "chain_id": 56},
        "policy": {},
        "stats": {},
    }
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": 80.0,
        "bnb_balance": 0.05,
        "live_balance_ts": int(time.time()),
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    client = TestClient(app)
    r = client.get("/api/equity-series")
    body = r.json()
    assert body["source"] == "live_wallet"
    assert body["wallet_usdc"] == 80.0
    assert body["wallet_bnb"] == 0.05
    assert len(body["series"]) >= 1
    # The single seed point equals the wallet USDC
    assert body["series"][-1]["equity"] == 80.0


def test_equity_series_paper_book_on_testnet(monkeypatch, tmp_path):
    """v2.2.0: on testnet/mock/replay, the equity series comes
    from the paper book equity_history."""
    from dashboard.backend.main import app

    state = {
        "config": {"mode": "testnet", "chain_id": 97},
        "policy": {},
        "stats": {},
        "components": {},
    }
    # Provide a portfolio-like object with equity_history
    fake_pf = MagicMock()
    fake_pf.equity_history = [(100, 100.0), (101, 100.5), (102, 101.2)]
    state["components"]["portfolio"] = fake_pf
    monkeypatch.setattr("dashboard.backend.main.DASHBOARD_STATE", state)

    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({}))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    client = TestClient(app)
    r = client.get("/api/equity-series")
    body = r.json()
    assert body["source"] == "paper_book"
    assert len(body["series"]) == 3
    assert body["series"][-1]["equity"] == 101.2


# ------------------------------------------------------------------
# /api/competition/register: refuses re-registration
# ------------------------------------------------------------------

def test_competition_register_refuses_double_registration(monkeypatch, tmp_path):
    """v2.2.0: re-registering when the cache shows a successful
    registration must return HTTP 409, not submit a second tx."""
    from dashboard.backend.main import app

    # Mock the cache as if we already registered
    fake_cache = {
        "ok": True,
        "tx_hash": "0xabc123",
        "network": "mainnet",
        "timestamp": int(time.time()),
        "agent_address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
    }
    monkeypatch.setattr(
        "scripts.competition_register._load_cache",
        lambda: fake_cache,
    )

    # Skip auth (so we hit the route)
    monkeypatch.setattr(
        "dashboard.backend.main._auth.require_admin",
        lambda: None,
    )

    client = TestClient(app)
    r = client.post("/api/competition/register", json={"network": "mainnet"})
    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "already_registered"
    assert body["tx_hash"] == "0xabc123"


# ------------------------------------------------------------------
# TokenModule lock
# ------------------------------------------------------------------

def test_token_module_locked_during_contest_window():
    """v2.2.0: TokenModule.is_deploy_unlocked() must return False
    between 2026-06-03 and 2026-07-06 (the BNB HACK 2026 contest
    window). The dashboard shows a 🔒 banner with the unlock
    date."""
    from agents.token_module import TokenModule
    unlocked, reason = TokenModule.is_deploy_unlocked()
    # We're in the contest window right now (2026-06-22)
    assert unlocked is False
    assert "2026-07-07" in reason or "2026-07-06" in reason or "locked" in reason.lower()
