"""2-mode password wrapper for the public demo.

v2.1.5: the dashboard is going on a public VPS via Coolify. We need
a way to gate the "operator" controls (setup wizard, policy sign,
persona edit, registration, kill switch, wallet export) from the
"judge demo" controls (live state, chat, replay, persona read).

Two passwords, two roles:
  * JUDGE_PASSWORD -> "judge" role -> read-mostly demo
  * ADMIN_PASSWORD -> "admin" role -> full operator access

A single flag controls whether auth is on at all:
  * BNBAGENT_AUTH_ENABLED=true (default OFF) -> password required
  * BNBAGENT_AUTH_ENABLED=false              -> no auth, everyone is admin

The OFF mode is for local dev: `bash bnbagent` on a laptop doesn't need
a password. The ON mode is for the Coolify public deploy.

Session is a signed cookie (HMAC-SHA256, stdlib only — no new dep).
Cookie carries {role, exp}; expired cookies are rejected. The secret
key is read from BNBAGENT_AUTH_SECRET (stable across restarts) with a
fallback to a dev-only constant + a warning. Set BNBAGENT_AUTH_SECRET
in production or sessions will be invalidated on every restart.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Literal

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)

# --- configuration ---------------------------------------------------------

Role = Literal["judge", "admin"]

# Public, so the frontend can read its own role.
AUTH_ENABLED: bool = os.environ.get("BNBAGENT_AUTH_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
# Default passwords are intentionally obvious — they're dev defaults.
# In production, set both env vars to non-guessable values (or random
# ones via `openssl rand -hex 32`).
JUDGE_PASSWORD: str = os.environ.get("JUDGE_PASSWORD", "judge")
ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "admin")

# Cookie settings. httponly + samesite=strict to mitigate XSS. The
# 'secure' flag is set dynamically based on the request scheme in
# set_session_cookie() so the cookie works over http (local dev) and
# https (Coolify deploy) without config.
COOKIE_NAME: str = "bnbagent_session"
COOKIE_MAX_AGE: int = 86400  # 1 day

# Dev-only fallback secret. WARN at import time so production deploys
# see the warning in their startup logs and set the real env var.
_DEV_FALLBACK_SECRET = "bnbagent-dev-only-do-not-use-in-prod-c2c6a5b9"
_SECRET_FROM_ENV = os.environ.get("BNBAGENT_AUTH_SECRET", "")
if _SECRET_FROM_ENV:
    SECRET: str = _SECRET_FROM_ENV
else:
    if AUTH_ENABLED:
        log.warning(
            "BNBAGENT_AUTH_SECRET is not set. Using a dev-only fallback; "
            "sessions will be invalidated on every restart. Set "
            "BNBAGENT_AUTH_SECRET to a stable value (e.g. `openssl rand -hex 32`) "
            "for production."
        )
    SECRET = _DEV_FALLBACK_SECRET


# --- cookie sign / verify --------------------------------------------------

def _sign(payload: str) -> str:
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify(signed: str) -> str | None:
    if "." not in signed:
        return None
    payload, sig = signed.rsplit(".", 1)
    expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return payload


def make_token(role: Role) -> str:
    """Create a signed cookie value for a given role. Caller sets it on the
    response via set_session_cookie()."""
    expires = int(time.time()) + COOKIE_MAX_AGE
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": role, "exp": expires}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return _sign(payload)


def parse_token(token: str) -> Role | None:
    """Return the role encoded in a signed cookie, or None if the cookie
    is missing, malformed, expired, or has a bad signature."""
    if not token:
        return None
    payload = _verify(token)
    if not payload:
        return None
    try:
        # Re-pad base64 (we stripped '=' for cookie-cleanliness)
        data = json.loads(base64.urlsafe_b64decode(payload + "==").decode())
    except Exception:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    role = data.get("role")
    if role not in ("judge", "admin"):
        return None
    return role  # type: ignore[return-value]


# --- request-level role resolution ----------------------------------------

def _role_from_request(request: Request) -> Role | None:
    """Read the session cookie and return the role. When AUTH_ENABLED is
    off, returns 'admin' (bypass) without inspecting the cookie."""
    if not AUTH_ENABLED:
        return "admin"
    return parse_token(request.cookies.get(COOKIE_NAME, ""))


def current_role(request: Request) -> Role | None:
    """FastAPI dependency that returns the current user's role or None."""
    return _role_from_request(request)


def require_role(min_role: Role):
    """Factory for a FastAPI dependency that requires at least `min_role`.

    Order: admin > judge. Admin can do anything a judge can.
    When AUTH_ENABLED is off, every request is treated as admin.
    """
    required_level = 1 if min_role == "judge" else 2

    def dep(request: Request) -> Role:
        role = _role_from_request(request)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="login required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        level = 2 if role == "admin" else 1
        if level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{role}' cannot access {min_role}-only route",
            )
        return role

    return dep


# Convenience: use as `Depends(require_admin)` or `Depends(require_judge)`.
require_admin = require_role("admin")
require_judge = require_role("judge")


# --- login / logout helpers (called from main.py) -------------------------

def check_password(password: str) -> Role | None:
    """Return the role for a given password, or None if it's neither."""
    if password == ADMIN_PASSWORD:
        return "admin"
    if password == JUDGE_PASSWORD:
        return "judge"
    return None


def is_admin_request(request: Request) -> bool:
    """Cheap predicate for handlers that want to branch on admin without
    raising. Returns True for admin, False for judge, False for no auth."""
    return _role_from_request(request) == "admin"
