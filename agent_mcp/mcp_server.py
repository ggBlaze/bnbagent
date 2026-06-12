"""MCP server entry point. Exposes BNB Agent as MCP tools.

Usage:
    python -m mcp.server --transport stdio
    python -m mcp.server --transport sse --port 8765

The server reaches back into the running agent's components via the same
DASHBOARD_STATE bus the FastAPI backend uses. In production the agent
runs as a single Python process (via `bash bnbagent`) and the MCP server
is a child process or sibling — they share the agent's process via
importing core.main.DASHBOARD_STATE.

For a one-shot test (without a running agent), the server falls back to
booting its own in-process replica via core.boot.boot().
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("mcp.server")


# v2.0.8-M3: module-level so it can be imported and unit-tested.
# Same behavior as before; just hoisted out of main() for testability.
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _TokenAuthMiddleware(BaseHTTPMiddleware):
        """Optional Bearer-token auth for the MCP SSE transport.

        If BNBAGENT_MCP_TOKEN is set, every SSE / messages request must
        carry a matching Authorization: Bearer <token> header. If unset,
        the server logs a WARNING and accepts unauthenticated requests
        (safe for localhost binding). The token is never logged, never
        written to disk, never exposed in /api/* responses.
        """

        def __init__(self, app, token: str | None = None):
            super().__init__(app)
            self._token = token

        async def dispatch(self, request, call_next):
            if self._token is None:
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != self._token:
                return JSONResponse(
                    {"error": "missing or invalid Bearer token"},
                    status_code=401,
                )
            return await call_next(request)
except ImportError:
    # starlette not installed (e.g. minimal env without MCP extra)
    _TokenAuthMiddleware = None  # type: ignore


def _build_server():
    """Build the MCP Server. Imports core lazily to avoid circular imports."""
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    server = Server("bnbagent")

    def _state():
        try:
            from core.main import DASHBOARD_STATE
            return DASHBOARD_STATE or {}
        except ImportError:
            return {}

    def _components() -> dict:
        return _state().get("components", {}) or {}

    def _portfolio():
        return _components().get("portfolio") or (lambda: None)

    def _text(s: str) -> list:
        return [TextContent(type="text", text=s)]

    def _json(obj: Any) -> list:
        try:
            return _text(json.dumps(obj, default=str))
        except Exception:
            return _text(repr(obj))

    # --- tool registry --------------------------------------------------

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name="bnbagent_get_pnl", description="Live portfolio stats (equity, day PnL, drawdown, sleeve exposure).",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="bnbagent_list_positions", description="Open positions across all sleeves.",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="bnbagent_list_trades", description="Recent closed trades.",
                 inputSchema={"type": "object", "properties": {"n": {"type": "integer", "default": 20}}, "required": []}),
            Tool(name="bnbagent_get_policy", description="Current signed User Policy summary (no secrets).",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="bnbagent_recommend_risk_change", description="Recommend (do not apply) a policy risk change.",
                 inputSchema={"type": "object", "properties": {
                     "key": {"type": "string"}, "value": {"type": "number"},
                     "reason": {"type": "string"}}, "required": ["key", "value"]}),
            Tool(name="bnbagent_deploy_token", description="Deploy a token via TokenModule.",
                 inputSchema={"type": "object", "properties": {
                     "name": {"type": "string"}, "symbol": {"type": "string"},
                     "supply": {"type": "integer"}, "decimals": {"type": "integer", "default": 18},
                     "network": {"type": "string", "enum": ["testnet", "mainnet"], "default": "testnet"},
                     "confirm_mainnet": {"type": "boolean", "default": False},
                 }, "required": ["name", "symbol", "supply"]}),
            Tool(name="bnbagent_chat", description="Ask the agent a question in natural language.",
                 inputSchema={"type": "object", "properties": {
                     "message": {"type": "string"},
                     "history": {"type": "array", "default": []},
                 }, "required": ["message"]}),
            Tool(name="bnbagent_list_skills", description="List all Skills and their enabled state.",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="bnbagent_enable_skill", description="Enable a Skill by name.",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            Tool(name="bnbagent_disable_skill", description="Disable a Skill by name.",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            Tool(name="competition_register", description="Register this agent's wallet on the BNB HACK 2026 Track 1 BSC competition contract (0x212c61b9b72c95d95bf29cf032f5e5635629aed5). Required before the live trading window opens on 2026-06-22. The rules page documents this exact MCP action name.",
                 inputSchema={"type": "object", "properties": {
                     "network": {"type": "string", "enum": ["mainnet", "testnet"], "default": "mainnet"},
                 }, "required": []}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        comps = _components()

        if name == "bnbagent_get_pnl":
            pf = comps.get("portfolio")
            return _json(pf.stats() if pf else {"error": "no portfolio"})

        if name == "bnbagent_list_positions":
            pf = comps.get("portfolio")
            if not pf:
                return _json([])
            return _json([
                {"id": pid, "sleeve": p.sleeve, "symbol": p.symbol, "side": p.side,
                 "notional_usdc": float(p.notional_usdc)}
                for pid, p in pf.positions.items()
            ])

        if name == "bnbagent_list_trades":
            pf = comps.get("portfolio")
            n = int(arguments.get("n", 20))
            if not pf:
                return _json([])
            return _json(list(pf.closed_trades)[-n:][::-1])

        if name == "bnbagent_get_policy":
            pol = comps.get("policy") or {}
            return _json({
                "version": pol.get("version"),
                "evaluator": pol.get("evaluator_address"),
                "agent": pol.get("agent_address"),
                "global_risk": pol.get("global_risk"),
                "sleeve_allocations": pol.get("sleeve_allocations"),
                "signature": (pol.get("signature") or "")[:20] + "…",
            })

        if name == "bnbagent_recommend_risk_change":
            key = arguments.get("key", "")
            value = float(arguments.get("value", 0))
            pol = comps.get("policy") or {}
            current = (pol.get("global_risk") or {}).get(key)
            return _json({
                "recommendation": {
                    "key": key, "value": value, "current": current,
                    "tightening": (current is not None and value < current),
                },
                "apply_via": "open the BNB Agent dashboard → Setup wizard → re-sign the policy with the new value.",
            })

        if name == "bnbagent_deploy_token":
            tm = comps.get("token_module")
            # Safety check FIRST — never bypass the confirmation guard
            network = arguments.get("network", "testnet")
            if network == "mainnet" and not arguments.get("confirm_mainnet", False):
                return _json({"error": "mainnet requires confirm_mainnet=true"})
            if not tm:
                return _json({"error": "TokenModule not loaded (start the agent with `bash bnbagent` first)"})
            try:
                from dataclasses import asdict
                result = await tm.create_token(
                    name=arguments["name"], symbol=arguments["symbol"],
                    supply=int(arguments["supply"]), decimals=int(arguments.get("decimals", 18)),
                    network=network,
                )
                return _json(asdict(result))
            except Exception as e:
                return _json({"error": str(e)})

        if name == "bnbagent_chat":
            ca = comps.get("chat_agent")
            if not ca:
                return _json({"error": "chat agent not loaded"})
            msg = arguments.get("message", "")
            history = arguments.get("history", []) or []
            chunks: list[str] = []
            async for ev in ca.chat(msg, history):
                if ev.type == "delta":
                    chunks.append(ev.text)
            return _json({"reply": "".join(chunks)})

        if name == "bnbagent_list_skills":
            reg = comps.get("skill_registry")
            return _json(reg.list() if reg else [])

        if name == "bnbagent_enable_skill":
            reg = comps.get("skill_registry")
            if not reg:
                return _json({"error": "skill registry not loaded"})
            try:
                return _json(reg.enable(arguments.get("name", "")))
            except Exception as e:
                return _json({"error": str(e)})

        if name == "bnbagent_disable_skill":
            reg = comps.get("skill_registry")
            if not reg:
                return _json({"error": "skill registry not loaded"})
            return _json(reg.disable(arguments.get("name", "")))

        if name == "competition_register":
            from scripts.competition_register import main as _register_main, _load_cache
            network = arguments.get("network", "mainnet")
            if network not in ("mainnet", "testnet"):
                return _json({"error": f"network must be mainnet or testnet, got {network!r}"})
            import io, contextlib
            out, err = io.StringIO(), io.StringIO()
            rc = 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = _register_main(["--network", network])
            except SystemExit as e:
                rc = e.code or 0
            except Exception as e:
                return _json({"error": f"{type(e).__name__}: {e}",
                              "stdout": out.getvalue(), "stderr": err.getvalue()})
            cache = _load_cache()
            return _json({
                "ok":       rc == 0,
                "returncode": rc,
                "result":   cache,
                "stdout":   out.getvalue().strip(),
                "stderr":   err.getvalue().strip(),
            })

        return _json({"error": f"unknown tool: {name}"})

    return server


async def _run_stdio():
    from mcp.server.stdio import stdio_server
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    p = argparse.ArgumentParser(description="BNB Agent MCP server")
    p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    p.add_argument("--port", type=int, default=8765)
    # v2.0.8-M3: default bind address is 127.0.0.1 (was 0.0.0.0).
    # The MCP server has 10 tools (4 read-only, 1 recommend-only,
    # 1 mainnet-gated deploy, 1 chat, 2 skill toggles, 1 list).
    # Binding to 0.0.0.0 with no auth means anyone on the network
    # can read the portfolio, enable skills (some write to the
    # control file), and chat with the agent. Operators who need
    # remote access can opt in with --host 0.0.0.0 AND a Bearer
    # token via the BNBAGENT_MCP_TOKEN env var (enforced in
    # _enforce_token). The default is now safe.
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.transport == "stdio":
        asyncio.run(_run_stdio())
    else:
        # SSE transport: mount on a tiny FastAPI app
        try:
            from mcp.server.sse import SseServerTransport
            from starlette.applications import Starlette
            from starlette.middleware import Middleware
            from starlette.middleware.base import BaseHTTPMiddleware
            from starlette.requests import Request
            from starlette.responses import Response, JSONResponse
            from starlette.routing import Mount, Route
            import uvicorn
        except ImportError as e:
            print(f"SSE transport not available: {e}", file=sys.stderr)
            sys.exit(1)

        # v2.0.8-M3: optional Bearer token auth. If BNBAGENT_MCP_TOKEN
        # is set, every SSE / messages request must carry a matching
        # Authorization: Bearer <token> header. If unset, the server
        # logs a WARNING (not an error — local-only stdio is still safe)
        # and accepts unauthenticated requests. The token is NEVER
        # logged, NEVER written to disk, and NEVER exposed in the
        # /api/* responses.
        mcp_token = os.environ.get("BNBAGENT_MCP_TOKEN")

        sse = SseServerTransport("/messages")
        server = _build_server()

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
                await server.run(r, w, server.create_initialization_options())
            return Response()

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages", app=sse.handle_post_message),
            ],
            middleware=[Middleware(_TokenAuthMiddleware, token=mcp_token)],
        )
        log.info("MCP SSE listening on %s:%d (auth=%s)", args.host, args.port,
                 "required" if mcp_token else "disabled (local-only)")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
