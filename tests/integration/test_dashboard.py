"""Test the /api/data-source endpoints added in v2.1.

These tests target the dashboard FastAPI app (dashboard/backend/main.py).
The endpoints are:

  GET  /api/data-source                  -> active tier + status
  POST /api/data-source/select            -> persist + hot-swap
  POST /api/data-source/cmc-key           -> persist CMC Pro API key
  POST /api/data-source/base-rpcs         -> persist Base RPC list

The endpoints must not 5xx on a no-agent state (TestClient with empty
DASHBOARD_STATE); they must always return JSON, falling back to a
mock tier when the agent hasn't booted a router yet.
"""
from __future__ import annotations

import pytest
import respx


# --- data source endpoints (v2.1) ---

def test_get_data_source_returns_tier_and_status():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.get("/api/data-source")
    assert r.status_code == 200
    body = r.json()
    assert "tier" in body
    assert "status" in body


def test_post_data_source_select_persists():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/data-source/select", json={"tier": "binance"})
    assert r.status_code == 200
    # Re-read confirms the choice
    with TestClient(app) as client:
        r = client.get("/api/data-source")
    assert r.json()["tier"] == "binance"


def test_post_data_source_cmc_key_sets_key():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/data-source/cmc-key", json={"api_key": "test-key-xyz"})
    assert r.status_code == 200


def test_post_data_source_base_rpcs_persists_list():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    rpcs = ["https://mainnet.base.org", "https://base.publicnode.com"]
    with TestClient(app) as client:
        r = client.post("/api/data-source/base-rpcs", json={"base_rpcs": rpcs})
    assert r.status_code == 200
    with TestClient(app) as client:
        r = client.get("/api/data-source")
    assert r.json()["base_rpcs"] == rpcs


def test_post_data_source_base_rpcs_rejects_invalid_url():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/data-source/base-rpcs", json={"base_rpcs": ["not-a-url"]})
    assert r.status_code == 422  # validation error


def test_post_data_source_select_cmc_pro_without_key_returns_400(tmp_path, monkeypatch):
    """Selecting cmc_pro without a key must 400, not silently degrade to mock."""
    import yaml
    # v2.1.1: endpoint now reads via the local.yaml shadow pattern
    # (core.config_paths.load_config), which resolves `config/config.yaml`
    # + `config/local.yaml` relative to cwd. Lay the fixture out in
    # tmp_path/config/ to match the helper's resolution.
    cfg = {
        "data_source": {"tier": "cmc_pro", "cmc_api_key": "", "base_rpcs": []},
    }
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.chdir(tmp_path)

    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/data-source/select", json={"tier": "cmc_pro"})
    assert r.status_code == 400, r.text
    assert "cmc_api_key" in r.json().get("error", "").lower() or "api key" in r.json().get("error", "").lower()


def test_post_data_source_select_x402_without_base_address_returns_400(tmp_path, monkeypatch):
    """Selecting x402 without a Base address must 400, not silently degrade to mock."""
    import yaml
    cfg = {
        "data_source": {"tier": "x402", "cmc_api_key": "", "base_rpcs": ["https://mainnet.base.org"], "base_address": ""},
    }
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.chdir(tmp_path)

    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/data-source/select", json={"tier": "x402"})
    assert r.status_code == 400, r.text
    assert "base_address" in r.json().get("error", "").lower() or "base address" in r.json().get("error", "").lower()


# --- x402 balance polling (v2.1) ---

