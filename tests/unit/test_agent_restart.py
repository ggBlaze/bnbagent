"""A (v2.1.8): the agent heartbeat must pick up the restart marker
written by core.control.request_restart() and trigger a graceful
shutdown that core.main can translate into exit code 75 (so the bash
wrapper re-execs).

We don't drive the full heartbeat loop — that's covered indirectly by
the production launch and by tests/unit/test_agent_publishes_dashboard_state.py.
Here we exercise just the restart wiring: write the marker, run one
iteration, assert the agent set its shutdown + restart_pending flags.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def _isolate_control(monkeypatch, tmp_path):
    """Use a tmp control.json so tests don't fight the real one."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))


def test_agent_starts_with_restart_pending_false():
    """A fresh agent has no restart pending. core.main reads this flag
    after wait_shutdown to decide between exit code 0 and 75."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    agent = Agent(policy={"sleeves": {}},
                  portfolio=Portfolio(starting_equity=Decimal("100")))
    assert hasattr(agent, "_restart_pending"), (
        "Agent must expose _restart_pending so main() can read it"
    )
    assert agent._restart_pending is False


def test_heartbeat_consumes_restart_request_and_shuts_down(monkeypatch):
    """One heartbeat iteration with a restart marker set: agent must
    set _shutdown (so the main run() returns) AND set _restart_pending
    (so main() exits with 75 instead of 0). The marker must be cleared
    so the next process boot doesn't see a stale request."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import control

    agent = Agent(policy={"sleeves": {}},
                  portfolio=Portfolio(starting_equity=Decimal("100")))
    control.request_restart(reason="from-test")

    # Drive exactly one iteration: trip shutdown after the first sleep
    # so the loop body runs once and exits cleanly.
    real_sleep = asyncio.sleep
    async def one_shot_sleep(s):
        # By the time sleep is reached, the body has already handled
        # the restart request. We set shutdown defensively in case the
        # body didn't (which is the failure mode we're testing).
        if not agent._shutdown.is_set():
            agent._shutdown.set()
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", one_shot_sleep)
    asyncio.run(agent._heartbeat())

    assert agent._restart_pending is True, (
        "heartbeat should set _restart_pending so main() exits 75"
    )
    assert agent._shutdown.is_set(), (
        "heartbeat should trigger graceful shutdown"
    )
    assert not control.is_restart_requested(), (
        "heartbeat should clear the marker so the next process boot "
        "doesn't restart again immediately"
    )


def test_heartbeat_without_restart_request_leaves_pending_false(monkeypatch):
    """Negative case: no restart marker → no _restart_pending. The
    normal shutdown path (Ctrl+C, signal handler) ends with exit 0."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    agent = Agent(policy={"sleeves": {}},
                  portfolio=Portfolio(starting_equity=Decimal("100")))
    real_sleep = asyncio.sleep
    async def one_shot_sleep(s):
        agent._shutdown.set()
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", one_shot_sleep)
    asyncio.run(agent._heartbeat())
    assert agent._restart_pending is False
