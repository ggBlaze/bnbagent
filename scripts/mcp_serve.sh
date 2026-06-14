#!/usr/bin/env bash
# BNB Agent — MCP server (stdio transport)
# For local Claude Code / Goose / Cursor integration.
#
# Works on Ubuntu 24+/26+ and any system with `python3` on PATH.
# The venv (if it exists) takes precedence so pip-installed deps
# are picked up; otherwise we fall back to the system `python3`.
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
# Prefer the venv's python (if active) over the system python3.
# Both .venv/ created by `uv venv --seed` or `python3 -m venv` have
# a `python3` symlink, so this works whether the venv is activated
# or not (PYTHONPATH points at the project root for in-tree imports).
PY="$(command -v python3 || command -v python)"
if [[ -z "$PY" ]]; then
  echo "[mcp_serve] no python3 or python on PATH. Install Python 3.10+ or run 'bash install.sh' first." >&2
  exit 1
fi
exec "$PY" -m agent_mcp.mcp_server --transport stdio
