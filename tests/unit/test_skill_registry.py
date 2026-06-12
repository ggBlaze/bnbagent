"""Unit tests for the Skills registry + 6 built-ins."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.base import Skill, SkillContext


def test_registry_discover_loads_builtins(tmp_path):
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    names = [s["name"] for s in reg.list()]
    assert "telegram_alert" in names
    assert "farcaster_post" in names
    assert "webhook_dispatch" in names
    assert "x_sentiment" in names
    assert "cmc_global_filter" in names
    assert "glassnode_onchain" in names


def test_registry_persists_enabled_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    reg.enable("telegram_alert")
    # reload
    reg2 = SkillRegistry(state_path=tmp_path / "skills.json")
    reg2.discover()
    assert "telegram_alert" in reg2._enabled


def test_enable_missing_env_blocks(tmp_path):
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    with pytest.raises(RuntimeError, match="requires env"):
        reg.enable("telegram_alert")  # TELEGRAM_BOT_TOKEN not set


def test_disable_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    reg.enable("telegram_alert")
    reg.disable("telegram_alert")
    assert "telegram_alert" not in reg._enabled


def test_list_returns_ready_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    listing = {s["name"]: s for s in reg.list()}
    assert listing["telegram_alert"]["ready"] is True
    assert listing["farcaster_post"]["ready"] is False  # no WARPCAST_KEY
    assert "WARPCAST_KEY" in listing["farcaster_post"]["missing_env"]


def test_unknown_skill_raises(tmp_path):
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    with pytest.raises(ValueError, match="unknown skill"):
        reg.enable("nope_not_a_skill")


def test_skill_categories(tmp_path):
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    listing = {s["name"]: s for s in reg.list()}
    assert listing["telegram_alert"]["category"] == "notification"
    assert listing["x_sentiment"]["category"] == "data"


@pytest.mark.asyncio
async def test_telegram_skill_skips_non_close_events(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    from skills.notification.telegram_alert import TelegramAlertSkill
    s = TelegramAlertSkill()
    ctx = SkillContext(event="trade_open", extra={})
    out = await s.run(ctx)
    assert out["skipped"] is True


@pytest.mark.asyncio
async def test_webhook_skill_skips_without_url(tmp_path):
    from skills.notification.webhook_dispatch import WebhookDispatchSkill
    s = WebhookDispatchSkill()
    ctx = SkillContext(event="trade_close", extra={})
    out = await s.run(ctx)
    assert out["skipped"] is True


@pytest.mark.asyncio
async def test_x_sentiment_returns_cmc_fallback_score(tmp_path, monkeypatch):
    from skills.data.x_sentiment import XSentimentSkill
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    s = XSentimentSkill()
    # fake CMC
    class FakeCMC:
        async def quotes_latest(self, symbols, convert="USD"):
            return {"data": {s: {"quote": {"USD": {"percent_change_24h": 5.0}}} for s in symbols}}
    ctx = SkillContext(event="tick", components={"data_source": FakeCMC()})
    out = await s.run(ctx)
    assert out["source"] == "cmc_fallback"
    assert -1.0 <= out["score"] <= 1.0


@pytest.mark.asyncio
async def test_x_sentiment_skips_without_cmc(tmp_path, monkeypatch):
    from skills.data.x_sentiment import XSentimentSkill
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    s = XSentimentSkill()
    ctx = SkillContext(event="tick", components={})
    out = await s.run(ctx)
    assert out["skipped"] is True


def test_cmc_global_filter_status(tmp_path):
    from skills.data.cmc_global_filter import CmcGlobalFilterSkill
    s = CmcGlobalFilterSkill()
    assert s.status()["bear_threshold_pct"] == -3.0


def test_glassnode_is_stub(tmp_path):
    from skills.data.glassnode_onchain import GlassnodeOnchainSkill
    s = GlassnodeOnchainSkill()
    assert s.status()["stub"] is True


@pytest.mark.asyncio
async def test_run_hook_calls_enabled_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    reg = SkillRegistry(state_path=tmp_path / "skills.json")
    reg.discover()
    reg.enable("telegram_alert")
    # Simulate an event — not "trade_close", so telegram returns skipped.
    await reg.run_hook("tick", components={"portfolio": None, "policy": {}}, extra={"ts": 0})
    assert True  # no exception = pass
