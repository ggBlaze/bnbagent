# BNB Agent — Install

One command. Idempotent. Safe to re-run.

```bash
git clone <your-private-repo> bnbagent && cd bnbagent
bash install.sh
```

What it does:

1. Creates `.venv/` (Python 3.10+) and installs the local package in editable mode + test extras.
2. Runs `npm install` for `@trustwallet/cli` (used by the keystore CLI path; the agent
   also ships a pure-Python keystore path so the agent runs even without Node).
3. Generates `config/policy.yaml` with an **ephemeral dev key** and signs it
   with EIP-191. In production, re-sign with your real evaluator key:
   `python -m policy.policy_sign --pk 0xYOUR_KEY`.
4. Verifies the policy signature with `python -m policy.policy_verify`.
5. Bootstraps the **personas** (`agents/_pro_defaults/` → `agents/personas/`).
6. Optionally installs the **MCP SDK** (used by `agent_mcp/mcp_server.py`).
7. Prints the next command:

```
Next step — start the agent + dashboard with:
    bash bnbagent
```

## Run

| Command | What it does |
|---|---|
| `bash bnbagent` | start the agent + dashboard on **http://localhost:8000** (single terminal, Ctrl+C stops both) |
| `bash bnbagent --replay` | run a 7-day synthetic replay; report at `data/reports/replay.html` |
| `bash bnbagent --repl` | open a Python REPL with `p = boot(...)` pre-loaded |
| `bash scripts/mcp_serve.sh` | start the MCP server (stdio, for Claude Code / Goose / Cursor) |
| `bash scripts/mcp_serve_sse.sh` | start the MCP server (SSE, port 8765) |

## Production wallet

Set the env var before starting:

```bash
export TWAK_KEYSTORE=$HOME/.twak/wallet.json
export TWAK_PWD=...
python -m policy.policy_sign       # re-sign policy with the TWAK key
bash bnbagent                       # agent signs every tx with TWAK
```

Or, for dev:
```bash
export BNBAGENT_PRIVATE_KEY=0x...   # dev only
bash bnbagent
```

## LLM provider (for the AI agent team)

The 3 LLM layers (advisor / reviewer / chat) and the TokenModule website
generator are all provider-agnostic. Set ONE of:

```bash
# Easiest: one key covers all 4 agent roles
export OPENROUTER_API_KEY=sk-or-...

# Or direct providers
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# Or any OAI-compatible endpoint
export OAI_BASE=https://api.mistral.ai
export OAI_KEY=...
export OAI_MODEL=mistral-large-latest

# Or a local llama.cpp / ollama
export LOCAL_LLM_BASE=http://127.0.0.1:8080
```

Then `bash bnbagent`. The dashboard's LLM status panel (Chat → "view persona")
shows which providers are configured. If no key is set, the agent still
runs as a deterministic bot; the 3 LLM layers log "LLM disabled" and
no-op. See `agents/providers.yaml` for the routing config.

## Notification Skills (optional)

```bash
export TELEGRAM_BOT_TOKEN=...    # for telegram_alert skill
export TELEGRAM_CHAT_ID=...
export WARPCAST_KEY=...         # for farcaster_post skill
export WEBHOOK_URL=https://...   # for webhook_dispatch skill
```

Without these, the corresponding Skills are disabled in the dashboard's
Skills tab and the chat's `enable_skill` call returns an error.

## Environment variables (override config.yaml)

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
| `OPENROUTER_API_KEY`      | —      | covers all 4 agent roles |
| `ANTHROPIC_API_KEY`      | —      | direct Anthropic |
| `OPENAI_API_KEY`          | —      | direct OpenAI |
| `OAI_BASE` / `OAI_KEY`    | —      | generic OAI-compatible |
| `LOCAL_LLM_BASE`          | `http://127.0.0.1:8080` | llama.cpp / ollama |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | telegram_alert skill |
| `WARPCAST_KEY`            | —      | farcaster_post skill |
| `WEBHOOK_URL`             | —      | webhook_dispatch skill |
| `CMC_API_KEY`             | —      | optional Pro API key (x402 otherwise) |

See [`.env.example`](../.env.example) for the full list.
