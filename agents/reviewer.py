"""Layer 2 — TradeReviewer.

Called per-trade between `allow_trade` (circuit breaker) and `sign_transaction`.
Can only VETO. Hard guardrails run in code, never delegated to the LLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from agents.base import PersonaLoader, llm_complete, extract_json_object
from agents.providers import AgentRouting, LLMRouter
from core.risk import ProposedTrade

log = logging.getLogger(__name__)


# v2.1.8 (P3): the brace-balanced JSON extractor was lifted into
# agents/base.py so the advisor (and any future agent) can use it too.
# Keep the old name as a back-compat alias for any caller that imports
# `from agents.reviewer import _extract_json_object`.
_extract_json_object = extract_json_object


@dataclass
class ReviewVerdict:
    allow: bool
    confidence: float
    reason: str
    source: str   # "llm" | "no_reviewer" | "heuristic_veto" | "low_confidence" | "llm_timeout" | "llm_disabled" | "llm_error"


class TradeReviewer:
    """Layer 2 — per-trade veto."""

    CONFIDENCE_THRESHOLD = 0.70
    # v2.1.5: the per-call timeout is read from self.routing.timeout_s
    # (set by LLMRouter.for_agent() from agents.<name>.timeout_s in
    # providers.yaml, auto-defaulted to 10s for reasoning models and
    # 2s for fast chat models). The class constant below is the
    # fallback when the agent has no routing (e.g. in unit tests
    # that construct TradeReviewer() without a router).
    LATENCY_BUDGET_S = 5.0

    def __init__(self, *, sleeve: str, components: dict, router: LLMRouter,
                 persona_name: str | None = None,
                 decision_log: Path = Path("~/.bnbagent/decisions.jsonl").expanduser()):
        self.sleeve = sleeve
        self.components = components
        self.routing: AgentRouting = router.for_agent(persona_name or f"reviewer_{sleeve.lower()}")
        # if there's no per-sleeve routing, fall back to the generic "reviewer" agent
        if not self.routing.enabled:
            self.routing = router.for_agent("reviewer")
        self.loader = PersonaLoader(persona_name or "reviewer")
        self.decision_log = decision_log
        self.recent_buf: deque[dict] = deque(maxlen=200)

    # --- main entry ------------------------------------------------------

    async def review(self, proposed: ProposedTrade, sleeve_state: dict,
                     market_snapshot: dict | None = None) -> ReviewVerdict:
        market_snapshot = market_snapshot or {}
        if not self.routing.enabled:
            return ReviewVerdict(allow=True, confidence=1.0, reason="llm_disabled",
                                 source="no_reviewer")
        persona = self.loader.load()
        # v2.1.5: per-routing timeout (reasoning models get 10s, fast
        # models get 2s). Fall back to the class constant if the
        # routing has no timeout_s (e.g. unit tests with synthetic LLMs).
        budget = getattr(self.routing, "timeout_s", None) or self.LATENCY_BUDGET_S
        try:
            verdict = await asyncio.wait_for(
                self._llm_review(persona.system, proposed, sleeve_state, market_snapshot),
                timeout=budget,
            )
        except asyncio.TimeoutError:
            log.warning("reviewer[%s] LLM timeout — falling back to heuristic", self.sleeve)
            return self._heuristic_decision(sleeve_state, source="llm_timeout")
        except Exception as e:
            log.warning("reviewer[%s] LLM error: %s", self.sleeve, e)
            return self._heuristic_decision(sleeve_state, source="llm_error")

        # Post-LLM hard guardrails (never delegated)
        if not verdict.allow or verdict.confidence < self.CONFIDENCE_THRESHOLD:
            self._log(proposed, sleeve_state, verdict)
            return ReviewVerdict(allow=False,
                                 confidence=verdict.confidence,
                                 reason=f"{verdict.reason} (conf={verdict.confidence:.2f})",
                                 source=verdict.source if not verdict.allow else "low_confidence")
        # extra heuristic overlay
        ok, reason = self._heuristic_veto(sleeve_state)
        if not ok:
            v = ReviewVerdict(allow=False, confidence=verdict.confidence,
                              reason=reason, source="heuristic_veto")
            self._log(proposed, sleeve_state, v)
            return v
        v = ReviewVerdict(allow=True, confidence=verdict.confidence,
                          reason=verdict.reason, source=verdict.source)
        self._log(proposed, sleeve_state, v)
        return v

    # --- LLM call --------------------------------------------------------

    async def _llm_review(self, system: str, proposed: ProposedTrade, sleeve_state: dict,
                          market: dict) -> ReviewVerdict:
        user = self._render_user_prompt(proposed, sleeve_state, market)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # v2.1.5: pass the per-routing timeout through to llm_complete so
        # the inner HTTP call budget matches the outer wait_for budget.
        inner_timeout = getattr(self.routing, "timeout_s", None) or self.LATENCY_BUDGET_S
        raw = await llm_complete(self.routing, messages,
                                response_format={"type": "json_object"},
                                timeout_s=inner_timeout)
        if not raw:
            return self._heuristic_decision(sleeve_state, source="llm_disabled")
        try:
            # v2.1.8 (F3): scan past prose / fences / unclosed-think prefixes
            # so the parse survives any wrapping the model adds. See
            # tests/unit/test_reviewer_strips_think_block.py for the
            # contract and the production failure that motivated it.
            obj = _extract_json_object(raw)
            data = json.loads(obj)
            return ReviewVerdict(
                allow=bool(data.get("allow", True)),
                confidence=float(data.get("confidence", 0.0)),
                reason=str(data.get("reason", ""))[:120],
                source="llm",
            )
        except Exception as e:
            log.warning("reviewer[%s] bad JSON: %s", self.sleeve, e)
            return self._heuristic_decision(sleeve_state, source="llm_error")

    def _render_user_prompt(self, proposed: ProposedTrade, sleeve_state: dict, market: dict) -> str:
        # Compact JSON. The reviewer persona enforces "veto in doubt".
        trade_dict = {
            "symbol":         proposed.symbol,
            "side":           proposed.side,
            "notional_usdc":  float(proposed.notional_usdc),
            "risk_usdc":      float(proposed.risk_usdc),
            "is_new":         proposed.is_new,
            "sleeve":         proposed.sleeve,
        }
        return (
            f"You are the reviewer for sleeve {self.sleeve}.\n\n"
            "PROPOSED TRADE:\n"
            f"```json\n{json.dumps(trade_dict, indent=2)}\n```\n"
            f"SLEEVE STATE:\n```json\n{json.dumps(sleeve_state, default=str)[:2500]}\n```\n"
            f"MARKET SNAPSHOT:\n```json\n{json.dumps(market, default=str)[:1500]}\n```\n\n"
            "Return ONLY valid JSON:\n"
            '{"allow": true|false, "confidence": 0.0-1.0, "reason": "<=120 chars"}'
        )

    # --- heuristic -------------------------------------------------------

    # Recent-trades window. 10 trades, exponentially weighted so the most
    # recent trade has weight 1 and the 10th-back has weight 0.5^9 ≈ 0.002.
    # This catches slow drawdowns the old 4/5 window missed.
    RECENT_WINDOW = 10
    WEIGHTED_LOSS_VETO_THRESHOLD = 0.45  # policy-overridable; see _heuristic_veto

    def _weighted_loss_intensity(self, recent: list[dict], n: int | None = None) -> float:
        """Exponential decay: last trade gets 0.5, decays by half going back.
        Returns the weighted fraction of losing trades in [0, 1]."""
        n = n or self.RECENT_WINDOW
        if not recent:
            return 0.0
        last = recent[-n:]
        # 0.5 ** (n-1-i): i=last → 0.5**0 = 1, i=first → 0.5**(n-1)
        weights = [0.5 ** (len(last) - 1 - i) for i in range(len(last))]
        total = sum(weights) or 1.0
        weights = [w / total for w in weights]
        return sum(w for w, t in zip(weights, last) if float(t.get("pnl_pct", 0)) < 0)

    def _heuristic_veto(self, sleeve_state: dict) -> tuple[bool, str]:
        """Returns (allow, reason). True = no veto."""
        wr = float(sleeve_state.get("win_rate_ewma", 0.55))
        if wr < 0.20:
            return False, f"heuristic: win_rate_ewma={wr:.2f} < 0.20"
        if sleeve_state.get("loss_cooldown_active"):
            return False, "heuristic: post-loss cooldown active"
        sleeve_dd = float(sleeve_state.get("sleeve_dd_pct", 0))
        max_dd = float(sleeve_state.get("policy_max_dd_pct", 100))
        if max_dd > 0 and sleeve_dd > 0.5 * max_dd:
            return False, f"heuristic: sleeve_dd {sleeve_dd:.1f}% > 50% of policy cap"
        # Weighted loss intensity over the last N trades. Threshold can be
        # overridden via policy["global_risk"]["reviewer_loss_intensity_threshold"].
        recent = sleeve_state.get("recent_trades") or []
        if len(recent) >= 3:    # need a few data points to be meaningful
            threshold = float(
                (sleeve_state.get("policy_overrides") or {}).get(
                    "reviewer_loss_intensity_threshold",
                    self.WEIGHTED_LOSS_VETO_THRESHOLD,
                )
            )
            intensity = self._weighted_loss_intensity(recent)
            if intensity > threshold:
                return False, (
                    f"heuristic: weighted loss intensity {intensity:.2f} > "
                    f"{threshold:.2f} (last {len(recent[-self.RECENT_WINDOW:])} trades)"
                )
        return True, "ok"

    def _heuristic_decision(self, sleeve_state: dict, *, source: str) -> ReviewVerdict:
        ok, reason = self._heuristic_veto(sleeve_state)
        return ReviewVerdict(allow=ok, confidence=0.5 if ok else 0.0,
                             reason=reason, source=source)

    # --- log -------------------------------------------------------------

    def _log(self, proposed: ProposedTrade, sleeve_state: dict, v: ReviewVerdict) -> None:
        entry = {
            "ts": int(time.time()),
            "sleeve": self.sleeve,
            "symbol": proposed.symbol,
            "side":   proposed.side,
            "allow":  v.allow,
            "confidence": v.confidence,
            "source": v.source,
            "reason": v.reason,
        }
        self.recent_buf.append(entry)
        try:
            with self.decision_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def recent(self, n: int = 50) -> list[dict]:
        return list(self.recent_buf)[-n:][::-1]
