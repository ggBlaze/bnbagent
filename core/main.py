"""BNB Agent — main entry point.

Spawns the 3 sleeve loops, starts the agent heartbeat, and runs the
dashboard bus. Replaces the BNB HACK "build once, run for a week" flow.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import logger as agent_logger
from .boot import boot
from .tick import Agent
from strategies.sleeve_a_carry import SleeveACarry
from strategies.sleeve_b_momentum import SleeveBMomentum
from strategies.sleeve_c_meanrev import SleeveCMeanRev

log = logging.getLogger(__name__)


DASHBOARD_STATE: dict = {}


async def run(args):
    agent_logger.setup(level=args.log_level, json_mode=True, log_file="logs/agent.log")

    components = boot(
        starting_equity=Decimal(str(args.equity)),
        policy_path=args.policy,
        config_path=args.config,
        replay_tape=None,
    )

    portfolio = components["portfolio"]
    policy = components["policy"]
    cfg = components["config"]
    DASHBOARD_STATE.update({
        "config": cfg, "policy": policy,
        "components": {k: v for k, v in components.items()
                        if k in ("cmc", "bsc", "pancake", "perps", "erc8004", "erc8183", "ipfs", "identity")},
        "positions_view": [],
        "trades_view": [],
        "cmc_charges_view": [],
    })

    agent = Agent(policy, portfolio, dashboard_state=DASHBOARD_STATE)

    # Instantiate sleeves
    a = SleeveACarry(name="A", components=components, agent=agent)
    b = SleeveBMomentum(name="B", components=components, agent=agent)
    c = SleeveCMeanRev(name="C", components=components, agent=agent)

    agent.register("A", cfg["ticks"]["A"], a.tick)
    agent.register("B", cfg["ticks"]["B"], b.tick)
    agent.register("C", cfg["ticks"]["C"], c.tick)

    log.info("starting agent: %d sleeves", len(agent.sleeves))
    await agent.start()

    # graceful shutdown
    stop_evt = asyncio.Event()

    def _on_sig(*_):
        log.info("signal received — shutting down")
        stop_evt.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _on_sig)
        except NotImplementedError:
            pass

    await stop_evt.wait()
    await agent.stop()
    log.info("agent stopped cleanly")


def main():
    p = argparse.ArgumentParser(prog="bnbagent")
    p.add_argument("--equity", type=float, default=100.0)
    p.add_argument("--policy", default="config/policy.yaml")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--log-level", default=os.environ.get("BNBAGENT_LOG_LEVEL", "INFO"))
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
