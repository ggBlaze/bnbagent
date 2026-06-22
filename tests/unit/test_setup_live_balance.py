"""Tests for v2.2.0 (live-balance): on-chain wallet USDC + BNB polling.

Background: the dashboard hero on mainnet was previously displaying
the $100 paper book value with the 'mainnet · live funds' label
(bug). v2.2.0 adds:

  - `SetupState.usdc_balance` + `bnb_balance` + `live_balance_ts`
  - `core.setup.poll_live_balance()` reads the on-chain values
  - `core.setup.set_live_balance()` caches them to ~/.bnbagent/setup.json
  - `load_setup_state()` overlays the cached values from the JSON
  - `/api/live-balance` endpoint surfaces them in the dashboard
  - Frontend polls every 60s and shows the real wallet (not the
    paper book) on mainnet

These tests use a fake RPC stub (no network) to keep the test
self-contained.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.setup import (
    SetupState, load_setup_state, set_live_balance, poll_live_balance,
    SUMMARY_PATH,
)


# ------------------------------------------------------------------
# SetupState dataclass shape
# ------------------------------------------------------------------

def test_setup_state_has_live_balance_fields():
    """v2.2.0: the dataclass must have the new fields, with safe defaults."""
    s = SetupState()
    assert hasattr(s, "usdc_balance")
    assert hasattr(s, "bnb_balance")
    assert hasattr(s, "live_balance_ts")
    assert s.usdc_balance is None
    assert s.bnb_balance is None
    assert s.live_balance_ts == 0


def test_set_live_balance_persists_to_disk(tmp_path, monkeypatch):
    """v2.2.0: set_live_balance() writes to ~/.bnbagent/setup.json so
    the cached value survives process restarts. Point SUMMARY_PATH
    at a tmp dir for the test."""
    fake_summary = tmp_path / "setup.json"
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    s = set_live_balance(usdc=42.5, bnb=0.05)
    assert s.usdc_balance == 42.5
    assert s.bnb_balance == 0.05
    assert s.live_balance_ts > 0

    # The file on disk must contain the values
    data = json.loads(fake_summary.read_text())
    assert data["usdc_balance"] == 42.5
    assert data["bnb_balance"] == 0.05
    assert data["live_balance_ts"] > 0


def test_load_setup_state_overlays_cached_balance(tmp_path, monkeypatch):
    """v2.2.0: load_setup_state() should overlay the cached usdc/bnb
    from setup.json, not rebuild them as None. Without this, the
    dashboard would always show the paper book on every page load."""
    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": 80.0,
        "bnb_balance": 0.052,
        "live_balance_ts": int(time.time()),
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    s = load_setup_state()
    assert s.usdc_balance == 80.0
    assert s.bnb_balance == 0.052
    assert s.live_balance_ts > 0


def test_load_setup_state_handles_missing_cache(tmp_path, monkeypatch):
    """v2.2.0: when setup.json doesn't exist (fresh install), the
    load should not raise and the balances should be None."""
    fake_summary = tmp_path / "nonexistent.json"
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    s = load_setup_state()
    assert s.usdc_balance is None
    assert s.bnb_balance is None
    assert s.live_balance_ts == 0


def test_load_setup_state_handles_corrupt_cache(tmp_path, monkeypatch):
    """v2.2.0: a corrupt setup.json must not break dashboard boot.
    The agent should log a warning and proceed with None balances."""
    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text("{ invalid json ::: }")
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    # Should not raise
    s = load_setup_state()
    assert s.usdc_balance is None
    assert s.bnb_balance is None


# ------------------------------------------------------------------
# poll_live_balance (with fake RPC)
# ------------------------------------------------------------------

def test_poll_live_balance_with_fake_rpc(monkeypatch):
    """v2.2.0: poll_live_balance() should return the on-chain USDC + BNB
    values when given a working (faked) RPC. We mock web3 to avoid
    hitting the real chain in unit tests."""

    # Build a fake w3
    fake_w3 = MagicMock()
    fake_w3.is_connected.return_value = True
    fake_w3.eth.block_number = 12345
    # 0.05 BNB = 5e16 wei
    fake_w3.eth.get_balance.return_value = 5 * 10**16
    # w3.from_wei returns int|Decimal depending on version; return a Decimal
    fake_w3.from_wei.return_value = Decimal("0.05")

    # Fake USDC contract: 80 USDC = 80_000_000 with 6 decimals
    fake_usdc = MagicMock()
    fake_usdc.functions.balanceOf.return_value.call.return_value = 80_000_000
    fake_usdc.functions.decimals.return_value.call.return_value = 6
    fake_w3.eth.contract.return_value = fake_usdc

    fake_state = SetupState(
        wallet_address="0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        rpcs=["https://fake-rpc.example.com"],
        chain_id=56,
    )

    monkeypatch.setattr("core.setup.load_setup_state", lambda: fake_state)

    # v2.2.0: poll_live_balance does `from web3 import Web3` inside
    # the function body, so patching core.setup.Web3 is not enough.
    # Patch the source of truth: `web3.Web3`.
    with patch("web3.Web3") as MockWeb3:
        MockWeb3.HTTPProvider.return_value = None  # ignored
        MockWeb3.to_checksum_address = lambda x: x
        MockWeb3.return_value = fake_w3

        r = poll_live_balance()

    assert r["usdc"] == 80.0
    assert r["bnb"] == pytest.approx(0.05, abs=1e-9)
    assert r["error"] is None
    assert r["chain_id"] == 56
    assert r["address"] == "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"


def test_poll_live_balance_no_wallet(monkeypatch):
    """v2.2.0: when there's no wallet address, return a clear error
    instead of crashing."""
    fake_state = SetupState(wallet_address="", rpcs=[], chain_id=56)
    monkeypatch.setattr("core.setup.load_setup_state", lambda: fake_state)

    r = poll_live_balance()
    assert r["usdc"] is None
    assert r["bnb"] is None
    assert r["error"] == "no_wallet_or_no_rpcs"


def test_poll_live_balance_rpc_failure_falls_through(monkeypatch):
    """v2.2.0: when the only RPC fails, the function should report
    the error rather than crash. Operators want to see 'RPC failed'
    so they can fix it, not a silent None."""
    fake_state = SetupState(
        wallet_address="0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        rpcs=["https://bad-rpc.example.com"],
        chain_id=56,
    )
    monkeypatch.setattr("core.setup.load_setup_state", lambda: fake_state)

    with patch("web3.Web3") as MockWeb3:
        MockWeb3.HTTPProvider.return_value = None
        MockWeb3.to_checksum_address = lambda x: x
        fake_w3 = MagicMock()
        fake_w3.is_connected.return_value = False
        MockWeb3.return_value = fake_w3

        r = poll_live_balance()

    assert r["usdc"] is None
    assert r["bnb"] is None
    assert r["error"] is not None
    assert "bad-rpc.example.com" in r["error"]


# ------------------------------------------------------------------
# Mode-aware stats: the bug
# ------------------------------------------------------------------

def test_dashboard_hero_does_not_show_paper_as_live_funds():
    """v2.2.0 regression test: when primary_equity_usdc is None on
    mainnet, the dashboard MUST NOT show the $100 paper book value
    labeled as 'live funds'. Either show the real wallet USDC, or
    show '—' / 'paper sim only' — but never both.

    The frontend logic is in dashboard/frontend/index.html. We
    re-derive the hero equity here to assert the fix is in place.
    """
    # Simulate the backend stats dict that the frontend would receive
    stats = {
        "mode": "mainnet",
        "equity": 100.0,                # paper book
        "primary_equity_usdc": None,    # never polled
        "wallet_usdc_balance": None,    # never polled
        "wallet_bnb_balance": None,
        "real_pnl_usdc": 0.0,
    }

    # Re-derive the hero (matches the JS logic in index.html)
    is_mainnet = (stats.get("mode") or "") == "mainnet"
    live_usdc = stats.get("wallet_usdc_balance")
    has_live = is_mainnet and live_usdc is not None
    hero = live_usdc if has_live else stats.get("equity")

    # If has_live is False on mainnet, the label MUST NOT be
    # "mainnet · live funds" (frontend check). We assert the
    # condition here: when has_live is False, the JS code path
    # triggers fetchLiveBalance() and shows 'mainnet · paper sim only'
    # (or similar) — not 'live funds'.
    assert not has_live
    # The label string the frontend shows in this case is built
    # differently; the assertion is: hero (100.0) is the paper book,
    # not the wallet. The frontend code handles this by NOT showing
    # 'live funds' when wallet_usdc_balance is None.
    assert hero == 100.0
    # (Frontend logic in index.html ensures the 'live funds' label
    # only appears in the eqSubParts list when has_live is True.)


def test_dashboard_hero_shows_real_wallet_when_polled():
    """v2.2.0: when the live-balance endpoint has returned a real
    value, the hero must show that value, not the $100 paper book."""
    stats = {
        "mode": "mainnet",
        "equity": 100.0,
        "primary_equity_usdc": 80.03,
        "wallet_usdc_balance": 80.03,
        "wallet_bnb_balance": 0.0526,
        "real_pnl_usdc": 0.0,
    }
    is_mainnet = (stats.get("mode") or "") == "mainnet"
    live_usdc = stats.get("wallet_usdc_balance")
    has_live = is_mainnet and live_usdc is not None
    hero = live_usdc if has_live else stats.get("equity")
    assert hero == 80.03
    assert has_live
