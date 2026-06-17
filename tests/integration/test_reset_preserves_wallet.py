"""P7 (v2.1.8): the wizard's Reset Everything button silently wipes
the operator's encrypted wallet.

Symptom: operator imports wallet at ~/.twak/wallet.json, later clicks
"Reset Everything" (intent: clear bad config), now their wallet is
gone too. The pre-fix reset() always nuked the keystore. Documented
intent or not, this is at odds with the principle that destructive
operations should be explicit opt-ins.

Fix: reset() keeps ~/.twak/wallet.json by default. To also wipe the
wallet (e.g. wallet-rotation flow, hand-off to another operator),
the caller passes `include_wallet=True`. The endpoint reads the flag
from the request body so the frontend can offer two clearly-labeled
options ("Reset Config" vs "Reset Everything").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """Redirect ~/.twak and ~/.bnbagent to tmp dirs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".twak").mkdir()
    ks = home / ".twak" / "wallet.json"
    ks.write_text(json.dumps({"address": "0x" + "ed" * 20, "encrypted": {}}))
    monkeypatch.setenv("TWAK_KEYSTORE", str(ks))
    yield home


def test_reset_keeps_wallet_by_default(fake_home, monkeypatch, tmp_path):
    """The single biggest UX trap: clicking Reset must NOT silently
    take the operator's encrypted wallet with it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\n")
    (tmp_path / "config" / "local.yaml").write_text("mode: mainnet\n")
    from core.setup import reset
    ks_path = fake_home / ".twak" / "wallet.json"
    assert ks_path.exists()
    reset()
    assert ks_path.exists(), (
        "reset() must keep the wallet keystore by default; the operator "
        "explicitly imported it and only opts in to wiping with include_wallet=True"
    )
    # The other files SHOULD still be wiped (existing behavior).
    assert not (tmp_path / "config" / "local.yaml").exists()


def test_reset_with_include_wallet_true_wipes_keystore(fake_home, monkeypatch, tmp_path):
    """Explicit opt-in: include_wallet=True wipes the keystore too.
    Used for wallet rotation / handing the agent to another operator."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\n")
    from core.setup import reset
    ks_path = fake_home / ".twak" / "wallet.json"
    assert ks_path.exists()
    reset(include_wallet=True)
    assert not ks_path.exists(), (
        "include_wallet=True must wipe the keystore"
    )


def test_setup_reset_endpoint_keeps_wallet_by_default(fake_home, monkeypatch, tmp_path):
    """End-to-end: hit /api/setup/reset with empty body → wallet stays."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\n")
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    ks_path = fake_home / ".twak" / "wallet.json"
    assert ks_path.exists()
    resp = client.post("/api/setup/reset", json={})
    assert resp.status_code == 200
    assert ks_path.exists(), (
        "POST /api/setup/reset without include_wallet must KEEP the keystore"
    )


def test_setup_reset_endpoint_with_include_wallet_true_wipes(fake_home, monkeypatch, tmp_path):
    """Explicit opt-in via the endpoint body."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("mode: testnet\n")
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    ks_path = fake_home / ".twak" / "wallet.json"
    resp = client.post("/api/setup/reset", json={"include_wallet": True})
    assert resp.status_code == 200
    assert not ks_path.exists()
