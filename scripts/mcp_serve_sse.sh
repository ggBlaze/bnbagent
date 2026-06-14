#!/usr/bin/env bash
# BNB Agent — MCP server (SSE transport, port 8765)
# For remote agents that connect over HTTP/SSE.
#
# Works on Ubuntu 24+/26+ and any system with `python3` on PATH.
# Venv takes precedence; otherwise falls back to system python3.
set -e
cd "$(dirname "$0")/.."
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f /tmp/venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source /tmp/venv/bin/activate
fi
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
# v2.1.5: pin str/bytes hash randomization (see bnbagent + commit msg).
export PYTHONHASHSEED=0
PY="$(command -v python3 || command -v python)"
if [[ -z "$PY" ]]; then
  echo "[mcp_serve_sse] no python3 or python on PATH. Install Python 3.10+ or run 'bash install.sh' first." >&2
  exit 1
fi
HOST="${BNBAGENT_MCP_HOST:-0.0.0.0}"
PORT="${BNBAGENT_MCP_PORT:-8765}"
exec "$PY" -m agent_mcp.mcp_server --transport sse --host "$HOST" --port "$PORT"
