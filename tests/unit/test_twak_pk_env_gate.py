"""Regression tests for the BNBAGENT_PRIVATE_KEY env var gate (H-1, v2.0.8).

The plaintext-private-key env var fallback in TWAKWallet.from_env was a
single-point-of-compromise: if BNBAGENT_PRIVATE_KEY was ever set in a
.env / docker-compose / shell history / systemd unit, the keystore
encryption was silently bypassed and the raw key sat in process memory.

Fix: the path is now opt-in via BNBAGENT_ALLOW_PK_ENV=1, and a CRITICAL
log line is emitted whenever the keystore is bypassed. The default is
to refuse to load a plaintext key and raise RuntimeError.

These tests cover both branches:
- default: BNBAGENT_PRIVATE_KEY set, BNBAGENT_ALLOW_PK_ENV unset → raise
- opt-in:  BNBAGENT_PRIVATE_KEY set, BNBAGENT_ALLOW_PK_ENV=1 → load
- dev:     no PK, no keystore → ephemeral key (unchanged behavior)
"""
import logging

import pytest

from connectors.twak import TWAKWallet
from tests.fixtures.wallets import EVALUATOR_KEY


class TestPKEnvGate:
    def test_default_refuses_plaintext_key(self, caplog, monkeypatch):
        """Setting BNBAGENT_PRIVATE_KEY without opt-in must raise."""
        monkeypatch.setenv("BNBAGENT_PRIVATE_KEY", EVALUATOR_KEY)
        monkeypatch.delenv("BNBAGENT_ALLOW_PK_ENV", raising=False)
        with caplog.at_level(logging.CRITICAL):
            with pytest.raises(RuntimeError, match="BNBAGENT_PRIVATE_KEY is set but not opted in"):
                TWAKWallet.from_env()
        # CRITICAL log line emitted
        assert any(
            rec.levelno == logging.CRITICAL
            and "BNBAGENT_PRIVATE_KEY" in rec.getMessage()
            for rec in caplog.records
        ), "expected CRITICAL log line refusing the PK env var"

    def test_opt_in_loads_plaintext_key(self, caplog, monkeypatch):
        """Setting BNBAGENT_PRIVATE_KEY WITH opt-in must load + warn CRITICAL."""
        monkeypatch.setenv("BNBAGENT_PRIVATE_KEY", EVALUATOR_KEY)
        monkeypatch.setenv("BNBAGENT_ALLOW_PK_ENV", "1")
        # ensure no keystore path is in the way
        monkeypatch.delenv("TWAK_KEYSTORE", raising=False)
        monkeypatch.delenv("TWAK_PWD", raising=False)
        with caplog.at_level(logging.CRITICAL):
            w = TWAKWallet.from_env()
        assert w is not None
        # recovered address matches the eval key
        from eth_account import Account
        assert w.address.lower() == Account.from_key(
            EVALUATOR_KEY[2:] if EVALUATOR_KEY.startswith("0x") else EVALUATOR_KEY
        ).address.lower()
        # CRITICAL log line emitted
        assert any(
            rec.levelno == logging.CRITICAL
            and "BNBAGENT_ALLOW_PK_ENV" in rec.getMessage()
            for rec in caplog.records
        ), "expected CRITICAL log line when bypassing the keystore"

    def test_no_env_uses_ephemeral_key(self, caplog, monkeypatch):
        """No keystore, no PK env: fallback to ephemeral dev key (unchanged)."""
        monkeypatch.delenv("BNBAGENT_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("BNBAGENT_ALLOW_PK_ENV", raising=False)
        monkeypatch.delenv("TWAK_KEYSTORE", raising=False)
        monkeypatch.delenv("TWAK_PWD", raising=False)
        with caplog.at_level(logging.WARNING):
            w = TWAKWallet.from_env()
        assert w is not None
        # ephemeral key — the address changes per call
        assert w.address.startswith("0x")
        # WARNING log line emitted
        assert any(
            rec.levelno == logging.WARNING
            and "ephemeral" in rec.getMessage().lower()
            for rec in caplog.records
        )

    def test_opt_in_must_be_exactly_one(self, monkeypatch):
        """BNBAGENT_ALLOW_PK_ENV must be exactly '1' (not 'true' or 'yes')."""
        monkeypatch.setenv("BNBAGENT_PRIVATE_KEY", EVALUATOR_KEY)
        monkeypatch.setenv("BNBAGENT_ALLOW_PK_ENV", "true")
        monkeypatch.delenv("TWAK_KEYSTORE", raising=False)
        monkeypatch.delenv("TWAK_PWD", raising=False)
        with pytest.raises(RuntimeError, match="not opted in"):
            TWAKWallet.from_env()
