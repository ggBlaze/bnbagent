# BNB Agent — MCP Server

The MCP (Model Context Protocol) server exposes the BNB Agent as
**10 tools** that any other MCP-compatible client can call. Claude Code,
Goose, Cursor, and other agentic UIs can drive the BNB Agent as if it
were a function library.

> **Opt-in service.** `bash bnbagent` does **not** start the MCP server.
> The default run is the agent + dashboard, self-contained. To expose
> the agent to other agents, start the MCP server as a separate process
> via `scripts/mcp_serve.sh` (stdio) or `scripts/mcp_serve_sse.sh` (SSE).
> This separation is intentional — a stray MCP client can't accidentally
> poke at a self-contained agent run.

## Tools exposed

| Tool | What it does |
|---|---|
| `bnbagent_get_pnl` | Live portfolio stats (equity, day PnL, drawdown, sleeve exposure) |
| `bnbagent_list_positions` | Open positions across all sleeves |
| `bnbagent_list_trades(n)` | Recent closed trades (default n=20) |
| `bnbagent_get_policy` | Current signed policy summary (no secret) |
| `bnbagent_recommend_risk_change(key, value, reason)` | Recommendation only — returns a UI prompt for the Setup wizard |
| `bnbagent_deploy_token(name, symbol, supply, decimals, network, confirm_mainnet)` | Token Module deploy |
| `bnbagent_chat(message, history)` | Talk to the LLM in natural language (read-only grounded) |
| `bnbagent_list_skills` | List all Skills + their enabled state |
| `bnbagent_enable_skill(name)` | Enable a Skill |
| `bnbagent_disable_skill(name)` | Disable a Skill |

7 of the 10 are **read-only** (`get_pnl`, `list_positions`, `list_trades`,
`get_policy`, `recommend_risk_change`, `list_skills`, `chat`). 3 are
**mutating** (`deploy_token`, `enable_skill`, `disable_skill`). Token
deploy on mainnet still requires `confirm_mainnet: true`.

## Transports

### stdio (for local Claude Code / Goose / Cursor)

```bash
bash scripts/mcp_serve.sh
```

The server speaks JSON-RPC over stdio. The launcher:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate || source /tmp/venv/bin/activate
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
exec python -m agent_mcp.mcp_server --transport stdio
```

### SSE (for remote agents)

```bash
bash scripts/mcp_serve_sse.sh
# → SSE on 0.0.0.0:8765
```

The server mounts a Starlette app with two routes:

- `GET /sse` — opens the SSE stream
- `POST /messages` — receives client messages

Configurable via `BNBAGENT_MCP_HOST` / `BNBAGENT_MCP_PORT`.

## Integration with MCP clients

The server speaks **stdio**, so any stdio-compatible MCP client can
drive the BNB Agent as a tool library. There is no opinionated desktop
app — pick whatever client fits your workflow.

### Claude Code (`~/.claude/mcp_servers.json`)

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

### Goose / Cursor / Continue / other MCP clients

Same shape — a stdio command that runs `scripts/mcp_serve.sh` (or
directly `python -m agent_mcp.mcp_server --transport stdio`). Check
your client's docs for the exact config-file location and JSON schema;
the keys are always `mcpServers.<name>.command` + `args` + `env`.

Once configured, the 10 `bnbagent_*` tools appear in the client
(`bnbagent_get_pnl`, `bnbagent_list_positions`,
`bnbagent_list_trades`, `bnbagent_get_policy`,
`bnbagent_recommend_risk_change`, `bnbagent_deploy_token`,
`bnbagent_chat`, `bnbagent_list_skills`, `bnbagent_enable_skill`,
`bnbagent_disable_skill`). Call them from your client and the agent
will sign any required transactions with TWAK, broadcast via the BNB
SDK, and return on-chain-verifiable results.

## How the server reads agent state

The server reads `core.main.DASHBOARD_STATE` — the same in-memory bus
the FastAPI backend uses. In production, this means the agent and the
MCP server run in the same Python process (or share the bus over a
lightweight IPC). In the contest demo, the MCP server runs as a child
process when the dashboard is started separately.

If `DASHBOARD_STATE` is empty (e.g. MCP server started standalone),
the read-only tools return graceful "not loaded" errors. The `chat` tool
returns "chat agent not loaded". The token deploy tool returns
"TokenModule not loaded". The user can still use the server to
introspect schema, then start the agent separately.

## Security

The MCP server:

- has **no filesystem access of its own** — it reads from `DASHBOARD_STATE`
- has **no network access of its own** — it calls the same in-process
  methods the dashboard uses (which use the same `httpx` clients)
- **mainnet deploys still require `confirm_mainnet: true`** — same as
  the dashboard API
- **the chat tool can recommend but never apply** — same as the dashboard
  Chat pane

**Do not expose the SSE port to the public internet** without
authenticating the MCP session. Add a reverse proxy + basic auth in
production:

```nginx
location /bnbagent-mcp/ {
  auth_basic "BNB Agent";
  auth_basic_user_file /etc/nginx/.htpasswd;
  proxy_pass http://127.0.0.1:8765/;
}
```

## Tests

`tests/integration/test_mcp.py` — 5 tests, spawning the server as a
subprocess and calling every tool via the official `mcp` client:

- `test_list_tools_returns_10_tools` — all 10 tools advertised
- `test_get_pnl_returns_error_when_no_agent` — graceful no-op
- `test_recommend_risk_change_does_not_write` — recommendation only
- `test_deploy_token_mainnet_without_confirm_rejected` — mainnet guard
- `test_unknown_tool_returns_error`

## Programmatic usage (without an MCP client)

You can also call the server's tools from any Python program by
importing the server's `_build_server()` and using the `call_tool`
handler directly. Useful for tests and batch jobs.

```python
from agent_mcp.mcp_server import _build_server
server = _build_server()
# call_tool is a coroutine; for unit tests you can directly call the
# inner functions (e.g. _portfolio_stats)
```
