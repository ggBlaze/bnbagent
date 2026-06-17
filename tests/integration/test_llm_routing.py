"""Integration tests for /api/llm/routing + /api/llm/models.

The wizard's per-agent model selector + the Config pane's per-agent
routing table both call these endpoints. /routing round-trips the
agent's current provider+model through .env (LLM_<AGENT>_PROVIDER /
LLM_<AGENT>_MODEL); /models fetches the provider's available model
ids so the wizard dropdown is populated with real options.
"""
from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response


AGENT_NAMES = ["advisor", "reviewer", "chat", "token_module"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    lines = "\n".join(f"{k}={v}" for k, v in vars.items())
    (tmp_path / ".env").write_text(lines + "\n")


# --- /api/llm/routing ------------------------------------------------------

def test_routing_get_returns_all_four_agents_with_defaults(client):
    """Without env overrides, /api/llm/routing returns each of the
    four agents with whatever providers.yaml says (or 'openrouter'
    default). The exact provider/model strings vary by what's loaded,
    so we only assert the shape and the four agent names."""
    c, _tmp = client
    r = c.get("/api/llm/routing")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == set(AGENT_NAMES), f"got {body.keys()}"
    for agent in AGENT_NAMES:
        assert "provider" in body[agent], body[agent]
        assert "model" in body[agent], body[agent]


def test_routing_get_reflects_env_overrides(client, monkeypatch):
    """Env vars LLM_<AGENT>_PROVIDER / LLM_<AGENT>_MODEL win over YAML.

    The router reads os.environ (not .env) at request time, so the
    test sets them via monkeypatch.setenv — same way the operator's
    shell env would carry them after a restart, and same way the
    POST handler will update them on save (next commit)."""
    monkeypatch.setenv("LLM_ADVISOR_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_ADVISOR_MODEL", "MiniMax-Mini")
    monkeypatch.setenv("LLM_REVIEWER_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_REVIEWER_MODEL", "MiniMax-M3")

    c, _tmp = client
    r = c.get("/api/llm/routing")
    body = r.json()
    assert body["advisor"]["provider"] == "minimax"
    assert body["advisor"]["model"] == "MiniMax-Mini"
    assert body["reviewer"]["provider"] == "minimax"
    assert body["reviewer"]["model"] == "MiniMax-M3"


def test_routing_post_persists_to_env(client):
    """POST /api/llm/routing with {agent, provider, model} writes
    LLM_<AGENT>_PROVIDER and LLM_<AGENT>_MODEL to .env."""
    c, tmp_path = client

    r = c.post("/api/llm/routing", json={
        "agent": "advisor",
        "provider": "minimax",
        "model": "MiniMax-Mini",
    })
    assert r.status_code == 200, r.text

    env_text = (tmp_path / ".env").read_text()
    assert "LLM_ADVISOR_PROVIDER=minimax" in env_text
    assert "LLM_ADVISOR_MODEL=MiniMax-Mini" in env_text


def test_routing_post_replaces_existing_env_value(client):
    """POST with the same agent twice replaces, doesn't duplicate."""
    c, tmp_path = client
    _seed_env(tmp_path, LLM_ADVISOR_PROVIDER="openrouter", LLM_ADVISOR_MODEL="openrouter-default")

    c.post("/api/llm/routing", json={
        "agent": "advisor", "provider": "minimax", "model": "MiniMax-Mini",
    })

    env_text = (tmp_path / ".env").read_text()
    adv_provider_lines = [
        ln for ln in env_text.splitlines()
        if ln.strip().startswith("LLM_ADVISOR_PROVIDER=")
    ]
    adv_model_lines = [
        ln for ln in env_text.splitlines()
        if ln.strip().startswith("LLM_ADVISOR_MODEL=")
    ]
    assert len(adv_provider_lines) == 1, f"got {adv_provider_lines}"
    assert len(adv_model_lines) == 1, f"got {adv_model_lines}"
    assert "LLM_ADVISOR_PROVIDER=minimax" in adv_provider_lines[0]
    assert "LLM_ADVISOR_MODEL=MiniMax-Mini" in adv_model_lines[0]


def test_routing_post_rejects_unknown_agent(client):
    """POST with agent='foo' must return 400, not silently write a
    bogus LLM_FOO_* pair."""
    c, _tmp = client
    r = c.post("/api/llm/routing", json={
        "agent": "attacker",
        "provider": "minimax",
        "model": "MiniMax-M3",
    })
    assert r.status_code == 400, r.text


def test_routing_post_rejects_unknown_provider(client):
    c, _tmp = client
    r = c.post("/api/llm/routing", json={
        "agent": "advisor",
        "provider": "totally-made-up",
        "model": "m",
    })
    assert r.status_code == 400, r.text


# --- /api/llm/models -------------------------------------------------------

@respx.mock
def test_models_minimax_calls_v1_models_and_returns_id_list(client):
    """GET /api/llm/models?provider=minimax hits /v1/models, returns
    a list of model id strings. Requires MINIMAX_API_KEY in .env so
    the auth probe goes out."""
    c, tmp_path = client
    _seed_env(tmp_path, MINIMAX_API_KEY="sk-cp-test-minimax-models")

    respx.get("https://api.minimaxi.chat/v1/models").mock(
        return_value=Response(200, json={
            "object": "list",
            "data": [
                {"id": "MiniMax-M3"},
                {"id": "MiniMax-Mini"},
                {"id": "MiniMax-M2"},
            ],
        })
    )

    r = c.get("/api/llm/models", params={"provider": "minimax"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "minimax"
    assert "MiniMax-M3" in body["models"]
    assert "MiniMax-Mini" in body["models"]
    assert "MiniMax-M2" in body["models"]


def test_models_unknown_provider_returns_400(client):
    c, _tmp = client
    r = c.get("/api/llm/models", params={"provider": "nope"})
    assert r.status_code == 400, r.text


def test_models_no_key_returns_needs_key_signal(client):
    """Without the API key, /models returns 200 with an empty model
    list and a 'needs-key' source marker so the wizard can render a
    'set the key first' hint instead of an empty dropdown."""
    c, tmp_path = client
    # .env is empty (fixture seeded empty)
    r = c.get("/api/llm/models", params={"provider": "minimax"})
    body = r.json()
    assert body["source"] == "needs-key"
    assert body["models"] == []
    assert "MINIMAX_API_KEY" in body.get("note", "")
