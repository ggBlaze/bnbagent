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
  GET  /api/version            build version + git commit (for the dashboard footer)
  GET  /api/wallet/balances    live BSC + Base balances for the operator wallet
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

# v2.1.8: load `.env` BEFORE any local imports so the dashboard sees the
# operator's TWAK_PWD / MINIMAX_API_KEY / BNBAGENT_AUTH_MODE / etc. on
# the very first request, without depending on the shell to source it.
# Default override=False so a shell export wins over the file.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Body, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

# v2.1.8 (F1): the dashboard and the agent are sibling processes (per
# `bash bnbagent`), so importing DASHBOARD_STATE from `core.main` here
# would only ever return THIS process's empty dict. The real state
# travels via `core/dashboard_state.py` (JSON snapshot file under
# ~/.bnbagent/dashboard_state.json). We still keep a local
# DASHBOARD_STATE dict for tests + same-process callers that mutate it
# directly — `_state()` layers the two (file wins).
DASHBOARD_STATE: dict = {}

from core import dashboard_state as _ds_file

from core.control import read_control, write_control
from . import auth as _auth
from core.setup import (
    SetupState, load_setup_state, set_runtime_config, generate_wallet,
    import_wallet, sign_current_policy, reset as reset_setup,
    export_env_for_process,
    set_live_balance, poll_live_balance,
)
from core.config_paths import (
    load_config as _load_merged_config,
    write_local as _write_local,
)
from agents.base import list_persona_names, read_persona_raw, PersonaLoader


# ---------------------------------------------------------------------------
# state accessors
# ---------------------------------------------------------------------------

def _state() -> dict:
    """Return the live agent state.

    Layered: the IPC snapshot from the agent process wins over any
    in-process DASHBOARD_STATE (which only callers in the same process
    can populate, like unit tests). Missing keys fall through.
    """
    file_state = _ds_file.read_state()
    if not file_state:
        return DASHBOARD_STATE or {}
    if not DASHBOARD_STATE:
        return file_state
    merged = dict(DASHBOARD_STATE)
    merged.update(file_state)
    return merged


def _component_attr(comp, attr: str, default=None):
    """v2.1.8 (P4): get an attribute / dict-key from a component that
    may be either a live class instance (in-process tests) or a
    `{tier, status}` dict snapshot the agent published into the IPC
    file (cross-process). One helper, one shape, every endpoint.
    """
    if comp is None:
        return default
    if isinstance(comp, dict):
        return comp.get(attr, default)
    value = getattr(comp, attr, default)
    if callable(value):
        try:
            return value()
        except Exception:
            return default
    return value


# v2.1.3: dotenv helpers for the LLM API key UI. The keys are env vars
# referenced by $VAR substitution in agents/providers.yaml; the dashboard
# writes them to .env (gitignored). Atomic-ish via .tmp + rename so a
# crash mid-write doesn't leave a half-written file.
_DOTENV_PATH = Path(".env")


