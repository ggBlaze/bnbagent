#!/usr/bin/env bash
# Launch the dashboard (port 8000).
set -e
cd "$(dirname "$0")/.."
exec python3 -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
