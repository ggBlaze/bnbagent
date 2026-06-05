"""Glassnode on-chain — stub for the contest; deterministic score for the UI."""
from __future__ import annotations

import logging
import time
from typing import Any

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class GlassnodeOnchainSkill:
    name = "glassnode_onchain"
    category = "data"
    description = "Exchange netflow (stub for the contest; deterministic score)."
    version = "0.1.0"
    cost_per_call_usdc = 0.0
    requires = []

    def status(self) -> dict:
        return {"stub": True}

    async def setup(self, components: dict) -> None:
        pass

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        # Deterministic score for the UI demo
        seed = int(time.time() // 3600)  # changes every hour
        score = ((seed * 2654435761) % 200 - 100) / 100.0  # ±1
        return {"stub": True, "score": score, "window": "1h"}
