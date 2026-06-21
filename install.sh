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
# Robust venv setup. Order: existing venv → uv (no apt dep, fast) →
# python3 -m venv (needs python3-venv on Debian/Ubuntu) → auto-install uv
# via the official one-liner. This makes the installer work out of the box
# on Ubuntu 24+/26+ and other modern distros where python3-venv is not
# installed by default.
if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -d "/tmp/venv" ]]; then
  say "reusing existing venv at /tmp/venv (set BNBAGENT_VENV to override)"
  # shellcheck disable=SC1091
  source /tmp/venv/bin/activate
else
  rm -rf .venv  # clear any partial venv from a previous failed attempt
  if command -v uv >/dev/null 2>&1; then
    say "creating .venv (uv)"
    uv venv --seed .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    ok "venv created (uv)"
  elif python3 -m venv .venv 2>/dev/null; then
    say "creating .venv (python3 -m venv)"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    ok "venv created (python3 -m venv)"
  else
    warn "no venv tool found — installing uv from astral.sh"
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | sh
    else
      die "Need uv or python3-venv. Install one of:
   • uv (recommended):  pip install --user uv  (then re-run this script)
   • python3-venv:      sudo apt install -y python3.12-venv  (Ubuntu 24+)
   • python3-venv:      sudo apt install -y python3.14-venv  (Ubuntu 26+)"
    fi
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      die "uv install failed — install python3-venv manually: sudo apt install python3.X-venv"
    fi
    say "creating .venv (uv, auto-installed)"
    uv venv --seed .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    ok "venv created (uv, auto-installed)"
  fi
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
# The TWAK CLI is an optional dependency — the wallet falls back to
# BNBAGENT_PRIVATE_KEY (dev only) if it's missing. Don't abort the install
# if `npm install` fails; just warn and continue.
if command -v npm >/dev/null 2>&1; then
  if [[ ! -d node_modules ]]; then
    say "npm install @trustwallet/cli"
    if npm install --silent --no-audit --no-fund 2>&1 | tail -3; then
      ok "TWAK CLI ready (npx twak …)"
    else
      warn "npm install failed — wallet will fall back to BNBAGENT_PRIVATE_KEY (dev only)"
    fi
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

# v2.1.8: copy config/local.yaml.example → config/local.yaml if the
# user-state shadow file doesn't exist yet. v2.1.8 change: the example
# is now pre-populated with mainnet defaults so a fresh install lands
# on BSC mainnet (chain 56, tier=binance, real RPCs) — no toggle needed.
# To switch to replay/testnet for safe dry-runs, edit local.yaml after
# install (or set BNBAGENT_MODE=replay env var).
if [[ ! -f config/local.yaml ]]; then
  if [[ -f config/local.yaml.example ]]; then
    cp config/local.yaml.example config/local.yaml
    ok "config/local.yaml bootstrapped with mainnet defaults (gitignored, your private overrides)"
  fi
else
  ok "config/local.yaml already present (left untouched)"
fi

mkdir -p logs data data/reports

# --- 4. Sanity check ---------------------------------------------------------
say "verifying policy signature"
python -m policy.policy_verify 2>&1 | tail -3

ok "all green."
echo
printf "Next step — start the agent + dashboard with:\n\n    \033[1;36mbash bnbagent\033[0m\n\n"
