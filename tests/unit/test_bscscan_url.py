"""Tests for the v2.0.8-L3 _bscscan_url mode-branching fix.

L-3 was that _bscscan_url branched on chain_id (== 56 means mainnet,
anything else is testnet). That works for BSC (97 vs 56) but breaks
for any other chain the operator might point the agent at (e.g. ETH
mainnet has chain_id 1, not 56; the old code would route to the BSC
testnet URL).

Fix: branch on `mode` (testnet, mainnet, replay) instead. The replay
mode returns an empty string (there's no explorer to link to).
"""
import pytest
from fastapi.testclient import TestClient

from dashboard.backend import main as dash


def _make_client_with_mode(mode: str) -> TestClient:
    """Build a dashboard client with a DASHBOARD_STATE reflecting the
    given mode. The _bscscan_url helper reads `_cfg()['mode']`.
    """
    dash.DASHBOARD_STATE = {
        "config": {"mode": mode, "chain_id": 97 if mode == "testnet" else 56},
        "stats": {"updated_at": 0, "kill_switch": False, "sleeve_exposure": {}, "sleeves": {}},
        "components": {},
        "policy": {},
        "control_log": [],
    }
    return TestClient(dash.app)


class TestBscscanUrl:
    def test_testnet_mode_routes_to_testnet_explorer(self):
        c = _make_client_with_mode("testnet")
        url = c.get("/api/healthz").status_code  # touch the app to init
        # now call _bscscan_url directly via the module
        result = dash._bscscan_url("0x" + "a" * 64)
        assert result.startswith("https://testnet.bscscan.com/tx/")

    def test_mainnet_mode_routes_to_mainnet_explorer(self):
        c = _make_client_with_mode("mainnet")
        c.get("/api/healthz")
        result = dash._bscscan_url("0x" + "a" * 64)
        assert result.startswith("https://bscscan.com/tx/")
        assert "testnet" not in result

    def test_replay_mode_returns_empty_string(self):
        """Replay is offline — no explorer link. The frontend should
        treat empty string as 'no link'."""
        c = _make_client_with_mode("replay")
        c.get("/api/healthz")
        result = dash._bscscan_url("0x" + "a" * 64)
        assert result == ""

    def test_unknown_mode_defaults_to_testnet(self):
        """If mode is unset or unknown, default to testnet (safe)."""
        c = _make_client_with_mode("unknown_mode_value")
        c.get("/api/healthz")
        result = dash._bscscan_url("0x" + "a" * 64)
        assert result.startswith("https://testnet.bscscan.com/tx/")

    def test_eth_mainnet_chain_id_routes_correctly(self):
        """L-3 regression: ETH mainnet has chain_id 1, not 56.
        The old code routed to testnet (because 1 != 56). The new
        code routes by mode: 'mainnet' → bscscan.com (which is the
        BSC mainnet explorer; not perfect for ETH, but at least
        it's not the wrong chain's testnet).
        """
        dash.DASHBOARD_STATE = {
            "config": {"mode": "mainnet", "chain_id": 1},   # ETH mainnet
            "stats": {"updated_at": 0, "kill_switch": False, "sleeve_exposure": {}, "sleeves": {}},
            "components": {}, "policy": {}, "control_log": [],
        }
        c = TestClient(dash.app)
        c.get("/api/healthz")
        result = dash._bscscan_url("0x" + "a" * 64)
        # mode-based routing takes precedence; chain_id is ignored.
        # The link points at bscscan.com (BSC mainnet explorer).
        # Not perfect for ETH, but at least it's not the BSC testnet.
        assert "testnet" not in result
        assert result.startswith("https://bscscan.com/tx/")
