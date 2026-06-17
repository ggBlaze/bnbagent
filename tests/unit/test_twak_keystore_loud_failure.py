"""#7 (v2.1.8): when TWAK_KEYSTORE points to a file that doesn't exist,
TWAKWallet.from_env() must fail loudly instead of silently generating
an ephemeral key.

Symptom in production: user imports wallet via wizard, agent restarts,
but the agent boots BEFORE the import (or with TWAK_KEYSTORE pointing
to a missing file). `from_env()` falls into the dev-fallback at
twak.py:115-119 and the agent runs with a wallet the operator never
authorized. Dashboard's /api/wallet/balances reads the keystore directly
and shows the right address; everything reading agent in-memory state
sees the ephemeral. Two wallets, confused operator.

Fix: distinguish (a) "operator declared a keystore but it's missing —
fail" from (b) "no keystore declared at all, this is a test/dev run —
generate". The case-(a) failure must include the env var name and the
file path so the operator can diagnose immediately.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from connectors.twak import TWAKWallet


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The conftest fixture scrubs operator .env into a clean test env;
    we explicitly delenv these in case the order changes and to make
    each test's preconditions obvious."""
    for k in ("TWAK_KEYSTORE", "TWAK_PWD", "BNBAGENT_PRIVATE_KEY",
              "BNBAGENT_ALLOW_PK_ENV"):
        monkeypatch.delenv(k, raising=False)


def test_missing_keystore_path_raises_loudly(monkeypatch, tmp_path):
    """Operator set TWAK_KEYSTORE but the file isn't there (wallet
    wiped, fresh install, typo). Must raise — not silently generate."""
    missing = tmp_path / "nope" / "wallet.json"
    monkeypatch.setenv("TWAK_KEYSTORE", str(missing))
    monkeypatch.setenv("TWAK_PWD", "any")
    with pytest.raises(RuntimeError) as exc:
        TWAKWallet.from_env()
    msg = str(exc.value)
    assert "TWAK_KEYSTORE" in msg, (
        f"error message must name the env var so the operator can fix it; "
        f"got: {msg!r}"
    )
    assert str(missing) in msg, (
        f"error message must include the missing path; got: {msg!r}"
    )


def test_keystore_set_but_no_password_raises_loudly(monkeypatch, tmp_path):
    """If the keystore file exists but TWAK_PWD is missing, that's also
    an operator misconfiguration — fail rather than fall back to
    ephemeral. (Operator may have set the keystore in one shell and
    forgotten the password env in another.)"""
    ks = tmp_path / "wallet.json"
    ks.write_text(json.dumps({"address": "0x" + "a" * 40, "encrypted": {}}))
    monkeypatch.setenv("TWAK_KEYSTORE", str(ks))
    # TWAK_PWD intentionally not set
    with pytest.raises(RuntimeError) as exc:
        TWAKWallet.from_env()
    assert "TWAK_PWD" in str(exc.value), (
        f"error must name TWAK_PWD; got: {exc.value!r}"
    )


def test_no_env_vars_at_all_still_generates_ephemeral(monkeypatch):
    """Pure dev / test path: no env at all. Backwards-compatible —
    tests, the replay harness, and a fresh `python -m core.main` with
    no setup all rely on this fallback. A WARNING-level log line is
    still emitted (the existing behavior)."""
    # All keystore-related vars already cleared by the fixture.
    w = TWAKWallet.from_env()
    assert w.address.startswith("0x")
    assert w._key is not None, "ephemeral wallet must have a key set"
