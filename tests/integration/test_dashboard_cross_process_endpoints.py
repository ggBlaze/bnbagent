"""UX4 (v2.1.8): endpoints reading per-component method calls must
not crash cross-process.

Pre-fix the dashboard returned 500 for:
  - /api/llm/status     (called `router.status()` on a dict)
  - /api/agent/advisor  (called `adv.recent()` on a dict)
  - /api/agent/reviewer (called `r.recent()` on a dict)

All three were broken once F1+P4 made components arrive as dict
snapshots in the cross-process IPC. The endpoints now use the
_component_attr helper (P4) or try/except + an IPC-field fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ipc_with_dict_components(tmp_path, monkeypatch):
    """Write a cross-process-shaped snapshot to the IPC file: components
    arrive as dicts (not objects). This is what the agent publishes
    via P4's _snapshot_for_publish."""
    p = tmp_path / "dashboard_state.json"
    p.write_text(json.dumps({
        "stats": {"updated_at": 1781734400, "equity": 100.0},
        "config": {"mode": "mainnet", "chain_id": 56},
        "components": {
            # llm_router as a dict with {status} — P4 shape.
            "llm_router": {"status": {
                "providers": {"MiniMax": {"key_set": True}},
                "agents": {"advisor": {"enabled": True}},
            }},
            # advisor / reviewers as dicts (no .recent method).
            "advisor": {"status": {"enabled": True}},
            "reviewers": {"A": {"status": {}}, "B": {"status": {}}},
        },
    }))
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(p))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()
    yield p
    ds._clear_cache_for_tests()


def test_llm_status_endpoint_does_not_crash_on_dict_router(ipc_with_dict_components):
    """Pre-fix: 500 with 'dict has no attribute status'. Post-fix: 200
    with the published status dict."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.clear()
    client = TestClient(dash.app)
    resp = client.get("/api/llm/status")
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "agents" in body or "providers" in body or body == {}


def test_advisor_endpoint_does_not_crash_on_dict_component(ipc_with_dict_components):
    """Pre-fix: 500 with 'dict has no attribute recent'. Post-fix:
    200 with empty decisions (since the agent hasn't published any)."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.clear()
    client = TestClient(dash.app)
    resp = client.get("/api/agent/advisor")
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "decisions" in body
    assert isinstance(body["decisions"], list)


def test_reviewer_endpoint_does_not_crash_on_dict_component(ipc_with_dict_components):
    """Pre-fix: 500 iterating reviewer dicts with .recent. Post-fix:
    200, empty decisions in cross-process mode."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.clear()
    client = TestClient(dash.app)
    resp = client.get("/api/agent/reviewer")
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "decisions" in body
    assert isinstance(body["decisions"], list)


def test_reviewer_endpoint_with_sleeve_filter(ipc_with_dict_components):
    """The ?sleeve=A path must also stay 200 when r is a dict."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.clear()
    client = TestClient(dash.app)
    resp = client.get("/api/agent/reviewer?sleeve=A")
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
