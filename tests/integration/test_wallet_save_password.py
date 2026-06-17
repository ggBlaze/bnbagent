"""Integration tests for the `save_password_to_env` extension of
POST /api/setup/wallet/import.

When the operator imports a wallet in the wizard, they can opt in to
saving TWAK_PWD to .env so the next `bash bnbagent` invocation can
auto-decrypt the keystore. Without this, the agent boots with an
ephemeral key every restart and trades can't be signed for the
operator's real wallet.

These tests exercise the wired-in FastAPI route via TestClient and
assert against a real on-disk .env in a tmp dir.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TestClient with auth disabled, chdir into tmp_path so
    the dashboard's .env read/write helpers operate there (never the
    operator's real .env)."""
    monkeypatch.chdir(tmp_path)
    # Seed an empty .env so the dashboard's _set_env_var_in_dotenv has
    # something to append to.
    (tmp_path / ".env").write_text("")

    # Auth disabled (default for local dev).
    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as main_mod
    saved = {"AUTH_ENABLED": auth_mod.AUTH_ENABLED}
    monkeypatch.delenv("BNBAGENT_AUTH_ENABLED", raising=False)
    auth_mod.AUTH_ENABLED = False
    # Allow wallet import for the test.
    monkeypatch.setenv("BNBAGENT_ALLOW_WALLET_IMPORT", "true")
    # Mock out the keystore write so we don't actually encrypt a wallet
    # — this test is about .env persistence, not keystore mechanics.
    called = {}
    def _fake_import(pk, password):
        called["pk"] = pk
        called["password"] = password
        return {"address": "0x" + "a" * 40, "keystore_path": "/tmp/fake.json"}
    monkeypatch.setattr(main_mod, "import_wallet", _fake_import)

    app = main_mod.build_app()
    with TestClient(app) as c:
        try:
            yield c, tmp_path, called
        finally:
            auth_mod.AUTH_ENABLED = saved["AUTH_ENABLED"]


def test_import_with_save_password_writes_twak_pwd_to_env(client):
    """save_password_to_env=true → TWAK_PWD written to .env with the
    exact password the operator typed."""
    c, tmp_path, _called = client
    r = c.post("/api/setup/wallet/import", json={
        "private_key": "0x" + "a" * 64,
        "password": "the-real-password-abc",
        "save_password_to_env": True,
    })
    assert r.status_code == 200, r.text
    env_text = (tmp_path / ".env").read_text()
    assert "TWAK_PWD=the-real-password-abc" in env_text, (
        f"TWAK_PWD not persisted to .env; got:\n{env_text}"
    )


def test_import_without_save_flag_does_not_touch_env(client):
    """Without save_password_to_env (or false), .env stays untouched."""
    c, tmp_path, _called = client
    # Pre-seed .env with an unrelated var so we can verify nothing
    # gets clobbered.
    (tmp_path / ".env").write_text("BNBAGENT_MODE=testnet\n")

    r = c.post("/api/setup/wallet/import", json={
        "private_key": "0x" + "a" * 64,
        "password": "the-real-password-abc",
    })
    assert r.status_code == 200, r.text
    env_text = (tmp_path / ".env").read_text()
    assert "TWAK_PWD" not in env_text, (
        f"TWAK_PWD should not be written when save_password_to_env is "
        f"not set; got:\n{env_text}"
    )
    assert "BNBAGENT_MODE=testnet" in env_text


def test_import_save_password_updates_existing_twak_pwd_in_env(client):
    """If .env already has a TWAK_PWD line (operator changed password
    or is rotating), the import overwrites it rather than appending
    a duplicate."""
    c, tmp_path, _called = client
    (tmp_path / ".env").write_text(
        "TWAK_PWD=old-password\n"
        "BNBAGENT_MODE=testnet\n"
    )

    r = c.post("/api/setup/wallet/import", json={
        "private_key": "0x" + "a" * 64,
        "password": "new-password-xyz",
        "save_password_to_env": True,
    })
    assert r.status_code == 200, r.text

    env_text = (tmp_path / ".env").read_text()
    twak_lines = [
        ln for ln in env_text.splitlines()
        if ln.strip().startswith("TWAK_PWD=")
    ]
    assert len(twak_lines) == 1, (
        f"expected exactly one TWAK_PWD line, got {twak_lines}"
    )
    assert "TWAK_PWD=new-password-xyz" in twak_lines[0]
    # Unrelated vars must be preserved
    assert "BNBAGENT_MODE=testnet" in env_text
