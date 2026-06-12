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
