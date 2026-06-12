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
  GET  /api/setup              current operator setup state
  POST /api/setup/config       update mode / chain / rpcs / cmc
  POST /api/setup/wallet       create a new wallet (returns address only, never the key)
  POST /api/setup/wallet/import   import existing private key (returns address only)
  POST /api/setup/sign         sign the current policy with the wallet's password
  GET  /api/setup/checklist    returns { complete: bool, missing: [...] }
  POST /api/setup/reset        wipe all operator state
  GET  /api/data-source        active data source tier + status
  POST /api/data-source/select persist + hot-swap the data source
  POST /api/data-source/cmc-key persist CMC Pro API key
  POST /api/data-source/base-rpcs persist Base RPC list
  GET  /api/data-source/x402-balance poll Base USDC balance of the wallet
  POST /api/wallet/export-mnemonic  reveal the 12/24-word phrase (password-gated)
  POST /api/chat               non-streamed chat
  POST /api/chat/tool          dispatch a tool call (used by dashboard)
  GET  /api/chat/tools         list available tools
  GET  /api/agent/advisor      last N advisor decisions
  GET  /api/agent/reviewer     last N reviewer decisions
  GET  /api/agent/personas     list persona names
  GET  /api/agent/personas/{n} get raw persona .md
  POST /api/agent/personas/{n} save persona body
  POST /api/agent/personas/{n}/reset  reset to pro default
  GET  /api/llm/status         which providers are configured
  GET  /api/tokens/config      TokenModule config
  POST /api/tokens/config      update TokenModule config
  POST /api/tokens/deploy      deploy a token (confirm_mainnet required for mainnet)
  GET  /api/skills             list all skills + enabled state
  POST /api/skills/{n}/enable  enable
  POST /api/skills/{n}/disable disable
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
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

try:
    from core.main import DASHBOARD_STATE
except ImportError:
    DASHBOARD_STATE = {}

