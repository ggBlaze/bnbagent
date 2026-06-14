# BNB Agent — HTTP API Reference

The FastAPI backend exposes **40+ endpoints**. All return JSON unless
noted. The dashboard calls all of them; the MCP server routes through a
subset. Everything is unauthenticated by default — front with a reverse
proxy + basic auth in production (see [`SECURITY.md`](SECURITY.md)).

Base URL: `http://localhost:8000` (configurable via `BNBAGENT_DASHBOARD_PORT`).

---

## Pages

| Method | Path | Returns |
|---|---|---|
| GET | `/` | HTML dashboard (single file) |
| GET | `/favicon.ico` | 204 |
| GET | `/static/*` | static assets (vendor js) |

---

## Live state

| Method | Path | Returns |
|---|---|---|
| GET | `/api/healthz` | `{status, ts, agent_updated_at, kill_switch}` |
| GET | `/api/stats` | `{equity, starting, peak, drawdown_pct, day_pnl_pct, open_positions, closed_trades, gross_exposure, sleeve_exposure, sleeves}` |
| GET | `/api/positions` | `{sleeve: notional_usdc}` |
| GET | `/api/trades` | closed trades (most recent first) |
| GET | `/api/equity-series` | `{series: [{ts, equity}]}` |
| GET | `/api/sleeves` | per-sleeve tick counts + last tick ts |
| GET | `/api/cmc-charges` | x402 microcharge ledger |
| GET | `/api/txs` | TWAK-signed transactions (parsed from agent.log) |
| GET | `/api/policy` | current policy (excluding `signature` secret) |
| GET | `/api/identity` | ERC-8004 identity `{token_id, cid, agent_address, 8004scan_url}` |
| GET | `/api/jobs` | ERC-8183 jobs |
| GET | `/api/control-log` | recent advisor + dashboard control edits |

Example: `curl http://localhost:8000/api/stats`

```json
{
  "equity": 100.42, "starting": 100.0, "peak": 100.50,
  "drawdown_pct": 0.08, "day_pnl_pct": 0.42, "open_positions": 2,
  "closed_trades": 5, "gross_exposure": 50.21,
  "sleeve_exposure": {"A": 30.10, "B": 15.07, "C": 5.04},
  "sleeves": {"A": {"tick_count": 120, "last_tick_ts": 1717593601, "period_s": 30}, ...}
}
```

---

## Control (dashboard → agent)

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/control` | — | current contents of `~/.bnbagent/control.json` |
| POST | `/api/control` | `{"kill": true, "kill_reason": "..."}` | engage kill switch |
| POST | `/api/control` | `{"resume": true}` | clear kill switch |
| POST | `/api/control` | `{"sleeves": {"B": false}}` | toggle a sleeve |
| POST | `/api/control` | `{"global_risk": {"per_trade_risk_pct": 0.5}}` | override a risk cap (advisor writes via the same path) |

The agent's heartbeat reads this file once per second via `core.control.apply_control`. The right-rail Control Log shows the last 20 edits with their source (`_source` key).

---

## Setup wizard (first-time operator)

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/setup` | — | current operator state (mode, chain, wallet, policy) |
| GET | `/api/setup/checklist` | — | `{complete, missing: [...]}` — drives the wizard stepper |
| POST | `/api/setup/config` | `{mode, chain_id, rpcs, cmc_api_key}` | write `config/config.yaml` |
| POST | `/api/setup/wallet` | `{password}` | create a new wallet, encrypt to `~/.twak/wallet.json` |
| POST | `/api/setup/wallet/import` | `{private_key, password}` | import an existing private key. **v2.1.6: env-gated** — returns 403 unless `BNBAGENT_ALLOW_WALLET_IMPORT=true`. |
| POST | `/api/setup/sign` | `{password}` | sign `config/policy.yaml` with the unlocked wallet |
| POST | `/api/setup/reset` | — | wipe config, policy, wallet, setup state |
| POST | `/api/wallet/export-mnemonic` | `{password}` | return the TWAK mnemonic. **v2.1.6: env-gated** — returns 403 unless `BNBAGENT_ALLOW_WALLET_EXPORT=true`. |
| POST | `/api/tokens/deploy` | `{name, symbol, supply, decimals, network, confirm_mainnet?}` | Token Module deploy. **v2.1.6: contest-locked** — returns 423 (Locked) before 2026-07-07 UTC; after that, requires `BNBAGENT_ALLOW_TOKEN_DEPLOY=true`. |

