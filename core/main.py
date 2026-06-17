"""BNB Agent — main entry point.

Spawns the 3 sleeve loops, starts the agent heartbeat, and runs the
dashboard bus. Replaces the BNB HACK "build once, run for a week" flow.

Also wires the 3-LLM agent team (advisor, reviewer, chat) when LLM
providers are configured. Graceful degradation: if no provider is set,
the agent still runs as a deterministic bot.
"""
from __future__ import annotations

# v2.1.8: load `.env` BEFORE any local imports so `os.environ` has the
# operator's TWAK_PWD / TWAK_KEYSTORE / MINIMAX_API_KEY / BNBAGENT_*
# before boot() / LLMRouter / TWAKWallet construction reads them.
# `bash bnbagent` doesn't source .env; the agent owning its own env
# load means `bash bnbagent` and `python -m core.main` behave the same.
# Default override=False so a shell export wins over the file (ops
# convention).
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

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
from agents.providers import LLMRouter, load_providers_config
from agents.advisor import StrategyAdvisor
from agents.reviewer import TradeReviewer
from agents.chat import ChatAgent
from strategies.sleeve_a_carry import SleeveACarry
from strategies.sleeve_b_momentum import SleeveBMomentum
from strategies.sleeve_c_meanrev import SleeveCMeanRev

log = logging.getLogger(__name__)


DASHBOARD_STATE: dict = {}


def _init_llm_components(components: dict):
    """Instantiate the LLM router + advisor + reviewers + chat agent.

    All are no-ops (or skipped) when the LLM is not configured.
    """
    router = LLMRouter()
    log.info("LLM status: %s", {k: v.get("enabled") for k, v in router.status()["agents"].items()})

    advisor = StrategyAdvisor(components=components, router=router, persona_name="advisor")
    reviewers = {
        "A": TradeReviewer(sleeve="A", components=components, router=router, persona_name="reviewer"),
        "B": TradeReviewer(sleeve="B", components=components, router=router, persona_name="reviewer"),
        "C": TradeReviewer(sleeve="C", components=components, router=router, persona_name="reviewer"),
    }
    chat = ChatAgent(components=components, router=router, persona_name="chat")
    return {"llm_router": router, "advisor": advisor, "reviewers": reviewers, "chat_agent": chat}


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

    # LLM agent team — graceful no-op if no provider configured
    llm_components = _init_llm_components(components)
    components.update(llm_components)
    # Skill registry (created in Phase 4) and TokenModule (Phase 3) hook in
    # here if their modules are present
    try:
        from skills.registry import SkillRegistry
        components["skill_registry"] = SkillRegistry()
        components["skill_registry"].discover()
        log.info("Skills registry: %s", [s["name"] for s in components["skill_registry"].list()])
    except Exception as e:
        log.info("Skills registry not loaded: %s", e)
    try:
        from agents.token_module import TokenModule
        components["token_module"] = TokenModule(components=components)
        log.info("TokenModule loaded")
    except Exception as e:
        log.info("TokenModule not loaded: %s", e)

    DASHBOARD_STATE.update({
        "config": cfg, "policy": policy,
        "components": {k: v for k, v in components.items()
                        if k in ("data_source", "bsc", "pancake", "perps", "erc8004", "erc8183", "ipfs",
                                 "identity", "llm_router", "advisor", "reviewers", "chat_agent",
                                 "skill_registry", "token_module")},
        "positions_view": [],
        "trades_view": [],
        "cmc_charges_view": [],
    })

    # v2.1.8 (F1): seed the IPC snapshot file before the first heartbeat
    # so the sidebar (mode/chain/wallet/identity) populates immediately
    # on dashboard load, not 1s later. The heartbeat refreshes it each
    # tick afterward.
    try:
        from . import dashboard_state as _ds_file
        _ds_file.write_state(DASHBOARD_STATE)
    except Exception as e:
        log.warning("dashboard_state seed write failed: %s", e)

    agent = Agent(policy, portfolio, dashboard_state=DASHBOARD_STATE, reviewers=llm_components["reviewers"])

    # Instantiate sleeves
    a = SleeveACarry(name="A", components=components, agent=agent)
    b = SleeveBMomentum(name="B", components=components, agent=agent)
    c = SleeveCMeanRev(name="C", components=components, agent=agent)

    agent.register("A", cfg["ticks"]["A"], a.tick)
    agent.register("B", cfg["ticks"]["B"], b.tick)
    agent.register("C", cfg["ticks"]["C"], c.tick)

    # Layer 1: strategy advisor — 5-min loop
    if llm_components["advisor"].routing.enabled or True:  # always register; no-op if disabled
        agent.register("advisor", 300, llm_components["advisor"].tick)
        log.info("advisor loop registered (enabled=%s)", llm_components["advisor"].routing.enabled)

    log.info("starting agent: %d sleeves + advisor", len(agent.sleeves))
    await agent.start()
    # Re-applied code below is unchanged; preserve rest of run().

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
    # v2.1.8 (A): if the heartbeat received a restart request via the
    # control IPC, signal main() to exit 75. The bash wrapper loops on
    # exit 75 to re-exec; any other exit code is a permanent stop.
    return bool(getattr(agent, "_restart_pending", False))


def main():
    p = argparse.ArgumentParser(prog="bnbagent")
    p.add_argument("--equity", type=float, default=100.0)
    p.add_argument("--policy", default="config/policy.yaml")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--log-level", default=os.environ.get("BNBAGENT_LOG_LEVEL", "INFO"))
    args = p.parse_args()
    restart_requested = asyncio.run(run(args))
    # v2.1.8 (A): exit 75 → bash wrapper re-execs. Any other exit (0,
    # signal, exception) → wrapper stops.
    if restart_requested:
        sys.exit(75)


if __name__ == "__main__":
    main()
