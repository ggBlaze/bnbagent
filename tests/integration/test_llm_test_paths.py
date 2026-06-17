"""Integration tests for POST /api/llm/test — each provider's test
branch must return a sensible status for a key it can verify.

v2.1.5+: MiniMax joined the supported providers. The test endpoint
was missing a branch for it and was returning 'no test path for
minimax' to the wizard. This test pins the new branch so it
doesn't regress.
"""
from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response


# --- Test fixtures ---------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with auth disabled, chdir into tmp_path so dotenv
    helpers read/write there instead of the operator's real .env."""
    monkeypatch.chdir(tmp_path)
    # Seed an empty .env so _set_env_var_in_dotenv has something.
    (tmp_path / ".env").write_text("")

    from dashboard.backend import auth as auth_mod
    from dashboard.backend import main as main_mod
    saved = {"AUTH_ENABLED": auth_mod.AUTH_ENABLED}
    monkeypatch.delenv("BNBAGENT_AUTH_ENABLED", raising=False)
    auth_mod.AUTH_ENABLED = False
    app = main_mod.build_app()
    with TestClient(app) as c:
        try:
            yield c, tmp_path
        finally:
            auth_mod.AUTH_ENABLED = saved["AUTH_ENABLED"]


def _seed_env(tmp_path, **vars):
    """Write KEY=value lines to .env (used to simulate 'operator set
    the key already' for the test endpoint)."""
    lines = "\n".join(f"{k}={v}" for k, v in vars.items())
    (tmp_path / ".env").write_text(lines + "\n")


# --- minimax ---------------------------------------------------------------

@respx.mock
def test_test_path_minimax_calls_v1_models(client):
    """POST /api/llm/test with provider=minimax must hit
    https://api.minimaxi.chat/v1/models with Bearer auth, and report
    status=valid on a 200."""
    c, tmp_path = client
    _seed_env(tmp_path, MINIMAX_API_KEY="sk-cp-test-minimax-12345")

    route = respx.get("https://api.minimaxi.chat/v1/models").mock(
        return_value=Response(200, json={
            "object": "list",
            "data": [{"id": "MiniMax-M3"}, {"id": "MiniMax-M2"}],
        })
    )

    r = c.post("/api/llm/test", json={"provider": "minimax"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "minimax"
    assert body["status"] == "valid", f"got {body!r}"
    assert route.called
    # Verify the Bearer header was set
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer sk-cp-test-minimax-12345"


@respx.mock
def test_test_path_minimax_reports_invalid_on_401(client):
    """MiniMax returns 401 for a bad/revoked key → status=invalid."""
    c, tmp_path = client
    _seed_env(tmp_path, MINIMAX_API_KEY="bad-key")

    respx.get("https://api.minimaxi.chat/v1/models").mock(
        return_value=Response(401, json={"error": "invalid api key"})
    )

    r = c.post("/api/llm/test", json={"provider": "minimax"})
    body = r.json()
    assert body["status"] == "invalid", f"got {body!r}"
    assert "401" in body["note"]


def test_test_path_minimax_reports_missing_when_key_not_in_env(client):
    """No MINIMAX_API_KEY in .env → status=missing (not 'no test path')."""
    c, tmp_path = client
    _seed_env(tmp_path)  # empty

    r = c.post("/api/llm/test", json={"provider": "minimax"})
    body = r.json()
    assert body["status"] == "missing", f"got {body!r}"
    assert "MINIMAX_API_KEY" in body["note"]
