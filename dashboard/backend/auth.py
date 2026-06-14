"""3-mode auth wrapper for the dashboard.

v2.1.5: 2-mode password wrapper for the public demo (Coolify).
v2.1.7: added `readonly` mode — passwordless public view where every
mutation route returns 403. Built for the BNB HACK 2026 contest
submission URL: judges can hit the page, see the agent live (live
state, sleeves, holdings, chat, replay), but cannot change anything.
The operator can SSH in and flip the env var to `password` to do
operator work, or to `disabled` to remove the wrapper entirely.

Three modes, one env var:
  * BNBAGENT_AUTH_MODE=disabled   (default) -> no auth, every request is admin
  * BNBAGENT_AUTH_MODE=password              -> JUDGE/ADMIN_PASSWORD gate
  * BNBAGENT_AUTH_MODE=readonly              -> no auth, mutations return 403

Backward compat: BNBAGENT_AUTH_ENABLED=true is treated as
BNBAGENT_AUTH_MODE=password. BNBAGENT_AUTH_ENABLED=false (or unset)
is treated as BNBAGENT_AUTH_MODE=disabled. The new var wins if both
are set, with a startup log line.

Two passwords, two roles (only meaningful in `password` mode):
  * JUDGE_PASSWORD -> "judge" role -> read-mostly demo
  * ADMIN_PASSWORD -> "admin" role -> full operator access

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
AuthMode = Literal["disabled", "password", "readonly"]
VALID_MODES = ("disabled", "password", "readonly")


def _resolve_mode() -> AuthMode:
    """Pick the active mode. New var wins; old var is the back-compat shim."""
    raw = os.environ.get("BNBAGENT_AUTH_MODE", "").strip().lower()
    if raw:
        if raw not in VALID_MODES:
            log.warning(
                "BNBAGENT_AUTH_MODE=%r is not one of %s. Defaulting to 'disabled'.",
                raw, VALID_MODES,
            )
            return "disabled"
        return raw  # type: ignore[return-value]
    # Backward compat: BNBAGENT_AUTH_ENABLED=true -> password, else disabled.
    enabled = os.environ.get("BNBAGENT_AUTH_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    return "password" if enabled else "disabled"


AUTH_MODE: AuthMode = _resolve_mode()
# Back-compat alias. New code should branch on AUTH_MODE.
AUTH_ENABLED: bool = AUTH_MODE in ("password",)

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
    if AUTH_MODE == "password":
        log.warning(
            "BNBAGENT_AUTH_SECRET is not set. Using a dev-only fallback; "
            "sessions will be invalidated on every restart. Set "
            "BNBAGENT_AUTH_SECRET to a stable value (e.g. `openssl rand -hex 32`) "
            "for production."
        )
    SECRET = _DEV_FALLBACK_SECRET

# Log the active mode at import time so deploy logs are self-explanatory.
log.info("dashboard auth mode: %s", AUTH_MODE)


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
    """Resolve the request's effective role for the current AUTH_MODE.

    * disabled -> everyone is admin (no checks, no cookie)
    * readonly -> everyone is judge (mutations will 403, reads pass)
    * password -> read the signed cookie, return role or None
    """
    if AUTH_MODE == "disabled":
        return "admin"
    if AUTH_MODE == "readonly":
        return "judge"
    return parse_token(request.cookies.get(COOKIE_NAME, ""))


def current_role(request: Request) -> Role | None:
    """FastAPI dependency that returns the current user's role or None."""
    return _role_from_request(request)


def require_role(min_role: Role):
    """Factory for a FastAPI dependency that requires at least `min_role`.

    Order: admin > judge. Admin can do anything a judge can.

    * disabled mode -> every request is treated as admin (no checks)
    * readonly mode -> admin-only routes return 403 (no way to escalate
      to admin from the public URL; the operator must flip the env var)
    * password mode -> check the signed cookie
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
            # Different error message in readonly mode so the operator's
            # logs make it obvious why a write was rejected.
            detail = (
                f"readonly mode: mutations disabled ({min_role}-only route)"
                if AUTH_MODE == "readonly"
                else f"role '{role}' cannot access {min_role}-only route"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail,
            )
        return role

    return dep


# Convenience: use as `Depends(require_admin)` or `Depends(require_judge)`.
require_admin = require_role("admin")
require_judge = require_role("judge")


# --- login / logout helpers (called from main.py) -------------------------

def check_password(password: str) -> Role | None:
    """Return the role for a given password, or None if it's neither.

    Only meaningful in `password` mode. The /api/auth/login endpoint
    refuses to authenticate in any other mode.
    """
    if password == ADMIN_PASSWORD:
        return "admin"
    if password == JUDGE_PASSWORD:
        return "judge"
    return None


def is_admin_request(request: Request) -> bool:
    """Cheap predicate for handlers that want to branch on admin without
    raising. Returns True for admin, False for judge, False for no auth.
    In readonly mode this is always False (everyone is judge)."""
    return _role_from_request(request) == "admin"


def current_mode() -> AuthMode:
    """Return the active mode (for /api/auth/status and the frontend)."""
    return AUTH_MODE

