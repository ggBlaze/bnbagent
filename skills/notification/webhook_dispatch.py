"""Generic webhook dispatch — POST every event to a user-configured URL."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class WebhookDispatchSkill:
    name = "webhook_dispatch"
    category = "notification"
    description = "POST every event to a user-configured webhook URL (e.g. Zapier, n8n, Discord)."
    version = "1.0.0"
    cost_per_call_usdc = 0.0
    requires = ["WEBHOOK_URL"]

    def status(self) -> dict:
        return {"url_set": bool(os.environ.get("WEBHOOK_URL"))}

    async def setup(self, components: dict) -> None:
        pass

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        url = os.environ.get("WEBHOOK_URL")
        if not url:
            return {"skipped": True, "reason": "no WEBHOOK_URL"}
        payload = {
            "event": ctx.event,
            "ts": ctx.extra.get("ts") if ctx.extra else None,
            "extra": ctx.extra or {},
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post(url, json=payload)
            return {"ok": r.is_success, "status": r.status_code}
        except Exception as e:
            log.warning("webhook dispatch failed: %s", e)
            return {"ok": False, "error": str(e)}
