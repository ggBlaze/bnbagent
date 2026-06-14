"""Integration tests for the password wrapper — the end-to-end login +
role-gated route flow via FastAPI TestClient.

The unit tests in test_auth.py cover the cookie format + dependencies.
These cover the wired-in routes inside the actual FastAPI app:
  * /api/auth/status returns 200 with the current role
  * /api/auth/login sets a cookie on success, 401 on bad password
  * /api/auth/logout clears the cookie
  * Admin-only routes (e.g. /api/setup/sign) require a real admin cookie
  * Judge-only routes (e.g. /api/chat) accept either judge or admin
"""
import importlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_enabled_client(monkeypatch):
    """Build a TestClient with BNBAGENT_AUTH_ENABLED=true and known passwords.

    Important: we patch the auth module's globals directly rather than
    reloading, because `from . import auth as _auth` in main.py makes a
    named binding (not a live reference) that survives module reload.
    Patching globals is the only way to change the visible state.
    """
    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as main_mod

    # Save the original module-level state so we can restore on teardown.
    saved = {
        "AUTH_ENABLED": auth_mod.AUTH_ENABLED,
        "JUDGE_PASSWORD": auth_mod.JUDGE_PASSWORD,
        "ADMIN_PASSWORD": auth_mod.ADMIN_PASSWORD,
        "SECRET": auth_mod.SECRET,
    }
    # Patch env + module globals
    monkeypatch.setenv("BNBAGENT_AUTH_ENABLED", "true")
    monkeypatch.setenv("JUDGE_PASSWORD", "judge-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test")
    monkeypatch.setenv("BNBAGENT_AUTH_SECRET", "test-secret-integration-98765")
    auth_mod.AUTH_ENABLED   = True
    auth_mod.JUDGE_PASSWORD = "judge-test"
    auth_mod.ADMIN_PASSWORD = "admin-test"
    auth_mod.SECRET         = "test-secret-integration-98765"

    app = main_mod.build_app()
    with TestClient(app) as client:
        try:
            yield client
        finally:
            for k, v in saved.items():
                setattr(auth_mod, k, v)


@pytest.fixture
def auth_disabled_client(monkeypatch):
    """Build a TestClient with BNBAGENT_AUTH_ENABLED=false (default)."""
    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as main_mod
    saved = {"AUTH_ENABLED": auth_mod.AUTH_ENABLED}
    monkeypatch.delenv("BNBAGENT_AUTH_ENABLED", raising=False)
    auth_mod.AUTH_ENABLED = False
    app = main_mod.build_app()
    with TestClient(app) as client:
        try:
            yield client
        finally:
            auth_mod.AUTH_ENABLED = saved["AUTH_ENABLED"]


# --- /api/auth/status -----------------------------------------------------

def test_status_public_when_auth_disabled(auth_disabled_client):
    """When AUTH_ENABLED=false, status returns role=admin (bypass)."""
    r = auth_disabled_client.get("/api/auth/status")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert data["role"] == "admin"


def test_status_no_role_when_auth_enabled_no_cookie(auth_enabled_client):
    r = auth_enabled_client.get("/api/auth/status")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["role"] is None


def test_status_returns_role_after_login(auth_enabled_client):
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "judge-test"})
    r = client.get("/api/auth/status")
    assert r.json()["role"] == "judge"


# --- /api/auth/login ------------------------------------------------------

