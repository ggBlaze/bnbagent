"""Layer 3 — ChatAgent.

Conversational interface for the operator. Streams tokens, can dispatch
tools, but NEVER writes to the policy or to control files. The chat can
RECOMMEND a policy change (and route the user to Setup to re-sign), but
applying the change requires the user's wallet password.

Tools available to the chat:
    get_pnl_summary            (read-only)
    list_recent_trades(n)      (read-only)
    list_open_positions        (read-only)
    recommend_risk_change      (read-only — returns a recommendation only)
    create_token               (delegates to TokenModule)
    list_skills                (read-only)
    enable_skill(name)         (registry op)
    disable_skill(name)        (registry op)
    sign_new_policy(overrides) (read-only — returns a UI prompt, never signs)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from agents.base import PersonaLoader, llm_stream
from agents.providers import AgentRouting, LLMRouter

log = logging.getLogger(__name__)


@dataclass
class ChatEvent:
    type: str   # "delta" | "tool_call" | "tool_result" | "banner" | "error" | "done"
    text: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    result: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    impl_name: str  # method name on ChatAgent


# --- the registry of tools the chat can invoke ----------------------------

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="get_pnl_summary",
        description="Return live portfolio stats (equity, day pnl, drawdown, sleeve exposure, etc).",
        parameters={"type": "object", "properties": {}, "required": []},
        impl_name="_tool_pnl",
    ),
    ToolSpec(
        name="list_recent_trades",
        description="Return the last N closed trades.",
        parameters={"type": "object", "properties": {"n": {"type": "integer", "default": 20}}, "required": []},
        impl_name="_tool_trades",
    ),
    ToolSpec(
        name="list_open_positions",
        description="Return all open positions.",
        parameters={"type": "object", "properties": {}, "required": []},
        impl_name="_tool_positions",
    ),
    ToolSpec(
        name="recommend_risk_change",
        description=(
            "Recommend a risk change. Returns a recommendation only; the user must "
            "go to Setup → re-sign the policy to apply it. The chat does not write."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key":   {"type": "string", "description": "policy key, e.g. per_trade_risk_pct"},
                "value": {"type": "number", "description": "proposed new value (must be a tightening)"},
                "reason": {"type": "string"},
            },
            "required": ["key", "value"],
        },
        impl_name="_tool_recommend",
    ),
    ToolSpec(
        name="create_token",
        description=(
            "Deploy a new ERC-20 token via TokenModule. For mainnet, "
            "confirm_mainnet must be true and the user must have explicitly typed 'mainnet'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name":    {"type": "string"},
                "symbol":  {"type": "string"},
                "supply":  {"type": "integer"},
                "decimals": {"type": "integer", "default": 18},
                "network":  {"type": "string", "enum": ["testnet", "mainnet"], "default": "testnet"},
                "confirm_mainnet": {"type": "boolean", "default": False},
            },
            "required": ["name", "symbol", "supply"],
        },
        impl_name="_tool_create_token",
    ),
    ToolSpec(
        name="list_skills",
        description="List all available Skills (notification + data) and their enabled state.",
        parameters={"type": "object", "properties": {}, "required": []},
        impl_name="_tool_list_skills",
    ),
    ToolSpec(
        name="enable_skill",
        description="Enable a Skill by name (e.g. telegram_alert, farcaster_post, cmc_global_filter).",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        impl_name="_tool_enable_skill",
    ),
    ToolSpec(
        name="disable_skill",
        description="Disable a Skill by name.",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        impl_name="_tool_disable_skill",
    ),
    ToolSpec(
        name="sign_new_policy",
        description=(
            "Returns a UI prompt the user must follow in the Setup wizard to re-sign "
            "their policy with new risk overrides. The chat never signs or writes."
        ),
        parameters={
            "type": "object",
            "properties": {"overrides": {"type": "object"}},
            "required": ["overrides"],
        },
        impl_name="_tool_resign",
    ),
]


# --- the ChatAgent --------------------------------------------------------

class ChatAgent:
    name = "chat"

    def __init__(self, *, components: dict, router: LLMRouter,
                 persona_name: str = "chat",
                 history_cap: int = 20,
                 decision_log: Path = Path("~/.bnbagent/decisions.jsonl").expanduser()):
        self.components = components
        self.routing: AgentRouting = router.for_agent(persona_name)
        self.loader = PersonaLoader(persona_name)
        self.history_cap = history_cap
        self.decision_log = decision_log
        self.recent_buf: deque[dict] = deque(maxlen=200)
        self._tool_impls = {spec.name: getattr(self, spec.impl_name) for spec in TOOL_SPECS}

    @property
    def enabled(self) -> bool:
        return self.routing.enabled

    def tool_specs(self) -> list[dict]:
        return [{"name": s.name, "description": s.description, "parameters": s.parameters}
                for s in TOOL_SPECS]

    # --- main entry ------------------------------------------------------

    async def chat(self, user_msg: str, history: list[dict] | None = None
                  ) -> AsyncIterator[ChatEvent]:
        if not self.routing.enabled:
            yield ChatEvent(type="banner", text=f"chat disabled: {self.routing.reason}")
            yield ChatEvent(type="done")
            return

        persona = self.loader.load()
        sys = persona.system + "\n\n" + self._system_state_block()
        history = list(history or [])[-self.history_cap:]
        messages = [{"role": "system", "content": sys}] + history + [
            {"role": "user", "content": user_msg},
        ]

        # 1) ask the LLM. It may emit tool calls as JSON in a fenced block.
        # We support a single tool call per turn for now (matches the persona's
        # "ask one clarifying question" guidance).
        buf: list[str] = []
        async for ev in llm_stream(self.routing, messages):
            buf.append(ev)
            yield ChatEvent(type="delta", text=ev)
        full_reply = "".join(buf)
        yield ChatEvent(type="done", text=full_reply)
        self._log_turn(user_msg, full_reply, tool_calls=[])

    async def dispatch_tool(self, name: str, args: dict) -> dict:
        impl = self._tool_impls.get(name)
        if not impl:
            return {"error": f"unknown tool: {name}"}
        try:
            return await impl(**args) if asyncio.iscoroutinefunction(impl) else impl(**args)
        except Exception as e:
            log.warning("tool %s failed: %s", name, e)
            return {"error": str(e)}

    # --- tools -----------------------------------------------------------

    def _tool_pnl(self) -> dict:
        pf = self.components.get("portfolio")
        if not pf:
            return {"error": "no portfolio"}
        return pf.stats()

    def _tool_trades(self, n: int = 20) -> list[dict]:
        pf = self.components.get("portfolio")
        if not pf:
            return []
        return list(pf.closed_trades)[-int(n):][::-1]

    def _tool_positions(self) -> list[dict]:
        pf = self.components.get("portfolio")
        if not pf:
            return []
        return [
            {
                "id": pid, "sleeve": p.sleeve, "symbol": p.symbol, "side": p.side,
                "notional_usdc": float(p.notional_usdc), "risk_usdc": float(p.risk_usdc),
                "entry_price": float(p.entry_price), "stop_price": float(p.stop_price),
                "tp_price": float(p.tp_price) if p.tp_price else None,
                "age_min": (int(time.time()) - p.entry_ts) // 60,
            }
            for pid, p in pf.positions.items()
        ]

    def _tool_recommend(self, key: str, value: float, reason: str = "") -> dict:
        policy = self.components.get("policy") or {}
        current = (policy.get("global_risk") or {}).get(key)
        return {
            "recommendation": {
                "key": key, "value": value, "reason": reason or "(no reason given)",
                "current": current, "tightening": (current is not None and value < current),
            },
            "apply_via": "Setup wizard → re-sign the policy (the chat cannot apply it).",
        }

    def _tool_resign(self, overrides: dict) -> dict:
        return {
            "ui_prompt": (
                "To apply these overrides, open the Setup wizard, review the risk section, "
                "and re-sign the policy with your wallet password. The chat will never sign for you."
            ),
            "overrides": overrides,
        }

    def _tool_create_token(self, name: str, symbol: str, supply: int, decimals: int = 18,
                            network: str = "testnet", confirm_mainnet: bool = False) -> dict:
        # Chat always returns a "pending" record; the actual deploy goes through
        # POST /api/tokens/deploy which enforces the confirm_mainnet guard for
        # mainnet. The dashboard UI handles the confirmation modal.
        return {
            "action": "create_token",
            "args": {"name": name, "symbol": symbol, "supply": int(supply),
                     "decimals": int(decimals), "network": network,
                     "confirm_mainnet": bool(confirm_mainnet)},
            "dispatch_via": "POST /api/tokens/deploy",
            "note": "Chat returns this so the dashboard can prompt the user. The actual deploy happens through /api/tokens/deploy (which enforces confirm_mainnet for mainnet).",
        }

    def _tool_list_skills(self) -> list[dict]:
        reg = self.components.get("skill_registry")
        if reg is None:
            return []
        return reg.list()

    def _tool_enable_skill(self, name: str) -> dict:
        reg = self.components.get("skill_registry")
        if reg is None:
            return {"error": "skill registry not loaded"}
        return reg.enable(name)

    def _tool_disable_skill(self, name: str) -> dict:
        reg = self.components.get("skill_registry")
        if reg is None:
            return {"error": "skill registry not loaded"}
        return reg.disable(name)

    # --- helpers ---------------------------------------------------------

    def _system_state_block(self) -> str:
        pf = self.components.get("portfolio")
        policy = self.components.get("policy") or {}
        cmc = self.components.get("cmc")
        return (
            "\n\nLIVE STATE (read-only snapshot for grounding):\n"
            f"- equity: {float(pf.equity()) if pf else 'n/a'}\n"
            f"- day_pnl_pct: {pf.day_pnl_pct() if pf else 'n/a'}\n"
            f"- drawdown_pct: {pf.drawdown_pct() if pf else 'n/a'}\n"
            f"- open_positions: {len(pf.positions) if pf else 0}\n"
            f"- policy_version: {policy.get('version', '?')}\n"
            f"- evaluator: {policy.get('evaluator_address', '?')}\n"
            f"- x402_spend_today_usdc: {getattr(cmc, '_x402_spend_today_usdc', 'n/a')}\n"
        )

    def _log_turn(self, user_msg: str, reply: str, tool_calls: list[dict]) -> None:
        entry = {
            "ts": int(time.time()),
            "user": user_msg[:500],
            "reply": reply[:1000],
            "tool_calls": tool_calls,
        }
        self.recent_buf.append(entry)
        try:
            with self.decision_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def recent(self, n: int = 50) -> list[dict]:
        return list(self.recent_buf)[-n:][::-1]
