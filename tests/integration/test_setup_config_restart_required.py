"""P6 (v2.1.8): /api/setup/config response signals the operator
needs to restart the agent for new config to take effect.

The wizard's Configure step writes mode / chain / RPCs to
config/local.yaml via this endpoint. The running agent (if any)
won't see the change until it re-execs — pre-P6 the operator had
to know to click the Restart Agent button (P5) AFTER saving. P6
returns `restart_required: true` so the frontend can either auto-
fire /api/agent/restart or prompt the operator inline.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_setup_config_response_includes_restart_required(monkeypatch, tmp_path):
    """A successful save MUST tell the caller a restart is needed.
    Saving config without restarting leaves the agent on its stale
    in-memory copy — the exact bug operators kept hitting."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    # Minimal config.yaml so set_runtime_config has a base to merge into.
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\nchain_id: 97\nrpcs: []\n")
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    resp = client.post("/api/setup/config", json={
        "mode": "mainnet", "chain_id": 56,
        "rpcs": ["https://bsc-dataseed.binance.org"],
    })
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("restart_required") is True, (
        "saving runtime config means the agent's in-memory copy is now "
        "stale; the response must signal restart_required=True so the "
        "frontend can fire /api/agent/restart"
    )


def test_setup_config_failure_does_not_set_restart_required(monkeypatch, tmp_path):
    """Don't tell the operator to restart if the save failed — there's
    nothing new to pick up."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\nchain_id: 97\nrpcs: []\n")
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    # chain_id=99 is invalid (must be 56 or 97) — set_runtime_config raises.
    resp = client.post("/api/setup/config", json={
        "mode": "mainnet", "chain_id": 99, "rpcs": [],
    })
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("ok") is False
    assert "restart_required" not in body or body.get("restart_required") is False, (
        "failure path must not claim restart_required"
    )


def test_frontend_save_handler_acts_on_restart_required():
    """The wizard's Configure-step save handler must check the
    restart_required flag and call /api/agent/restart when it's true."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    # The flag must be read somewhere in the JS.
    assert "restart_required" in html, (
        "frontend must check response.restart_required from /api/setup/config"
    )
