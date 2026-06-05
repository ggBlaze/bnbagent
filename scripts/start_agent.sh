#!/usr/bin/env bash
# Launch the agent (testnet mode by default).
set -e
cd "$(dirname "$0")/.."
exec python3 -m core.main --equity "${BNBAGENT_EQUITY:-100}" --log-level "${BNBAGENT_LOG_LEVEL:-INFO}"
