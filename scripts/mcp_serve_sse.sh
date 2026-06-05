#!/usr/bin/env bash
# BNB Agent — MCP server (SSE transport, port 8765)
# For remote agents that connect over HTTP/SSE.
set -e
cd "$(dirname "$0")/.."
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f /tmp/venv/bin/activate ]]; then
  source /tmp/venv/bin/activate
fi
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
HOST="${BNBAGENT_MCP_HOST:-0.0.0.0}"
PORT="${BNBAGENT_MCP_PORT:-8765}"
exec python -m agent_mcp.mcp_server --transport sse --host "$HOST" --port "$PORT"
