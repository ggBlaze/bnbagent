# BNB Agent — Operations

This is the live-rail manual for the dashboard. The dashboard runs on
`http://localhost:8000` when you start the system with `bash bnbagent`.

## Pane reference

The dashboard has 7 panes: **Setup**, **Live**, **Chat** (Layer 3),
**Tokens**, **Config**, **Logs**, **Replay**.

| Pane | Source(s) | Refresh | Purpose |
|---|---|---|---|
| **Setup** | `/api/setup/*` | — | First-time wizard (Network → Wallet → Sign Policy → Ready) |
| **Live** | `/api/stats`, `/api/equity-series` | 1.5s | Hero strip, SVG equity chart, sleeve cards, ledgers, identity, jobs, trades |
| **Chat** | `/api/chat`, `/api/agent/advisor`, `/api/agent/reviewer` | per message | Talk to the LLM; persona modal; recent decisions table |
| **Tokens** | `/api/tokens/*` | — | Token Module config + deploy button + result card |
| **Config** | `/api/control`, `/api/llm/status` | on change | Sleeve toggles, risk overrides, LLM provider config |
| **Logs** | `/api/logs/stream` (SSE) | live | Live agent log; color-coded by level |
| **Replay** | — | — | Pointer to `bash bnbagent --replay` |

All updates are non-blocking; the UI never freezes waiting for the agent.

## Right rail (always visible)

- System status (mode, chain, address, wallet, last update)
- Sleeve toggles
- Control log (advisor + dashboard + skills edits)
- **Kill switch** (red button; also reachable from the Setup wizard)

## Config pane

Edits here write a **dashboard intent** to `~/.bnbagent/control.json`. The
agent's heartbeat (`core.tick.Agent._heartbeat`) reads that file once per
second and applies:

- **Sleeve toggles** — flip A/B/C on or off. Sleeve B + C respect the post-loss
  cooldown so flipping a sleeve on does not trigger a wave of stale signals.
- **Risk overrides** — `daily_loss_circuit_breaker_pct`, `per_trade_risk_pct`,
  `max_gross_leverage`, `max_single_position_pct`. Values are validated to
  match the policy schema bounds (0.1–20 / 0.1–5 / 1–5 / 1–50).

The agent never re-signs the policy on a dashboard edit — the on-disk
`policy.yaml` is still the source of truth. Dashboard edits are a **runtime
override** that is logged in `/api/control-log` and visible in the right rail.

### Data source card (v2.1.0)

The Config pane now includes a **Data source** card showing the
currently-selected market data source. It is sourced from
`/api/data-source` and refreshes every 5s.

```
┌─ Data source ──────────────────────────────────────┐
│  Active: x402 on Base                              │
│  Daily spend: $0.04 / $10.00                       │
│  Last call: 3 calls ago (POST /v1/quotes/latest)   │
│  Base USDC balance: 4.21 USDC                      │
│                                                    │
│  [ Change data source ]                            │
└────────────────────────────────────────────────────┘
```

Clicking **Change data source** opens a modal with the same 3-way radio
from the Setup wizard (CMC Pro / x402 on Base / Binance). The selection
persists to `config/config.yaml` → `data_source.kind`; the agent picks
it up on the next heartbeat. To switch to a Pro API key, paste the key
in the modal — it's stored as `data_source.cmc_api_key` and exported as
`CMC_API_KEY` at boot.

## Data source banner (Live pane, v2.1.0)

A persistent banner across the top of the **Live** pane shows the active
data source and its health. It is also sourced from `/api/data-source`.

```
┌──────────────────────────────────────────────────────────────────┐
│  Data source: x402 on Base   |   3 calls/min   |   $0.04 / $10   │
└──────────────────────────────────────────────────────────────────┘
```

The banner's color reflects health: acid-lime (healthy), amber (≥ 80% of
daily cap), red (degraded — fallback in use). When the active source is
degraded and a fallback is in use, the banner shows the fallback name in
parentheses (e.g. "x402 on Base (fallback: Binance)").

## Chat pane (Layer 3)

- Send a message in the input box; the LLM streams a response.
- 9 tools the chat can call: `get_pnl_summary`, `list_recent_trades`,
  `list_open_positions`, `recommend_risk_change`, `create_token`,
  `list_skills`, `enable_skill`, `disable_skill`, `sign_new_policy`.
- **Critical:** the chat can RECOMMEND a policy change but cannot APPLY
  it. `recommend_risk_change` returns a UI prompt to the Setup wizard.
  The user must re-sign the policy with their wallet password.
- **Persona controls** at the bottom of the pane: "view persona" (modal
  with the current .md + edit), "reset to pro" (restores from
  `agents/_pro_defaults/`), "diverged" badge if the user has changed
  the pro default.
- **Recent decisions** collapsible: shows the last 5 advisor decisions
  and the last 5 reviewer decisions (per sleeve).

## Tokens pane

- Form: network (testnet/mainnet), protocol (erc20_minimal/bep20/openzeppelin),
  default supply, default decimals, "create website" checkbox, website
  theme textarea.
- "Deploy now" button → on mainnet, a `<dialog>` requires the user to
  type the token name in full.
- Result card shows: contract address, tx hash, deployer, supply, decimals,
  network, IPFS CID, "View on BscScan" link, and (if create_website) a
  "Download website.html" button.

## Logs pane

Live SSE stream of `logs/agent.log`. Capped at 500 lines in the viewport.
Color-coded by log level: info, warn, error.

## Kill switch

A red **Engage Kill** button in the right rail sets `policy._kill_switch=true`.
The risk engine (`core.risk.circuit_breaker_check`) refuses every new order
with `"kill switch engaged"`. Existing positions are NOT force-closed — the
sleeve monitors still tick and will TP / stop on their own.

Press the same button (now labeled `◼ Kill Active · Resume`) to clear the
flag. The agent resumes on the next heartbeat.

## Health

```bash
curl http://localhost:8000/api/healthz
# {"status":"ok","ts":1717593600,"agent_updated_at":1717593601,"kill_switch":false}
```

Use this in your uptime monitor. The `agent_updated_at` is the timestamp of
the last agent heartbeat; if it stops advancing, the agent loop is wedged.

## Where the LLM writes appear in the control log

The control log (`/api/control-log`) shows the source of every edit:

- `_source: "dashboard"` — from a user click in the Config pane
- `_source: "advisor"` — from the Layer 1 StrategyAdvisor (5-min loop)
- `_source: "skill:cmc_global_filter"` — from the cmc_global_filter Skill

You can always tell who made the change. To disable the advisor from
ever touching a particular key, just set the key in your policy lower
than the advisor will request — the `_apply` filter rejects anything
that isn't a tightening.

## LLM provider changes

LLM providers are configured in `agents/providers.yaml`. The
Config pane shows the current status (`/api/llm/status`) and can write
new per-agent routing (`/api/llm/config`). Restart the agent for
provider changes to take effect.

## MCP server

If you start the agent, the MCP server is a separate process:

```bash
bash scripts/mcp_serve.sh        # stdio (Claude Code / Goose)
bash scripts/mcp_serve_sse.sh   # SSE on port 8765
```

The MCP server reads the agent's in-memory `DASHBOARD_STATE` via Python
imports. In production you'd run them as sibling processes sharing a bus
(or in the same process). For the contest demo, start the agent first
(`bash bnbagent`), then start the MCP server in another terminal.