def test_login_with_admin_password_sets_cookie(auth_enabled_client):
    r = auth_enabled_client.post("/api/auth/login", json={"password": "admin-test"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "role": "admin"}
    assert "bnbagent_session" in r.cookies


def test_login_with_judge_password_sets_cookie(auth_enabled_client):
    r = auth_enabled_client.post("/api/auth/login", json={"password": "judge-test"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "role": "judge"}


def test_login_rejects_bad_password(auth_enabled_client):
    r = auth_enabled_client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401
    assert "bnbagent_session" not in r.cookies


def test_login_does_not_leak_which_password_was_wrong(auth_enabled_client):
    """Both wrong-judge and wrong-admin should give the same generic 401."""
    r1 = auth_enabled_client.post("/api/auth/login", json={"password": "almost-judge"})
    r2 = auth_enabled_client.post("/api/auth/login", json={"password": "almost-admin"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()


# --- /api/auth/logout -----------------------------------------------------

def test_logout_clears_cookie(auth_enabled_client):
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    # Sanity: status reports admin before logout
    assert client.get("/api/auth/status").json()["role"] == "admin"
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    # After logout, the cookie is gone (or set to empty)
    # The next /api/auth/status should report no role
    r2 = client.get("/api/auth/status")
    assert r2.json()["role"] is None


# --- Role-gated mutations -------------------------------------------------

def test_admin_route_requires_admin_cookie(auth_enabled_client):
    """POST /api/setup/sign is admin-only. Without auth, 401."""
    r = auth_enabled_client.post("/api/setup/sign", json={})
    assert r.status_code == 401


def test_admin_route_rejects_judge_cookie(auth_enabled_client):
    """A judge role can't hit admin-only routes."""
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "judge-test"})
    r = client.post("/api/setup/sign", json={})
    assert r.status_code == 403


def test_admin_route_accepts_admin_cookie(auth_enabled_client):
    """An admin role can hit admin routes (may still 4xx on bad body, but
    not 401/403)."""
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/setup/sign", json={})
    # 4xx because the body is wrong, but NOT 401/403
    assert r.status_code != 401
    assert r.status_code != 403


def test_judge_route_accepts_judge_cookie(auth_enabled_client):
    """A judge role can hit judge-level routes (chat)."""
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "judge-test"})
    r = client.post("/api/chat", json={"message": "hi"})
    # Will be 503 because the chat agent isn't loaded in this test env,
    # but NOT 401/403.
    assert r.status_code != 401
    assert r.status_code != 403


def test_judge_route_accepts_admin_cookie(auth_enabled_client):
    """An admin can do anything a judge can do (admin > judge)."""
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code != 401
    assert r.status_code != 403


# --- AUTH_ENABLED=false bypass -------------------------------------------

def test_admin_route_works_without_password_when_disabled(auth_disabled_client):
    """Local dev (AUTH_ENABLED=false) needs no auth. Admin routes are open."""
    r = auth_disabled_client.post("/api/setup/sign", json={})
    # 4xx (bad body) is fine; 401/403 would mean auth is still on
    assert r.status_code != 401
    assert r.status_code != 403


# --- Wallet + Token protection (the "operator-only" surface) -----------

def test_export_mnemonic_blocked_by_default_even_for_admin(auth_enabled_client, monkeypatch):
    """Even with a valid admin cookie, /api/wallet/export-mnemonic is
    blocked unless BNBAGENT_ALLOW_WALLET_EXPORT=true. This is the
    defense-in-depth: a judge who somehow learns the admin password
    still cannot dump the seed phrase."""
    from dashboard.backend import auth as auth_mod
    # BNBAGENT_ALLOW_WALLET_EXPORT is unset (default) — must be blocked
    monkeypatch.delenv("BNBAGENT_ALLOW_WALLET_EXPORT", raising=False)
    # In case the host env has it set, scrub from the module too
    saved = getattr(auth_mod, "_WALLET_EXPORT_SAVED", None)
    if saved is None:
        os.environ.pop("BNBAGENT_ALLOW_WALLET_EXPORT", None)
    # Stub the token module so the route's other checks pass
    from dashboard.backend import main as main_mod
    main_mod.DASHBOARD_STATE.setdefault("components", {})["token_module"] = type("TM", (), {"config": {}})()

    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/wallet/export-mnemonic", json={"password": "anything"})
    assert r.status_code == 403
    assert "BNBAGENT_ALLOW_WALLET_EXPORT" in r.json()["detail"]


def test_export_mnemonic_unlocked_with_env_flag(auth_enabled_client, monkeypatch, tmp_path):
    """With BNBAGENT_ALLOW_WALLET_EXPORT=true AND admin cookie AND
    correct wallet password, the route returns the mnemonic. We
    write a fake keystore on disk and verify."""
    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as main_mod

    # Build a fake keystore on disk
    import json as _json
    import base64 as _b64
    ks_path = tmp_path / "wallet.json"
    # Use the real keystore format: encrypted AES-GCM blob. Easier: monkey-patch
    # the load_keystore function to return a controlled dict.
    ks_path.write_text(_json.dumps({"mnemonic": "***  dummy  mnemonic  phrase"}))
    monkeypatch.setenv("TWAK_KEYSTORE", str(ks_path))
    monkeypatch.setenv("BNBAGENT_ALLOW_WALLET_EXPORT", "true")

    def _fake_load(path, password):
        return {"mnemonic": "***  dummy  mnemonic  phrase"}
    monkeypatch.setattr("connectors.keystore.load_keystore", _fake_load)

    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/wallet/export-mnemonic", json={"password": "anything"})
    assert r.status_code == 200
    assert "***  dummy  mnemonic  phrase" in r.json()["mnemonic"]


