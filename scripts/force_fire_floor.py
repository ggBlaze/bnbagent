#!/usr/bin/env python3
"""Write a force-fire marker to control.json.

The next agent heartbeat (within 1s) will consume it and fire the
daily trade floor immediately, bypassing the (hour, minute) gate.

Usage:
    source .venv/bin/activate
    export PYTHONPATH=$PWD
    python3 scripts/force_fire_floor.py [reason]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.control import request_force_fire_floor  # noqa: E402

reason = sys.argv[1] if len(sys.argv) > 1 else "manual CLI force-fire"
request_force_fire_floor(reason)
print(f"[force_fire] requested: {reason!r}")
print("[force_fire] the next agent heartbeat (within 1s) will fire the daily trade floor")
print("[force_fire] watch logs/agent.log for 'floor_trade_open' + onchain tx hash")