@respx.mock
def test_get_x402_balance_returns_decimal():
    """GET /api/data-source/x402-balance polls the Base USDC balance.

    Test uses Option A: the endpoint accepts ?address=0x... so we don't
    need a wallet in the test process. We patch _get_web3 (the seam
    exposed by connectors/x402.py) since web3 uses the `requests`
    library, which respx doesn't intercept.
    """
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app

    test_address = "0x" + "ab" * 20
    # 1.5 USDC = 1_500_000 raw = 0x16e360
    fake_w3 = MagicMock()
    fake_w3.eth.call.return_value = int(1_500_000).to_bytes(32, "big")
    with respx.mock, patch("connectors.x402._get_web3", return_value=fake_w3):
        with TestClient(app) as client:
            r = client.get(f"/api/data-source/x402-balance?address={test_address}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "balance_usdc" in body
    assert "ready" in body
    assert "address" in body
    # 1_500_000 raw / 1_000_000 = 1.5 USDC
    assert abs(body["balance_usdc"] - 1.5) < 1e-9
    assert body["ready"] is True


# --- export mnemonic (v2.1) ---

def test_export_mnemonic_requires_password():
    """POST without a password should return 400/401/422."""
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/wallet/export-mnemonic", json={})
    assert r.status_code in (400, 401, 422)


def test_export_mnemonic_returns_phrase_with_correct_password(monkeypatch):
    """Mock the keystore loader; verify the endpoint returns the phrase."""
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app

    # Patch wherever the endpoint imports the load_keystore function.
    # The endpoint does `from connectors.keystore import load_keystore`
    # inside the handler, so the import will resolve via sys.modules['connectors.keystore'].
    test_mnemonic = "test test test test test test test test test test test junk"
    import connectors.keystore as _ks_mod
    monkeypatch.setattr(
        _ks_mod,
        "load_keystore",
        lambda path, password: {"mnemonic": test_mnemonic, "address": "0x" + "11" * 20},
    )

    with TestClient(app) as client:
        r = client.post("/api/wallet/export-mnemonic", json={"password": "anything"})
    assert r.status_code == 200
    body = r.json()
    assert "mnemonic" in body
    assert body["mnemonic"] == test_mnemonic


# --- LLM API key UI (v2.1.3) -----------------------------------------------

def test_post_llm_key_writes_to_dotenv(tmp_path, monkeypatch):
    """Setting a provider key writes (or replaces) the env var in .env."""
    import os
    # Use a fresh tmp dir so we don't clobber the real .env
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/key", json={"provider": "openrouter", "key": "sk-test-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "openrouter"
    assert body["env_var"] == "OPENROUTER_API_KEY"
    assert body["restart_required"] is True
    # Read back from .env
    dotenv = (tmp_path / ".env").read_text()
    assert "OPENROUTER_API_KEY=sk-test-123" in dotenv


def test_post_llm_key_replaces_existing(tmp_path, monkeypatch):
    """Calling set twice with the same provider replaces, not appends."""
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        client.post("/api/llm/key", json={"provider": "openai", "key": "sk-first"})
        r2 = client.post("/api/llm/key", json={"provider": "openai", "key": "sk-second"})
    assert r2.status_code == 200
    dotenv = (tmp_path / ".env").read_text()
    # Only one entry for OPENAI_API_KEY, and it's the latest.
    assert dotenv.count("OPENAI_API_KEY=") == 1
    assert "OPENAI_API_KEY=sk-second" in dotenv
    assert "sk-first" not in dotenv


def test_post_llm_key_preserves_other_env_vars(tmp_path, monkeypatch):
    """Setting a key doesn't clobber unrelated entries in .env."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# my config\n"
        "FOO=bar\n"
        "OPENROUTER_API_KEY=old-key\n"
        "BAZ=qux\n"
    )
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/key", json={"provider": "openrouter", "key": "new-key"})
    assert r.status_code == 200
    dotenv = (tmp_path / ".env").read_text()
    assert "# my config" in dotenv
    assert "FOO=bar" in dotenv
    assert "BAZ=qux" in dotenv
    assert "OPENROUTER_API_KEY=new-key" in dotenv
    assert "old-key" not in dotenv


def test_post_llm_key_rejects_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/key", json={"provider": "made-up", "key": "x"})
    assert r.status_code == 400
    assert "unknown provider" in r.json()["error"].lower()


def test_post_llm_key_rejects_empty_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/key", json={"provider": "openrouter", "key": ""})
    assert r.status_code == 400
    assert "key required" in r.json()["error"].lower()


def test_post_llm_test_reports_missing(tmp_path, monkeypatch):
    """Test reads .env directly, not os.environ. Missing key = 'missing' status."""
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/test", json={"provider": "openrouter"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "missing"
    assert "OPENROUTER_API_KEY" in body["note"]


def test_post_llm_test_local_is_na(tmp_path, monkeypatch):
    """The 'local' provider has no key — /api/llm/test returns n/a status."""
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/test", json={"provider": "local"})
    assert r.status_code == 200
    assert r.json()["status"] == "n/a"


def test_post_llm_test_oai_compat_requires_base(tmp_path, monkeypatch):
    """oai_compat needs OAI_BASE in .env or test returns missing-base."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OAI_KEY=sk-test-123\n")
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    with TestClient(app) as client:
        r = client.post("/api/llm/test", json={"provider": "oai_compat"})
    assert r.status_code == 200
    assert r.json()["status"] == "missing-base"
    assert "OAI_BASE" in r.json()["note"]