def _get_env_var_from_dotenv(name: str) -> str:
    """Read an env var from .env directly (not from os.environ).

    Why: the in-process router has env vars cached from boot. If the
    user just set a key in .env and hasn't restarted, os.environ still
    has the old value. Reading .env directly lets the /api/llm/test
    endpoint verify the user's new key without requiring a restart.
    """
    if not _DOTENV_PATH.exists():
        return ""
    for line in _DOTENV_PATH.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(f"{name}="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _set_env_var_in_dotenv(name: str, value: str) -> None:
    """Set or replace an env var in .env. Atomic-ish (.tmp + rename).

    Preserves comments + ordering of unrelated lines. If `name` is not
    in the file yet, appends it. The value is written as-is (no
    quoting/escaping) — we strip user input at the API layer.
    """
    lines = _DOTENV_PATH.read_text().splitlines() if _DOTENV_PATH.exists() else []
    found = False
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and s.split("=", 1)[0].strip() == name:
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    _DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DOTENV_PATH.with_suffix(_DOTENV_PATH.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    tmp.replace(_DOTENV_PATH)


def _stats() -> dict:
    return _state().get("stats", {})


def _cfg() -> dict:
    return _state().get("config", {})


def _policy() -> dict:
    return _state().get("policy", {})


def _identity() -> dict:
    s = _state()
    return s.get("components", {}).get("identity", {}) or {}


def _mode_aware_stats() -> dict:
    """Return stats with a mode-aware 'primary' PnL view.

    v2.1.8 (live-paper): the BNB HACK design has TWO PnL streams — paper
    (the $100 paper book, used for sizing and contest scoring) and real
    (trades that actually settled on PCS). The dashboard used to show
    both side-by-side, which was confusing when running on mainnet with
    real funds. This wrapper:

      - adds `mode` so the UI can render a clear badge
      - adds `primary_pnl`, `primary_equity`, `primary_trades`, `primary_label`
        so the UI can show the "main" PnL for the active mode:
          * mainnet  → real PnL (settled trades on the venue)
          * testnet  → paper PnL
          * mock     → paper PnL
          * replay   → paper PnL
      - adds `live_funds` with the actual BSC USDC balance so the
        operator sees their on-chain money alongside the PnL
      - keeps all the original fields (paper_*, real_*, starting,
        peak, drawdown_pct) for backward compat with anything else
        that reads them

    Paper book stays the source of truth for the BNB HACK PnL evaluation
    (the contest scores the strategy, not the wallet). The "primary"
    PnL is what the operator cares about day-to-day; the contest still
    sees the same paper numbers.
    """
    s = _stats()
    cfg = _cfg()
    setup = load_setup_state()
    mode = cfg.get("mode") or "mock"
    is_mainnet = mode == "mainnet"

    # The "primary" PnL is real on mainnet, paper everywhere else.
    if is_mainnet:
        primary_pnl = float(s.get("real_pnl_usdc", 0) or 0)
        primary_trades = int(s.get("real_trades", 0) or 0)
        primary_label = "real (settled on PCS)"
        # v2.2.0 (live-balance): use the on-chain USDC balance if the
        # /api/live-balance endpoint has polled it. Otherwise fall back
        # to None so the frontend knows to show '—' instead of lying
        # with the $100 paper book value labeled as 'live funds'.
        cached_usdc = getattr(setup, "usdc_balance", None)
        primary_equity = float(cached_usdc) if cached_usdc is not None else None
    else:
        primary_pnl = float(s.get("paper_pnl_usdc", 0) or 0)
        primary_trades = int(s.get("paper_trades", 0) or 0)
        primary_label = f"paper sim ({mode})"
        primary_equity = None  # paper book equity comes from the IPC stats

    out = dict(s)
    out["mode"] = mode
    out["chain_id"] = cfg.get("chain_id")
    out["primary_pnl_usdc"] = primary_pnl
    out["primary_equity_usdc"] = primary_equity
    out["primary_trades"] = primary_trades
    out["primary_label"] = primary_label
    # v2.2.0 (live-balance): expose the cached on-chain balances so the
    # frontend can show the wallet USDC + BNB alongside the PnL.
    out["wallet_usdc_balance"] = getattr(setup, "usdc_balance", None)
    out["wallet_bnb_balance"] = getattr(setup, "bnb_balance", None)
    out["wallet_balance_ts"] = getattr(setup, "live_balance_ts", 0)
    # v2.2.0 (onchain-floor): surface the on-chain floor trades so the
    # dashboard's BNB HACK panel can link to BscTrace. The agent writes
    # these into dashboard_state["floor_onchain_txs"] on every successful
    # submit; the IPC file is the source of truth.
    out["floor_onchain_txs"] = _state().get("floor_onchain_txs", [])[-10:]
    # v2.2.0 (live-only): the paper book is the contest's strategy
    # simulation. On mainnet we surface it as a clearly-labeled
    # secondary view; the hero is the real wallet. The contest
    # scoring reads `paper_pnl_usdc` + `paper_trades` (unchanged).
    if is_mainnet:
        out["paper_sim_equity"]   = float(s.get("equity", 0) or 0)
        out["paper_sim_starting"] = float(s.get("starting", 0) or 0)
        out["paper_sim_peak"]     = float(s.get("peak", 0) or 0)
        out["paper_sim_pnl"]      = float(s.get("paper_pnl_usdc", 0) or 0)
        out["paper_sim_trades"]   = int(s.get("paper_trades", 0) or 0)
    return out


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
    from core.version import __version__
    app = FastAPI(title="BNB Agent Dashboard", version=__version__)
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

    # --- v2.1.5: 2-mode password wrapper for the public demo ---
    # BNBAGENT_AUTH_ENABLED=true (default OFF) gates operator-only
    # routes. Two passwords: JUDGE_PASSWORD and ADMIN_PASSWORD, both
    # defaulting to obvious dev values. See dashboard/backend/auth.py
    # for the full contract (cookie format, expiry, env vars).
    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        # Public endpoint — returns the current role (or null). The
        # frontend calls this on load to decide whether to show the
        # login form. Always 200; the role field is what matters.
        role = _auth.current_role(request)
        return JSONResponse({
            "enabled": _auth.AUTH_ENABLED,
            "mode":   _auth.current_mode(),
            "role":   role,
        })

    @app.post("/api/auth/login")
    async def auth_login(body: dict, response: Response):
        # Only meaningful in `password` mode. In `disabled` the wrapper
        # is bypassed anyway (no login needed), in `readonly` the
        # public URL must not be able to escalate to admin via the
        # password gate.
        if _auth.AUTH_MODE != "password":
            raise HTTPException(
                status_code=403,
                detail=(
                    f"login is disabled in {(_auth.AUTH_MODE or 'unknown')} mode. "
                    "Only the 'password' mode uses the JUDGE/ADMIN gate."
                ),
            )
        password = body.get("password", "")
        role = _auth.check_password(password)
        if role is None:
            # Don't leak which password was wrong — single 401 either way.
            raise HTTPException(status_code=401, detail="bad password")
        token = _auth.make_token(role)
        # In production (https) the cookie should be 'secure'; in local
        # dev (http) the browser would drop a secure cookie, so we
        # only set it when the operator explicitly opts in via
        # BNBAGENT_AUTH_COOKIE_SECURE=true. The reverse proxy (Coolify /
        # Caddy) terminates TLS, so the FastAPI app sees http by default.
        secure_cookie = os.environ.get("BNBAGENT_AUTH_COOKIE_SECURE", "false").lower() in (
            "1", "true", "yes", "on",
        )
        # Set the cookie on the injected Response. We return a dict
        # (NOT a JSONResponse) so FastAPI wraps it in a default
        # JSONResponse while preserving the cookies/headers we set on
        # the injected response parameter.
        response.set_cookie(
            key=_auth.COOKIE_NAME,
            value=token,
            max_age=_auth.COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
            secure=secure_cookie,
            path="/",
        )
        return {"ok": True, "role": role}

    @app.post("/api/auth/logout")
    async def auth_logout(response: Response):
        response.delete_cookie(_auth.COOKIE_NAME, path="/")
        # Return a dict (not JSONResponse) so FastAPI preserves the
        # Set-Cookie: <name>=; Max-Age=0 header that delete_cookie sets.
        return {"ok": True}

    @app.get("/api/healthz")
    async def healthz():
        return {"status": "ok", "ts": int(time.time()),
                "agent_updated_at": _stats().get("updated_at"),
                "kill_switch": _stats().get("kill_switch", False)}

    @app.get("/api/version")
    async def version():
        """Return the canonical version + git commit for the dashboard footer."""
        from core.version import version_info
        return JSONResponse(version_info())

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(_mode_aware_stats())

    @app.get("/api/positions")
    async def positions():
        return JSONResponse(_stats().get("sleeve_exposure", {}))

    @app.get("/api/trades")
    async def trades():
        """Recent closed trades.

        v2.2.0 (live-only): on mainnet, return only real on-chain
        trades (is_paper=False). The agent doesn't yet sign on-chain
        orders, so this list is empty until v2.3.0 wires the BNB
        SDK. The frontend shows 'no on-chain trades yet' for that
        case. On non-mainnet modes we return the paper-book trades
        (the strategy simulation).

        The agent publishes trades via the IPC `trades_view` field
        (see core/tick.py); cross-process callers (the dashboard)
        read from there. In-process callers (tests) get the live
        portfolio directly.
        """
        s = _state()
        cfg = _cfg()
        is_mainnet = (cfg.get("mode") or "") == "mainnet"
        # Prefer the IPC-published trades_view (cross-process)
        trades_view = s.get("trades_view")
        if isinstance(trades_view, list):
            all_trades = trades_view
            source = "ipc_snapshot"
        else:
            # Fall back to the in-process portfolio (tests)
            pf = s.get("components", {}).get("portfolio")
            if pf and hasattr(pf, "closed_trades"):
                all_trades = list(pf.closed_trades)
                source = "in_process"
            else:
                return JSONResponse({
                    "trades": [],
                    "source": "none",
                    "is_mainnet": is_mainnet,
                })
        if is_mainnet:
            real = [t for t in all_trades if not t.get("is_paper", True)]
            return JSONResponse({
                "trades": real,
                "source": "live_onchain",
                "is_mainnet": True,
                "paper_trades_count": len(all_trades) - len(real),
            })
        return JSONResponse({
            "trades": all_trades,
            "source": "paper_book",
            "is_mainnet": False,
        })

    @app.get("/api/cmc-charges")
    async def cmc_charges():
        s = _state()
        cmc = s.get("components", {}).get("data_source") or s.get("components", {}).get("cmc")
        if cmc and hasattr(cmc, "calls"):
            return JSONResponse(cmc.calls)
        return JSONResponse([])

    @app.get("/api/wallet/balances")
    async def wallet_balances():
        """Live on-chain balances for the operator wallet (BSC + Base if x402).

        Returns a JSON-safe dict with {wallet, chain_id, bsc:{native, tokens[]},
        base?:{native, tokens[]}, base_active, fetched_at, error}. The frontend
        renders the right-rail 'Wallet Holdings' panel from this and polls on
        a 30s cadence. All reads are best-effort; a failed RPC is captured
        per-chain and the endpoint never raises.
        """
        from core.balances import get_wallet_balances, balances_to_dict
        setup = load_setup_state()
        cfg = _cfg()
        ds_cfg = (cfg.get("data_source", {}) or {})
        base_rpcs = ds_cfg.get("base_rpcs", []) or []
        # x402 is active when the selected data source tier is 'x402'
        base_active = str(ds_cfg.get("tier", "")).lower() == "x402"
        b = get_wallet_balances(
            wallet_address=setup.wallet_address,
            bsc_rpcs=setup.rpcs or [],
            chain_id=setup.chain_id,
            base_active=base_active,
            base_rpcs=base_rpcs,
        )
        return JSONResponse(balances_to_dict(b))

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

    # --- v2.1.4: BNB HACK 2026 Track 1 on-chain competition registration ---
    # The rules page (https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail)
    # requires every Track 1 participant to register on the BSC
    # competition contract before the live window opens on 2026-06-22.
    # These endpoints wrap `python -m scripts.competition_register`.

    @app.get("/api/competition/register/status")
    async def competition_register_status():
        """Return the cached registration state, plus the contract address.

        v2.2.0: if the cache shows `ok=true` but no `tx_hash`, the
        original registration was done via a path that didn't capture
        the tx hash (e.g. direct Web3 call, manual MCP action). In
        that case the agent is still registered (the on-chain
        `isRegistered` view is the source of truth) but we don't
        have the tx hash locally. We surface a `tx_hash_unknown: true`
        flag so the frontend can show a clear message instead of
        silently rendering '—'.
        """
        from scripts.competition_register import COMPETITION_CONTRACT, _load_cache
        cache = _load_cache()
        tx_hash = cache.get("tx_hash")
        registered = bool(cache.get("ok"))
        return JSONResponse({
            "contract":          COMPETITION_CONTRACT,
            "bsctrace_url":      f"https://bsctrace.com/address/{COMPETITION_CONTRACT}",
            "bsctrace_agent_url": f"https://bsctrace.com/address/{cache.get('agent_address', '')}",
            "rules_url":         "https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail",
            "registered":        registered,
            "tx_hash":           tx_hash,
            "tx_hash_unknown":   registered and not tx_hash,
            "register_method":   cache.get("method"),  # 'npx_twak' or 'direct_web3' or 'mcp'
            "agent_address":     cache.get("agent_address"),
            "network":           cache.get("network"),
            "timestamp":         cache.get("timestamp"),
            "error":             cache.get("stderr") if not registered else None,
        })

    @app.post("/api/competition/register", dependencies=[Depends(_auth.require_admin)])
    async def competition_register(payload: dict = Body(default_factory=dict)):
        """Trigger `npx twak compete register` via the script wrapper.

        Body: {"network": "mainnet"} (default "mainnet")
        Returns the script's JSON output.

        v2.2.0 (register-guard): if a previous registration is
        already on-disk (cached tx hash + ok=True), the endpoint
        refuses with HTTP 409 Conflict. Re-registering would burn
        another tx fee (npx twak compete register submits a fresh
        tx) and risks confusing the operator or the contract state.
        The frontend disables the button when `registered=true`;
        this is the belt-and-suspenders backend guard.
        """
        from scripts.competition_register import (
            main as register_main, _load_cache as _reg_cache,
        )
        network = (payload or {}).get("network", "mainnet")
        if network not in ("mainnet", "testnet"):
            return JSONResponse({"error": f"network must be mainnet or testnet, got {network!r}"})
        # v2.2.0: refuse re-registration if the cache shows we're
        # already registered on the same network. A different
        # network (e.g. mainnet → testnet) is allowed because the
        # contract is the same and the same wallet may legitimately
        # want to test on testnet first.
        existing = _reg_cache()
        if existing.get("ok") and (existing.get("network") or "mainnet") == network:
            return JSONResponse({
                "ok": False,
                "error": "already_registered",
                "message": f"agent is already registered on {network} (tx: {existing.get('tx_hash')}). "
                           "Re-registration is refused to prevent double tx fees.",
                "tx_hash": existing.get("tx_hash"),
                "network": network,
                "timestamp": existing.get("timestamp"),
            }, status_code=409)
        argv = ["--network", network]
        # Capture stdout/stderr
        import io, contextlib
        out_buf, err_buf = io.StringIO(), io.StringIO()
        rc = 0
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                rc = register_main(argv)
        except SystemExit as e:
            rc = e.code or 0
        except Exception as e:
            return JSONResponse({
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "stdout": out_buf.getvalue(),
                "stderr": err_buf.getvalue(),
            })
        # Try to parse the script's JSON output (it prints the result dict).
        out_text = out_buf.getvalue().strip()
        parsed = None
        if out_text.startswith("{"):
            try:
                import json as _json
                parsed = _json.loads(out_text)
            except Exception:
                pass
        return JSONResponse({
            "ok":      rc == 0,
            "returncode": rc,
            "result":  parsed,
            "stdout":  out_text,
            "stderr":  err_buf.getvalue(),
        })

    @app.post("/api/competition/register/emit-mcp", dependencies=[Depends(_auth.require_admin)])
    async def competition_register_emit_mcp():
        """Print the MCP `competition_register` action the user can drive
        from any MCP client (Claude Code, Goose, Cursor, etc.). Useful
        for the demo video — shows the agent is reachable as an MCP
        server, not just a CLI.
        """
        from scripts.competition_register import _resolve_agent_address, _emit_mcp_action, COMPETITION_CONTRACT
        addr = _resolve_agent_address()
        if not addr:
            return JSONResponse({"error": "could not resolve agent_address (sign the policy or set BNBAGENT_PRIVATE_KEY)"})
        return JSONResponse(_emit_mcp_action(addr, "mainnet"))

    @app.get("/api/eligibility")
    async def eligibility_status():
        """The BNB HACK 2026 eligible 149 BEP-20 universe + the filter mode."""
        from core.eligibility import report
        return JSONResponse(report())

    @app.get("/api/jobs")
    async def jobs():
        s = _state()
        jobs_obj = s.get("components", {}).get("erc8183")
        if jobs_obj and hasattr(jobs_obj, "all"):
            return JSONResponse(jobs_obj.all())
        return JSONResponse([])

    @app.get("/api/equity-series")
    async def equity_series():
        """Equity curve for the dashboard chart.

        v2.2.0 (live-only): on mainnet, the chart must reflect the
        real wallet USDC, not the $100 paper book. The agent doesn't
        have real on-chain trade history yet (the on-chain order
        submission path is a v2.3.0 workstream), so we build the
        series from the live balance: a single point per poll,
        starting at the wallet USDC at the live window open.

        On testnet/mock/replay we keep the paper-book equity_history
        because that's the only PnL signal available.
        """
        import time as _t
        cfg = _cfg()
        is_mainnet = (cfg.get("mode") or "") == "mainnet"
        s = load_setup_state()
        live_usdc = getattr(s, "usdc_balance", None)
        live_ts = int(getattr(s, "live_balance_ts", 0) or 0)

        if is_mainnet and live_usdc is not None and live_usdc > 0:
            # Real mode + real wallet. Build a series from the live
            # balance. The agent's real-trade list is empty (no
            # on-chain orders yet) so there's nothing to add; the
            # series is a single point at the current wallet USDC.
            # Future ticks (when the agent signs on-chain) will
            # extend this in /api/equity-series/history below.
            history = _state().get("real_equity_history", [])
            if not history:
                history = [{"ts": live_ts or int(_t.time()),
                            "equity": float(live_usdc)}]
            return JSONResponse({
                "series": history[-2000:],
                "source": "live_wallet",
                "wallet_usdc": float(live_usdc),
                "wallet_bnb":  float(getattr(s, "bnb_balance", 0) or 0),
                "live_window_start": cfg.get("live_window_start"),
            })

        # Paper / testnet / mock / replay — use the paper-book history
        pf = _state().get("components", {}).get("portfolio")
        if not pf or not hasattr(pf, "equity_history"):
            return JSONResponse({"series": [], "source": "none"})
        return JSONResponse({
            "series": [{"ts": ts, "equity": float(e)}
                       for ts, e in list(pf.equity_history)[-2000:]],
            "source": "paper_book",
        })

    @app.get("/api/sleeves")
    async def sleeves():
        return JSONResponse(_stats().get("sleeves", {}))

    @app.get("/api/control-log")
    async def control_log():
        return JSONResponse(_state().get("control_log", []))

    # ----------------------------------------------------------------- control
    @app.post("/api/control", dependencies=[Depends(_auth.require_admin)])
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

    # v2.2.0 (live-balance): poll the on-chain BSC wallet USDC + BNB
    # balance. The dashboard hero previously showed the $100 paper book
    # labeled as 'mainnet · live funds' which was a lie. This endpoint
    # caches the real value into setup.json and returns it. Frontend
    # polls this on a 60s interval.
    @app.get("/api/live-balance")
    async def live_balance(refresh: bool = False):
        s = load_setup_state()
        if not s.wallet_address:
            return JSONResponse({
                "usdc": None, "bnb": None, "ts": 0,
                "error": "no_wallet", "address": "",
            })
        if refresh or s.live_balance_ts == 0 or (int(time.time()) - s.live_balance_ts) > 60:
            result = poll_live_balance()
            if result.get("usdc") is not None or result.get("bnb") is not None:
                set_live_balance(result.get("usdc"), result.get("bnb"))
            return JSONResponse(result)
        return JSONResponse({
            "usdc": s.usdc_balance, "bnb": s.bnb_balance,
            "ts": s.live_balance_ts, "address": s.wallet_address,
            "error": None, "cached": True,
        })

    @app.post("/api/setup/config", dependencies=[Depends(_auth.require_admin)])
    async def setup_config(body: dict):
        try:
            cfg = set_runtime_config(
                mode=str(body.get("mode", "testnet")),
                chain_id=int(body.get("chain_id", 97)),
                rpcs=list(body.get("rpcs", []) or []),
                cmc_api_key=str(body.get("cmc_api_key", "")),
                cmc_x402_base=body.get("cmc_x402_base"),
            )
            # v2.1.8 (P6): saving config changes mode/chain/RPCs in
            # local.yaml, but the running agent has its config frozen
            # in memory from boot. Signal restart_required so the
            # frontend can auto-fire /api/agent/restart and the
            # operator doesn't have to remember to click Restart Agent
            # after every wizard save.
            return JSONResponse({"ok": True, "config": cfg, "restart_required": True})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/wallet", dependencies=[Depends(_auth.require_admin)])
    async def setup_wallet(body: dict):
        """Generate a new wallet. Returns ONLY the address; the private key
        is encrypted to disk and never leaves the host process."""
        password = body.get("password", "")
        try:
            r = generate_wallet(password)
            return JSONResponse({"ok": True, **r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/wallet/import", dependencies=[Depends(_auth.require_admin)])
    async def setup_wallet_import(body: dict):
        """Import an existing private key. Returns ONLY the address; the key
        is encrypted to disk and never leaves the host process.

        Like /api/wallet/export-mnemonic, this is gated by
        BNBAGENT_ALLOW_WALLET_IMPORT (default OFF in production). A
        judge who somehow gets the admin password should NOT be able
        to swap the operator's wallet for their own (which would let
        them drain the funds, register a fake identity, etc.).

        v2.1.8: optional `save_password_to_env: true` body field. When
        set, the typed password is also written to .env as TWAK_PWD
        so the next `bash bnbagent` invocation can auto-decrypt the
        keystore. Without this, every restart boots the agent with
        an ephemeral key and trades can't be signed for the
        operator's real wallet.
        """
        if os.environ.get("BNBAGENT_ALLOW_WALLET_IMPORT", "").lower() not in (
            "1", "true", "yes", "on",
        ):
            raise HTTPException(
                403,
                "wallet import is disabled. Set BNBAGENT_ALLOW_WALLET_IMPORT=true "
                "in the server env and restart to enable (operator-only operation).",
            )
        pk = body.get("private_key", "")
        password = body.get("password", "")
        save_password_to_env = bool(body.get("save_password_to_env", False))
        try:
            r = import_wallet(pk, password)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        # Persist the password to .env (gitignored) so the next agent
        # boot can decrypt the keystore. Strictly opt-in: the operator
        # must tick "Save password to .env" in the wizard for this to
        # fire. We also update os.environ so the running dashboard
        # process picks it up immediately (the agent loop is a
        # separate process and still needs a restart).
        if save_password_to_env and password:
            try:
                _set_env_var_in_dotenv("TWAK_PWD", password)
                os.environ["TWAK_PWD"] = password
            except Exception as e:
                return JSONResponse(
                    {"ok": False,
                     "error": f"keystore imported, but failed to save "
                              f"password to .env: {e}",
                     **r},
                    status_code=500,
                )
        return JSONResponse({"ok": True, **r})

    @app.post("/api/setup/sign", dependencies=[Depends(_auth.require_admin)])
    async def setup_sign(body: dict):
        """Sign the current policy.yaml with the unlocked wallet."""
        password = body.get("password", "")
        try:
            r = sign_current_policy(password)
            return JSONResponse({"ok": True, **r})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/setup/reset", dependencies=[Depends(_auth.require_admin)])
    async def setup_reset(body: dict | None = Body(default=None)):
        # v2.1.8 (P7): wallet is preserved by default. Pass
        # {"include_wallet": true} to also wipe ~/.twak/wallet.json
        # (wallet rotation / hand-off / true factory-reset).
        include_wallet = bool((body or {}).get("include_wallet", False))
        r = reset_setup(include_wallet=include_wallet)
        return JSONResponse({"ok": True, **r})

    # v2.1.8 (A): trigger an agent restart from the dashboard. Writes the
    # restart marker to ~/.bnbagent/control.json; the running agent's
    # heartbeat picks it up on its next tick (≤1s), gracefully shuts
    # down with exit code 75, and the `bnbagent` bash wrapper re-execs.
    # Use this after changing config in the wizard (mode, RPCs, etc.)
    # so the live agent picks up the new settings without the operator
    # having to Ctrl+C and re-launch from the terminal.
    @app.post("/api/agent/restart", dependencies=[Depends(_auth.require_admin)])
    async def agent_restart(body: dict | None = Body(default=None)):
        from core.control import request_restart, read_control
        reason = (body or {}).get("reason", "") or "dashboard restart button"
        request_restart(reason=reason)
        r = read_control().get("restart", {})
        return JSONResponse({
            "ok": True,
            "reason": r.get("reason"),
            "requested_at": r.get("requested_at"),
        })

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
        # Try in-memory config first; fall back to reading the merged
        # (shipped + local) view from disk so the endpoint works even
        # when no agent is booted (which is the common case for the
        # test suite). v2.1.1: uses the local.yaml shadow pattern.
        cfg = s.get("config") or _load_merged_config()
        ds_cfg = cfg.get("data_source", {}) or {}
        # The Base address for x402 is the agent wallet's EVM address
        # (BSC and Base share the same secp256k1 address format). core/boot.py
        # writes it to data_source.base_address on every boot.
        base_address = ds_cfg.get("base_address", "")
        if router is not None:
            base_rpcs = ds_cfg.get("base_rpcs", [])
            # v2.1.8 (P4): `router` may be either a live DataSourceRouter
            # (in-process tests) or a dict snapshot {tier, status} the
            # agent published into the IPC file (cross-process). Handle
            # both shapes via the small _component_attr helper.
            router_tier = _component_attr(router, "tier", default="unknown")
            router_status = _component_attr(router, "status", default={})
            # Prefer the live source's status (x402 carries base_rpcs there);
            # fall back to config.
            try:
                src_status = router.source.status or {}
            except Exception:
                src_status = router_status if isinstance(router_status, dict) else {}
            if "base_rpcs" in src_status:
                base_rpcs = src_status["base_rpcs"]
            return JSONResponse({
                "tier": router_tier,
                "status": router_status,
                "base_rpcs": base_rpcs,
                "base_address": base_address,
            })
        # No live router — return whatever the config file says
        tier = ds_cfg.get("tier", "mock")
        return JSONResponse({
            "tier": tier,
            "status": {"tier": tier, "note": "no agent running"},
            "base_rpcs": ds_cfg.get("base_rpcs", []),
            "base_address": base_address,
        })

    @app.post("/api/data-source/select", dependencies=[Depends(_auth.require_admin)])
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

        # v2.1.1: read the merged view (shipped + local) so existing
        # local overrides (cmc_api_key, base_rpcs, base_address set
        # by previous wizard runs) are visible to the prereq check.
        cfg = _load_merged_config()
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
        # v2.1.1: write to local.yaml (the user-state shadow), not
        # the shipped config.yaml. atomic-ish via .tmp + rename inside
        # the helper.
        _write_local(cfg)

        # Hot-swap the live router if the agent is running. v2.1.8 (E):
        # the cross-process hot-swap is unreliable (the IPC snapshot
        # stringifies the live router to a dict, so router.set_source()
        # fails silently). When the agent is in a separate process
        # (the common case here), the only reliable way to pick up the
        # new tier is to restart the agent so it re-loads local.yaml
        # and re-constructs its DataSourceRouter from scratch. Mirror
        # the /api/setup/config restart_required contract.
        s = _state()
        router = s.get("components", {}).get("data_source") or s.get("data_source")
        restart_required = False
        if router is not None and hasattr(router, "set_source"):
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
                restart_required = True
        else:
            # The IPC snapshot is a dict, not a live router — the only
            # way to apply the new tier is to restart the agent loop.
            restart_required = True
        if restart_required:
            try:
                from core.control import request_restart
                request_restart(reason="data_source tier change")
            except Exception as e:
                log.warning("data_source: failed to signal agent restart: %s", e)
        return JSONResponse({
            "ok": True,
            "tier": tier,
            "restart_required": restart_required,
            "note": "agent will restart within ~5s to pick up the new tier" if restart_required else None,
        })

    @app.post("/api/data-source/cmc-key", dependencies=[Depends(_auth.require_admin)])
    async def data_source_cmc_key(body: dict):
        """Persist the CMC Pro API key in local.yaml (user-state shadow)."""
        api_key = (body.get("api_key") or "").strip()
        if not api_key:
            return JSONResponse({"ok": False, "error": "api_key required"},
                                status_code=400)
        # v2.1.1: read merged (so we don't drop other local overrides),
        # mutate, write back to local.yaml.
        cfg = _load_merged_config()
        cfg.setdefault("data_source", {})["cmc_api_key"] = api_key
        _write_local(cfg)
        return JSONResponse({"ok": True})

    @app.post("/api/data-source/base-rpcs", dependencies=[Depends(_auth.require_admin)])
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
        # v2.1.1: read merged, mutate, write to local.yaml.
        cfg = _load_merged_config()
        cfg.setdefault("data_source", {})["base_rpcs"] = list(rpcs)
        _write_local(cfg)
        return JSONResponse({"ok": True, "base_rpcs": list(rpcs)})

    @app.get("/api/data-source/x402-balance")
    async def get_x402_balance(address: str | None = None):
        """Poll the Base USDC balance of `address` (or the configured one).

        Optional query param ?address=0x... overrides the config-stored
        address. Returns {address, balance_usdc, ready}. The wizard
        enables the Continue button when balance_usdc >= 0.50.
        """
        from connectors.x402 import check_balance
        # v2.1.1: use the merged view (shipped + local) so the user
        # can override base_rpcs in local.yaml without editing the
        # shipped config.
        cfg = _load_merged_config()
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

    @app.post("/api/wallet/export-mnemonic", dependencies=[Depends(_auth.require_admin)])
    async def post_export_mnemonic(payload: dict):
        """Return the TWAK mnemonic if the correct password is provided.

        **DISABLED BY DEFAULT in production.** Even with an admin cookie,
        this route refuses to dump the seed phrase unless the operator
        explicitly opts in via BNBAGENT_ALLOW_WALLET_EXPORT=true in the
        environment. This is a defense-in-depth measure: if a judge
        (or anyone else) ever learns the admin password, they still
        can't exfiltrate the operator's private key. To recover the
        mnemonic, the operator must:
          1. SSH into the host, AND
          2. Set BNBAGENT_ALLOW_WALLET_EXPORT=true in .env / Coolify, AND
          3. Restart the service, AND
          4. Re-authenticate as admin, AND
          5. Provide the wallet password.
        That's 5 factors; no single admin-password leak can drain
        the wallet.

        One-time per request — the password is not retained, the mnemonic
        is not logged. The keystore is the same AES-256-GCM blob that
        /api/setup/wallet wrote; we decrypt it briefly, return the phrase,
        and discard the key.
        """
        if os.environ.get("BNBAGENT_ALLOW_WALLET_EXPORT", "").lower() not in (
            "1", "true", "yes", "on",
        ):
            # 403 + a clear operational hint. The route is intentionally
            # registered (so a stale link/UI doesn't 404 mysteriously)
            # but the body explains what to do.
            raise HTTPException(
                403,
                "mnemonic export is disabled. Set BNBAGENT_ALLOW_WALLET_EXPORT=true "
                "in the server env and restart to enable (operator-only operation).",
            )
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
        """Return a live ChatAgent for the dashboard process.

        The previous implementation read chat_agent from the IPC
        snapshot, but json.dumps(..., default=str) stringifies
        Python objects, so the dashboard saw a string instead of
        the live ChatAgent. The endpoint then crashed with
        AttributeError("'str' object has no attribute 'chat'").

        Fix: lazy-instantiate a ChatAgent in the dashboard process
        using the dashboard's own LLMRouter (which reads the same
        providers.yaml + .env as the agent loop). The chat persona
        .md file is shipped with the repo, so the agent has the
        same identity and tool spec as the agent loop's instance.

        When the operator changes LLM routing from the Config
        pane, the next chat call picks up the new routing because
        the router is created fresh per call (the underlying
        LLMRouter also caches clients by provider name but is
        cheap to re-construct). If the agent_loop is running and
        populates the IPC with a live object, prefer that one to
        keep the in-process state and the agent loop's view in
        lockstep.
        """
        from agents.chat import ChatAgent
        from agents.providers import LLMRouter
        # Prefer the in-process agent if the agent_loop is also
        # running in this process (test contexts + dev mode).
        s = _state()
        live = s.get("components", {}).get("chat_agent")
        if live is not None and hasattr(live, "chat"):
            return live
        # Dashboard-process fallback. The components dict here is
        # minimal — just enough to satisfy ChatAgent._system_state_block.
        # The dashboard process never makes trades, so portfolio is a
        # stub with the methods returning neutral values.
        try:
            router = LLMRouter()
        except Exception as e:
            log.warning("dashboard: LLMRouter init failed: %s", e)
            return None
        # Build a minimal components dict from the IPC snapshot.
        # Portfolio is stubbed: equity() returns the value from
        # /api/stats if available, positions returns []. Policy is
        # loaded from disk so evaluator_address is correct.
        from pathlib import Path
        import yaml as _yaml
        policy_path = Path("config/policy.yaml")
        policy = {}
        if policy_path.exists():
            try:
                policy = _yaml.safe_load(policy_path.read_text()) or {}
            except Exception:
                policy = {}
        stats = s.get("stats", {}) or {}
        class _PortfolioStub:
            def equity(self): return float(stats.get("equity", 0) or 0)
            def day_pnl_pct(self): return float(stats.get("day_pnl_pct", 0) or 0)
            def drawdown_pct(self): return float(stats.get("drawdown_pct", 0) or 0)
            positions = []
        # v2.1.8: wire a real DataSourceRouter so the chat agent's
        # get_market_snapshot tool can answer "how is the market?"
        # with live quotes. Without this, the chat is blind to the
        # data the trading agent is using every tick.
        #
        # Force tier=binance for the chat regardless of the trading
        # agent's tier. Reasons:
        #  1. The x402 path (paid in USDC on Base) can return 402
        #     even when the wallet is funded (the x402 payment
        #     protocol is finicky and the chat is the wrong place
        #     to debug it). The trading agent has its own retry
        #     logic; the chat just needs an answer.
        #  2. Binance is free, no auth, and returns 200 every time.
        #  3. The chat's job is "show me prices" — it doesn't need
        #     the sponsor-track signal. The BNB HACK judge can still
        #     see the trading agent's x402 usage in logs/agent.log.
        # The trading agent in core.main still uses the configured
        # tier (x402 when funded) for its actual decisions.
        data_source = None
        try:
            from connectors.data_source import DataSourceRouter
            from connectors.binance import BinanceClient
            data_source = DataSourceRouter(BinanceClient())
        except Exception as e:
            log.warning("dashboard: DataSourceRouter init failed for chat: %s", e)
        minimal_components = {
            "portfolio": _PortfolioStub(),
            "policy": policy,
            "data_source": data_source,
        }
        try:
            return ChatAgent(
                components=minimal_components,
                router=router,
                persona_name="chat",
            )
        except Exception as e:
            log.warning("dashboard: ChatAgent init failed: %s", e)
            return None

    def _advisor():
        s = _state()
        return s.get("components", {}).get("advisor")

    def _reviewers():
        s = _state()
        return s.get("components", {}).get("reviewers", {})

    @app.post("/api/chat", dependencies=[Depends(_auth.require_judge)])
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

    @app.post("/api/chat/tool", dependencies=[Depends(_auth.require_judge)])
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
        # v2.1.8 (UX4): cross-process — `adv` may be a dict snapshot
        # (no .recent() method). Try the call; fall back to the IPC
        # field `advisor_decisions` if the agent published it.
        if adv is None:
            return JSONResponse({"decisions": []})
        try:
            decisions = adv.recent(limit)
        except (AttributeError, TypeError):
            decisions = (_state().get("advisor_decisions") or [])[-limit:]
        return JSONResponse({"decisions": decisions})

    @app.get("/api/agent/reviewer")
    async def agent_reviewer(limit: int = 50, sleeve: str | None = None):
        revs = _reviewers()
        out = []
        # v2.1.8 (UX4): cross-process — `revs` values may be dicts
        # (no .recent() method). Try each; fall back to the IPC field
        # `reviewer_decisions` (a flat list keyed by sleeve).
        if sleeve:
            r = revs.get(sleeve) if isinstance(revs, dict) else None
            if r is not None:
                try:
                    out = r.recent(limit)
                except (AttributeError, TypeError):
                    out = [d for d in (_state().get("reviewer_decisions") or [])
                           if d.get("sleeve") == sleeve][-limit:]
        else:
            if isinstance(revs, dict):
                for r in revs.values():
                    try:
                        out.extend(r.recent(limit))
                    except (AttributeError, TypeError):
                        pass
            if not out:
                out = (_state().get("reviewer_decisions") or [])[-limit:]
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

    @app.post("/api/agent/personas/{name}", dependencies=[Depends(_auth.require_admin)])
    async def agent_persona_save(name: str, body: dict):
        loader = PersonaLoader(name)
        body_text = body.get("body", "")
        version = str(body.get("version", "1.0.0"))
        try:
            p = loader.save_user(body_text, version=version)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "sha256": p.sha256, "diverged": p.diverged})

    @app.post("/api/agent/personas/{name}/reset", dependencies=[Depends(_auth.require_admin)])
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
        # v2.1.8 (UX4): router may be the live LLMRouter object (in-proc
        # tests) OR a {status: {...}} dict (cross-process — P4
        # serialization). Use _component_attr to read either shape.
        status = _component_attr(router, "status", default={})
        return JSONResponse(status or {"providers": {}, "agents": {}})

    @app.post("/api/llm/config", dependencies=[Depends(_auth.require_admin)])
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

    # v2.1.3: LLM API key UI. The keys come from env vars (referenced by
    # $VAR substitution in agents/providers.yaml). The dashboard lets the
    # user set + verify them via .env (gitignored). The in-process router
    # has the env vars cached from boot, so a key change requires an
    # agent restart — but the user can "Test" the key (reads .env directly,
    # not os.environ) to confirm it's correct before restarting.
    LLM_PROVIDER_ENV_VARS = {
        "anthropic":  "ANTHROPIC_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "oai_compat": "OAI_KEY",
        "minimax":    "MINIMAX_API_KEY",  # MiniMax via api.minimaxi.chat (OAI-compat)
        # local: no key needed
    }

    @app.post("/api/llm/key", dependencies=[Depends(_auth.require_admin)])
    async def llm_key_set(body: dict):
        provider = (body.get("provider") or "").strip()
        key = (body.get("key") or "").strip()
        # The shipped providers.yaml lists 5 providers; "local" has no
        # key, so we accept it but refuse the key field if present.
        all_providers = set(LLM_PROVIDER_ENV_VARS) | {"local"}
        if provider not in all_providers:
            return JSONResponse(
                {"ok": False, "error": f"unknown provider: {provider}. choose from {sorted(all_providers)}"},
                status_code=400,
            )
        if provider == "local":
            return JSONResponse({
                "ok": False, "error": "local provider has no key; it's a local-LLM base URL only",
            }, status_code=400)
        if not key:
            return JSONResponse({"ok": False, "error": "key required"}, status_code=400)
        env_var = LLM_PROVIDER_ENV_VARS[provider]
        try:
            _set_env_var_in_dotenv(env_var, key)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"failed to write .env: {e}"}, status_code=500)
        return JSONResponse({
            "ok": True,
            "provider": provider,
            "env_var": env_var,
            "restart_required": True,
            "note": f"Saved to .env ({env_var}). Restart the agent (Ctrl+C then 'bash bnbagent') to apply.",
        })

    @app.post("/api/llm/test", dependencies=[Depends(_auth.require_admin)])
    async def llm_key_test(body: dict):
        provider = (body.get("provider") or "").strip()
        all_providers = set(LLM_PROVIDER_ENV_VARS) | {"local"}
        if provider not in all_providers:
            return JSONResponse(
                {"ok": False, "error": f"unknown provider: {provider}"},
                status_code=400,
            )
        if provider == "local":
            return JSONResponse({
                "ok": True, "provider": provider, "status": "n/a",
                "note": "local provider has no key",
            })
        env_var = LLM_PROVIDER_ENV_VARS[provider]
        try:
            key = _get_env_var_from_dotenv(env_var) or ""
        except Exception as e:
            return JSONResponse({"ok": True, "provider": provider, "status": "error", "note": f"read failed: {e}"})
        if not key:
            return JSONResponse({
                "ok": True, "provider": provider, "status": "missing",
                "note": f"{env_var} is not set in .env",
            })
        # Tiny test call to the provider's auth endpoint.
        import httpx
        try:
            if provider == "openrouter":
                r = httpx.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
            elif provider == "anthropic":
                # 401 = bad key, 400 with "credit" / "billing" = valid key but no quota.
                # We treat 401 as invalid, anything else as a pass on the auth check.
                r = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "claude-3-5-haiku-latest",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    timeout=10,
                )
                if r.status_code == 401:
                    return JSONResponse({
                        "ok": True, "provider": provider, "status": "invalid",
                        "note": "401 from anthropic — key is wrong or revoked",
                    })
            elif provider == "openai":
                r = httpx.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
            elif provider == "minimax":
                # v2.1.5+: MiniMax (Anthropic competitor; OpenAI-compatible
                # chat-completions endpoint at api.minimaxi.chat). The
                # base in providers.yaml is bare (no /v1); the chat client
                # appends /v1/chat/completions, so /v1/models is the
                # matching auth probe.
                r = httpx.get(
                    "https://api.minimaxi.chat/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
            elif provider == "oai_compat":
                base = _get_env_var_from_dotenv("OAI_BASE") or ""
                if not base:
                    return JSONResponse({
                        "ok": True, "provider": provider, "status": "missing-base",
                        "note": "OAI_BASE is not set in .env",
                    })
                r = httpx.get(
                    f"{base.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
            else:
                return JSONResponse({
                    "ok": True, "provider": provider, "status": "unknown",
                    "note": f"no test path for {provider}",
                })
            r.raise_for_status()
            return JSONResponse({
                "ok": True, "provider": provider, "status": "valid",
                "note": "key verified",
            })
        except httpx.HTTPStatusError as e:
            return JSONResponse({
                "ok": True, "provider": provider, "status": "invalid",
                "note": f"{e.response.status_code} from provider: {e.response.text[:120]}",
            })
        except Exception as e:
            return JSONResponse({
                "ok": True, "provider": provider, "status": "error",
                "note": f"verification failed: {e}",
            })

    # ---------------------------------------------------------- per-agent routing

    # The four agents the wizard + Config pane let the operator route
    # independently. Keep in sync with agents.providers.LLMRouter /
    # providers.yaml (the YAML hardcodes these four).
    _LLM_AGENT_NAMES = ("advisor", "reviewer", "chat", "token_module")

    @app.get("/api/llm/routing")
    async def llm_routing_get():
        """Current per-agent {provider, model} as resolved by the
        LLMRouter (env-var overrides win over providers.yaml). The
        wizard's model selector and the Config pane's routing table
        both read this to show what's active."""
        from agents.providers import LLMRouter
        try:
            router = LLMRouter()
        except Exception as e:
            return JSONResponse({"error": f"router init failed: {e}"}, status_code=500)
        out = {}
        for name in _LLM_AGENT_NAMES:
            r = router.for_agent(name)
            out[name] = {
                "provider": r.provider_name,
                "model":    r.model,
                "enabled":  r.enabled,
                "reason":   r.reason,
            }
        return JSONResponse(out)

    @app.post("/api/llm/routing", dependencies=[Depends(_auth.require_admin)])
    async def llm_routing_set(body: dict):
        """Persist a per-agent provider+model override to .env.

        Body: { agent: 'advisor', provider: 'minimax', model: 'MiniMax-Mini' }
        Writes LLM_<AGENT>_PROVIDER + LLM_<AGENT>_MODEL to .env.
        Replaces existing lines (no duplicates). Strict input
        validation: unknown agent or provider → 400.
        """
        agent = (body.get("agent") or "").strip().lower()
        provider = (body.get("provider") or "").strip().lower()
        model = (body.get("model") or "").strip()
        if agent not in _LLM_AGENT_NAMES:
            return JSONResponse(
                {"ok": False, "error": f"unknown agent: {agent!r}; "
                                       f"must be one of {list(_LLM_AGENT_NAMES)}"},
                status_code=400,
            )
        if provider not in LLM_PROVIDER_ENV_VARS and provider != "local":
            return JSONResponse(
                {"ok": False, "error": f"unknown provider: {provider!r}; "
                                       f"must be one of {list(LLM_PROVIDER_ENV_VARS) + ['local']}"},
                status_code=400,
            )
        if not model:
            return JSONResponse(
                {"ok": False, "error": "model is required"},
                status_code=400,
            )
        env_prefix = f"LLM_{agent.upper()}"
        try:
            _set_env_var_in_dotenv(f"{env_prefix}_PROVIDER", provider)
            _set_env_var_in_dotenv(f"{env_prefix}_MODEL", model)
            # Also update os.environ so the running dashboard process
            # sees the change immediately. The agent loop is a separate
            # process and still needs a restart to pick up the new
            # routing in its LLMRouter.
            os.environ[f"{env_prefix}_PROVIDER"] = provider
            os.environ[f"{env_prefix}_MODEL"] = model
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"failed to write .env: {e}"},
                status_code=500,
            )
        return JSONResponse({
            "ok": True,
            "agent": agent,
            "provider": provider,
            "model": model,
            "note": f"Saved to .env ({env_prefix}_PROVIDER + {env_prefix}_MODEL). "
                    f"Restart the agent (Ctrl+C the terminal running `bash bnbagent`, "
                    f"then re-run) to apply.",
        })

    @app.get("/api/llm/models")
    async def llm_models_get(provider: str = ""):
        """List available model ids for a provider, by hitting the
        provider's /v1/models endpoint (OpenAI-compatible shape).

        Returns a list of model id strings. The wizard dropdown uses
        this to populate the model selector without hardcoding.

        For providers without an OpenAI-compatible /v1/models
        (anthropic), returns a small hardcoded list of well-known
        models — the operator can still type any model id manually.
        """
        provider = (provider or "").strip().lower()
        if provider not in LLM_PROVIDER_ENV_VARS and provider != "local":
            return JSONResponse(
                {"ok": False, "error": f"unknown provider: {provider!r}"},
                status_code=400,
            )
        # Hardcoded lists for providers whose /v1/models endpoint
        # either doesn't exist (anthropic) or we don't want to
        # require a key just to enumerate.
        HARDCODED = {
            "anthropic": [
                "claude-3-5-haiku-latest",
                "claude-3-5-sonnet-latest",
                "claude-opus-4-8",
                "claude-sonnet-4-6",
            ],
        }
        if provider in HARDCODED:
            return JSONResponse({"provider": provider, "models": HARDCODED[provider],
                                 "source": "hardcoded"})
        if provider == "local":
            return JSONResponse({"provider": provider, "models": ["local"],
                                 "source": "hardcoded"})
        # OpenAI-compatible providers — try /v1/models with the key
        # already in .env. If no key set, return a generic placeholder
        # so the dropdown isn't empty.
        env_var = LLM_PROVIDER_ENV_VARS[provider]
        key = _get_env_var_from_dotenv(env_var) or ""
        # Base URLs per provider (must NOT include /v1)
        BASES = {
            "openai":     "https://api.openai.com",
            "openrouter": "https://openrouter.ai/api",
            "oai_compat": _get_env_var_from_dotenv("OAI_BASE") or "",
            "minimax":    "https://api.minimaxi.chat",
        }
        base = BASES[provider]
        if not base:
            return JSONResponse(
                {"ok": False, "error": f"OAI_BASE not set for {provider}"},
                status_code=400,
            )
        if not key:
            return JSONResponse({
                "provider": provider, "models": [],
                "source": "needs-key",
                "note": f"set {env_var} in .env to enumerate models",
            })
        import httpx
        try:
            r = httpx.get(
                f"{base.rstrip('/')}/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            ids = sorted({m.get("id") for m in (data.get("data") or []) if m.get("id")})
            return JSONResponse({"provider": provider, "models": ids, "source": "upstream"})
        except Exception as e:
            return JSONResponse(
                {"ok": False, "provider": provider, "error": f"upstream call failed: {e}"},
                status_code=502,
            )

    # ---------------------------------------------------------- tokens (stub)
    @app.get("/api/tokens/config")
    async def tokens_config_get():
        s = _state()
        tm = s.get("components", {}).get("token_module")
        if tm is None:
            return JSONResponse({"error": "TokenModule not loaded"}, status_code=503)
        return JSONResponse(tm.config)

    @app.post("/api/tokens/deploy", dependencies=[Depends(_auth.require_admin)])
    async def tokens_deploy(body: dict):
        s = _state()
        tm = s.get("components", {}).get("token_module")
        if tm is None:
            return JSONResponse({"error": "TokenModule not loaded"}, status_code=503)
        # Hard date lock + env opt-in. The TokenModule.create_token()
        # call below also enforces this, but we surface a friendlier
        # JSON 423 (Locked) here so the dashboard can show a clear
        # "disabled until 2026-07-07" message instead of a generic 400.
        from agents.token_module import TokenModule as _TM
        unlocked, reason = _TM.is_deploy_unlocked()
        if not unlocked:
            return JSONResponse(
                {"error": "token_deploy_locked", "message": reason},
                status_code=423,
            )
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

    @app.post("/api/skills/{name}/enable", dependencies=[Depends(_auth.require_admin)])
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

    @app.post("/api/skills/{name}/disable", dependencies=[Depends(_auth.require_admin)])
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
