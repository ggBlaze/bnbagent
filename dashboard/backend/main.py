"""FastAPI backend for the BNB Agent dashboard.

Endpoints
---------
  GET  /api/stats              live portfolio stats
  GET  /api/positions          open positions
  GET  /api/trades             recent closed trades
  GET  /api/cmc-charges        x402 microcharge ledger
  GET  /api/txs                TWAK-signed transactions (with BscScan deep links)
  GET  /api/policy             current policy version + IPFS + sig
  GET  /api/identity           ERC-8004 identity
  GET  /api/jobs               ERC-8183 jobs
  GET  /api/equity-series      equity curve (for the chart)
  GET  /api/sleeves            per-sleeve breakdown
  GET  /api/healthz            liveness probe (200 if process alive)
  GET  /api/logs               last N log lines
  GET  /api/logs/stream        SSE stream of new log lines
  POST /api/control            dashboard → agent intent (kill/resume/sleeve toggles/risk)
  GET  /api/config-schema      JSON schema for config.yaml (for the editor)
  GET  /api/config             current effective config
  WS   /ws                     real-time stats push (1Hz)
  GET  /                       single-file frontend
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

try:
    from core.main import DASHBOARD_STATE
except ImportError:
    DASHBOARD_STATE = {}

from core.control import read_control, write_control


# ---------------------------------------------------------------------------
# state accessors
# ---------------------------------------------------------------------------

def _state() -> dict:
    return DASHBOARD_STATE or {}


def _stats() -> dict:
    return _state().get("stats", {})


def _cfg() -> dict:
    return _state().get("config", {})


def _policy() -> dict:
    return _state().get("policy", {})


def _identity() -> dict:
    s = _state()
    return s.get("components", {}).get("identity", {}) or {}


def _bscscan_url(tx_hash: str) -> str:
    chain_id = _cfg().get("chain_id", 97)
    base = "https://bscscan.com" if chain_id == 56 else "https://testnet.bscscan.com"
    return f"{base}/tx/{tx_hash}"


def _logs_path() -> Path:
    p = Path(_cfg().get("log", {}).get("file", "logs/agent.log"))
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def _tail_log_lines(n: int = 200) -> list[str]:
    p = _logs_path()
    if not p.exists():
        return []
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # read last 64KB or whole file, whichever is smaller
            window = min(size, 64 * 1024)
            f.seek(size - window)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()[-n:]
        return lines
    except Exception:
        return []


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    app = FastAPI(title="BNB Agent Dashboard", version="1.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ pages
    @app.get("/")
    async def root():
        html_path = Path(__file__).parent.parent / "frontend" / "index.html"
        return HTMLResponse(html_path.read_text())

    @app.get("/favicon.ico")
    async def favicon():
        return HTMLResponse("", status_code=204)

    # ------------------------------------------------------------------ api
    @app.get("/api/healthz")
    async def healthz():
        return {"status": "ok", "ts": int(time.time()),
                "agent_updated_at": _stats().get("updated_at"),
                "kill_switch": _stats().get("kill_switch", False)}

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(_stats())

    @app.get("/api/positions")
    async def positions():
        return JSONResponse(_stats().get("sleeve_exposure", {}))

    @app.get("/api/trades")
    async def trades():
        s = _state()
        pf = s.get("components", {}).get("portfolio")
        if pf and hasattr(pf, "closed_trades"):
            return JSONResponse(list(pf.closed_trades))
        return JSONResponse([])

    @app.get("/api/cmc-charges")
    async def cmc_charges():
        s = _state()
        cmc = s.get("components", {}).get("cmc")
        if cmc and hasattr(cmc, "calls"):
            return JSONResponse(cmc.calls)
        return JSONResponse([])

    @app.get("/api/txs")
    async def txs():
        log_path = _logs_path()
        if not log_path.exists():
            return JSONResponse([])
        out = []
        for line in log_path.read_text().splitlines()[-1000:]:
            try:
                rec = json.loads(line)
                if rec.get("event") in ("position_open", "tx_signed", "tx_broadcast"):
                    out.append({
                        "ts": rec.get("ts") or time.time(),
                        "tx_hash": rec.get("tx_hash") or "",
                        "sleeve": rec.get("sleeve"),
                        "symbol": rec.get("symbol"),
                        "bscscan": _bscscan_url(rec.get("tx_hash", "0x0")),
                    })
            except Exception:
                continue
        return JSONResponse(out[-100:])

    @app.get("/api/policy")
    async def policy():
        p = _policy()
        if not p:
            return JSONResponse({"error": "no policy loaded"})
        return JSONResponse({
            "version":         p.get("version"),
            "issued_at":       p.get("issued_at"),
            "expires_at":      p.get("expires_at"),
            "evaluator":       p.get("evaluator_address"),
            "agent":           p.get("agent_address"),
            "global_risk":     p.get("global_risk"),
            "sleeve_allocations": p.get("sleeve_allocations"),
            "sleeves":         p.get("sleeves"),
            "signature":       p.get("signature"),
        })

    @app.get("/api/identity")
    async def identity():
        ident = _identity()
        if not ident:
            return JSONResponse({"error": "no identity registered"})
        chain_id = _cfg().get("chain_id", 97)
        return JSONResponse({
            "token_id":       ident.get("token_id"),
            "cid":            ident.get("cid"),
            "agent_address":  ident.get("agent_address"),
            "evaluator":      ident.get("evaluator_address"),
            "version":        ident.get("version"),
            "8004scan_url":   f"https://www.8004scan.io/agents/{ident.get('agent_address', '')}",
        })

    @app.get("/api/jobs")
    async def jobs():
        s = _state()
        jobs_obj = s.get("components", {}).get("erc8183")
        if jobs_obj and hasattr(jobs_obj, "all"):
            return JSONResponse(jobs_obj.all())
        return JSONResponse([])

    @app.get("/api/equity-series")
    async def equity_series():
        pf = _state().get("components", {}).get("portfolio")
        if not pf or not hasattr(pf, "equity_history"):
            return JSONResponse({"series": []})
        return JSONResponse({
            "series": [{"ts": ts, "equity": float(e)} for ts, e in list(pf.equity_history)[-2000:]]
        })

    @app.get("/api/sleeves")
    async def sleeves():
        return JSONResponse(_stats().get("sleeves", {}))

    @app.get("/api/control-log")
    async def control_log():
        return JSONResponse(_state().get("control_log", []))

    # ----------------------------------------------------------------- control
    @app.post("/api/control")
    async def control(intent: dict):
        """Dashboard → agent intent. Validated, merged with existing control file."""
        allowed = {"kill", "resume", "kill_reason", "sleeves", "global_risk"}
        clean = {k: v for k, v in intent.items() if k in allowed}
        if not clean:
            return JSONResponse({"error": "no recognized intent keys"}, status_code=400)
        current = read_control()
        # merge sleeves + global_risk dicts, replace kill flags
        merged = dict(current)
        for k, v in clean.items():
            if k in ("sleeves", "global_risk") and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
            else:
                merged[k] = v
        merged["_requested_at"] = int(time.time())
        write_control(merged)
        return JSONResponse({"ok": True, "merged": merged})

    @app.get("/api/control")
    async def control_get():
        return JSONResponse(read_control())

    # ------------------------------------------------------------------ logs
    @app.get("/api/logs")
    async def logs(n: int = 200):
        return JSONResponse(_tail_log_lines(n))

    @app.get("/api/logs/stream")
    async def logs_stream(request: Request):
        async def gen():
            last_size = 0
            while True:
                if await request.is_disconnected():
                    break
                p = _logs_path()
                if p.exists():
                    try:
                        cur = p.stat().st_size
                        if cur != last_size:
                            with p.open("rb") as f:
                                f.seek(last_size)
                                chunk = f.read(cur - last_size).decode("utf-8", errors="replace")
                            for line in chunk.splitlines():
                                yield f"data: {line}\n\n"
                            last_size = cur
                    except FileNotFoundError:
                        pass
                await asyncio.sleep(0.5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    # ------------------------------------------------------------------ config
    @app.get("/api/config")
    async def config():
        return JSONResponse(_cfg())

    @app.get("/api/config-schema")
    async def config_schema():
        # minimal schema; enough for the editor to render
        return JSONResponse({
            "type": "object",
            "properties": {
                "mode":   {"enum": ["testnet", "mainnet", "replay"]},
                "chain_id": {"type": "integer"},
                "global_risk": {
                    "type": "object",
                    "properties": {
                        "daily_loss_circuit_breaker_pct": {"type": "number", "min": 0.1, "max": 20},
                        "per_trade_risk_pct":             {"type": "number", "min": 0.1, "max": 5},
                        "max_gross_leverage":             {"type": "number", "min": 1,   "max": 5},
                        "max_single_position_pct":        {"type": "number", "min": 1,   "max": 50},
                        "max_drawdown_pct":               {"type": "number", "min": 1,   "max": 50},
                    }
                },
                "sleeves": {
                    "type": "object",
                    "properties": {
                        "A": {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
                        "B": {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
                        "C": {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
                    }
                }
            }
        })

    # --------------------------------------------------------------------- ws
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json({
                    "stats": _stats(),
                    "ts": _stats().get("updated_at") or int(time.time()),
                })
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass

    # ------------------------------------------------------------------ static
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    return app


app = build_app()


def run():
    import uvicorn
    port = int(os.environ.get("BNBAGENT_DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()
