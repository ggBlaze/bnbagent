"""F1: the agent's heartbeat must write its dashboard_state to the IPC
file each tick so the sibling-process dashboard can read it.

We don't spin up the full heartbeat loop (that's an integration test).
We just verify the wiring: calling the publish helper writes the file
the dashboard reads from."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _ipc_path(monkeypatch, tmp_path):
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH",
                        str(tmp_path / "dashboard_state.json"))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()
    yield
    ds._clear_cache_for_tests()


def test_agent_exposes_publish_method():
    """Agent has a `_publish_dashboard_state` method that writes the
    current dashboard_state to the IPC file. Pulled out as a method so
    the test can call it deterministically without driving the full
    heartbeat loop."""
    from core.tick import Agent
    assert hasattr(Agent, "_publish_dashboard_state"), (
        "Agent should expose _publish_dashboard_state for the heartbeat"
    )


def test_publish_writes_dashboard_state_to_ipc_file():
    """publish writes a JSON file whose content equals the
    dashboard_state dict (modulo str() coercion for non-JSON types)."""
    from core.tick import Agent
    from core import dashboard_state as ds
    from core.portfolio import Portfolio
    from decimal import Decimal

    state = {
        "stats": {"equity_usdc": Decimal("100.50"), "kill_switch": False},
        "config": {"mode": "mainnet", "chain_id": 56},
        "components": {"identity": {"token_id": "42"}},
        "updated_at": 1718600000,
    }
    portfolio = Portfolio(starting_equity=Decimal("100"))
    agent = Agent(policy={}, portfolio=portfolio, dashboard_state=state)
    agent._publish_dashboard_state()
    out = ds.read_state()
    # Identity dict is preserved.
    assert out["components"]["identity"] == {"token_id": "42"}
    # Decimal → str via the default=str serializer.
    assert out["stats"]["equity_usdc"] == "100.50"
    assert out["config"]["mode"] == "mainnet"
    assert out["updated_at"] == 1718600000


def test_heartbeat_publishes_each_iteration(monkeypatch):
    """A single iteration of the heartbeat loop must write the file.

    Drive one iteration by setting the shutdown event AFTER the first
    write. We monkeypatch asyncio.sleep so the loop exits immediately
    after setting the shutdown flag — no real wall-clock wait.
    """
    from core.tick import Agent
    from core import dashboard_state as ds
    from core.portfolio import Portfolio
    from decimal import Decimal

    portfolio = Portfolio(starting_equity=Decimal("100"))
    agent = Agent(policy={"sleeves": {}}, portfolio=portfolio)
    # Trip shutdown after the first sleep so the loop body runs exactly once.
    real_sleep = asyncio.sleep

    async def one_shot_sleep(s):
        agent._shutdown.set()
        # Yield control without waiting the real second.
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", one_shot_sleep)
    asyncio.run(agent._heartbeat())
    # The file should exist with at least the stats key populated by the
    # heartbeat body (line 122: dashboard_state["stats"] = stats).
    out = ds.read_state()
    assert "stats" in out, f"heartbeat should publish stats key, got {out!r}"
    assert "updated_at" in out
