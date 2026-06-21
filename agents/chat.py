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
        name="get_market_snapshot",
        description=(
            "Return live market prices for the BNB HACK universe "
            "(BTC, ETH, BNB, SOL, CAKE, plus the top movers by 24h). "
            "Uses the same data source tier the trading agent is using "
            "(x402 / binance / cmc_pro). Read-only; no spend."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of symbols. Default: full BNB HACK universe.",
                },
            },
            "required": [],
        },
        impl_name="_tool_market_snapshot",
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
        state_block = await self._system_state_block()
        sys = persona.system + "\n\n" + state_block
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

    async def _tool_market_snapshot(self, symbols: list[str] | None = None) -> dict:
        """Live market prices via the same data source the trading
        agent uses. v2.1.8: closes the 'how is the market?' gap —
        the chat can now answer with real quotes instead of pointing
        to other skills.

        Default universe: the BNB HACK 2026 BEP-20 allowlist (the
        coins the agent is actually evaluating on every tick), plus
        the two big caps (BTC, ETH) for context. The list is
        deliberately short so the x402 cost-per-call stays low (one
        batched quotes_latest call, not 149).
        """
        ds = self.components.get("data_source")
        if ds is None:
            return {"error": "no data source available"}
        # Default BNB HACK universe. Kept tight on purpose:
        # the live trading agent watches this exact set, so the
        # chat's "what's the market doing" answer is grounded in
        # the same prices the agent is making decisions on.
        default_universe = [
            "BTC", "ETH", "BNB", "SOL", "CAKE",
            "XRP", "ADA", "AVAX", "LINK", "DOGE",
            "UNI", "AAVE", "ATOM", "LTC", "BCH",
        ]
        syms = [s.upper() for s in (symbols or default_universe)]
        try:
            quotes_data = await ds.quotes_latest(syms)
        except Exception as e:
            return {"error": f"quotes_latest failed: {e}"}
        # Shape: data sources return {symbol: {price, pct_change_24h, ...}}
        # Build a flat list for the LLM to read.
        out = []
        for s in syms:
            q = quotes_data.get(s) if isinstance(quotes_data, dict) else None
            if not q:
                out.append({"symbol": s, "price": None, "change_24h_pct": None, "note": "no quote"})
                continue
            price = q.get("price") or q.get("quote") or q.get("USD")
            ch = q.get("percent_change_24h") or q.get("change_24h_pct")
            out.append({
                "symbol": s,
                "price": float(price) if price is not None else None,
                "change_24h_pct": float(ch) if ch is not None else None,
            })
        # Sort by |change_24h_pct| desc so the top movers surface.
        out.sort(key=lambda r: abs(r.get("change_24h_pct") or 0), reverse=True)
        return {
            "tier": getattr(ds, "tier", "unknown"),
            "as_of": "live",
            "quotes": out,
            "summary": {
                "symbols_with_quotes": sum(1 for r in out if r.get("price") is not None),
                "symbols_requested": len(syms),
                "top_mover": out[0]["symbol"] if out and out[0].get("change_24h_pct") is not None else None,
            },
        }

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

    async def _system_state_block(self) -> str:
        pf = self.components.get("portfolio")
        policy = self.components.get("policy") or {}
        cmc = self.components.get("data_source")
        base = (
            "\n\nLIVE STATE (read-only snapshot for grounding):\n"
            f"- equity: {float(pf.equity()) if pf else 'n/a'}\n"
            f"- day_pnl_pct: {pf.day_pnl_pct() if pf else 'n/a'}\n"
            f"- drawdown_pct: {pf.drawdown_pct() if pf else 'n/a'}\n"
            f"- open_positions: {len(pf.positions) if pf else 0}\n"
            f"- policy_version: {policy.get('version', '?')}\n"
            f"- evaluator: {policy.get('evaluator_address', '?')}\n"
            f"- x402_spend_today_usdc: {getattr(cmc, '_x402_spend_today_usdc', 'n/a')}\n"
        )
        # v2.1.8: include a live market snapshot so the LLM can answer
        # "how is the market?" without needing a tool dispatch (the
        # chat() method doesn't dispatch tool calls today — the LLM
        # emits the JSON but the runtime never executes it). Keep
        # the symbol list tight (the live trading agent watches this
        # exact set) so the x402 cost-per-call stays low.
        if cmc is not None and hasattr(cmc, "quotes_latest"):
            default_universe = [
                "BTC", "ETH", "BNB", "SOL", "CAKE",
                "XRP", "ADA", "AVAX", "LINK", "DOGE",
                "UNI", "AAVE", "ATOM", "LTC", "BCH",
            ]
            try:
                quotes = await cmc.quotes_latest(default_universe)
                # v2.1.8 (G): the Binance shape wraps quotes under
                # "data": {"data": {symbol: {...}}, "status": {...}}.
                # x402 wraps under "data" too (similar). Unwrap so
                # the inner per-symbol lookup works.
                if isinstance(quotes, dict) and "data" in quotes and isinstance(quotes["data"], dict) and not any(
                    sym in quotes for sym in default_universe
                ):
                    quotes = quotes["data"]
                lines = [f"- data_source_tier: {getattr(cmc, 'tier', 'unknown')}"]
                for sym in default_universe:
                    q = quotes.get(sym) if isinstance(quotes, dict) else None
                    if not q:
                        continue
                    # v2.1.8 (F): support the Binance response shape
                    # `{symbol: {"quote": {"USD": {"price": N, "percent_change_24h": M}}}`.
                    # The x402 shape is `{symbol: {"price": N, "percent_change_24h": M}}`
                    # (flat). Walk both without coupling to one source.
                    inner = q
                    if "quote" in q and isinstance(q["quote"], dict):
                        convert_map = q["quote"].get("USD") or q["quote"].get("USDC")
                        if isinstance(convert_map, dict):
                            inner = convert_map
                    price = inner.get("price") if isinstance(inner, dict) else None
                    ch = inner.get("percent_change_24h") if isinstance(inner, dict) else None
                    if ch is None and isinstance(inner, dict):
                        ch = inner.get("change_24h_pct")
                    if price is None:
                        continue
                    price_str = (
                        f"- {sym}: ${float(price):,.4f}"
                        f" ({'+' if (ch or 0) >= 0 else ''}{float(ch):.2f}% 24h)"
                        if ch is not None else
                        f"- {sym}: ${float(price):,.4f}"
                    )
                    lines.append(price_str)
                if len(lines) > 1:
                    base += "\nLIVE MARKET (data source quote, 24h change):\n" + "\n".join(lines) + "\n"
            except Exception as _e:
                # Market data is best-effort; never break the chat
                # if the data source is having a moment.
                base += f"\n(market snapshot unavailable: {_e})\n"
        return base

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
