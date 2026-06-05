"""CMC global filter — pauses all sleeves if global market regime turns bearish.

This is the only Skill that writes to the control file. It's marked
`_source: "skill:cmc_global_filter"` so the dashboard distinguishes its
edits from operator edits and advisor edits.
"""
from __future__ import annotations

import logging
from typing import Any

from skills.base import Skill, SkillContext

log = logging.getLogger(__name__)


class CmcGlobalFilterSkill:
    name = "cmc_global_filter"
    category = "data"
    description = "Pauses all sleeves when CMC global metrics signal bear regime."
    version = "1.0.0"
    cost_per_call_usdc = 0.01
    requires = []
    BEAR_THRESHOLD: float = -3.0  # 24h global market cap % change

    def status(self) -> dict:
        return {"bear_threshold_pct": self.BEAR_THRESHOLD}

    async def setup(self, components: dict) -> None:
        pass

    async def run(self, ctx: SkillContext, **kwargs) -> dict:
        cmc = (ctx.components or {}).get("cmc")
        if cmc is None:
            return {"skipped": True, "reason": "no CMC client"}
        try:
            m = await cmc.global_metrics()
            quote = (m.get("data", {}) or {}).get("quote", {}).get("USD", {}) or {}
            pct_24h = float(quote.get("total_market_cap_yesterday_percentage_change") or 0)
        except Exception as e:
            log.info("cmc_global_filter: metrics call failed: %s", e)
            return {"skipped": True, "error": str(e)}

        if pct_24h < self.BEAR_THRESHOLD:
            # bear regime — pause all sleeves via control file
            from core.control import write_control
            write_control({
                "sleeves": {"A": False, "B": False, "C": False},
                "_source": "skill:cmc_global_filter",
                "_reason": f"global_market_cap_24h={pct_24h:.2f}% < {self.BEAR_THRESHOLD}%",
            })
            return {"paused": True, "pct_24h": pct_24h, "threshold": self.BEAR_THRESHOLD}
        return {"paused": False, "pct_24h": pct_24h, "threshold": self.BEAR_THRESHOLD}
