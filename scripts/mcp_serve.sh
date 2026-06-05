#!/usr/bin/env bash
# BNB Agent — MCP server (stdio transport)
# For local Claude Code / Goose / Cursor integration.
set -e
cd "$(dirname "$0")/.."
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f /tmp/venv/bin/activate ]]; then
  source /tmp/venv/bin/activate
fi
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
exec python -m agent_mcp.mcp_server --transport stdio
