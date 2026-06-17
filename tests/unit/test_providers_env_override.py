"""Unit tests for LLM_<AGENT>_PROVIDER / LLM_<AGENT>_MODEL env-var
overrides in LLMRouter.for_agent().

v2.1.5: providers.yaml hardcoded the per-agent model (all four
agents on MiniMax-M3). The wizard's model selector saves the
operator's choice to .env as LLM_<AGENT>_PROVIDER / LLM_<AGENT>_MODEL;
for_agent() must honor those env vars over the YAML so the operator
can change models without editing agents/providers.yaml.
"""
from __future__ import annotations

import pytest

from agents.providers import LLMRouter


def _router_with(*, default_provider="anthropic", agent_overrides=None) -> LLMRouter:
    """Build an LLMRouter pointed at the test provider `fake`. The fake
    provider has a base + key so the routing resolves to enabled=True,
    which is what we need to assert the env vars actually flip the
    model that comes back."""
    return LLMRouter(config={
        "default": default_provider,
        "providers": {
            "fake":   {"base": "http://x", "key": "k", "default_model": "fake-default"},
            "other":  {"base": "http://y", "key": "k", "default_model": "other-default"},
        },
        "agents": agent_overrides or {
            "advisor":       {"provider": "fake",  "model": "yaml-advisor"},
            "reviewer":      {"provider": "fake",  "model": "yaml-reviewer"},
            "chat":          {"provider": "fake",  "model": "yaml-chat"},
            "token_module":  {"provider": "fake",  "model": "yaml-token"},
        },
    })


def test_env_var_overrides_model(monkeypatch):
    """LLM_ADVISOR_MODEL=overridden must beat providers.yaml's model."""
    monkeypatch.setenv("LLM_ADVISOR_MODEL", "overridden-model")
    r = _router_with()
    routing = r.for_agent("advisor")
    assert routing.model == "overridden-model"


def test_env_var_overrides_provider(monkeypatch):
    """LLM_REVIEWER_PROVIDER=other must beat providers.yaml's provider."""
    monkeypatch.setenv("LLM_REVIEWER_PROVIDER", "other")
    r = _router_with()
    routing = r.for_agent("reviewer")
    assert routing.provider_name == "other"


def test_env_var_overrides_both_provider_and_model(monkeypatch):
    """Setting both env vars routes the agent to a completely different
    provider+model combo without editing providers.yaml."""
    monkeypatch.setenv("LLM_CHAT_PROVIDER", "other")
    monkeypatch.setenv("LLM_CHAT_MODEL", "chat-from-env")
    r = _router_with()
    routing = r.for_agent("chat")
    assert routing.provider_name == "other"
    assert routing.model == "chat-from-env"


def test_env_var_unspecified_falls_back_to_yaml(monkeypatch):
    """Without the env vars, providers.yaml's value wins (regression
    guard — the new code path must NOT change default behavior)."""
    monkeypatch.delenv("LLM_ADVISOR_MODEL", raising=False)
    monkeypatch.delenv("LLM_ADVISOR_PROVIDER", raising=False)
    r = _router_with()
    routing = r.for_agent("advisor")
    assert routing.provider_name == "fake"
    assert routing.model == "yaml-advisor"


def test_env_var_unknown_provider_disables_agent(monkeypatch):
    """Env var can point at a provider that exists in providers.yaml
    but has no base/key configured — agent should report enabled=False
    with reason='no base url', same as if YAML pointed at a broken
    provider. (Defends against typos in the wizard.)"""
    monkeypatch.setenv("LLM_TOKEN_MODULE_PROVIDER", "nonexistent")
    r = _router_with()
    routing = r.for_agent("token_module")
    assert routing.provider_name == "nonexistent"
    assert routing.enabled is False
    assert "no base url" in routing.reason
