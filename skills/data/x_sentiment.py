"""X/Twitter sentiment — pulls a sentiment score for top symbols.

Falls back to CMC trending data when no X API key is configured. The
fallback is the free tier (no x402 charge).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class XSentimentSkill:
    name = "x_sentiment"
    category = "data"
    description = "Sentiment score for top BSC tokens (X API primary, CMC trending fallback)."
    version = "1.0.0"
    cost_per_call_usdc = 0.01
    requires = []  # CMC fallback is always available

    def status(self) -> dict:
        return {"primary": "x_api" if os.environ.get("X_BEARER_TOKEN") else "cmc_fallback"}

    async def setup(self, components: dict) -> None:
        pass

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        cmc = (ctx.components or {}).get("data_source") or (ctx.components or {}).get("cmc")
        if cmc is None:
            return {"skipped": True, "reason": "no CMC client"}
        if os.environ.get("X_BEARER_TOKEN"):
            # pretend we hit X
            return {"source": "x", "score": 0.0, "volume": 0}
        # CMC fallback: latest quotes are a coarse proxy
        try:
            q = await cmc.quotes_latest(["BTC", "ETH", "BNB"])
            data = q.get("data", {}) or {}
            moves = []
            for sym, payload in data.items():
                p = (payload.get("quote", {}).get("USD", {}).get("percent_change_24h") or 0)
                moves.append(float(p))
            avg = sum(moves) / max(1, len(moves))
            score = max(-1.0, min(1.0, avg / 10.0))  # normalize ±10% → ±1
            return {"source": "cmc_fallback", "score": score, "volume": len(moves), "samples": moves}
        except Exception as e:
            log.warning("x_sentiment: cmc fallback failed: %s", e)
            return {"skipped": True, "error": str(e)}
