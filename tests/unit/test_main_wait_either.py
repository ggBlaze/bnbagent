"""A bugfix (v2.1.8): run() must return when EITHER the OS signal
handler fires OR the agent's internal _shutdown flag flips (which the
heartbeat does on restart request). Pre-fix `run()` only waited on the
OS signal; setting agent._shutdown from inside the heartbeat had no
effect on run() and the bash wrapper never saw exit 75.

This pins the helper that does the wait so the run() body stays
ergonomic (no nested asyncio.wait noise inline).
"""
from __future__ import annotations

import asyncio

import pytest


def test_wait_either_returns_on_signal_event():
    """Helper returns when the signal Event is set first."""
    from core.main import _wait_either

    async def run():
        sig = asyncio.Event()
        agent_shutdown = asyncio.Event()

        async def trip():
            await asyncio.sleep(0.05)
            sig.set()

        asyncio.create_task(trip())
        return await asyncio.wait_for(_wait_either(sig, agent_shutdown), timeout=1.0)

    asyncio.run(run())  # must not raise TimeoutError


def test_wait_either_returns_on_agent_shutdown():
    """Helper returns when the agent's internal _shutdown is set first
    (this is the path the heartbeat takes when restart is requested)."""
    from core.main import _wait_either

    async def run():
        sig = asyncio.Event()
        agent_shutdown = asyncio.Event()

        async def trip():
            await asyncio.sleep(0.05)
            agent_shutdown.set()

        asyncio.create_task(trip())
        return await asyncio.wait_for(_wait_either(sig, agent_shutdown), timeout=1.0)

    asyncio.run(run())  # must not raise


def test_wait_either_does_not_wait_for_both():
    """Helper returns on first event, not all events. Belt-and-suspenders
    against someone refactoring to `asyncio.gather(...)` which would
    wait for both."""
    from core.main import _wait_either

    async def run():
        sig = asyncio.Event()
        agent_shutdown = asyncio.Event()

        async def trip_only_one():
            await asyncio.sleep(0.05)
            agent_shutdown.set()
            # sig is NEVER set. If _wait_either is using gather() the
            # outer wait_for(1.0) will TimeoutError.

        asyncio.create_task(trip_only_one())
        await asyncio.wait_for(_wait_either(sig, agent_shutdown), timeout=1.0)

    asyncio.run(run())
