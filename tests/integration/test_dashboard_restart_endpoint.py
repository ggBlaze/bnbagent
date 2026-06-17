"""A (v2.1.8): the dashboard endpoint that triggers an agent restart.

POST /api/agent/restart writes the restart marker to the control file
via core.control.request_restart(). The running agent's heartbeat picks
it up on its next tick (≤1s), gracefully shuts down with exit code 75,
and the bash wrapper re-execs. Admin-gated (mutates agent lifecycle).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_post_restart_writes_marker_to_control_file(monkeypatch, tmp_path):
    """The endpoint must persist the request so the agent (a sibling
    process) sees it. End-to-end: hit endpoint → control.json has
    'restart' key."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))
    from dashboard.backend import main as dash
    from core import control
    # Auth-disabled mode (no BNBAGENT_AUTH_* in env, which the conftest
    # scrubs automatically) means admin auth is bypassed.
    client = TestClient(dash.app)
    assert not control.is_restart_requested()
    resp = client.post("/api/agent/restart", json={"reason": "config change"})
    assert resp.status_code == 200, f"unexpected: {resp.status_code} {resp.text}"
    assert control.is_restart_requested(), "control file missing restart marker"


def test_post_restart_returns_requested_at_timestamp(monkeypatch, tmp_path):
    """The response carries the timestamp so the UI can show a 'restart
    requested 2s ago — waiting for agent to come back' indicator."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    resp = client.post("/api/agent/restart", json={"reason": "x"})
    body = resp.json()
    assert "requested_at" in body
    assert isinstance(body["requested_at"], (int, float))
    assert body["requested_at"] > 0


def test_post_restart_accepts_empty_body(monkeypatch, tmp_path):
    """No body → default reason. The UI can fire-and-forget."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))
    from dashboard.backend import main as dash
    from core import control
    client = TestClient(dash.app)
    resp = client.post("/api/agent/restart")
    assert resp.status_code == 200
    raw = control.read_control()
    assert raw["restart"]["reason"]  # non-empty default


def test_post_restart_is_admin_gated(monkeypatch, tmp_path):
    """When auth mode is `password`, the endpoint requires admin —
    a no-auth client gets 401/403. (Matches the gating on
    /api/control, /api/setup/*, /api/wallet/export-mnemonic.)

    AUTH_MODE is captured at module import; the existing
    tests/integration/test_auth.py pattern is to monkeypatch both the
    env var AND the cached module attribute, so we do the same."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))
    monkeypatch.setenv("BNBAGENT_AUTH_MODE", "password")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test")
    monkeypatch.setenv("JUDGE_PASSWORD", "judge-test")
    monkeypatch.setenv("BNBAGENT_AUTH_SECRET", "test-secret-restart-endpoint")
    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as dash
    from core import control
    original_mode = auth_mod.AUTH_MODE
    auth_mod.AUTH_MODE = "password"
    try:
        client = TestClient(dash.app)
        resp = client.post("/api/agent/restart")
        assert resp.status_code in (401, 403), (
            f"unauthenticated POST should be rejected; got {resp.status_code} "
            f"body={resp.text!r}"
        )
        assert not control.is_restart_requested(), (
            "rejected request must NOT write the marker"
        )
    finally:
        auth_mod.AUTH_MODE = original_mode
