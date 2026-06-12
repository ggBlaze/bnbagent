# 🤖 BNB Agent

> **An autonomous AI trading team on BNB Smart Chain. Funding carry + DEX momentum + mean-reversion, signed by you once, run by the agent for a week.**
>
> **Built for the [BNB HACK 2026](https://coinmarketcap.com/api/hackathon/) (CoinMarketCap × Trust Wallet × BNB Chain — $36K prize pool).**
>
> **By Blaze · MIT License**

[![CoinMarketCap](https://img.shields.io/badge/CoinMarketCap-Agent%20Hub-yellow)](https://coinmarketcap.com/api/agent-hub/)
[![Trust Wallet](https://img.shields.io/badge/Trust%20Wallet-TWAK-purple)](https://developer.trustwallet.com/)
[![BNB Chain](https://img.shields.io/badge/BNB%20Chain-AI%20Agent%20SDK-orange)](https://www.bnbchain.org/en/solutions/ai-agent)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-275%2F275%20passing-brightgreen)](tests/)
[![CI](https://img.shields.io/badge/CI-enforced-blueviolet)](.github/workflows/ci.yml)

---

BNB Agent is an **autonomous BSC trading agent** plus a **3-layer LLM agent team** (advisor / reviewer / chat) plus a **Token Module** (deploy + landing-page generator) plus a **Skills registry** plus an **MCP server** exposing all of the above to other agents.

It runs three strategies in parallel on BNB Smart Chain, pays for its own market data with USDC via x402, signs its own transactions with Trust Wallet's Agent Kit (TWAK), registers its own identity NFT via ERC-8004, and escrows its own PnL deliverables via ERC-8183 jobs — all gated by a single user-signed policy.

You sign **once**. The agent runs for a week. You can kill it with one button.

---

## Table of contents

1. [Quick start](#1-quick-start)
2. [What it does](#2-what-it-does)
3. [Architecture](#3-architecture)
4. [The 3-LLM agent team](#4-the-3-llm-agent-team)
5. [Sponsor integration](#5-sponsor-integration)
6. [Token Module](#6-token-module)
7. [Skills registry](#7-skills-registry)
8. [MCP server](#8-mcp-server-other-agents-can-call-ours)
9. [Risk engine](#9-risk-engine)
10. [Dashboard](#10-dashboard)
11. [Configuration](#11-configuration)
12. [Environment variables](#12-environment-variables)
13. [Repository layout](#13-repository-layout)
14. [Documentation](#14-documentation)
15. [Testing](#15-testing)
16. [Security model](#16-security-model)
17. [Deployment](#17-deployment)
18. [Contributing](#18-contributing)
19. [License](#19-license)

---

## 1. Quick start

```bash
git clone <your-private-repo> bnbagent && cd bnbagent
bash install.sh                       # one command — venv, deps, signed dev policy
export OPENROUTER_API_KEY=sk-or-...   # any one of 5 supported providers (optional)
bash bnbagent                         # agent + dashboard on http://localhost:8000
```

That's it. The dashboard auto-loads; first-time users land in the **Setup wizard** (Network → Wallet → Sign Policy → **Data source** → Ready). After the wizard, the dashboard switches to the **Live** pane. The Setup wizard has a new 'Data source' step (CMC Pro / x402 / Binance).

| Command | What it does |
|---|---|
| `bash bnbagent` | start the agent + dashboard, Ctrl+C stops both |
| `bash bnbagent --replay` | run a 7-day synthetic replay; HTML report at `data/reports/replay.html` |
| `bash bnbagent --repl` | open a Python REPL with `p = boot(...)` pre-loaded |
| `bash scripts/mcp_serve.sh` | **opt-in** — run the MCP server (stdio, for Claude Code / Goose) as a separate process |
| `bash scripts/mcp_serve_sse.sh` | **opt-in** — run the MCP server (SSE, port 8765) for remote agents |

> Requires **Python 3.10+** and (optionally) **Node 18+** for the TWAK CLI fallback. The dashboard works offline; the only required network access is to BSC RPCs and (if LLM is enabled) to your LLM provider.

---

## 2. What it does

### Strategy — three sleeves composed for the PnL-replay judging axes

| Sleeve | Capital | Strategy | Expected PnL | Drawdown |
|---|---|---|---|---|
| **A — Funding carry** | 70% | Long spot on PancakeSwap v3 + short perp on a BSC venue. Delta-neutral. Collects funding every 8h. | +0.5% APR baseline | very low |
| **B — DEX momentum** | 20% | CMC signals (volume spike + 4h breakout) → 1–4h long with ATR stop and 3% TP. Quarter-Kelly sizing. | positive alpha | capped at 1%/trade |
| **C — Mean reversion** | 10% | Fades 1h drops >2.5σ on top-20 BSC tokens. 2% stop, 1% target. | positive alpha | capped at 0.5%/trade |

70% of capital is **hedged** (delta-neutral carry), with a 5% daily circuit breaker + 1% per-trade cap + 2× leverage cap. The agent is *designed* for low drawdown — see `data/reports/replay_{bull,bear,chop}.json` for the honest backtest (max DD < 2% in all three synthetic regimes).

### Hard risk controls (the only UX prompt the user sees)

- **Daily loss circuit breaker**: 3%
- **Per-trade risk cap**: 1%
- **Max gross leverage**: 2×
- **Max single position**: 15%
- **Curated token allowlist** (top-50 CMC + vetted BSC DEX list)
- **Per-symbol post-loss cool-off** (4h for momentum, 6h for mean-rev) to prevent revenge trades
- **Signed User Policy** — `policy.yaml` is the *only* file the user signs (EIP-191). Every order is checked against it via `circuit_breaker_check()` before going on-chain.
- **Dashboard kill switch** — halts all new orders, leaves open positions to TP/stop themselves

### Audited

A 10-item trading-logic audit pass before the live window, all fixes applied. See [`docs/audit-2026-06-05.md`](docs/audit-2026-06-05.md).

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER  signs policy.yaml ONCE  (EIP-191)                                    │
└────────────────────────┬─────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────────────┐
│  BNB AGENT  (Python, asyncio)                                                │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────────────┐   │
│  │ Sleeve A    │  │ Sleeve B    │  │ Sleeve C    │  │  AI Agent Team     │   │
│  │ 70% carry   │  │ 20% momentum│  │ 10% meanrev │  │  (3 LLM layers)    │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────┬──────────┘   │
│         └─────────────────┼──────────────────┘             │              │
│                           ▼                                │              │
│         ┌──────────────────────────────────────┐   ┌───────▼────────┐      │
│         │      core/risk.py                    │   │  Advisor       │      │
│         │      circuit_breaker_check()        │   │  (5-min loop,  │      │
│         │   + Agent.review_trade()  L2        │   │   can only     │      │
│         └──────────────┬───────────────────────┘   │   TIGHTEN)     │      │
│                        │                           ├────────────────┤      │
│         ┌──────────────▼───────────────────────┐   │  Reviewer      │      │
│         │       core/portfolio.py             │   │  (per-trade,   │      │
│         │       equity, peak, PnL, DD         │   │   can only     │      │
│         └──────────────┬───────────────────────┘   │   VETO)        │      │
│                        │                           ├────────────────┤      │
│                        │   ┌──────────────────┐    │  Chat          │      │
│                        │   │  Token Module    │◄───┤  (conversational│    │
│                        │   │  + Token Website │    │   + 9 tools)   │      │
│                        │   └──────────────────┘    └────────────────┘      │
│                        │                           ┌────────────────┐      │
│                        │                           │  Skills Reg.   │      │
│                        │                           │  (6 built-ins) │      │
│                        │                           └────────────────┘      │
└────┬───────────────────┬───────────────────┬──────────────────────────────┘
     │                   │                   │
┌────▼─────────┐  ┌──────▼──────┐  ┌─────────▼────────┐  ┌──────────────────┐
│ CoinMarketCap│  │  Trust      │  │  BNB AI Agent    │  │  Agent MCP        │
│  Agent Hub   │  │  Wallet     │  │  SDK (bnbagent-  │  │  Server           │
│  Data API +  │  │  Agent Kit  │  │  sdk)            │  │  (stdio + SSE)    │
│  x402 ($0.01)│  │  (TWAK)     │  │  BSC + PCS v3    │  │  10 MCP tools     │
│  + Skills    │  │  AES-256-   │  │  + perps         │  │  for other agents │
│              │  │  GCM        │  │  + ERC-8004      │  │  (Claude Code,   │
│              │  │  + PBKDF2   │  │  + ERC-8183      │  │  Goose, Cursor)  │
└──────────────┘  └─────────────┘  └──────────────────┘  └──────────────────┘
```

The **3 sponsor layers** are deeply integrated:

| Layer | Sponsor | What BNB Agent uses it for | Visible evidence |
|---|---|---|---|
| L1 | **CoinMarketCap Agent Hub** | Data API + Data MCP + Skills + **x402** ($0.01 USDC/request via EIP-3009 `transferWithAuthorization`) | Live **x402 microcharge ledger** on the dashboard, every cost line, BscScan-deep-linked |
| L2 | **Trust Wallet Agent Kit (TWAK)** | Self-custody local signing, AES-256-GCM keystore, PBKDF2 200k iters, at `~/.twak/wallet.json` | Live **TWAK-signed tx list** with BscScan deep links; "keys never left the host" |
| L3 | **BNB AI Agent SDK** | BSC mainnet, PancakeSwap v3, BSC perps, **ERC-8004 identity NFT**, **ERC-8183 job escrow**, x402 finality <200ms | Live **identity panel** (tokenId + IPFS metadata + 8004scan link) and **jobs lifecycle** (Open → Funded → Submitted → Completed) |

---

## 4. The 3-LLM agent team

BNB Agent runs an LLM in three distinct roles. Each is **strictly constrained** so the LLM is pure additive value on top of the deterministic trading engine.

| Layer | Role | Loop | Can do | Cannot do |
|---|---|---|---|---|
| **1 — Advisor** | Recommends **TIGHTENING** risk changes based on recent state | every 5 min | Lower risk caps, disable sleeves, **all** in `core.control.write_control` (same audit path as dashboard) | Loosen any cap, raise per-trade risk, enable a disabled sleeve |
| **2 — Reviewer** | Vetoes a trade before it goes on-chain | per-trade, 0.5s timeout | Veto, falls back to heuristic in <1ms | Raise risk, override circuit-breaker, bypass the policy |
| **3 — Chat** | Conversational interface for the operator | on user input | Read state, dispatch 9 tools, **recommend** policy changes | Apply any policy change — user must re-sign in Setup wizard |

Hard safety envelope (all enforced in code, **never** delegated to the LLM):

- The advisor's `_apply()` compares `new < old` for every numeric cap. Even a hostile LLM cannot raise a cap.
- The reviewer's `_heuristic_veto` always runs after the LLM and checks: sleeve drawdown > 50% of cap, win-rate < 20%, post-loss cooldown active.
- The chat's `recommend_risk_change` returns a recommendation and a UI prompt to the Setup wizard — it **never** writes to the policy or the control file.

### Provider-agnostic

`agents/providers.yaml` (5 adapters, no third-party SDKs):

```yaml
default: openrouter
providers:
  anthropic:  { base: https://api.anthropic.com,  key: $ANTHROPIC_API_KEY }
  openai:     { base: https://api.openai.com,     key: $OPENAI_API_KEY }
  openrouter: { base: https://openrouter.ai/api,  key: $OPENROUTER_API_KEY }
  oai_compat: { base: $OAI_BASE,                  key: $OAI_KEY }
  local:      { base: $LOCAL_LLM_BASE,             key: "" }   # llama.cpp / ollama

agents:
  advisor:        { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 512,  temperature: 0.1 }
  reviewer:       { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 256,  temperature: 0.0 }
  chat:           { provider: openrouter, model: anthropic/claude-3.5-sonnet, max_tokens: 2048, temperature: 0.4 }
  token_module:   { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 8000, temperature: 0.7 }
```

All 4 agents can use **different providers**. The agent still runs as a deterministic bot if no provider is configured.

### Personas

4 personas in markdown under `agents/_pro_defaults/`:

- `advisor.md` — strict tightening-only persona
- `reviewer.md` — strict veto-in-doubt persona
- `chat.md` — operator-facing persona with the 9-tool list
- `token_module.md` — token deploy + website generation

Each persona is **editable** from the dashboard (Chat pane → "view persona" → edit → "save") and **resettable** to the pro default ("reset to pro"). The dashboard shows a "diverged" warning when the user-edited copy drifts from the pro default.

Full details: [`docs/agents.md`](docs/agents.md).

---

## 5. Sponsor integration

### L1 — CoinMarketCap Agent Hub (Data API + Data MCP + Skills + x402)

Every market-data call (quotes, OHLCV, listings, info) goes through x402. The agent **pays for its own data** with USDC stablecoins, no API key required, $0.01/request. The flow:

```
agent → GET /v1/quotes/latest → 402 Payment Required
agent → EIP-3009 sign USDC transferWithAuthorization
agent → X-PAYMENT header (base64) → retry → 200 OK
```

Settlement is on **Base (chain 8453)** via native USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` with the x402 exact-EVM scheme. The retry header is `PAYMENT-SIGNATURE`. The dashboard shows the full microcharge ledger (`/api/cmc-charges`) with BaseScan-deep-linkable tx hashes. Daily spend is capped by `policy.fees.x402_max_usdc_per_day` (default $10).

### L2 — Trust Wallet Agent Kit (TWAK)

Every BSC transaction is signed locally with TWAK. The keystore at `~/.twak/wallet.json` is encrypted with AES-256-GCM (12-byte IV, 16-byte auth tag) and a key derived from the password with PBKDF2-HMAC-SHA256 (200k iterations). The **Setup wizard** in the dashboard creates/imports the wallet, encrypts it, and signs the policy with it.

The private key **never** leaves the host process. The dashboard never has it. The browser never sees it.

### L3 — BNB AI Agent SDK

The agent uses the `bnbagent-sdk` (or its connector equivalents):

- `BSCClient` — connection-pooled, RPC-rotating BSC client
- `PancakeV3` — swap + quote helpers
- `Perps` — multi-venue funding/OI/position adapter
- `ERC8004` — identity NFT registration
- `ERC8183` — job escrow lifecycle

The agent mints its own **ERC-8004 identity NFT** at startup (pinned to IPFS) and opens **ERC-8183 jobs** per evaluation window — one per sleeve + an aggregator. The user is the evaluator; they sign `complete()` at window end. USDC releases to the agent on-chain.

Full details: [`docs/onchain.md`](docs/onchain.md), [`docs/x402.md`](docs/x402.md), [`docs/architecture.md`](docs/architecture.md).

---

## 6. Token Module

A first-class dashboard tab (not a Skill). Deploys ERC-20 / BEP-20 / OpenZeppelin tokens on BSC, with optional single-file HTML landing-page generation.

```
Chat: "create a token called Mooncoin with symbol MOON and supply 1B"
  → TokenModule.create_token(name, symbol, supply, ...)
  → x402-pays CMC for token metadata (logged to microcharge ledger)
  → TWAK-signs the contract-creation tx
  → BNB SDK broadcasts; rcpt.contract_address is the new token's address
  → metadata pinned to IPFS
  → if create_website=true → LLM generates a single-file HTML page
    (sanitized against eval/document.write, no external resources)
  → returns { contract_address, tx_hash, explorer_url, website_html }
```

**Mainnet deploy** requires the user to type the token name in a confirmation dialog in the dashboard. The MCP tool also requires `confirm_mainnet: true`. Both are belt-and-suspenders.

Full details: [`docs/TOKEN_MODULE.md`](docs/TOKEN_MODULE.md).

---

## 7. Skills registry

6 built-in Skills, discoverable, hot-toggled:

| Skill | Category | Effect |
|---|---|---|
| `telegram_alert` | notification | DM on every trade close (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) |
| `farcaster_post` | notification | Auto-post PnL updates (`WARPCAST_KEY`, rate-limited 1/h) |
| `webhook_dispatch` | notification | POST every event to a user-configured URL (`WEBHOOK_URL`) |
| `x_sentiment` | data | Sentiment score for top BSC tokens (X API primary, CMC trending fallback) |
| `cmc_global_filter` | data | The only Skill that writes — pauses all sleeves on bear regime |
| `glassnode_onchain` | data | Stub for the contest; deterministic score for the UI |

State persisted to `~/.bnbagent/skills.json`. The chat can `enable_skill(name)` / `disable_skill(name)` directly. The dashboard's Skills tab lists all + their enabled state.

Full details: [`docs/SKILLS.md`](docs/SKILLS.md).

---

## 8. MCP server (other agents can call ours) — **opt-in**

> **Heads up:** the MCP server is **not** started by `bash bnbagent`. It's
> an **opt-in** service for when you want other agents to call into the
> BNB Agent. Run it as a separate process. This is intentional — the
> default `bnbagent` run is a self-contained agent + dashboard, with
> nothing listening for outside MCP clients.

`agent_mcp/mcp_server.py` exposes the BNB Agent as **10 MCP tools** over stdio or SSE:

| Tool | What it does |
|---|---|
| `bnbagent_get_pnl` | Live portfolio stats |
| `bnbagent_list_positions` | Open positions across all sleeves |
| `bnbagent_list_trades(n)` | Recent closed trades |
| `bnbagent_get_policy` | Current signed policy summary |
| `bnbagent_recommend_risk_change` | Recommendation only — never writes |
| `bnbagent_deploy_token` | TokenModule deploy (mainnet requires `confirm_mainnet=true` + `confirm_symbol` match) |
| `bnbagent_chat` | Talk to the LLM in natural language |
| `bnbagent_list_skills` | List all Skills + enabled state |
| `bnbagent_enable_skill` / `bnbagent_disable_skill` | Toggle Skills |

**Run the server (separate from `bnbagent`):**

```bash
# stdio — for Claude Code / Goose / Cursor (configure in their mcp_servers.json)
bash scripts/mcp_serve.sh

# SSE — for remote agents, port 8765 by default
bash scripts/mcp_serve_sse.sh
```

**Integrate with any MCP client** — the server speaks stdio, so any
MCP-compatible client can drive the BNB Agent. Example configs:

**Claude Code** (`~/.claude/mcp_servers.json`):

```json
{
  "mcpServers": {
    "bnbagent": {
      "command": "bash",
      "args": ["/home/style/bnbagent/scripts/mcp_serve.sh"],
      "env": { "PYTHONPATH": "/home/style/bnbagent" }
    }
  }
}
```

**Goose / Cursor / Continue / any other MCP client** — same shape: a
stdio command that runs `scripts/mcp_serve.sh` (or directly
`python -m agent_mcp.mcp_server --transport stdio`). Check your client's
docs for the exact config-file location and JSON schema; the keys
are always `mcpServers.<name>.command` + `args` + `env`.

Restart the client, then it will discover the 10 bnbagent tools
(`bnbagent_get_pnl`, `bnbagent_deploy_token`, `bnbagent_chat`, etc).
The integration test (`tests/integration/test_mcp.py`) spawns the
server and calls every tool end-to-end.

Full details: [`docs/MCP.md`](docs/MCP.md).

---

## 9. Risk engine

The risk engine is the **only enforcement of the signed User Policy**. Every order is gated by `circuit_breaker_check()` in [`core/risk.py`](core/risk.py).

| Check | Refuses if | Default |
|---|---|---|
| 0. Cooldown | `now < day_breach_active_until` | 60min after a daily breach |
| 1. Daily loss | `(day_start - equity) / day_start ≥ daily_loss_circuit_breaker_pct` | 3% |
| 2. Max drawdown | `(peak - equity) / peak ≥ max_drawdown_pct` | 8% |
| 3. Per-trade risk | `risk / equity > per_trade_risk_pct` | 1% |
| 4. Single position | `notional / equity > max_single_position_pct` | 15% |
| 5. Gross leverage | `gross / equity > max_gross_leverage` | 2× |
| 6. Allowlist | `symbol not in policy.allowlist.bsc_tokens` | top-50 CMC + BSC DEX |
| 7. Sleeve cap | `notional / equity > sleeves[X].max_position_pct` | per-sleeve |
| 8. Sleeve enabled | `sleeves[X].enabled == false` | — |
| 9. **Kill switch** (added in v2.0) | `policy._kill_switch == true` | dashboard button |

**Layer 2 (per-trade reviewer)** runs *after* the circuit breaker and *before* `sign_transaction`. Hard-coded timeout (0.5s) → heuristic-only fallback.

Full details: [`docs/agents.md`](docs/agents.md), [`docs/audit-2026-06-05.md`](docs/audit-2026-06-05.md).

---

## 10. Dashboard

Single-page app (vanilla HTML/CSS/JS, no build step, no framework, ~1800 lines) at `http://localhost:8000`. Mission-control aesthetic — Inter for display, JetBrains Mono for numerics, hand-drawn SVG equity chart with gradient fill, animated live-status pulse, acid-lime accent on near-black.

| Pane | Source | Refresh | Purpose |
|---|---|---|---|
| **Setup** | `setup` files | — | First-time user wizard (Network → Wallet → Sign Policy → Data source → Ready) |
| **Live** | `/api/stats`, `/api/equity-series` | 1.5s | Hero strip (equity, PnL, DD, Sharpe), SVG equity chart, sleeve cards, x402 ledger, TWAK txs, identity, jobs, trades |
| **Chat** | `/api/chat`, `/api/agent/advisor`, `/api/agent/reviewer` | per message | Talk to the LLM; persona modal; recent decisions table |
| **Tokens** | `/api/tokens/*` | — | Token Module config form + deploy button + result card with explorer link + website download |
| **Config** | `/api/control`, `/api/llm/status` | on change | Sleeve toggles, risk overrides, LLM provider config |
| **Logs** | `/api/logs/stream` (SSE) | live | Live agent log; color-coded by level |
| **Replay** | — | — | Pointer to `bash bnbagent --replay` |

**Right rail (always visible):**

- System status (mode, chain, address, wallet, last update)
- Sleeve toggles
- Control log (advisor + dashboard edits)
- **Kill switch** (red button, also in the Setup wizard)

Full details: [`docs/operations.md`](docs/operations.md).

---

## 11. Configuration

Two config files, both externalized so the agent can be configured without code changes:

| File | What | Who edits |
|---|---|---|
| `config/config.yaml` | Network, RPCs, gas, token registry, DEX/perps endpoints, CMC, tick intervals | operator (or via the Setup wizard) |
| `config/policy.yaml` | The signed User Policy (EIP-191) | operator (or via the Setup wizard) |

A third file, `agents/providers.yaml`, configures the LLM provider routing. The Setup wizard writes a default; the LLM Status page in the dashboard lets you change it.

A fourth file, `agents/token_module.yaml`, configures the Token Module (default protocol, default supply, default network, website theme).

A fifth file, `agents/mcp.yaml` (auto-generated if missing), configures the MCP server transport.

---

## 12. Environment variables

See [`.env.example`](.env.example) for the full list. All optional.

| Var | Default | Notes |
|---|---|---|
| `BNBAGENT_DASHBOARD_PORT` | `8000` | dashboard HTTP port |
| `BNBAGENT_EQUITY`         | `100`  | starting USDC equity |
| `BNBAGENT_LOG_LEVEL`      | `INFO` | agent log level |
| `BNBAGENT_CONTROL_FILE`   | `~/.bnbagent/control.json` | dashboard → agent IPC |
| `BNBAGENT_MCP_HOST`       | `0.0.0.0` | MCP SSE host |
| `BNBAGENT_MCP_PORT`       | `8765` | MCP SSE port |
| `TWAK_KEYSTORE`           | —      | TWAK JSON keystore path |
| `TWAK_PWD`                | —      | TWAK keystore password |
| `BNBAGENT_PRIVATE_KEY`    | —      | dev-only fallback (do not use in prod) |
| `OPENROUTER_API_KEY`      | —      | easiest LLM provider (covers all 4 agents) |
| `ANTHROPIC_API_KEY`      | —      | direct Anthropic |
| `OPENAI_API_KEY`          | —      | direct OpenAI |
| `OAI_BASE` / `OAI_KEY`    | —      | generic OAI-compatible |
| `LOCAL_LLM_BASE`          | `http://127.0.0.1:8080` | llama.cpp / ollama |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | telegram_alert skill |
| `WARPCAST_KEY`            | —      | farcaster_post skill |
| `WEBHOOK_URL`             | —      | webhook_dispatch skill |
| `CMC_API_KEY`             | —      | optional Pro API key (x402 otherwise) |
| `BASE_RPCS`               | `https://mainnet.base.org,https://base.publicnode.com,https://1rpc.io/base` | comma-separated Base mainnet RPC URLs (x402 funding detection polls all of them in rotation) |

---

## 13. Repository layout

```
bnbagent/
├── README.md                       ← you are here
├── LICENSE                        ← MIT
├── install.sh                     ← one-command installer
├── bnbagent                       ← one-command runner
├── pyproject.toml                 ← Python deps + setuptools packages
├── package.json                   ← Node deps (@trustwallet/cli)
├── .env.example                   ← every env var, all optional
│
├── config/                        ← externalized configs
│   ├── config.yaml                ← network/RPCs/tokens/DEX/perps/CMC/ticks
│   ├── policy.yaml                ← signed User Policy
│   ├── policy.schema.json         ← JSON schema
│   ├── allowlist.yaml             ← curated top-50 CMC + BSC DEX
│   ├── perps_venues.yaml          ← Aster/KiloEx/ApolloX/MUX
│   └── tokens.mainnet.yaml
│
├── core/                          ← the agent loop
│   ├── boot.py                    ← load configs, init wallet, register identity
│   ├── main.py                    ← entry point (3 sleeves + advisor)
│   ├── portfolio.py               ← equity, peak, PnL, DD, drawdown_pct
│   ├── risk.py                    ← circuit_breaker_check()
│   ├── tick.py                    ← TickLoop, Agent, review_trade hook
│   ├── control.py                 ← dashboard → agent IPC
│   ├── setup.py                   ← Setup wizard backend
│   └── utils.py                   ← shared helpers
│
├── connectors/                    ← sponsor adapters
│   ├── cmc.py                     ← CoinMarketCap Data API + x402
│   ├── x402.py                    ← EIP-3009 transferWithAuthorization
│   ├── twak.py                    ← Trust Wallet Agent Kit (signing)
│   ├── bnb_sdk.py                 ← BSC + PCS v3 + Perps + ERC-8004 + ERC-8183
│   ├── ipfs.py                    ← local IPFS pinning
│   └── keystore.py                ← AES-256-GCM + PBKDF2 wallet encryption
│
├── strategies/                    ← the 3 sleeves
│   ├── sleeve_a_carry.py
│   ├── sleeve_b_momentum.py
│   └── sleeve_c_meanrev.py
│
├── agents/                        ← AI Agent Team
│   ├── providers.py               ← LLMClient Protocol + 5 adapters + LLMRouter
│   ├── providers.yaml             ← per-agent provider+model routing
│   ├── base.py                    ← PersonaLoader + llm_complete + llm_stream
│   ├── advisor.py                 ← Layer 1: 5-min tightening loop
│   ├── reviewer.py                ← Layer 2: per-trade veto
│   ├── chat.py                    ← Layer 3: conversational + 9 tools
│   ├── token_module.py            ← TokenModule (deploy + website)
│   ├── _pro_defaults/             ← canonical pro personas (resettable)
│   │   ├── advisor.md
│   │   ├── reviewer.md
│   │   ├── chat.md
│   │   └── token_module.md
│   ├── personas/                  ← live user-editable personas
│   └── prompts/                   ← user-prompt templates
│
├── agent_mcp/                     ← MCP server (stdio + SSE)
│   ├── __init__.py
│   └── mcp_server.py              ← 10 MCP tools
│
├── skills/                        ← discoverable, hot-toggled modules
│   ├── base.py                    ← Skill abstract base
│   ├── registry.py                ← SkillRegistry (discover / enable / disable)
│   ├── notification/              ← telegram_alert, farcaster_post, webhook_dispatch
│   └── data/                      ← x_sentiment, cmc_global_filter, glassnode_onchain
│
├── policy/                        ← EIP-191 sign + verify + version bump
├── identity/                      ← ERC-8004 metadata + registration
├── jobs/                          ← ERC-8183 open / submit / finalize
│
├── dashboard/
│   ├── backend/                   ← FastAPI
│   │   ├── main.py                ← all API endpoints (40+)
│   │   ├── metrics.py
│   │   └── stream.py
│   └── frontend/                  ← single HTML file, ~1800 lines
│       └── index.html
│
├── backtest/                      ← replay harness + metrics
│   ├── fetch_history.py
│   ├── metrics.py
│   └── replay.py
│
├── tests/                         ← 275/275 passing (enforced by CI)
│   ├── unit/                      ← ~13 files
│   ├── integration/               ← 1 file (MCP)
│   └── fixtures/                  ← llm.py, wallets.py, skills.py
│
├── scripts/                       ← the granular launchers
│   ├── first_run.sh               ← 5-command sanity check
│   ├── sign_policy.sh
│   ├── register_agent.sh
│   ├── open_window.sh
│   ├── replay_week.sh
│   ├── start_agent.sh
│   ├── start_dashboard.sh
│   ├── finalize_window.sh
│   ├── mcp_serve.sh               ← MCP stdio launcher
│   └── mcp_serve_sse.sh           ← MCP SSE launcher
│
├── docs/                          ← full documentation
│   ├── agents.md
│   ├── architecture.md
│   ├── audit-2026-06-05.md
│   ├── API.md                     ← every endpoint
│   ├── CHANGELOG.md
│   ├── CONTRIBUTING.md
│   ├── demo-script.md
│   ├── install.md
│   ├── MCP.md
│   ├── onchain.md
│   ├── operations.md
│   ├── PERSONAS.md
│   ├── policy.md
│   ├── SECURITY.md
│   ├── setup-wizard.md
│   ├── SKILLS.md
│   ├── strategy.md
│   ├── submission.md
│   ├── TOKEN_MODULE.md
│   └── x402.md
│
└── infra/                         ← docker, systemd (optional)
```

---

## 14. Documentation

| File | Topic |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Architecture diagram + data flow + key design decisions |
| [`docs/strategy.md`](docs/strategy.md) | The 3 sleeves in plain English, plus the scoring rationale |
| [`docs/onchain.md`](docs/onchain.md) | ERC-8004 identity + ERC-8183 job lifecycle |
| [`docs/x402.md`](docs/x402.md) | x402 pay-per-request protocol, EIP-3009, daily spend cap |
| [`docs/policy.md`](docs/policy.md) | The signed User Policy, how signing works, how verification works |
| [`docs/POLICY_CHANGELOG.md`](docs/POLICY_CHANGELOG.md) | Per-version policy change log — every relaxation of a safety threshold with the rationale, the backtest tape, and the worst-case DD observed. v2.0.8-M1. |
| [`docs/install.md`](docs/install.md) | One-command install + every env var |
| [`docs/operations.md`](docs/operations.md) | Dashboard pane reference + kill switch + control log |
| [`docs/setup-wizard.md`](docs/setup-wizard.md) | The 5-step Setup wizard (Network → Wallet → Sign Policy → Data source → Ready) |
| [`docs/agents.md`](docs/agents.md) | The 3-LLM agent team in depth (advisor / reviewer / chat) |
| [`docs/TOKEN_MODULE.md`](docs/TOKEN_MODULE.md) | Token deploy + landing-page generation + mainnet guard |
| [`docs/SKILLS.md`](docs/SKILLS.md) | Skills registry + 6 built-ins + the cmc_global_filter pause rule |
| [`docs/MCP.md`](docs/MCP.md) | MCP server transport, 10 tools, generic MCP-client integration |
| [`docs/PERSONAS.md`](docs/PERSONAS.md) | Persona format + reset semantics + diverged flag |
| [`docs/API.md`](docs/API.md) | Every HTTP endpoint (40+) with request/response examples |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, signing, key mgmt, MCP exposure, audit trail |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Dev setup, code style, testing, PR process |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | v1.0 → v2.0 history |
| [`docs/audit-2026-06-05.md`](docs/audit-2026-06-05.md) | 10-item trading-logic audit pass + fixes |
| [`docs/submission.md`](docs/submission.md) | Hackathon form fields pre-filled + pre-submission checklist |
| [`docs/demo-script.md`](docs/demo-script.md) | 3-minute demo video script (judge-facing) |
| [`salepitch.md`](salepitch.md) | One-page sales pitch + feature pitch |

---

## 15. Testing

```bash
pytest -q                          # 275/275 passing (~3m cold, ~12s unit-only)
pytest tests/unit/                 # fast unit tests
pytest tests/integration/          # MCP end-to-end
pytest tests/unit/test_risk.py -v # 1 file
```

**Test layout:**

- `tests/unit/` — 13 files, ~140 unit tests
- `tests/integration/` — 1 file (MCP subprocess + tool calls)
- `tests/fixtures/llm.py` — `FakeLLMClient` (records calls, returns scripted responses)
- `tests/fixtures/wallets.py` — shared dev wallets

**Coverage of critical invariants:**

- `test_advisor.py::test_cannot_loosen_with_higher_value` — hostile LLM cannot raise `per_trade_risk_pct` to 5.0
- `test_reviewer.py::test_heuristic_overrides_llm` — LLM says allow, heuristic vetoes on win_rate < 20%
- `test_reviewer.py::test_llm_timeout_falls_back_to_heuristic` — slow LLM doesn't block the sleeve loop
- `test_chat.py::test_recommend_risk_change_does_not_write` — chat's recommend never writes to policy
- `test_token_module.py::test_sanitize_website_strips_eval` — generated HTML can't `eval` / `document.write`
- `test_skill_registry.py::test_enable_missing_env_blocks` — can't enable a Skill without its API keys
- `test_mcp.py::test_deploy_token_mainnet_without_confirm_rejected` — mainnet deploys always require explicit confirmation

---

## 16. Security model

| Concern | Mitigation |
|---|---|
| **Private key on disk** | AES-256-GCM at `~/.twak/wallet.json`, PBKDF2 200k iters, `chmod 600`. Password supplied via `TWAK_PWD` env var. |
| **Private key over the wire** | Never. The dashboard receives only the address. The browser never sees the key. The browser POSTs the password to the dashboard, which decrypts the keystore in-process. |
| **LLM agent team** | Layer 1 can only TIGHTEN. Layer 2 can only VETO. Layer 3 can only RECOMMEND. All three safety envelopes are enforced in code, never delegated to the LLM. |
| **Mainnet token deploy** | Requires `confirm_mainnet: true` in the API. Frontend requires the user to type the token name in a dialog. Pre-flight check: policy signed, wallet set, mode == "mainnet". |
| **x402 spend blow-up** | `fees.x402_max_usdc_per_day` (default $10) caps daily CMC spend. |
| **Mainnet gas blow-up** | `gas.max_gwei: 5` plus `max_gas_price_gwei` in policy. |
| **Perps venue rug** | Daily venue re-selection ranks 4 candidates; auto-switch on OI drop or `withdrawals_paused=True`. |
| **BSC RPC outage** | 3-RPC rotation in `BSCClient`; sleeves hold positions if all fail. |
| **MCP exposure** | The MCP server reads `core.main.DASHBOARD_STATE`; it has no filesystem or network access of its own. Mainnet deploys through MCP require the same `confirm_mainnet` guard as the dashboard. |
| **Skill side effects** | `cmc_global_filter` is the only Skill that writes; its writes are tagged `_source: "skill:cmc_global_filter"` and visible in the Control Log. |
| **Adversarial personas** | Persona files are loaded into the system prompt but the `BaseAgent` machinery is the same regardless. Reset to pro is one click. |
| **Replay safety** | The replay harness runs the strategies against a synthetic tape; no real txs are signed. The `replay` mode in `BSCClient.broadcast` returns deterministic stubs. |

Full details: [`docs/SECURITY.md`](docs/SECURITY.md).

### Security review (v2.0.8)

A focused security review was performed in v2.0.8 covering the wallet
keystore, BSC RPC layer, gas pricing, CMC data dependency, MCP server
surface, and vol-pause logic. The review identified **0 Critical,
4 High, 7 Medium, 6 Low, 4 Info** findings. All 4 Highs + 2 of the
Mediums were fixed in v2.0.8 (H1–H4, M3, M4). The full review is
**private** to the operator and is **not** committed to this public
repo. A summary of what was fixed (and what is deferred to
post-hackathon hardening) is in [`docs/SECURITY.md`](docs/SECURITY.md)
under "v2.0.8 security review".

The hardening commits are:

| Commit  | ID    | What it fixes |
|---------|-------|---------------|
| `1ea07b8` | H-4 | Gas-price cap from policy; refuse stuck-tx window |
| `4973b9d` | H-3 | `resync_nonce` to reconcile local cache from chain |
| `04719cb` | H-2 | Add `pycryptodome>=3.18` to deps + hoist AES imports |
| `a4924ec` | H-1 | Gate `BNBAGENT_PRIVATE_KEY` env var behind explicit opt-in |
| `2366d68` | M-4 | Vol filter fallback above pause threshold |
| `bd9cb93` | M-3 | MCP SSE default bind 127.0.0.1 + optional Bearer auth |

---

## 17. Deployment

### Local development

```bash
bash install.sh && bash bnbagent
```

### Public dashboard (for judges / live replay)

```bash
# On a VPS with a public IP
git clone <repo> bnbagent && cd bnbagent
bash install.sh
export TWAK_KEYSTORE=$HOME/.twak/wallet.json
export TWAK_PWD=...               # use systemd-creds or a secrets manager
bash bnbagent                     # foreground, or use systemd / docker
```

Reverse proxy with TLS (Caddy is easiest):

```
bnbagent.example {
  reverse_proxy 127.0.0.1:8000
  basic_auth {
    judge    JDJhJDE0JDc...      # bcrypt hash
  }
}
```

### Docker (optional, see `infra/`)

```bash
docker compose -f infra/docker-compose.yml up
```

This brings up the agent, the dashboard, a local IPFS node, and a local BSC testnet fork.

### Production checklist (pre-live-replay)

- [ ] Re-signed `config/policy.yaml` with the production evaluator's key
- [ ] Wallet has real BNB (gas) + real USDC (trading)
- [ ] `mode: mainnet` in `config/config.yaml`
- [ ] ERC-8004 identity NFT minted on mainnet
- [ ] At least 1 ERC-8183 job in `Completed` state on mainnet
- [ ] Backtest report `data/reports/replay.html` is committed
- [ ] Dashboard reachable at a public URL with TLS + auth
- [ ] BscScan + 8004scan deep links work

Full details: [`docs/operations.md`](docs/operations.md).

---

## 18. Contributing

PRs welcome. See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) for:

- Dev setup (venv, pre-commit, linting)
- Code style (ruff, type hints, docstrings)
- Test conventions (class-based, replay-tape fixtures, FakeLLMClient)
- PR review process

For the contest: please open issues for any bugs you find in the live
PnL-replay window. Speed matters; we'll triage and patch.

---

## 19. License

**MIT** — see [`LICENSE`](LICENSE). Copyright (c) 2026 Blaze.

```
  __  ___
 /  |/  /  by Blaze · built for the BNB HACK 2026
/ /|_/ /   CoinMarketCap × Trust Wallet × BNB Chain
/_/  /_/   $36K prize pool · live PnL-replay 2026-06-22 → 2026-06-28
```
