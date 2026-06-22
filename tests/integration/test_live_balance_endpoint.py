"""Integration test: /api/live-balance endpoint with TestClient."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


def test_live_balance_endpoint_returns_polled_values(tmp_path, monkeypatch):
    """v2.2.0: /api/live-balance should call poll_live_balance on first
    hit (no cache) and return the on-chain values."""
    from dashboard.backend.main import app

    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": None,
        "bnb_balance": None,
        "live_balance_ts": 0,
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    fake_poll = {
        "usdc": 80.02935, "bnb": 0.0526, "ts": int(time.time()),
        "error": None, "address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        "rpc": "https://test-rpc.example", "chain_id": 56,
    }
    with patch("dashboard.backend.main.poll_live_balance", return_value=fake_poll), \
         patch("dashboard.backend.main.set_live_balance") as mock_set:
        client = TestClient(app)
        r = client.get("/api/live-balance?refresh=1")

    assert r.status_code == 200
    body = r.json()
    assert body["usdc"] == 80.02935
    assert body["bnb"] == 0.0526
    assert body["error"] is None
    assert body["chain_id"] == 56
    # set_live_balance should have been called with the polled values
    mock_set.assert_called_once_with(80.02935, 0.0526)


def test_live_balance_endpoint_uses_cache_when_fresh(tmp_path, monkeypatch):
    """v2.2.0: if the cached value is < 60s old, return it without
    hitting the chain again. The RPC is rate-limited and we don't
    want to hammer it from the dashboard."""
    from dashboard.backend.main import app

    cached_ts = int(time.time()) - 5  # 5 seconds old
    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": 80.0,
        "bnb_balance": 0.05,
        "live_balance_ts": cached_ts,
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    with patch("dashboard.backend.main.poll_live_balance") as mock_poll:
        client = TestClient(app)
        r = client.get("/api/live-balance")

    assert r.status_code == 200
    body = r.json()
    assert body["usdc"] == 80.0
    assert body["bnb"] == 0.05
    assert body["cached"] is True
    # poll_live_balance should NOT have been called
    mock_poll.assert_not_called()


def test_live_balance_endpoint_re_polls_when_stale(tmp_path, monkeypatch):
    """v2.2.0: if the cache is > 60s old, re-poll the chain."""
    from dashboard.backend.main import app

    stale_ts = int(time.time()) - 120  # 2 minutes old
    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": 80.0,
        "bnb_balance": 0.05,
        "live_balance_ts": stale_ts,
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    fresh = {
        "usdc": 75.5, "bnb": 0.04, "ts": int(time.time()),
        "error": None, "address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        "rpc": "https://test-rpc.example", "chain_id": 56,
    }
    with patch("dashboard.backend.main.poll_live_balance", return_value=fresh), \
         patch("dashboard.backend.main.set_live_balance") as mock_set:
        client = TestClient(app)
        r = client.get("/api/live-balance")

    body = r.json()
    assert body["usdc"] == 75.5
    assert body["bnb"] == 0.04
    mock_set.assert_called_once_with(75.5, 0.04)


def test_stats_endpoint_exposes_wallet_balances(tmp_path, monkeypatch):
    """v2.2.0: /api/stats must surface wallet_usdc_balance + bnb
    for the dashboard hero. Also: on mainnet, primary_equity_usdc
    must equal the wallet balance (not the paper book)."""
    from dashboard.backend.main import app

    # The test harness boots the app in mock mode by default; the
    # /api/stats endpoint reads mode from the in-process state. We
    # just verify the wallet fields are surfaced and don't depend on
    # mode for the assertion (the mode is already exercised by the
    # unit tests in test_setup_live_balance.py).
    fake_summary = tmp_path / "setup.json"
    fake_summary.write_text(json.dumps({
        "usdc_balance": 80.0,
        "bnb_balance": 0.05,
        "live_balance_ts": int(time.time()),
    }))
    monkeypatch.setattr("core.setup.SUMMARY_PATH", fake_summary)

    client = TestClient(app)
    r = client.get("/api/stats")
    body = r.json()

    # The wallet fields are surfaced regardless of mode
    assert body["wallet_usdc_balance"] == 80.0
    assert body["wallet_bnb_balance"] == 0.05
    assert body["wallet_balance_ts"] > 0
