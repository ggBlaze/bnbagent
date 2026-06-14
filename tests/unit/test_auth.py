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


# --- v2.1.7: 3-mode auth (disabled | password | readonly) -----------------

def test_auth_mode_disabled_by_default():
    """No env vars set -> mode='disabled', every request is admin."""
    with patch.dict("os.environ", {}, clear=False):
        # remove any set vars
        import os
        for k in ("BNBAGENT_AUTH_MODE", "BNBAGENT_AUTH_ENABLED",
                  "BNBAGENT_AUTH_SECRET", "JUDGE_PASSWORD", "ADMIN_PASSWORD"):
            os.environ.pop(k, None)
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_MODE == "disabled"
        assert auth_mod.AUTH_ENABLED is False  # back-compat alias
        assert auth_mod.current_mode() == "disabled"


def test_auth_mode_password_via_legacy_flag():
    """BNBAGENT_AUTH_ENABLED=true (no BNBAGENT_AUTH_MODE) -> mode='password'."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_ENABLED": "true",
                                  "BNBAGENT_AUTH_SECRET": "test-secret-12345"},
                   clear=False):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_MODE == "password"
        assert auth_mod.AUTH_ENABLED is True


def test_auth_mode_new_var_wins_over_legacy():
    """BNBAGENT_AUTH_MODE takes precedence over the legacy flag."""
    with patch.dict("os.environ", {
        "BNBAGENT_AUTH_MODE": "readonly",
        "BNBAGENT_AUTH_ENABLED": "true",   # would say password, but mode wins
        "BNBAGENT_AUTH_SECRET": "test-secret-12345",
    }, clear=False):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_MODE == "readonly"


def test_auth_mode_invalid_value_defaults_to_disabled():
    """Unknown mode -> disabled + warning log (not crash)."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_MODE": "garbage"}, clear=False):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        assert auth_mod.AUTH_MODE == "disabled"


def test_auth_mode_readonly_everyone_is_judge():
    """In readonly mode, no cookie is needed and every request is 'judge'."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_MODE": "readonly"},
                   clear=False):
        import importlib
        from fastapi import Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        # No cookie at all
        req = Request(scope={"type": "http", "headers": []})
        assert auth_mod.current_role(req) == "judge"
        assert auth_mod.is_admin_request(req) is False
        # Judge-only route passes
        assert auth_mod.require_judge(req) == "judge"
        # Admin-only route is 403
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            auth_mod.require_admin(req)
        assert exc.value.status_code == 403
        assert "readonly" in exc.value.detail.lower()


def test_auth_mode_readonly_ignores_admin_cookie():
    """Even if someone forges an admin cookie, readonly mode treats them as judge.

    The signed cookie is bypassed entirely in readonly mode \u2014 there's no
    way to escalate from a public URL.
    """
    with patch.dict("os.environ", {
        "BNBAGENT_AUTH_MODE": "readonly",
        "BNBAGENT_AUTH_SECRET": "test-secret-12345",
    }, clear=False):
        import importlib
        from fastapi import Request
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        admin_token = auth_mod.make_token("admin")
        req = Request(scope={
            "type": "http",
            "headers": [(b"cookie", f"bnbagent_session={admin_token}".encode())],
        })
        # Even with a real admin cookie, readonly mode downgrades to judge
        assert auth_mod.current_role(req) == "judge"
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            auth_mod.require_admin(req)


def test_check_password_only_meaningful_in_password_mode():
    """check_password works in any mode (returns the role), but only
    password mode actually issues sessions via /api/auth/login."""
    with patch.dict("os.environ", {"BNBAGENT_AUTH_MODE": "readonly"},
                   clear=False):
        import importlib
        import dashboard.backend.auth as auth_mod
        importlib.reload(auth_mod)
        # The function still classifies passwords correctly, but the
        # login endpoint refuses non-password modes (verified in the
        # integration test).
        assert auth_mod.check_password("admin") == "admin"
        assert auth_mod.check_password("judge") == "judge"
        assert auth_mod.check_password("nope")  is None
