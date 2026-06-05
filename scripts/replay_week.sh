#!/usr/bin/env bash
# Run a 7-day synthetic replay + generate the report.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/reports
python3 -m backtest.replay --report data/reports/replay.html
echo
echo "Open: data/reports/replay.html"
