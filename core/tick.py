"""Shared tick harness — runs each sleeve on its own asyncio task."""
from __future__ import annotations

import asyncio
import logging
import signal
from decimal import Decimal
from typing import Awaitable, Callable

from .portfolio import Portfolio
from .risk import circuit_breaker_check, ProposedTrade

log = logging.getLogger(__name__)


class TickLoop:
    def __init__(self, name: str, period_s: int, fn: Callable[[], Awaitable[None]]):
        self.name = name
        self.period_s = period_s
        self.fn = fn
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.tick_count = 0
        self.last_tick_ts: int = 0

    async def _run(self):
        log.info(f"sleeve {self.name} starting (period={self.period_s}s)")
        while not self._stop.is_set():
            try:
                await self.fn()
                self.tick_count += 1
                self.last_tick_ts = int(__import__('time').time())
            except Exception as e:
                log.exception(f"sleeve {self.name} tick failed: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.period_s)
            except asyncio.TimeoutError:
                pass
        log.info(f"sleeve {self.name} stopped after {self.tick_count} ticks")

    def start(self):
        self._task = asyncio.create_task(self._run(), name=f"sleeve-{self.name}")
        return self._task

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()


class Agent:
    """Top-level orchestrator. Owns the portfolio, policy, sleeve loops, and dashboard bus."""

    def __init__(self, policy: dict, portfolio: Portfolio, dashboard_state: dict | None = None,
                 reviewers: dict | None = None):
        self.policy = policy
        self.portfolio = portfolio
        self.sleeves: dict[str, TickLoop] = {}
        self.dashboard_state = dashboard_state or {}
        self.reviewers: dict = reviewers or {}
        self._shutdown = asyncio.Event()

    def register(self, name: str, period_s: int, fn):
        self.sleeves[name] = TickLoop(name, period_s, fn)

    async def start(self):
        for s in self.sleeves.values():
            s.start()
        # main heartbeat
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        for s in self.sleeves.values():
            await s.stop()
        self._shutdown.set()

    async def wait_shutdown(self):
        await self._shutdown.wait()

    async def _heartbeat(self):
        from .control import apply_control
        while not self._shutdown.is_set():
            # Pull any pending intents from the dashboard / control file
            try:
                msgs = apply_control(self.policy, self.portfolio)
                if msgs:
                    for m in msgs:
                        log.warning("control: %s", m)
                    self.dashboard_state["control_log"] = (
                        self.dashboard_state.get("control_log", []) + msgs
                    )[-100:]
            except Exception as e:
                log.warning("control apply failed: %s", e)
            self.portfolio.update_peak()
            stats = self.portfolio.stats()
            stats["sleeves"] = {
                name: {
                    "tick_count": s.tick_count,
                    "last_tick_ts": s.last_tick_ts,
                    "period_s": s.period_s,
                }
                for name, s in self.sleeves.items()
            }
            stats["kill_switch"] = self.portfolio.kill_switch
            stats["kill_reason"] = self.portfolio.kill_reason
            self.dashboard_state["stats"] = stats
            self.dashboard_state["updated_at"] = int(__import__('time').time())
            await asyncio.sleep(1.0)

    # --- convenience: check policy + log + return result ---

    def allow_trade(self, proposed: ProposedTrade) -> tuple[bool, str]:
        self.portfolio.update_peak()
        equity = self.portfolio.equity()
        peak = self.portfolio.peak_equity
        open_pos = list(self.portfolio.positions.values())
        ds = self.portfolio.day_start_equity.get(self.portfolio._today())
        ok, reason = circuit_breaker_check(
            current_equity=equity,
            peak_equity=peak,
            open_positions=open_pos,
            proposed=proposed,
            policy=self.policy,
            day_start_equity=ds,
            day_breach_active_until=self.portfolio.day_breach_active_until,
        )
        log.info("circuit_breaker_check", extra={
            "event": "risk_check",
            "proposed": proposed.sleeve, "symbol": proposed.symbol,
            "notional": str(proposed.notional_usdc),
            "allow": ok, "reason": reason,
        })
        return ok, reason

    # --- Layer 2: per-trade reviewer hook (optional, set via main.py) ----

    async def review_trade(self, proposed: ProposedTrade, sleeve_state: dict,
                           market_snapshot: dict | None = None) -> tuple[bool, str, str]:
        """Returns (allow, reason, source). Called between allow_trade and sign_transaction.

        If no reviewer is registered for the proposed sleeve, returns (True, "ok", "no_reviewer").
        """
        reviewer = (self.reviewers or {}).get(proposed.sleeve)
        if reviewer is None:
            return True, "ok", "no_reviewer"
        try:
            v = await reviewer.review(proposed, sleeve_state, market_snapshot or {})
        except Exception as e:
            log.warning("review_trade: reviewer[%s] failed: %s — proceeding", proposed.sleeve, e)
            return True, f"reviewer_error: {e}", "llm_error"
        return v.allow, v.reason, v.source
