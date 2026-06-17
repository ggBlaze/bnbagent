"""F1: dashboard endpoints read agent state from the IPC file.

End-to-end check: write a fake `dashboard_state.json` (what the agent
heartbeat would produce), hit `/api/stats`, `/api/config`, `/api/identity`,
and assert each endpoint sees the file content.

Also pins the layered-fallback contract: when the file is missing, the
dashboard falls back to its in-process `DASHBOARD_STATE` dict so existing
tests that mutate that dict directly keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Point the IPC file at a tmp path BEFORE the dashboard module is imported,
# so anything that resolves the path at import time picks up the override.
@pytest.fixture
def ipc_path(tmp_path, monkeypatch):
    p = tmp_path / "dashboard_state.json"
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(p))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()
    yield p
    ds._clear_cache_for_tests()


@pytest.fixture
def client(ipc_path):
    """Fresh FastAPI TestClient with the IPC path overridden."""
    from dashboard.backend import main as dash
    # Clear any leftover in-process state from earlier tests in this
    # process so we observe the file-vs-in-proc behavior cleanly.
    dash.DASHBOARD_STATE.clear()
    return TestClient(dash.app)


def _write_ipc(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()


def test_api_stats_reads_from_ipc_file(client, ipc_path):
    """The agent writes; the dashboard reads. Sidebar / tiles see live
    values within 2s of agent boot."""
    _write_ipc(ipc_path, {
        "stats": {
            "equity_usdc": "100.50",
            "pnl_today_usdc": "1.25",
            "drawdown_pct": "0.5",
            "kill_switch": False,
        },
        "updated_at": 1718600000,
    })
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["equity_usdc"] == "100.50"
    assert body["pnl_today_usdc"] == "1.25"


def test_api_config_reads_from_ipc_file(client, ipc_path):
    """`/api/config` powers the sidebar's mode/chain/addr/wallet rows."""
    _write_ipc(ipc_path, {
        "config": {"mode": "mainnet", "chain_id": 56,
                    "wallet_address": "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"},
    })
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "mainnet"
    assert body["chain_id"] == 56


def test_api_identity_reads_components_identity_from_ipc_file(client, ipc_path):
    """`/api/identity` populates the Agent Identity panel."""
    _write_ipc(ipc_path, {
        "config": {"chain_id": 56},
        "components": {
            "identity": {
                "token_id": "42",
                "cid": "bafyTEST",
                "agent_address": "0xAAA0000000000000000000000000000000000000",
                "evaluator_address": "0xBBB0000000000000000000000000000000000000",
                "version": "2.1.8",
            }
        },
    })
    resp = client.get("/api/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_id"] == "42"
    assert body["cid"] == "bafyTEST"
    assert body["agent_address"] == "0xAAA0000000000000000000000000000000000000"


def test_falls_back_to_in_process_state_when_ipc_missing(client):
    """If the IPC file does not exist (e.g. unit tests that mutate
    DASHBOARD_STATE directly), the dashboard sees the in-process dict.

    This is the back-compat path: every existing test that does
    `dash.DASHBOARD_STATE.update({...})` still works."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.update({
        "stats": {"equity_usdc": "999.00"},
        "config": {"mode": "testnet"},
    })
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json()["equity_usdc"] == "999.00"
    resp = client.get("/api/config")
    assert resp.json()["mode"] == "testnet"


def test_ipc_file_takes_precedence_over_in_process(client, ipc_path):
    """When both layers have data, the file is authoritative (it's the
    live agent state; in-proc may be stale leftover from test setup)."""
    from dashboard.backend import main as dash
    dash.DASHBOARD_STATE.update({"stats": {"equity_usdc": "stale"}})
    _write_ipc(ipc_path, {"stats": {"equity_usdc": "live"}})
    resp = client.get("/api/stats")
    assert resp.json()["equity_usdc"] == "live"
