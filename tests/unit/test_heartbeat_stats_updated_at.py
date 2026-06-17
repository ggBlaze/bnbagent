"""UX2 (v2.1.8): the agent writes `updated_at` at the top level of
the IPC state, but the frontend + WS both read `stats.updated_at`.

  core/tick.py:_heartbeat:
    self.dashboard_state["stats"] = stats
    self.dashboard_state["updated_at"] = int(time())   # top-level

  dashboard/frontend/index.html:
    $('sys-updated').textContent = stats.updated_at ? ts(...) : '—';

  dashboard/backend/main.py (WS):
    "ts": _stats().get("updated_at") or int(time.time())

So the sidebar "Updated" field always shows "—" and the WS ts always
falls back to wall-clock. Sleeves[*].last_tick_ts is set correctly
because the heartbeat puts those *inside* the stats dict. Move
`updated_at` inside the stats dict alongside the sleeves entries so
all three consumers (sys-updated, /api/healthz.agent_updated_at,
WS.ts) align with one contract.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def _ipc(monkeypatch, tmp_path):
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH",
                        str(tmp_path / "dashboard_state.json"))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()
    yield
    ds._clear_cache_for_tests()


def test_heartbeat_writes_updated_at_inside_stats(monkeypatch):
    """The frontend reads stats.updated_at, so updated_at MUST live
    inside the stats dict — not just at the top level."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import dashboard_state as ds

    agent = Agent(policy={"sleeves": {}},
                  portfolio=Portfolio(starting_equity=Decimal("100")))
    real_sleep = asyncio.sleep
    async def one_shot(_s):
        agent._shutdown.set()
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", one_shot)
    asyncio.run(agent._heartbeat())

    out = ds.read_state()
    stats = out.get("stats", {})
    assert "updated_at" in stats, (
        f"stats dict must contain updated_at so the frontend can show "
        f"a live 'Updated' timestamp; got stats keys={sorted(stats.keys())}"
    )
    assert isinstance(stats["updated_at"], int)
    assert stats["updated_at"] > 0


def test_top_level_updated_at_also_preserved(monkeypatch):
    """Top-level updated_at stays too (some callers may read it).
    Don't break either consumer."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import dashboard_state as ds

    agent = Agent(policy={"sleeves": {}},
                  portfolio=Portfolio(starting_equity=Decimal("100")))
    real_sleep = asyncio.sleep
    async def one_shot(_s):
        agent._shutdown.set()
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", one_shot)
    asyncio.run(agent._heartbeat())

    out = ds.read_state()
    assert out.get("updated_at"), "top-level updated_at must remain set"
    # Both must agree (same tick wrote them).
    assert out["updated_at"] == out["stats"]["updated_at"]
