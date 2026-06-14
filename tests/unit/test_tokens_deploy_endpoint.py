"""Test the /api/tokens/deploy endpoint's mainnet confirm-symbol guard.

The endpoint must reject a mainnet deploy when `confirm_symbol` is
missing, empty, or doesn't match `symbol` case-insensitively. Symbol
match (not name match) is the canonical identifier on-chain forever.

v2.1.6: The route is also guarded by TokenModule.is_deploy_unlocked()
(contest rule: no launches before 2026-07-07 UTC). The fixture below
unlocks the date lock via monkeypatching _now_utc to a post-window
timestamp + BNBAGENT_ALLOW_TOKEN_DEPLOY=true. The date-lock logic
itself is covered by tests/unit/test_token_lock.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def client_and_state(monkeypatch):
    """Build a TestClient with a mocked TokenModule in DASHBOARD_STATE."""
    from fastapi.testclient import TestClient
    from dashboard.backend import main as dash_main
    from agents import token_module as tm_mod

    # Unlock the date lock so we can test the symbol-confirm path.
    # The date-lock logic itself is covered by tests/unit/test_token_lock.py.
    fake_now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tm_mod.TokenModule, "_now_utc",
                        classmethod(lambda cls: fake_now))
    monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")

    tm = MagicMock()
    tm.create_token = AsyncMock(return_value=MagicMock(
        contract_address="0x" + "a" * 40,
        tx_hash="0x" + "b" * 64,
        deployer="0x" + "c" * 40,
        total_supply=1000,
        decimals=18,
        network="mainnet",
        ipfs_metadata_cid="Qmtest",
        explorer_url="https://bscscan.com/token/0xabc",
        website_html=None,
    ))
    # TokenModule is checked against the real Portfolio .close_position path
    # so the mock just needs to return a Dataclass-like result.
    from dataclasses import dataclass
    @dataclass
    class _R:
        contract_address: str
        tx_hash: str
        deployer: str
        total_supply: int
        decimals: int
        network: str
        ipfs_metadata_cid: str
        explorer_url: str
        website_html: str | None
    tm.create_token = AsyncMock(return_value=_R(
        contract_address="0x" + "a" * 40,
        tx_hash="0x" + "b" * 64,
        deployer="0x" + "c" * 40,
        total_supply=1000,
        decimals=18,
        network="mainnet",
        ipfs_metadata_cid="Qmtest",
        explorer_url="https://bscscan.com/token/0xabc",
        website_html=None,
    ))

    # Stuff into the module-level DASHBOARD_STATE
    dash_main.DASHBOARD_STATE = {
        "components": {"token_module": tm},
    }
    client = TestClient(dash_main.app)
    yield client, tm


def test_mainnet_requires_confirm_symbol(client_and_state):
    client, _tm = client_and_state
    r = client.post("/api/tokens/deploy", json={
        "name": "Mooncoin", "symbol": "MOON", "supply": 1000, "decimals": 18,
        "network": "mainnet",
        "confirm_mainnet": True,
        # no confirm_symbol
    })
    assert r.status_code == 400
    assert "confirm_symbol" in r.json()["error"]


def test_mainnet_rejects_mismatched_symbol(client_and_state):
    client, _tm = client_and_state
    r = client.post("/api/tokens/deploy", json={
        "name": "Mooncoin", "symbol": "MOON", "supply": 1000, "decimals": 18,
        "network": "mainnet",
        "confirm_mainnet": True,
        "confirm_symbol": "WRONG",
    })
    assert r.status_code == 400
    assert "MOON" in r.json()["error"]


def test_mainnet_accepts_case_insensitive_symbol_match(client_and_state):
    """'moon' (lowercase) must match 'MOON' (the symbol typed in the form)."""
    client, tm = client_and_state
    r = client.post("/api/tokens/deploy", json={
        "name": "Mooncoin", "symbol": "MOON", "supply": 1000, "decimals": 18,
        "network": "mainnet",
        "confirm_mainnet": True,
        "confirm_symbol": "moon",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    tm.create_token.assert_awaited_once()


def test_mainnet_accepts_mixed_case_symbol(client_and_state):
    client, tm = client_and_state
    r = client.post("/api/tokens/deploy", json={
        "name": "Mooncoin", "symbol": "MOON", "supply": 1000, "decimals": 18,
        "network": "mainnet",
        "confirm_mainnet": True,
        "confirm_symbol": "Moon",
    })
    assert r.status_code == 200


def test_testnet_does_not_require_confirm_symbol(client_and_state):
    """Testnet has no real-BNB risk, so symbol confirm is not required."""
    client, tm = client_and_state
    r = client.post("/api/tokens/deploy", json={
        "name": "Mooncoin", "symbol": "MOON", "supply": 1000, "decimals": 18,
        "network": "testnet",
    })
    assert r.status_code == 200
    tm.create_token.assert_awaited_once()