from core.control import read_control, write_control
from core.setup import (
    SetupState, load_setup_state, set_runtime_config, generate_wallet,
    import_wallet, sign_current_policy, reset as reset_setup,
    export_env_for_process,
)
from agents.base import list_persona_names, read_persona_raw, PersonaLoader


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
    """v2.0.8-L3: branch on `mode`, not on `chain_id`.

    The old check was `chain_id == 56` to decide mainnet vs testnet.
    That works for BSC (97 vs 56) but breaks for any other chain
    the operator might point the agent at (e.g. ETH mainnet has
    chain_id 1, not 56; the old check would route to the BSC
    testnet URL). The right key is `mode`: testnet, mainnet, replay.
    Replay URLs are intentionally absent (there's nothing to link
    to — replay is offline).
    """
    mode = _cfg().get("mode", "testnet")
    if mode == "mainnet":
        base = "https://bscscan.com"
    elif mode == "replay":
        base = ""  # no explorer; the link is a no-op in replay
    else:  # testnet (default)
        base = "https://testnet.bscscan.com"
    if not base:
        return ""   # replay: caller should treat as 'no link'
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
        cmc = s.get("components", {}).get("data_source") or s.get("components", {}).get("cmc")
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

    # ------------------------------------------------------------------ setup
    @app.get("/api/setup")
    async def setup_get():
        s = load_setup_state()
        return JSONResponse({
            "mode": s.mode,
            "chain_id": s.chain_id,
            "rpcs": s.rpcs,
            "cmc_api_key_set": bool(s.cmc_api_key),
            "cmc_x402_base": s.cmc_x402_base,
            "wallet_address": s.wallet_address,
            "keystore_path": s.keystore_path,
            "evaluator_address": s.evaluator_address,
            "policy_signed": s.policy_signed,
            "policy_signature": s.policy_signature,
            "policy_version": s.policy_version,
            "is_complete": s.is_complete(),
            "missing": s.missing(),
            "env": export_env_for_process(),
        })

    @app.get("/api/setup/checklist")
    async def setup_checklist():
        s = load_setup_state()
        return JSONResponse({
            "complete": s.is_complete(),
            "missing": s.missing(),
            "wallet_ready": bool(s.wallet_address),
            "evaluator_set": bool(s.evaluator_address),
            "policy_signed": s.policy_signed,
            "chain_id": s.chain_id,
            "mode": s.mode,
        })

    @app.post("/api/setup/config")
    async def setup_config(body: dict):
        try:
            cfg = set_runtime_config(
                mode=str(body.get("mode", "testnet")),
                chain_id=int(body.get("chain_id", 97)),
                rpcs=list(body.get("rpcs", []) or []),
                cmc_api_key=str(body.get("cmc_api_key", "")),
                cmc_x402_base=body.get("cmc_x402_base"),
            )
            return JSONResponse({"ok": True, "config": cfg})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/wallet")
    async def setup_wallet(body: dict):
        """Generate a new wallet. Returns ONLY the address; the private key
        is encrypted to disk and never leaves the host process."""
        password = body.get("password", "")
        try:
            r = generate_wallet(password)
            return JSONResponse({"ok": True, **r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/wallet/import")
    async def setup_wallet_import(body: dict):
        """Import an existing private key. Returns ONLY the address; the key
        is encrypted to disk and never leaves the host process."""
        pk = body.get("private_key", "")
        password = body.get("password", "")
        try:
            r = import_wallet(pk, password)
            return JSONResponse({"ok": True, **r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/sign")
    async def setup_sign(body: dict):
        """Sign the current policy.yaml with the unlocked wallet."""
        password = body.get("password", "")
        try:
            r = sign_current_policy(password)
            return JSONResponse({"ok": True, **r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/reset")
    async def setup_reset():
        r = reset_setup()
        return JSONResponse({"ok": True, **r})

    # ------------------------------------------------------------- data source
    @app.get("/api/data-source")
    async def data_source_get():
        """Return the active data source tier + status.

        Graceful fallback: if no router is loaded (e.g. the dashboard
        was started before the agent booted), we still return a
        tier/status payload by reading config/config.yaml directly,
        so the frontend never has to special-case a missing router.
        """
        s = _state()
        router = s.get("components", {}).get("data_source") or s.get("data_source")
        # Try in-memory config first; fall back to reading config.yaml
        # from disk so the endpoint works even when no agent is booted
        # (which is the common case for the test suite).
        cfg = s.get("config") or {}
        if not cfg:
            cfg_path = Path("config/config.yaml")
            if cfg_path.exists():
                try:
                    cfg = yaml.safe_load(cfg_path.read_text()) or {}
                except Exception:
                    cfg = {}
        ds_cfg = cfg.get("data_source", {}) or {}
        if router is not None:
            base_rpcs = ds_cfg.get("base_rpcs", [])
            # Prefer the live source's status (x402 carries base_rpcs there);
            # fall back to config.
            try:
                src_status = router.source.status or {}
            except Exception:
                src_status = {}
            if "base_rpcs" in src_status:
                base_rpcs = src_status["base_rpcs"]
            return JSONResponse({
                "tier": router.tier,
                "status": router.status,
                "base_rpcs": base_rpcs,
            })
        # No live router — return whatever the config file says
        tier = ds_cfg.get("tier", "mock")
        return JSONResponse({
            "tier": tier,
            "status": {"tier": tier, "note": "no agent running"},
            "base_rpcs": ds_cfg.get("base_rpcs", []),
        })

    @app.post("/api/data-source/select")
    async def data_source_select(body: dict):
        """Persist the user's data-source choice + hot-swap the live source.

        Body: {"tier": "cmc_pro"|"x402"|"binance"|"mock"}

        Returns 400 with a clear error if the chosen tier's prerequisites
        aren't met (e.g. cmc_pro without a key, x402 without a funded Base
        wallet). The config is NOT written unless prereqs are satisfied.
        """
        tier = (body.get("tier") or "").strip()
        if tier not in ("cmc_pro", "x402", "binance", "mock"):
            return JSONResponse({"ok": False, "error": f"invalid tier: {tier}"},
                                status_code=400)

        cfg_path = Path("config/config.yaml")
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        else:
            cfg = {}
        ds = cfg.setdefault("data_source", {})

        # Prereq checks — return 400 BEFORE writing config so a bad
        # selection never lands on disk.
        if tier == "cmc_pro" and not ds.get("cmc_api_key"):
            return JSONResponse(
                {"ok": False,
                 "error": "cmc_pro requires a CMC API key; POST /api/data-source/cmc-key first"},
                status_code=400,
            )
        if tier == "x402":
            base_address = ds.get("base_address", "")
            if not base_address:
                return JSONResponse(
                    {"ok": False,
                     "error": "x402 requires a Base address; set data_source.base_address in config or pass ?address= when polling"},
                    status_code=400,
                )
            if not ds.get("base_rpcs"):
                return JSONResponse(
                    {"ok": False,
                     "error": "x402 requires at least one Base RPC; set data_source.base_rpcs in config"},
                    status_code=400,
                )

        ds["tier"] = tier
        # atomic-ish write: write to .tmp, then rename
        tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
        tmp.replace(cfg_path)

        # Hot-swap the live router if the agent is running
        s = _state()
        router = s.get("components", {}).get("data_source") or s.get("data_source")
        if router is not None:
            try:
                from connectors.data_source import (
                    DataSourceRouter, MockClient,
                )
                from connectors.cmc import CMCProClient, CMCX402Client
                from connectors.binance import BinanceClient
                wallet = s.get("wallet")
                if tier == "cmc_pro":
                    new = CMCProClient(api_key=ds["cmc_api_key"])
                elif tier == "x402" and wallet is not None:
                    new = CMCX402Client(
                        wallet=wallet,
                        base_rpcs=ds.get("base_rpcs"),
                    )
                elif tier == "binance":
                    new = BinanceClient()
                else:
                    new = MockClient()
                router.set_source(new)
            except Exception as e:
                log.warning("data_source: hot-swap failed: %s", e)
        return JSONResponse({"ok": True, "tier": tier})

    @app.post("/api/data-source/cmc-key")
    async def data_source_cmc_key(body: dict):
        """Persist the CMC Pro API key in config/config.yaml."""
        api_key = (body.get("api_key") or "").strip()
        if not api_key:
            return JSONResponse({"ok": False, "error": "api_key required"},
                                status_code=400)
        cfg_path = Path("config/config.yaml")
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        else:
            cfg = {}
        cfg.setdefault("data_source", {})["cmc_api_key"] = api_key
        tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
        tmp.replace(cfg_path)
        return JSONResponse({"ok": True})

    @app.post("/api/data-source/base-rpcs")
    async def data_source_base_rpcs(body: dict):
        """Persist the Base RPC list. Each URL must be a valid http(s) URL."""
        rpcs = body.get("base_rpcs")
        if not isinstance(rpcs, list) or not rpcs:
            return JSONResponse({"ok": False, "error": "base_rpcs must be a non-empty list"},
                                status_code=422)
        if len(rpcs) > 5:
            return JSONResponse({"ok": False, "error": "max 5 base_rpcs"},
                                status_code=422)
        from urllib.parse import urlparse
        for u in rpcs:
            parsed = urlparse(u)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return JSONResponse({"ok": False, "error": f"invalid URL: {u}"},
                                    status_code=422)
        cfg_path = Path("config/config.yaml")
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        else:
            cfg = {}
        cfg.setdefault("data_source", {})["base_rpcs"] = list(rpcs)
        tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
        tmp.replace(cfg_path)
        return JSONResponse({"ok": True, "base_rpcs": list(rpcs)})

    @app.get("/api/data-source/x402-balance")
    async def get_x402_balance(address: str | None = None):
        """Poll the Base USDC balance of `address` (or the configured one).

        Optional query param ?address=0x... overrides the config-stored
        address. Returns {address, balance_usdc, ready}. The wizard
        enables the Continue button when balance_usdc >= 0.50.
        """
        from connectors.x402 import check_balance
        cfg_path = Path("config/config.yaml")
        if cfg_path.exists():
            try:
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
            except Exception:
                cfg = {}
        else:
            cfg = {}
        ds_cfg = cfg.get("data_source", {}) or {}
        base_rpcs = ds_cfg.get("base_rpcs", [])
        if not base_rpcs:
            raise HTTPException(422, "no base_rpcs configured")
        usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        if not address:
            address = ds_cfg.get("base_address", "")
        if not address:
            raise HTTPException(
                400,
                "no address provided; pass ?address=0x... or set data_source.base_address in config",
            )
        try:
            raw = check_balance(base_rpcs, address, usdc)
        except Exception as e:
            raise HTTPException(502, f"all Base RPCs failed: {e}")
        balance_usdc = float(raw) / 1_000_000  # USDC has 6 decimals
        return {
            "address": address,
            "balance_usdc": balance_usdc,
            "ready": balance_usdc >= 0.50,
        }

    @app.post("/api/wallet/export-mnemonic")
    async def post_export_mnemonic(payload: dict):
        """Return the TWAK mnemonic if the correct password is provided.

        One-time per request — the password is not retained, the mnemonic
        is not logged. The keystore is the same AES-256-GCM blob that
        /api/setup/wallet wrote; we decrypt it briefly, return the phrase,
        and discard the key.
        """
        password = payload.get("password", "") if isinstance(payload, dict) else ""
        if not password:
            raise HTTPException(400, "password required")
        from connectors.keystore import load_keystore
        keystore_path = os.path.expanduser(
            os.environ.get("TWAK_KEYSTORE", "~/.twak/wallet.json")
        )
        try:
            ks = load_keystore(keystore_path, password=password)
        except FileNotFoundError:
            raise HTTPException(404, f"keystore not found at {keystore_path}")
        except Exception as e:
            raise HTTPException(401, f"invalid password or corrupt keystore: {e}")
        mnemonic = ks.get("mnemonic", "")
        if not mnemonic:
            raise HTTPException(500, "keystore has no mnemonic field")
        return {"mnemonic": mnemonic}

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

    # ----------------------------------------------------------- chat + agent
    def _chat_agent():
        s = _state()
        return s.get("components", {}).get("chat_agent")

    def _advisor():
        s = _state()
        return s.get("components", {}).get("advisor")

    def _reviewers():
        s = _state()
        return s.get("components", {}).get("reviewers", {})

    @app.post("/api/chat")
    async def chat_post(body: dict):
        ca = _chat_agent()
        if ca is None:
            return JSONResponse({"error": "chat agent not loaded"}, status_code=503)
        msg = body.get("message", "").strip()
        history = body.get("history", []) or []
        if not msg:
            return JSONResponse({"error": "message required"}, status_code=400)
        chunks: list[str] = []
        async for ev in ca.chat(msg, history):
            if ev.type == "delta":
                chunks.append(ev.text)
        return JSONResponse({"reply": "".join(chunks)})

    @app.post("/api/chat/tool")
    async def chat_tool(body: dict):
        ca = _chat_agent()
        if ca is None:
            return JSONResponse({"error": "chat agent not loaded"}, status_code=503)
        name = body.get("name", "")
        args = body.get("args", {}) or {}
        result = await ca.dispatch_tool(name, args)
        return JSONResponse({"ok": True, "result": result})

    @app.get("/api/chat/tools")
    async def chat_tools():
        ca = _chat_agent()
        if ca is None:
            return JSONResponse({"error": "chat agent not loaded"}, status_code=503)
        return JSONResponse({"enabled": ca.enabled, "tools": ca.tool_specs(),
                            "provider": ca.routing.provider_name, "model": ca.routing.model,
                            "reason": ca.routing.reason})

    @app.get("/api/agent/advisor")
    async def agent_advisor(limit: int = 20):
        adv = _advisor()
        if adv is None:
            return JSONResponse({"decisions": []})
        return JSONResponse({"decisions": adv.recent(limit)})

    @app.get("/api/agent/reviewer")
    async def agent_reviewer(limit: int = 50, sleeve: str | None = None):
        revs = _reviewers()
        out = []
        if sleeve:
            r = revs.get(sleeve)
            if r:
                out = r.recent(limit)
        else:
            for r in revs.values():
                out.extend(r.recent(limit))
        return JSONResponse({"decisions": out[-limit:]})

    @app.get("/api/agent/personas")
    async def agent_personas():
        return JSONResponse({"names": list_persona_names()})

    @app.get("/api/agent/personas/{name}")
    async def agent_persona_get(name: str):
        loader = PersonaLoader(name)
        try:
            p = loader.load()
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({
            "name": p.name, "system": p.system, "version": p.version,
            "sha256": p.sha256, "pro_default_sha256": p.pro_default_sha256,
            "diverged": p.diverged, "path": str(p.path),
        })

    @app.post("/api/agent/personas/{name}")
    async def agent_persona_save(name: str, body: dict):
        loader = PersonaLoader(name)
        body_text = body.get("body", "")
        version = str(body.get("version", "1.0.0"))
        try:
            p = loader.save_user(body_text, version=version)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "sha256": p.sha256, "diverged": p.diverged})

    @app.post("/api/agent/personas/{name}/reset")
    async def agent_persona_reset(name: str):
        loader = PersonaLoader(name)
        try:
            p = loader.reset_to_pro()
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "sha256": p.sha256, "diverged": p.diverged})

    @app.get("/api/llm/status")
    async def llm_status():
        s = _state()
        router = s.get("components", {}).get("llm_router")
        if router is None:
            return JSONResponse({"providers": {}, "agents": {}})
        return JSONResponse(router.status())

    @app.post("/api/llm/config")
    async def llm_config(body: dict):
        # Minimal: write agents/providers.yaml with the new per-agent routing.
        from pathlib import Path
        import yaml as _yaml
        cfg_path = Path("agents/providers.yaml")
        try:
            doc = _yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            doc = {}
        doc.setdefault("agents", {})
        for agent_name, agent_cfg in body.items():
            if not isinstance(agent_cfg, dict):
                continue
            doc["agents"][agent_name] = agent_cfg
        cfg_path.write_text(_yaml.safe_dump(doc, sort_keys=False))
        return JSONResponse({"ok": True, "note": "restart the agent to apply"})

    # ---------------------------------------------------------- tokens (stub)
    @app.get("/api/tokens/config")
    async def tokens_config_get():
        s = _state()
        tm = s.get("components", {}).get("token_module")
        if tm is None:
            return JSONResponse({"error": "TokenModule not loaded"}, status_code=503)
        return JSONResponse(tm.config)

    @app.post("/api/tokens/deploy")
    async def tokens_deploy(body: dict):
        s = _state()
        tm = s.get("components", {}).get("token_module")
        if tm is None:
            return JSONResponse({"error": "TokenModule not loaded"}, status_code=503)
        network = body.get("network", "testnet")
        symbol = (body.get("symbol") or "").strip()
        if network == "mainnet":
            if not body.get("confirm_mainnet", False):
                return JSONResponse({"error": "mainnet requires confirm_mainnet=true"},
                                    status_code=400)
            # Server-side re-check of the symbol match. The dashboard
            # prompts the user to type the symbol; we verify here too in
            # case the client was bypassed (curl, MCP, malicious script).
            typed = (body.get("confirm_symbol") or "").strip()
            if not typed or typed.upper() != symbol.upper():
                return JSONResponse(
                    {"error": f"mainnet requires confirm_symbol matching '{symbol}' (case-insensitive)"},
                    status_code=400,
                )
        try:
            result = await tm.create_token(
                name=body.get("name", ""),
                symbol=symbol,
                supply=int(body.get("supply", 0)),
                decimals=int(body.get("decimals", 18)),
                network=network,
                protocol=body.get("protocol"),
            )
            from dataclasses import asdict
            return JSONResponse({"ok": True, "result": asdict(result)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # --------------------------------------------------------------- skills
    @app.get("/api/skills")
    async def skills_list():
        s = _state()
        reg = s.get("components", {}).get("skill_registry")
        if reg is None:
            return JSONResponse({"skills": [], "note": "skill registry not loaded"})
        return JSONResponse({"skills": reg.list()})

    @app.post("/api/skills/{name}/enable")
    async def skills_enable(name: str):
        s = _state()
        reg = s.get("components", {}).get("skill_registry")
        if reg is None:
            return JSONResponse({"error": "skill registry not loaded"}, status_code=503)
        try:
            r = reg.enable(name)
            return JSONResponse({"ok": True, "result": r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/skills/{name}/disable")
    async def skills_disable(name: str):
        s = _state()
        reg = s.get("components", {}).get("skill_registry")
        if reg is None:
            return JSONResponse({"error": "skill registry not loaded"}, status_code=503)
        try:
            r = reg.disable(name)
            return JSONResponse({"ok": True, "result": r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    return app


app = build_app()


def run():
    import uvicorn
    port = int(os.environ.get("BNBAGENT_DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()
