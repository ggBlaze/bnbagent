#!/usr/bin/env bash
# BNB Agent — one-command installer
# -----------------------
#   bash install.sh
#
# Idempotent. Safe to re-run.
#   * creates .venv/ if missing
#   * pip-installs the local package + test extras
#   * npm-installs @trustwallet/cli if Node is on PATH
#   * writes config/policy.yaml stubs if missing
#   * runs `python -m policy.policy_verify` against the default policy
#   * prints the next command
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

say()  { printf "\033[1;36m[install]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[ok]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m[fatal]\033[0m %s\n" "$*"; exit 1; }

# --- 1. Python venv -----------------------------------------------------------
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -d "/tmp/venv" ]]; then
  say "reusing existing venv at /tmp/venv (set BNBAGENT_VENV to override)"
  # shellcheck disable=SC1091
  source /tmp/venv/bin/activate
else
  say "creating .venv (python3)"
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  ok "venv created"
fi
say "pip install -e .[test]"
pip install -q --upgrade pip
pip install -q -e ".[test]" 2>&1 | tail -5 || {
  warn "pip install failed (likely offline); continuing with existing packages"
}

# Optional: MCP SDK for the external-agent server (agent_mcp/)
if /tmp/venv/bin/pip show mcp >/dev/null 2>&1 || python -c "import mcp" 2>/dev/null; then
  ok "MCP SDK present"
else
  warn "MCP SDK not installed (run 'pip install mcp' to enable the MCP server)"
fi

# --- 2. Node deps (TWAK) -----------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  if [[ ! -d node_modules ]]; then
    say "npm install @trustwallet/cli"
    npm install --silent --no-audit --no-fund
    ok "TWAK CLI ready (npx twak …)"
  else
    ok "node_modules already present"
  fi
else
  warn "npm not on PATH — wallet will fall back to BNBAGENT_PRIVATE_KEY (dev only)"
fi

# --- 3. Config + policy stubs ------------------------------------------------
if [[ ! -f config/policy.yaml ]]; then
  say "generating config/policy.yaml (dev default, signed with ephemeral key)"
  python -m policy.policy_sign --config config/config.yaml --out config/policy.yaml --dev
  ok "config/policy.yaml written"
else
  ok "config/policy.yaml already present (left untouched)"
fi

mkdir -p logs data data/reports

# --- 4. Sanity check ---------------------------------------------------------
say "verifying policy signature"
python -m policy.policy_verify 2>&1 | tail -3

ok "all green."
echo
printf "Next step — start the agent + dashboard with:\n\n    \033[1;36mbash bnbagent\033[0m\n\n"
