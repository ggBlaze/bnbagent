"""v2.1.8 (live-paper): the /api/stats endpoint should expose a
mode-aware "primary" PnL view so the dashboard doesn't show paper
and real numbers side-by-side on mainnet.

Before the fix, the dashboard showed:
  - equity: 100 (paper book)
  - paper_pnl_usdc: 0
  - real_pnl_usdc: 0
both at once, which was confusing when running on mainnet with real
funds. The fix adds:
  - mode: "mainnet" | "testnet" | "replay" | "mock"
  - primary_pnl_usdc: the PnL that matters for this mode
      (real on mainnet, paper elsewhere)
  - primary_equity_usdc: the live BSC USDC balance (mainnet only)
  - primary_trades: real_trades on mainnet, paper_trades elsewhere
  - primary_label: a short human string like "real (settled on PCS)"
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest


def _setup_mocks(state: dict, usdc_balance=None):
    """Return (state_mock, setup_mock) configured for one _mode_aware_stats call."""
    state_mock = MagicMock(return_value=state)
    setup = MagicMock()
    if usdc_balance is not None:
        setup.usdc_balance = usdc_balance
    setup_mock = MagicMock(return_value=setup)
    return state_mock, setup_mock


def test_mainnet_funded_returns_real_pnl_and_live_equity():
    from dashboard.backend.main import _mode_aware_stats
    state = {
        "config": {"mode": "mainnet", "chain_id": 56},
        "stats": {
            "equity": 102.0, "starting": 100.0, "peak": 102.0,
            "paper_pnl_usdc": 2.0, "real_pnl_usdc": 0.5,
            "paper_trades": 5, "real_trades": 1, "closed_trades": 5,
        },
    }
    state_mock, setup_mock = _setup_mocks(state, usdc_balance=75.0)
    with patch("dashboard.backend.main._state", state_mock), \
         patch("dashboard.backend.main.load_setup_state", setup_mock):
        r = _mode_aware_stats()
    assert r["mode"] == "mainnet"
    assert r["chain_id"] == 56
    assert r["primary_label"] == "real (settled on PCS)"
    assert r["primary_pnl_usdc"] == 0.5     # real PnL, not paper
    assert r["primary_equity_usdc"] == 75.0  # live wallet USDC
    assert r["primary_trades"] == 1         # real trades
    # All original fields preserved for backward compat
    assert r["equity"] == 102.0
    assert r["paper_pnl_usdc"] == 2.0
    assert r["real_pnl_usdc"] == 0.5


def test_mainnet_empty_wallet_uses_zero_for_live_equity():
    from dashboard.backend.main import _mode_aware_stats
    state = {
        "config": {"mode": "mainnet", "chain_id": 56},
        "stats": {
            "equity": 100.0, "starting": 100.0,
            "paper_pnl_usdc": 0.0, "real_pnl_usdc": 0.0,
            "paper_trades": 0, "real_trades": 0, "closed_trades": 0,
        },
    }
    state_mock, setup_mock = _setup_mocks(state, usdc_balance=0)
    with patch("dashboard.backend.main._state", state_mock), \
         patch("dashboard.backend.main.load_setup_state", setup_mock):
        r = _mode_aware_stats()
    assert r["mode"] == "mainnet"
    assert r["primary_pnl_usdc"] == 0.0
    assert r["primary_equity_usdc"] == 0.0
    assert r["primary_label"] == "real (settled on PCS)"


def test_testnet_returns_paper_pnl_and_no_live_equity():
    from dashboard.backend.main import _mode_aware_stats
    state = {
        "config": {"mode": "testnet", "chain_id": 97},
        "stats": {
            "equity": 100.5, "starting": 100.0,
            "paper_pnl_usdc": 0.5, "real_pnl_usdc": 0.0,
            "paper_trades": 1, "real_trades": 0, "closed_trades": 1,
        },
    }
    state_mock, setup_mock = _setup_mocks(state, usdc_balance=0)
    with patch("dashboard.backend.main._state", state_mock), \
         patch("dashboard.backend.main.load_setup_state", setup_mock):
        r = _mode_aware_stats()
    assert r["mode"] == "testnet"
    assert r["primary_label"] == "paper sim (testnet)"
    assert r["primary_pnl_usdc"] == 0.5       # paper PnL
    assert r["primary_equity_usdc"] is None   # no live funds concept
    assert r["primary_trades"] == 1


def test_replay_mode_returns_paper_pnl():
    from dashboard.backend.main import _mode_aware_stats
    state = {
        "config": {"mode": "replay", "chain_id": 56},
        "stats": {
            "equity": 100.0, "starting": 100.0,
            "paper_pnl_usdc": 0.0, "real_pnl_usdc": 0.0,
            "paper_trades": 0, "real_trades": 0, "closed_trades": 0,
        },
    }
    state_mock, setup_mock = _setup_mocks(state)
    with patch("dashboard.backend.main._state", state_mock), \
         patch("dashboard.backend.main.load_setup_state", setup_mock):
        r = _mode_aware_stats()
    assert r["mode"] == "replay"
    assert r["primary_label"] == "paper sim (replay)"
    assert r["primary_pnl_usdc"] == 0.0


def test_mainnet_without_usdc_balance_attribute_falls_back_to_none():
    """If load_setup_state() returns a setup object without usdc_balance
    (older wizard runs), primary_equity_usdc should be None so the
    frontend can fall back to the paper book value."""
    from dashboard.backend.main import _mode_aware_stats
    state = {
        "config": {"mode": "mainnet", "chain_id": 56},
        "stats": {"equity": 100.0, "starting": 100.0, "paper_pnl_usdc": 0.0,
                  "real_pnl_usdc": 0.0, "paper_trades": 0, "real_trades": 0,
                  "closed_trades": 0},
    }
    state_mock = MagicMock(return_value=state)
    setup = MagicMock(spec=[])  # no usdc_balance attribute
    setup_mock = MagicMock(return_value=setup)
    with patch("dashboard.backend.main._state", state_mock), \
         patch("dashboard.backend.main.load_setup_state", setup_mock):
        r = _mode_aware_stats()
    assert r["primary_equity_usdc"] is None
    assert r["primary_pnl_usdc"] == 0.0  # real PnL still surfaces
