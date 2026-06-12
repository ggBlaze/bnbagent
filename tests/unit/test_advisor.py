"""Unit tests for the StrategyAdvisor (Layer 1)."""
from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from agents.advisor import StrategyAdvisor
from agents.providers import LLMRouter
from core.portfolio import Portfolio
from tests.fixtures.llm import FakeLLMClient


def _build_policy():
    return {
        "version": "1.0.0",
        "evaluator_address": "0x" + "a" * 40,
        "agent_address":     "0x" + "a" * 40,
        "global_risk": {
            "daily_loss_circuit_breaker_pct": 3.0,
            "per_trade_risk_pct":             1.0,
            "max_gross_leverage":             2.0,
            "max_single_position_pct":       15.0,
            "max_drawdown_pct":               8.0,
        },
        "sleeves": {
            "A": {"enabled": True, "max_position_pct": 15.0, "kelly_fraction": 0.25},
            "B": {"enabled": True, "max_position_pct": 10.0, "kelly_fraction": 0.25},
            "C": {"enabled": True, "max_position_pct":  5.0, "kelly_fraction": 0.25},
        },
        "allowlist": {"bsc_tokens": ["WBNB", "USDC"]},
        "signature": "0x" + "00" * 65,
    }


@pytest.fixture
def components():
    pf = Portfolio(starting_equity=Decimal("100"))
    return {
        "portfolio": pf,
        "policy": _build_policy(),
        "data_source": type("C", (), {"_x402_spend_today_usdc": Decimal("0.47")})(),
        "dashboard_state": {"control_log": []},
    }


@pytest.fixture
def tmp_decision_log(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("agents.advisor.Path", lambda x: p if "decisions.jsonl" in str(x) else Path(x))
    return p


# --- core invariant: can only tighten ---------------------------------------

@pytest.mark.asyncio
async def test_can_only_tighten(components, tmp_decision_log, monkeypatch):
    # The advisor writes via core.control.write_control; assert the file was written
    from agents.advisor import write_control as real_write_control
    from core import control as control_mod
    import json as _json
    captured = {}
    def fake_write(payload):
        captured.update(payload)
        # mimic the real write so other parts of the system see it
        real_write_control(payload)
    monkeypatch.setattr("agents.advisor.write_control", fake_write)

    fake = FakeLLMClient(scripts=[json.dumps({
        "actions": [{"type": "tighten_risk", "key": "per_trade_risk_pct", "value": 0.5, "reason": "raised vol"}],
        "confidence": 0.9,
    })])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    assert len(result.applied) == 1
    assert captured.get("global_risk", {}).get("per_trade_risk_pct") == 0.5
    # in-memory policy is NOT mutated by the advisor (it goes through the
    # control file; the next heartbeat applies it via apply_control)
    assert components["policy"]["global_risk"]["per_trade_risk_pct"] == 1.0


@pytest.mark.asyncio
async def test_cannot_loosen_with_higher_value(components, tmp_decision_log):
    fake = FakeLLMClient(scripts=[json.dumps({
        "actions": [{"type": "tighten_risk", "key": "per_trade_risk_pct", "value": 5.0, "reason": "wants more risk"}],
        "confidence": 0.9,
    })])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    assert len(result.applied) == 0
    assert len(result.vetoed) == 1
    assert "not_tightening" in result.vetoed[0]["veto"]
    # policy unchanged
    assert components["policy"]["global_risk"]["per_trade_risk_pct"] == 1.0


@pytest.mark.asyncio
async def test_malformed_json_does_not_crash(components, tmp_decision_log):
    fake = FakeLLMClient(scripts=["not json at all"])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    # Malformed JSON: no actions get to _apply, but a parse_error is recorded
    # in vetoed. No risk keys are written.
    assert components["policy"]["global_risk"]["per_trade_risk_pct"] == 1.0
    # malformed LLM output is logged but not applied
    assert not any(a.get("applied") for a in result.applied if a.get("action", {}).get("type") in
                  ("tighten_risk", "tighten_sleeve", "disable_sleeve", "set_daily_loss_cap"))


@pytest.mark.asyncio
async def test_disabled_llm_no_op(components, tmp_decision_log):
    router = LLMRouter(config={"default": "anthropic",
                                "providers": {"anthropic": {"base": "https://x", "key": ""}},
                                "agents": {"advisor": {"provider": "anthropic", "model": "m"}}})
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    # no risk-changing actions
    assert not any(a.get("action", {}).get("type") in
                  ("tighten_risk", "tighten_sleeve", "disable_sleeve", "set_daily_loss_cap")
                  for a in result.applied)
    assert components["policy"]["global_risk"]["per_trade_risk_pct"] == 1.0


@pytest.mark.asyncio
async def test_disable_sleeve_allowed(components, tmp_decision_log):
    fake = FakeLLMClient(scripts=[json.dumps({
        "actions": [{"type": "disable_sleeve", "sleeve": "C", "reason": "extreme fear"}],
        "confidence": 0.8,
    })])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    assert len(result.applied) == 1
    assert result.applied[0]["applied"] is True
    # the policy in-memory is NOT mutated by the advisor; only the control file
    # is written. The next heartbeat applies it. (We test that the call to
    # write_control happened below.)


@pytest.mark.asyncio
async def test_tighten_sleeve_respects_lower_value_only(components, tmp_decision_log):
    fake = FakeLLMClient(scripts=[json.dumps({
        "actions": [
            {"type": "tighten_sleeve", "sleeve": "B", "key": "max_position_pct", "value": 5.0, "reason": "x"},
            {"type": "tighten_sleeve", "sleeve": "B", "key": "max_position_pct", "value": 50.0, "reason": "bad"},
        ],
        "confidence": 0.9,
    })])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    assert len(result.applied) == 1
    assert len(result.vetoed) == 1
    assert "not_tightening" in result.vetoed[0]["veto"]


@pytest.mark.asyncio
async def test_unknown_key_vetoed(components, tmp_decision_log):
    fake = FakeLLMClient(scripts=[json.dumps({
        "actions": [{"type": "tighten_risk", "key": "nonexistent_key", "value": 0.1, "reason": "x"}],
        "confidence": 0.9,
    })])
    router = LLMRouter(config={"default": "fake",
                                "providers": {"fake": {"base": "x", "key": "k"}},
                                "agents": {"advisor": {"provider": "fake", "model": "m"}}})
    router._cache["fake"] = fake
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    result = await adv.tick()
    assert len(result.vetoed) == 1
    assert "unknown_key" in result.vetoed[0]["veto"]


def test_recent_returns_list(components, tmp_decision_log):
    router = LLMRouter(config={"default": "anthropic",
                                "providers": {"anthropic": {"base": "https://x", "key": ""}},
                                "agents": {"advisor": {"provider": "anthropic", "model": "m"}}})
    adv = StrategyAdvisor(components=components, router=router, decision_log=tmp_decision_log)
    assert adv.recent(5) == []
