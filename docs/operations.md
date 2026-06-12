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

## Config file resolution (v2.1.1)

The agent reads the runtime config from a **two-file shadow**:

1. `config/config.yaml` (tracked, immutable at runtime) — the shipped
   defaults. Updated only by `git pull` or a fresh clone.
2. `config/local.yaml` (gitignored) — the user-specific overrides.
   Created on first `bash install.sh` by copying
   `config/local.yaml.example`. Written by the Setup wizard, the
   dashboard data-source endpoints, and `core/boot.py` (the
   `base_address` auto-write).

Read resolution: `core/config_paths.py::load_config()` returns the
deep-merge of both — keys in `local.yaml` override the same keys in
`config.yaml`; lists in `local.yaml` replace lists in `config.yaml`.
Lists do NOT deep-merge (e.g. setting `rpcs:` in `local.yaml`
replaces the entire list, doesn't append).

Write resolution: every runtime write goes to `local.yaml`. The
shipped `config.yaml` is never mutated at runtime. This means:

- The CMC Pro API key is safe from accidental commit (the file
  holding it is gitignored).
- A `git pull` that updates `config.yaml` will not clobber your
  local overrides — the merge happens at read time, not at
  install time.
- The working tree stays clean across wizard interactions; only
  the `config/config.yaml` defaults file is tracked.

To reset to shipped defaults: `rm config/local.yaml` and re-run
`bash install.sh` (which re-bootstraps from the example).

## Repo cleanliness contract (v2.1.2)

The principle: **all the user does with the repo does not affect
the repo development.** Every file the runtime writes at runtime
must be gitignored. Every template the user copies from must be
tracked.

Tracked (shipped defaults + templates — these go in commits):

| Path | What it is |
|---|---|
| `config/config.yaml` | Shipped runtime defaults (testnet, mock tier, etc.) |
| `config/local.yaml.example` | Template for the user-state shadow |
| `config/policy.yaml.example` | Template for the operator-signed policy |
| `config/policy.schema.json` | JSON schema for `policy.yaml` |
| `config/allowlist.yaml` | Token + venue allowlist (signed by policy) |
| `config/perps_venues.yaml` | Perps venue registry |
| `config/tokens.mainnet.yaml` | Token registry |
| `agents/providers.yaml` | LLM provider routing (env-var NAMES only) |
| `agents/personas/{name}.md` | Shipped LLM personas (= pro defaults) |
| `agents/_pro_defaults/{name}.md` | Pro persona source of truth |

Gitignored (user-specific state + build outputs — these must NEVER
land in a commit):

| Path | What writes here |
|---|---|
| `config/local.yaml` | Setup wizard, dashboard data-source endpoints, boot's `base_address` write (v2.1.1) |
| `config/policy.yaml` | `policy_sign --dev`, Setup wizard's "Sign Policy" step (v2.1.2) |
| `agents/token_module.yaml` | Dashboard Token pane via `token_module.update_config()` (v2.1.2) |
| `~/.twak/wallet.json` | TWAK keystore (AES-256-GCM, password-gated) |
| `~/.bnbagent/identity.json` | ERC-8004 token + agent address |
| `~/.bnbagent/setup.json` | Operator summary (read by dashboard) |
| `~/.bnbagent/personas/{name}.md` | Runtime copy of personas (takes precedence over shipped) |
| `~/.bnbagent/skills.json` | Enabled-skills state |
| `data/reports/*.html` | Replay HTMLs (render-only) |
| `data/parquet/`, `data/recordings/` | Local data caches |
| `data/jobs-*.json`, `data/window-*-summary.json` | ERC-8183 job state |
| `.venv/`, `node_modules/`, `dist/`, `build/`, `logs/`, `__pycache__/` | Build/runtime artifacts |
| `.env`, `.env.local` | Env vars (API keys) |

**Contract test:** `tests/unit/test_repo_cleanliness.py` (36 tests)
pins the contract. Adding a new runtime write path to a tracked
file will fail the test on the next CI run.

## Dashboard UI (v2.1.3)

The dashboard has 7 panes: **Setup**, **Live**, **Chat**, **Tokens**,
**Config**, **Logs**, **Replay**. The Config pane is the operator's
control room after the Setup wizard completes.

### LLM API key (Config pane → "LLM API key" section)

The shipped `agents/providers.yaml` references 5 providers via env
var substitution (`$ANTHROPIC_API_KEY`, `$OPENAI_API_KEY`,
`$OPENROUTER_API_KEY`, `$OAI_KEY`, `$LOCAL_LLM_BASE` for the
`local` provider which has no key). To set or change a key:

1. Open the **Config** pane → scroll to "LLM API key".
2. Pick the provider from the dropdown.
3. Paste the key into the masked field.
4. Click **Set** — this writes the env var to `.env` (gitignored,
   atomic-ish `.tmp` + rename).
5. Click **Test** — this reads `.env` directly (not `os.environ`,
   so the result reflects what the NEXT boot will see) and makes a
   tiny auth call to the provider. Status shows:
   - `valid` — provider accepted the key
   - `missing` — env var is not in `.env`
   - `missing-base` — `OAI_KEY` is set but `OAI_BASE` is not (for
     `oai_compat` only)
   - `invalid` — provider rejected the key
6. **Restart the agent** for the change to take effect in-process
   (the LLMRouter has env vars cached from boot):
   - Press Ctrl+C in the terminal where `bash bnbagent` is running
   - Run `bash bnbagent` again
   - The chat banner should turn green and the agent should start
     responding

### Personas (Config pane → "Personas" section)

The 4 personas (`advisor`, `reviewer`, `chat`, `token_module`) are
Markdown files with YAML frontmatter. Each row in the Personas
section shows:

- The persona's name
- Status: `pro default` (green) or `diverged from pro` (amber)
- A sha256 prefix so you can tell if your runtime copy is the
  same as the shipped one
- **View** — opens a new window with the markdown body
- **Edit** — opens a `prompt()` with the body for inline editing
- **Reset to pro** — overwrites your runtime copy at
  `~/.bnbagent/personas/{name}.md` with the pro default

The runtime copy takes precedence over the shipped copy, so editing
the runtime file gives you a "private override" without touching
the tracked repo. The chat persona also has View/Edit/Reset links
in the Chat pane (kept for discoverability).

### Token Module (Tokens pane)

The form has inline fields for everything the deploy needs:

- **Network** (dropdown) — Testnet / Mainnet
- **Protocol** (dropdown) — ERC-20 minimal / BEP-20 / OpenZeppelin
- **Token name** (text) — e.g. "Mooncoin", max 64 chars
- **Symbol** (text) — 3-5 uppercase chars, e.g. "MOON"
- **Total supply** (number) — default 1,000,000,000
- **Decimals** (number) — 0-18, default 18
- **Generate a landing-page website** (checkbox + theme textarea)

A prominent network notice at the top of the form updates in
real-time when the Network dropdown changes:

- 🟢 Green for testnet: "BSC Testnet (chain 97, free, recommended)"
- 🟥 Red for mainnet: "⚠️ BSC MAINNET — real BNB, IRREVERSIBLE"

Mainnet deploys still ask the user to type the symbol to confirm
(the symbol is the canonical on-chain identifier).
