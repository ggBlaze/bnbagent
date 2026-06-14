"""Unit tests for the Token Module date lock + env opt-in.

The BNB HACK 2026 contest rules forbid token launches between
2026-06-03 and 2026-07-06. We enforce this in code (not docs) via
`TokenModule.is_deploy_unlocked()`:

  * Before 2026-07-07 00:00 UTC: always locked, regardless of env.
  * At/after 2026-07-07 00:00 UTC: locked unless BNBAGENT_ALLOW_TOKEN_DEPLOY=true.

This belt-and-suspenders design means a misconfigured prod env
can't accidentally start launching tokens the moment the clock
passes 00:00 UTC on July 7.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def test_locked_before_contest_window(monkeypatch):
    """Now < 2026-07-07 00:00 UTC -> locked, even with the env opt-in."""
    from agents.token_module import TokenModule
    fake_now = datetime(2026, 7, 6, 23, 59, 59, tzinfo=timezone.utc)
    with patch.object(TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        # Even if the operator sets the env, we're still locked
        monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")
        unlocked, reason = TokenModule.is_deploy_unlocked()
        assert unlocked is False
        assert "2026-07-07" in reason
        assert "BNB HACK 2026" in reason


def test_locked_on_2026_07_06_235959(monkeypatch):
    """One second before midnight: still locked."""
    from agents.token_module import TokenModule
    fake_now = datetime(2026, 7, 6, 23, 59, 59, tzinfo=timezone.utc)
    with patch.object(TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        unlocked, _ = TokenModule.is_deploy_unlocked()
        assert unlocked is False


def test_unlocked_at_2026_07_07_000000_only_with_env(monkeypatch):
    """At 00:00 UTC on 2026-07-07: unlocked ONLY if env is set."""
    from agents.token_module import TokenModule
    fake_now = datetime(2026, 7, 7, 0, 0, 0, tzinfo=timezone.utc)
    with patch.object(TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        # No env -> still locked (belt-and-suspenders)
        monkeypatch.delenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", raising=False)
        unlocked, reason = TokenModule.is_deploy_unlocked()
        assert unlocked is False
        assert "BNBAGENT_ALLOW_TOKEN_DEPLOY=true" in reason

        # With env -> unlocked
        monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")
        unlocked, reason = TokenModule.is_deploy_unlocked()
        assert unlocked is True
        assert reason == "deploy unlocked"


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_env_opt_in_accepts_common_truthy(monkeypatch, truthy):
    from agents.token_module import TokenModule
    fake_now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    with patch.object(TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", truthy)
        unlocked, _ = TokenModule.is_deploy_unlocked()
        assert unlocked is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "maybe"])
def test_env_opt_in_rejects_falsy(monkeypatch, falsy):
    from agents.token_module import TokenModule
    fake_now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    with patch.object(TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", falsy)
        unlocked, _ = TokenModule.is_deploy_unlocked()
        assert unlocked is False


def test_create_token_raises_permission_error_when_locked(monkeypatch):
    """The async create_token() entry point must raise PermissionError
    (not silently proceed) when the lock is on. The dashboard reads
    this exception to show a clear 'disabled until 2026-07-07' message.
    """
    from agents import token_module as tm_mod

    # Build a minimal TokenModule with mocked components
    class _FakeBs:
        chain_id = 97  # testnet

    class _FakeWallet:
        address = "0x" + "0" * 40

    tm = tm_mod.TokenModule(components={"wallet": _FakeWallet(), "bsc": _FakeBs()})

    # Lock the module
    fake_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    with patch.object(tm_mod.TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        import asyncio
        with pytest.raises(PermissionError) as excinfo:
            asyncio.run(tm.create_token(
                name="Test", symbol="TEST", supply=1000,
                network="testnet", protocol="erc20_minimal",
            ))
        assert "2026-07-07" in str(excinfo.value)


def test_create_token_succeeds_when_unlocked(monkeypatch):
    """When both gates pass, create_token() gets past the lock check
    and proceeds to the validation logic. We stub the network calls
    so we don't need a real RPC."""
    from agents import token_module as tm_mod

    class _FakeBs:
        chain_id = 97
        def next_nonce(self, addr):
            return 0
        def broadcast(self, signed):
            return type("R", (), {"contract_address": "0x" + "c" * 40, "tx_hash": "0x" + "h" * 64})()

    class _FakeWallet:
        address = "0x" + "0" * 40
        def sign_transaction(self, tx, *, chain_id, max_gas_price_gwei=None):
            return {**tx, "signed": True}

    tm = tm_mod.TokenModule(components={"wallet": _FakeWallet(), "bsc": _FakeBs()})

    async def _fake_enrich(name, symbol):
        return {"name": name, "symbol": symbol}
    tm._enrich_metadata = _fake_enrich
    tm._broadcast = lambda *a, **kw: {"tx_hash": "0x" + "a" * "a" * 64 if False else "0x" + "a" * 64}

    fake_now = datetime(2026, 7, 7, 0, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")
    with patch.object(tm_mod.TokenModule, "_now_utc", classmethod(lambda cls: fake_now)):
        import asyncio
        result = asyncio.run(tm.create_token(
            name="Test", symbol="TEST", supply=1_000_000,
            network="testnet", protocol="erc20_minimal",
        ))
        assert result.symbol == "TEST"
        assert result.network == "testnet"
