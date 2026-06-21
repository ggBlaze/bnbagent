"""v2.2.0: _identity() must actually return the identity dict.

The 2026-06-21 08:42 incident (continued): even with the identity.json
fixed, the dashboard's /api/identity endpoint returned
{"error": "no identity registered"} because _identity() in
dashboard/backend/main.py had no return statement — the function
body was just `s = _state()`. The correct `return s.get(...)` line
was in the file but had drifted into the wrong function
(_mode_aware_stats) as dead code after a refactor. This test fails
if _identity() ever returns None again.
"""
from __future__ import annotations

import importlib
import sys


def test_identity_returns_dict_with_agent_address(monkeypatch):
    """_identity() must return the identity dict, not None."""
    # Re-import the main module so we get the latest version of _identity
    if "dashboard.backend.main" in sys.modules:
        importlib.reload(sys.modules["dashboard.backend.main"])
    from dashboard.backend import main

    fake_identity = {
        "token_id": 1,
        "cid": "QmTest",
        "agent_address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        "evaluator_address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9",
        "version": "2.2.0",
    }
    # Monkey-patch _state() to return our fake
    monkeypatch.setattr(main, "_state", lambda: {"components": {"identity": fake_identity}})
    result = main._identity()
    assert result is not None, "_identity() returned None — the return statement is missing"
    assert result.get("agent_address") == "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"
    assert result.get("token_id") == 1
    assert result.get("version") == "2.2.0"


def test_identity_returns_empty_dict_when_no_components(monkeypatch):
    """_identity() returns {} when there's no components.identity, but NEVER None."""
    if "dashboard.backend.main" in sys.modules:
        importlib.reload(sys.modules["dashboard.backend.main"])
    from dashboard.backend import main

    monkeypatch.setattr(main, "_state", lambda: {})
    result = main._identity()
    assert result == {}, f"_identity() should return {{}} for empty state, got: {result!r}"
    assert result is not None, "_identity() must never return None"