All write endpoints validate against the policy schema bounds (mode ∈ {testnet, mainnet, replay}, chain_id ∈ {56, 97}, password ≥ 8 chars, etc).

---

## LLM agent team

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/llm/status` | — | which providers are configured, per-agent enabled state |
| POST | `/api/llm/config` | `{advisor: {provider, model, ...}, ...}` | write `agents/providers.yaml` |
| GET | `/api/agent/advisor?limit=20` | — | last N advisor decisions |
| GET | `/api/agent/reviewer?limit=50&sleeve=B` | — | last N reviewer decisions (optionally filtered) |
| GET | `/api/agent/personas` | — | list persona names |
| GET | `/api/agent/personas/{name}` | — | raw persona .md + sha256 + diverged flag |
| POST | `/api/agent/personas/{name}` | `{body, version}` | save edited persona |
| POST | `/api/agent/personas/{name}/reset` | — | restore from `_pro_defaults/` |
| POST | `/api/chat` | `{message, history}` | non-streamed chat |
| POST | `/api/chat/tool` | `{name, args}` | dispatch a tool from the chat |
| GET | `/api/chat/tools` | — | list available tools + LLM status |

Example: `curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d '{"message":"what is my PnL?","history":[]}'`

```json
{"reply": "Your equity is $100.42 (started at $100), up 0.42% today..."}
```

---

## Token Module

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/tokens/config` | — | current TokenModule config |
| POST | `/api/tokens/deploy` | `{name, symbol, supply, decimals?, network?, confirm_mainnet?}` | deploy a token |

**Mainnet deploys** require `confirm_mainnet: true` and `network: "mainnet"`. Without confirm: `400 {"error": "mainnet requires confirm_mainnet=true"}`.

Example: `curl -X POST http://localhost:8000/api/tokens/deploy -H "Content-Type: application/json" -d '{"name":"Mooncoin","symbol":"MOON","supply":1000000,"network":"testnet"}'`

```json
{
  "ok": true,
  "result": {
    "contract_address": "0xabcd...1234",
    "tx_hash": "0x...",
    "deployer": "0x...",
    "name": "Mooncoin", "symbol": "MOON", "decimals": 18, "total_supply": 1000000,
    "ipfs_metadata_cid": "Qm...",
    "explorer_url": "https://testnet.bscscan.com/tx/0x...",
    "website_html": "<!doctype html>...",
    "network": "testnet", "protocol": "erc20_minimal"
  }
}
```

---

## Skills registry

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/skills` | — | list all skills + enabled state |
| POST | `/api/skills/{name}/enable` | — | enable (env vars must be set) |
| POST | `/api/skills/{name}/disable` | — | disable |

Example: `curl http://localhost:8000/api/skills`

```json
{
  "skills": [
    {"name": "telegram_alert", "category": "notification", "enabled": false,
     "ready": false, "missing_env": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"], ...},
    {"name": "x_sentiment", "category": "data", "enabled": true, "ready": true, ...}
  ]
}
```

---

## Logs

| Method | Path | Returns |
|---|---|---|
| GET | `/api/logs?n=200` | last N JSON log lines (one per event) |
| GET | `/api/logs/stream` | SSE — `data: <log line>` events as they're written |

`curl -N http://localhost:8000/api/logs/stream` opens a streaming tail.

---

## WebSocket

`WS /ws` — broadcasts `{stats, ts}` every second. The dashboard's live polling is at 1.5s; the WebSocket is for tighter integrations.

---

## Error responses

All error responses are JSON with `{"error": "..."}` and the appropriate HTTP status:

| Status | When |
|---|---|
| 400 | Bad input (e.g. short symbol, missing mainnet confirm) |
| 404 | Endpoint not found |
| 503 | Component not loaded (e.g. `/api/chat` when no LLM agent has been built) |
| 500 | Unhandled exception (logged with traceback) |

---

## CORS

`allow_origins=["*"]` by default. Tighten this in `dashboard/backend/main.py` for production deployments.
