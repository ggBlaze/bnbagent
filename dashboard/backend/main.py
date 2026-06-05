"""FastAPI backend for the BNB Agent dashboard.

Exposes:
  GET  /api/stats              → live portfolio stats
  GET  /api/positions          → open positions
  GET  /api/trades             → recent closed trades
  GET  /api/cmc-charges        → x402 microcharge ledger
  GET  /api/txs                → TWAK-signed transactions (with BscScan deep links)
  GET  /api/policy             → current policy version + IPFS + sig
  GET  /api/identity           → ERC-8004 identity
  GET  /api/jobs               → ERC-8183 jobs
  GET  /api/equity-series      → equity curve (for the chart)
  GET  /api/sleeves            → per-sleeve breakdown
  WS   /ws                     → real-time stream (stats every 1s)

The data source is the in-memory DASHBOARD_STATE dict from core.main.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

# Import the shared state from core.main (if running in-process)
try:
    from core.main import DASHBOARD_STATE
except ImportError:
    DASHBOARD_STATE = {}


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


def build_app() -> FastAPI:
    app = FastAPI(title="BNB Agent Dashboard", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root():
        return HTMLResponse(open(Path(__file__).parent.parent / "frontend" / "index.html").read())

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(_stats())

    @app.get("/api/positions")
    async def positions():
        s = _state()
        portfolio = s.get("components", {}).get("portfolio") or {}
        return JSONResponse(_stats().get("sleeve_exposure", {}))

    @app.get("/api/trades")
    async def trades():
        s = _state()
        # If portfolio is in state, dump closed trades
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
        # fallback: read from log file
        log_path = Path("logs/agent.log")
        if log_path.exists():
            lines = log_path.read_text().splitlines()[-200:]
            charges = [json.loads(l) for l in lines if '"event"' not in l and "cmc" in l.lower()]
            return JSONResponse(charges[-100:])
        return JSONResponse([])

    @app.get("/api/txs")
    async def txs():
        # Read transactions from log file
        log_path = Path("logs/agent.log")
        if not log_path.exists():
            return JSONResponse([])
        out = []
        for line in log_path.read_text().splitlines()[-1000:]:
            try:
                rec = json.loads(line)
                if rec.get("event") == "position_open" or "tx_hash" in (rec.get("msg") or ""):
                    out.append({
                        "ts": rec.get("ts"),
                        "tx_hash": rec.get("tx_hash") or rec.get("msg", ""),
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
            "series": [{"ts": ts, "equity": float(e)} for ts, e in list(pf.equity_history)[-1000:]]
        })

    @app.get("/api/sleeves")
    async def sleeves():
        return JSONResponse(_stats().get("sleeves", {}))

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "ts": _stats().get("updated_at")}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        import asyncio
        try:
            while True:
                await websocket.send_json({
                    "stats": _stats(),
                    "ts": _stats().get("updated_at"),
                })
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass

    # mount frontend
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
