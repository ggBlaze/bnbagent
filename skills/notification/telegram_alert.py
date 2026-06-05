"""Telegram alert — DM on every trade close (or other event).

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Uses the public Bot API
(no SDK required).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class TelegramAlertSkill:
    name = "telegram_alert"
    category = "notification"
    description = "DM a Telegram chat on every trade close (and other agent events)."
    version = "1.0.0"
    cost_per_call_usdc = 0.0
    requires = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    _rate_limit_per_hour: int = 60
    _last_sent: list[float] = []

    def status(self) -> dict:
        return {
            "rate_limit_per_hour": self._rate_limit_per_hour,
            "recent_sends": len(self._last_sent),
        }

    async def setup(self, components: dict) -> None:
        # No persistent state; just verify env
        for k in self.requires:
            if not os.environ.get(k):
                log.warning("telegram_alert: %s not set", k)

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        if ctx.event != "trade_close":
            return {"skipped": True, "reason": f"event {ctx.event} not handled"}
        # rate limit
        import time
        now = time.time()
        self._last_sent = [t for t in self._last_sent if now - t < 3600]
        if len(self._last_sent) >= self._rate_limit_per_hour:
            return {"skipped": True, "reason": "rate_limited"}
        trade = ctx.extra.get("trade") or {}
        text = self._format(trade)
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": text, "parse_mode": "HTML"})
            if r.status_code == 200:
                self._last_sent.append(now)
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "body": r.text[:200]}
        except Exception as e:
            log.warning("telegram send failed: %s", e)
            return {"ok": False, "error": str(e)}

    def _format(self, trade: dict) -> str:
        sleeve = trade.get("sleeve", "?")
        sym = trade.get("symbol", "?")
        pnl = trade.get("pnl_usdc", "?")
        reason = trade.get("reason", "?")
        sign = "+" if str(pnl).startswith("-") is False else ""
        return f"<b>{sleeve} · {sym}</b>\nP&L: {sign}{pnl} USDC\nReason: {reason}"
