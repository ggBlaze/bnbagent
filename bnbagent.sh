#!/usr/bin/env bash
# BNB Agent — single command to run the whole system.
#
#   bash bnbagent            # agent + dashboard on http://localhost:8000
#   bash bnbagent --replay   # 7-day synthetic replay (no live network)
#   bash bnbagent --repl     # open a python REPL with components pre-loaded
#
# Press Ctrl+C to stop both the agent and the dashboard.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f /tmp/venv/bin/activate ]]; then
  source /tmp/venv/bin/activate
else
  echo "[bnbagent] no venv found. Run 'bash install.sh' first." >&2
  exit 1
fi

export PYTHONPATH="$ROOT:${PYTHONPATH:-}"

say()  { printf "\033[1;36m[bnbagent]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[bnbagent]\033[0m %s\n" "$*"; }

DASHBOARD_PORT="${BNBAGENT_DASHBOARD_PORT:-8000}"
EQUITY="${BNBAGENT_EQUITY:-100}"

case "${1:-}" in
  --replay)
    say "running 7-day synthetic replay (no live network)…"
    exec python -m backtest.replay \
        --tape data/synthetic_week.json \
        --report data/reports/replay.html \
        --equity "$EQUITY"
    ;;
  --repl)
    say "opening Python REPL with components pre-loaded (p is the boot dict)…"
    exec python -i -c "from core.boot import boot; from decimal import Decimal; p = boot(Decimal('$EQUITY')); print('p =', list(p.keys()))"
    ;;
  "")
    say "starting agent (equity=\$$EQUITY) and dashboard on http://localhost:$DASHBOARD_PORT"
    say "Ctrl+C to stop."
    echo
    # run dashboard in the background, agent in the foreground so Ctrl+C stops both
    python -m dashboard.backend.main >"logs/dashboard.log" 2>&1 &
    DASH_PID=$!
    trap 'kill $DASH_PID 2>/dev/null || true' INT TERM EXIT
    # give the dashboard a moment to bind
    sleep 1
    ok "dashboard PID=$DASH_PID → http://localhost:$DASHBOARD_PORT"
    python -m core.main --equity "$EQUITY" --config config/config.yaml --policy config/policy.yaml
    ;;
  *)
    echo "usage: bash bnbagent [--replay|--repl]" >&2
    exit 2
    ;;
esac
