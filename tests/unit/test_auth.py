"""Unit tests for dashboard/backend/auth.py — the 2-mode password wrapper.

The wrapper is the only thing standing between a public Coolify URL
and a judge's accidental operator action. These tests lock the
contract: cookie format, role gating, AUTH_ENABLED bypass."""
import base64
import json
import time
from unittest.mock import patch

import pytest

# Reset module-level config between tests so env-var mocking is clean.
@pytest.fixture(autouse=True)
def reset_auth_module():
    """Force a re-import of the auth module so env-var reads happen
    fresh per test (otherwise module-level SECRET / AUTH_ENABLED
    get frozen at first import)."""
    import importlib
    import dashboard.backend.auth as auth_mod
    importlib.reload(auth_mod)
    yield
    importlib.reload(auth_mod)


def test_auth_disabled_by_default():
    """BNBAGENT_AUTH_ENABLED defaults to false. Local dev just works."""
    with patch.dict("os.environ", {}, clear=False):
        # remove the var if set
        import os
        os.environ.pop("BNBAGENT_AUTH_ENABLED", None)
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_ENABLED is False


def test_auth_enabled_via_env():
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_ENABLED is True


def test_check_password_default_judge():
    """The default judge password is 'judge' (dev)."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.check_password("judge") == "judge"
        assert auth_mod.check_password("admin") == "admin"
        assert auth_mod.check_password("wrong") is None
        assert auth_mod.check_password("") is None


def test_custom_passwords_override_defaults():
    with patch.dict("os.environ", {
        "BNBAGENT_AUTH_ENABLED": "true",
        "JUDGE_PASSWORD": "see-the-agent",
        "ADMIN_PASSWORD": "operate-the-agent",
    }):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.check_password("see-the-agent") == "judge"
        assert auth_mod.check_password("operate-the-agent") == "admin"
        # Defaults no longer work
        assert auth_mod.check_password("judge") is None
        assert auth_mod.check_password("admin") is None


def test_make_and_parse_token_roundtrip():
    """A token issued for a role should be parseable back to that role."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        token = auth_mod.make_token("admin")
        assert auth_mod.parse_token(token) == "admin"
        token = auth_mod.make_token("judge")
        assert auth_mod.parse_token(token) == "judge"


def test_parse_token_rejects_bad_signature():
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        token = auth_mod.make_token("admin")
        # Tamper with the signature
        tampered = token[:-5] + "XXXXX"
        assert auth_mod.parse_token(tampered) is None


def test_parse_token_rejects_expired():
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        # Make an already-expired token by setting a tiny expiry
        import base64
        payload = base64.urlsafe_b64encode(
            json.dumps({"role": "admin", "exp": int(time.time()) - 100}).encode()
        ).decode().rstrip("=")
        token = auth_mod._sign(payload)
        assert auth_mod.parse_token(token) is None


def test_parse_token_rejects_unknown_role():
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        payload = base64.urlsafe_b64encode(
            json.dumps({"role": "god", "exp": int(time.time()) + 1000}).encode()
        ).decode().rstrip("=")
        token = auth_mod._sign(payload)
        assert auth_mod.parse_token(token) is None


def test_require_admin_rejects_judge():
    """A judge-role token can't access admin-only routes."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        from fastapi import HTTPException
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        from fastapi import Request
        # Build a fake request with a judge cookie
        token = auth_mod.make_token("judge")
        req = Request(scope={
            "type": "http",
            "headers": [(b"cookie", f"bnbagent_session={token}".encode())],
        })
        with pytest.raises(HTTPException) as exc_info:
            auth_mod.require_admin(req)
        assert exc_info.value.status_code == 403


def test_require_admin_accepts_admin():
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        from fastapi import Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        token = auth_mod.make_token("admin")
        req = Request(scope={
            "type": "http",
            "headers": [(b"cookie", f"bnbagent_session={token}".encode())],
        })
        assert auth_mod.require_admin(req) == "admin"


def test_require_admin_rejects_no_auth_when_enabled():
    """When AUTH_ENABLED=true and no cookie, raise 401 (not bypass)."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        from fastapi import HTTPException, Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        req = Request(scope={"type": "http", "headers": []})
        with pytest.raises(HTTPException) as exc_info:
            auth_mod.require_admin(req)
        assert exc_info.value.status_code == 401


def test_require_admin_bypasses_when_disabled():
    """When AUTH_ENABLED=false, every request is treated as admin (no auth)."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "false"}):
        import importlib
        from fastapi import Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        # Empty request, no cookie, no header — should still be 'admin'
        req = Request(scope={"type": "http", "headers": []})
        assert auth_mod.require_admin(req) == "admin"


def test_require_judge_accepts_both_roles():
    """The judge dependency is satisfied by either judge or admin role."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"}):
        import importlib
        from fastapi import Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        for role in ("judge", "admin"):
            token = auth_mod.make_token(role)
            req = Request(scope={
                "type": "http",
                "headers": [(b"cookie", f"bnbagent_session={token}".encode())],
            })
            assert auth_mod.require_judge(req) == role
