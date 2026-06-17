"""Layer 1 — StrategyAdvisor.

Runs every 5 minutes. Observes recent state, asks the LLM for a
TIGHTENING recommendation, applies the recommendation via the existing
`core.control.write_control` path (so the audit log captures advisor
edits identically to operator edits).

The "can only tighten" constraint is enforced in `_apply()`, not by the
LLM. Even a hostile LLM that returns `{"actions":[{"type":"loosen_risk",
"key":"per_trade_risk_pct","value":99.0}]}` will be rejected.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from agents.base import PersonaLoader, llm_complete, extract_json_object
from agents.providers import AgentRouting, LLMRouter
from core.control import write_control
from core.portfolio import Portfolio

log = logging.getLogger(__name__)


# Constants for the keys the advisor is allowed to touch. Mirrors
# `core.control.apply_control` allowlist so we never send a key that
# `apply_control` would silently drop.
_GLOBAL_RISK_KEYS = {
    "daily_loss_circuit_breaker_pct",
    "per_trade_risk_pct",
    "max_gross_leverage",
    "max_single_position_pct",
    "max_drawdown_pct",
}
_SLEEVE_KEYS = {
    "max_position_pct", "kelly_fraction",
    # strategy-specific knobs
    "volume_spike_mult", "tp_pct", "stop_pct", "zscore_threshold",
}


@dataclass
class Advice:
    raw: str = ""
    actions: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    parsed_ok: bool = False
    error: str = ""


@dataclass
class ApplyResult:
    proposed: list[dict] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    vetoed:  list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class StrategyAdvisor:
    """Layer 1 — runs every N seconds via TickLoop."""

    name = "advisor"

    def __init__(self, *, components: dict, router: LLMRouter,
                 persona_name: str = "advisor",
                 decision_log: Path = Path("~/.bnbagent/decisions.jsonl").expanduser(),
                 policy_get=None):
        self.components = components
        self.routing: AgentRouting = router.for_agent(persona_name)
        self.loader = PersonaLoader(persona_name)
        self.decision_log = decision_log
        self.decision_log.parent.mkdir(parents=True, exist_ok=True)
        # rolling buffer of last decisions, exposed to dashboard
        self.recent_buf: deque[dict] = deque(maxlen=100)
        self._policy_get = policy_get or (lambda: components.get("policy") or {})

    # --- main entry: called by Agent.register("advisor", 300, ...) -------

    async def tick(self) -> ApplyResult:
        persona = self.loader.load()
        policy = self._policy_get()
        if not policy:
            return ApplyResult()
        ctx = self._gather_context(policy)
        advice = await self._decide(persona.system, ctx)
        result = self._apply(advice, policy)
        self._log(advice, result, ctx)
        return result

    # --- context ---------------------------------------------------------

    def _gather_context(self, policy: dict) -> dict:
        portfolio: Portfolio = self.components.get("portfolio")
        closed = list(portfolio.closed_trades)[-50:] if portfolio else []
        trades_compact = [
            {"sleeve": t.get("sleeve"), "symbol": t.get("symbol"),
             "pnl_pct": float(Decimal(str(t.get("pnl_usdc", 0))) / max(1, Decimal(str(t.get("notional", 1)))) * 100)
             if t.get("notional") else 0.0,
             "reason": t.get("reason")}
            for t in closed
        ]
        cmc = self.components.get("data_source")
        x402_spend = float(getattr(cmc, "_x402_spend_today_usdc", 0)) if cmc else 0.0
        sleeve_exp = portfolio.sleeve_exposures() if portfolio else {}
        return {
            "ts": int(time.time()),
            "policy_global_risk": dict(policy.get("global_risk", {})),
            "policy_sleeves": {k: dict(v) for k, v in policy.get("sleeves", {}).items()},
            "portfolio": {
                "equity":          float(portfolio.equity())           if portfolio else 0,
                "peak_equity":     float(portfolio.peak_equity)        if portfolio else 0,
                "drawdown_pct":    portfolio.drawdown_pct()            if portfolio else 0,
                "day_pnl_pct":     portfolio.day_pnl_pct()             if portfolio else 0,
                "sleeve_exposure": {k: float(v) for k, v in sleeve_exp.items()},
            },
            "x402_spend_today_usdc": x402_spend,
            "recent_trades": trades_compact,
            "control_log": list(self.components.get("dashboard_state", {}).get("control_log", []))[-10:],
        }

    # --- decide ----------------------------------------------------------

    async def _decide(self, system_prompt: str, ctx: dict) -> Advice:
        if not self.routing.enabled:
            log.info("advisor: LLM disabled (%s) — no_op", self.routing.reason)
            return Advice(actions=[{"type": "no_op", "reason": "llm_disabled"}], confidence=0.0,
                          parsed_ok=True, error="llm_disabled")
        user_msg = self._render_user_prompt(ctx)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ]
        raw = await llm_complete(self.routing, messages, response_format={"type": "json_object"})
        if not raw:
            return Advice(raw=raw, actions=[{"type": "no_op", "reason": "llm_empty"}],
                          confidence=0.0, parsed_ok=True, error="llm_empty")
        try:
            # v2.1.8 (P3): scan past prose / fences / unclosed-think
            # prefixes so the parse survives any wrapping the model adds.
            # Same helper used by the reviewer (F3). Production failure
            # mode this fixes: ```json\n{... fenced responses landing
            # in parsed_ok=False, advisor falling back to no_op.
            obj = extract_json_object(raw)
            data = json.loads(obj)
            actions = data.get("actions", [])
            confidence = float(data.get("confidence", 0))
            return Advice(raw=raw, actions=actions, confidence=confidence, parsed_ok=True)
        except Exception as e:
            log.warning("advisor: malformed JSON: %s — raw=%r", e, raw[:200])
            return Advice(raw=raw, confidence=0.0, parsed_ok=False, error=str(e))

    def _render_user_prompt(self, ctx: dict) -> str:
        # Compact JSON dump of the context
        return (
            "You are advising the BNB Agent. The signed User Policy below is the "
            "ceiling. You may only TIGHTEN. Respond with a JSON object matching the "
            "schema in your system prompt. Do not include any prose.\n\n"
            f"CONTEXT (JSON):\n```json\n{json.dumps(ctx, indent=2, default=str)[:6000]}\n```"
        )

    # --- apply (the safety envelope) -------------------------------------

    def _apply(self, advice: Advice, policy: dict) -> ApplyResult:
        result = ApplyResult(proposed=list(advice.actions))
        # If LLM returned no actions or empty list, no-op cleanly
        if not advice.actions:
            return result
        # If parse failed, no writes
        if not advice.parsed_ok:
            result.vetoed.append({"reason": f"parse_error: {advice.error}"})
            return result

        # Build a single write_control call so the audit log sees one event
        global_risk_patches: dict = {}
        sleeve_patches: dict = {}
        for action in advice.actions:
            t = action.get("type")
            if t == "no_op":
                result.applied.append({"action": action, "applied": True, "reason": "no_op"})
                continue
            if t in ("tighten_risk", "set_daily_loss_cap"):
                key = action.get("key")
                new = _to_float(action.get("value"))
                if key not in _GLOBAL_RISK_KEYS or new is None:
                    result.vetoed.append({"action": action, "veto": f"unknown_key({key})"})
                    continue
                cur = _to_float(policy["global_risk"].get(key))
                if cur is None:
                    result.vetoed.append({"action": action, "veto": "no_current_value"})
                    continue
                if new >= cur:
                    result.vetoed.append({"action": action, "veto": f"not_tightening({cur}->{new})"})
                    continue
                global_risk_patches[key] = new
                result.applied.append({"action": action, "applied": True, "old": cur, "new": new})
            elif t == "tighten_sleeve":
                sleeve = action.get("sleeve")
                key = action.get("key")
                new = _to_float(action.get("value"))
                if sleeve not in policy.get("sleeves", {}) or key not in _SLEEVE_KEYS or new is None:
                    result.vetoed.append({"action": action, "veto": "unknown_sleeve_or_key"})
                    continue
                cur = _to_float(policy["sleeves"][sleeve].get(key))
                if cur is None:
                    result.vetoed.append({"action": action, "veto": "no_current_value"})
                    continue
                if new >= cur:
                    result.vetoed.append({"action": action, "veto": f"not_tightening({cur}->{new})"})
                    continue
                sleeve_patches.setdefault(sleeve, {})[key] = new
                result.applied.append({"action": action, "applied": True, "old": cur, "new": new})
            elif t == "disable_sleeve":
                sleeve = action.get("sleeve")
                if sleeve not in policy.get("sleeves", {}):
                    result.vetoed.append({"action": action, "veto": "unknown_sleeve"})
                    continue
                if not policy["sleeves"][sleeve].get("enabled", True):
                    result.vetoed.append({"action": action, "veto": "already_disabled"})
                    continue
                sleeve_patches.setdefault(sleeve, {})["enabled"] = False
                result.applied.append({"action": action, "applied": True})
            else:
                result.vetoed.append({"action": action, "veto": f"unsupported_type({t})"})

        if global_risk_patches or sleeve_patches:
            payload: dict[str, Any] = {"_source": "advisor"}
            if global_risk_patches:
                payload["global_risk"] = global_risk_patches
            if sleeve_patches:
                payload["sleeves"] = sleeve_patches
            try:
                write_control(payload)
            except Exception as e:
                log.warning("advisor write_control failed: %s", e)
                result.vetoed.append({"veto": f"write_failed: {e}"})

        return result

    # --- log + recent ----------------------------------------------------

    def _log(self, advice: Advice, result: ApplyResult, ctx: dict) -> None:
        entry = {
            "ts":           int(time.time()),
            "actions_proposed":  list(advice.actions),
            "actions_applied":  result.applied,
            "actions_vetoed":   result.vetoed,
            "confidence":   advice.confidence,
            "error":        advice.error,
            "context_summary": {
                "equity":         ctx["portfolio"]["equity"],
                "drawdown_pct":   ctx["portfolio"]["drawdown_pct"],
                "day_pnl_pct":    ctx["portfolio"]["day_pnl_pct"],
            },
        }
        self.recent_buf.append(entry)
        try:
            with self.decision_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            log.warning("advisor: failed to write decision log: %s", e)

    def recent(self, n: int = 20) -> list[dict]:
        return list(self.recent_buf)[-n:][::-1]


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