def test_wallet_import_blocked_by_default_even_for_admin(auth_enabled_client, monkeypatch):
    """A judge with admin cookie still can't replace the wallet with
    their own key unless BNBAGENT_ALLOW_WALLET_IMPORT=true."""
    monkeypatch.delenv("BNBAGENT_ALLOW_WALLET_IMPORT", raising=False)
    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/setup/wallet/import", json={
        "private_key": "0x" + "a" * 64,
        "password": "test-pwd-1234",
    })
    assert r.status_code == 403
    assert "BNBAGENT_ALLOW_WALLET_IMPORT" in r.json()["detail"]


def test_wallet_import_unlocked_with_env_flag(auth_enabled_client, monkeypatch):
    """With the env flag set, import works (the route delegates to
    core.setup.import_wallet which would actually encrypt + write
    the keystore; we mock that)."""
    monkeypatch.setenv("BNBAGENT_ALLOW_WALLET_IMPORT", "true")

    from dashboard.backend import main as main_mod
    called = {}
    def _fake_import(pk, password):
        called["pk"] = pk
        called["password"] = password
        return {"address": "0x" + "a" * 40, "keystore_path": "/tmp/fake.json"}
    monkeypatch.setattr(main_mod, "import_wallet", _fake_import)

    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/setup/wallet/import", json={
        "private_key": "0x" + "a" * 64,
        "password": "test-pwd-1234",
    })
    assert r.status_code == 200
    assert called["pk"] == "0x" + "a" * 64
    assert r.json()["address"] == "0x" + "a" * 40


def test_token_deploy_returns_423_during_contest_window(auth_enabled_client, monkeypatch):
    """The /api/tokens/deploy route returns HTTP 423 (Locked) during
    the contest window, regardless of the BNBAGENT_ALLOW_TOKEN_DEPLOY
    env var. The TokenModule code path also raises PermissionError;
    the route short-circuits with a friendlier status for the UI."""
    from agents import token_module as tm_mod
    from datetime import datetime, timezone
    fake_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tm_mod.TokenModule, "_now_utc",
                        classmethod(lambda cls: fake_now))
    # Even with the env opt-in we're still locked during the contest
    monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")

    # Stub the token module so the route's other checks pass
    from dashboard.backend import main as main_mod
    class _FakeTM:
        def __init__(self): self.config = {}
    main_mod.DASHBOARD_STATE.setdefault("components", {})["token_module"] = _FakeTM()

    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/tokens/deploy", json={
        "name": "T", "symbol": "TST", "supply": 1000, "network": "testnet",
    })
    assert r.status_code == 423
    assert r.json()["error"] == "token_deploy_locked"
    assert "2026-07-07" in r.json()["message"]


def test_token_deploy_works_after_contest_with_env(auth_enabled_client, monkeypatch):
    """After 2026-07-07 with BNBAGENT_ALLOW_TOKEN_DEPLOY=true, the
    route is unlocked. We stub create_token to avoid real RPC."""
    from agents import token_module as tm_mod
    from datetime import datetime, timezone
    fake_now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tm_mod.TokenModule, "_now_utc",
                        classmethod(lambda cls: fake_now))
    monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")

    from dashboard.backend import main as main_mod
    from dataclasses import dataclass

    @dataclass
    class _R:
        symbol: str = "TST"
        network: str = "testnet"
        address: str = "0x" + "a" * 40
        tx_hash: str = "0x" + "b" * 64
        cid: str = None
        website: str = None

    class _FakeTM:
        def __init__(self): self.config = {}
        async def create_token(self, **kw): return _R()

    main_mod.DASHBOARD_STATE.setdefault("components", {})["token_module"] = _FakeTM()

    client = auth_enabled_client
    client.post("/api/auth/login", json={"password": "admin-test"})
    r = client.post("/api/tokens/deploy", json={
        "name": "Test", "symbol": "TST", "supply": 1000, "network": "testnet",
    })
    assert r.status_code == 200
    assert r.json()["result"]["symbol"] == "TST"
