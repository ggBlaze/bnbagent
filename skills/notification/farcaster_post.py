"""Farcaster auto-post — PnL updates to a Warpcast account.

Requires WARPCAST_KEY. Rate-limited to 1 post per hour to avoid spam.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class FarcasterPostSkill:
    name = "farcaster_post"
    category = "notification"
    description = "Auto-post PnL updates and agent events to Farcaster (Warpcast)."
    version = "1.0.0"
    cost_per_call_usdc = 0.0
    requires = ["WARPCAST_KEY"]
    _last_post: float = 0.0

    def status(self) -> dict:
        return {"cooldown_s": 3600, "last_post": self._last_post}

    async def setup(self, components: dict) -> None:
        pass

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        if ctx.event not in ("trade_close", "deploy", "advisor"):
            return {"skipped": True, "reason": f"event {ctx.event} not handled"}
        now = time.time()
        if now - self._last_post < 3600:
            return {"skipped": True, "reason": "cooldown"}
        text = self._format(ctx)
        if not text:
            return {"skipped": True, "reason": "no content"}
        key = os.environ.get("WARPCAST_KEY")
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post("https://api.warpcast.com/v2/casts",
                                  headers={"Authorization": f"Bearer {key}"},
                                  json={"text": text})
            if r.status_code in (200, 201):
                self._last_post = now
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "body": r.text[:200]}
        except Exception as e:
            log.warning("farcaster post failed: %s", e)
            return {"ok": False, "error": str(e)}

    def _format(self, ctx: SkillContext) -> str:
        if ctx.event == "trade_close":
            t = ctx.extra.get("trade") or {}
            return f"BNB Agent closed {t.get('sleeve','?')} {t.get('symbol','?')} | P&L {t.get('pnl_usdc','?')} USDC"
        if ctx.event == "deploy":
            d = ctx.extra.get("result") or {}
            return f"BNB Agent deployed ${d.get('symbol','?')} ({d.get('name','?')}) at {d.get('contract_address','?')}"
        if ctx.event == "advisor":
            return f"BNB Agent advisor: {ctx.extra.get('summary', 'decision made')}"
        return ""
