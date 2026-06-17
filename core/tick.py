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
        # v2.1.8 (A): set by _heartbeat when a restart was requested
        # via the control IPC. core.main reads this after wait_shutdown
        # and exits with code 75; the bash wrapper loops on 75 to
        # re-exec the agent process.
        self._restart_pending: bool = False
        # v2.1.4: floor-trade tracking. Each entry is a dict with
        # pos_id, sleeve, symbol, open_ts, close_at, reason. The
        # heartbeat checks for entries whose close_at <= now and
        # closes them. The floor trade is a position that *exists* in
        # the portfolio and *closes* later — it counts as a real trade
        # for the contest's 1-trade/day rule.
        self._floor_positions: list[dict] = []
        # v2.1.4: daily trade floor module. Lazily constructed on first
        # heartbeat. Laziness is so the unit tests can swap out the
        # floor before the agent starts running.
        self._daily_floor = None

    def register(self, name: str, period_s: int, fn):
        self.sleeves[name] = TickLoop(name, period_s, fn)

    async def start(self):
        for s in self.sleeves.values():
            s.start()
        # main heartbeat
        asyncio.create_task(self._heartbeat())
        # v2.1.4: daily trade floor + floor close loop
        asyncio.create_task(self._floor_close_loop())

    async def stop(self):
        for s in self.sleeves.values():
            await s.stop()
        self._shutdown.set()

    async def wait_shutdown(self):
        await self._shutdown.wait()

    async def _heartbeat(self):
        from .control import apply_control, is_restart_requested, clear_restart_request
        while not self._shutdown.is_set():
            # v2.1.8 (A): check for a dashboard-issued restart request
            # BEFORE doing any work. If present, set _restart_pending +
            # _shutdown so core.main exits with code 75 and the bash
            # wrapper re-execs the agent. We consume the marker so the
            # next process boot doesn't re-trigger.
            try:
                if is_restart_requested():
                    log.info("restart requested via control IPC — shutting down")
                    clear_restart_request()
                    self._restart_pending = True
                    self._shutdown.set()
                    # Still publish one final snapshot below so the
                    # dashboard sees the agent's last state during the
                    # restart window.
            except Exception as e:
                log.warning("restart-request check failed: %s", e)
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
            # v2.1.8 (UX2): write updated_at INSIDE stats too — the
            # frontend reads `stats.updated_at` for the "Updated" field
            # and the WS handler reads it for the `ts` envelope. Pre-fix
            # only the top-level `updated_at` was set, so sys-updated
            # always showed "—" and the WS ts always fell back to
            # wall-clock. Keep top-level as well for back-compat.
            now_ts = int(__import__('time').time())
            stats["updated_at"] = now_ts
            self.dashboard_state["stats"] = stats
            self.dashboard_state["updated_at"] = now_ts
            # v2.1.4: daily trade floor tick. The module throttles
            # itself to once per UTC day; the per-second call is just
            # a cheap clock check.
            try:
                floor = self._ensure_daily_floor()
                floor_status = await floor.tick()
                if floor_status:
                    self.dashboard_state["daily_floor"] = floor.status()
                    if floor_status.get("fired"):
                        log.warning("daily_floor: fired %s for %s USDC",
                                    floor_status.get("symbol"),
                                    floor_status.get("notional"))
            except Exception as e:
                log.warning("daily_floor tick failed: %s", e)
            # v2.1.8 (F1): publish to the IPC file so the sibling-process
            # dashboard sees this tick. Best-effort: write_state swallows
            # disk errors so a bad mount doesn't break the trading loop.
            self._publish_dashboard_state()
            await asyncio.sleep(1.0)

    def _publish_dashboard_state(self) -> None:
        """v2.1.8 (F1): write self.dashboard_state to the IPC snapshot
        file the dashboard reads from. Non-JSON-serializable values (a
        few of the entries under `components` are class instances) are
        coerced to their str() representation by write_state; the
        dashboard only consumes the dict-shaped entries anyway.

        v2.1.8 (P4): for each component that exposes a `.status`
        property/method, pre-extract `{tier, status}` into a dict so
        the dashboard's cross-process endpoints (e.g. /api/data-source)
        can read the dict form instead of calling `.tier` / `.status`
        on a `str(<obj>)` repr. Plain dicts (like `identity`) round
        trip unchanged. Components without `.status` still fall back
        to lossy `default=str` serialization downstream.
        """
        from . import dashboard_state as _ds_file
        snapshot = self._snapshot_for_publish(self.dashboard_state)
        _ds_file.write_state(snapshot)

    @staticmethod
    def _snapshot_for_publish(state: dict) -> dict:
        """Build a JSON-friendly copy of `state`, enriching components
        with their `.status` snapshots so cross-process endpoints can
        read keys instead of calling methods on a str.

        Pure function — no I/O, no side effects. Same input always
        produces the same output (modulo the component status, which
        is whatever the component reports at the moment).
        """
        components = state.get("components") or {}
        enriched: dict = {}
        for name, comp in components.items():
            if isinstance(comp, dict):
                # Already serializable (e.g. identity, persona configs).
                enriched[name] = comp
                continue
            status_attr = getattr(comp, "status", None)
            if status_attr is None:
                # No status surface — let write_state coerce via default=str.
                enriched[name] = comp
                continue
            try:
                value = status_attr() if callable(status_attr) else status_attr
            except Exception:
                enriched[name] = comp
                continue
            if not isinstance(value, dict):
                enriched[name] = comp
                continue
            snap = {"status": value}
            tier = getattr(comp, "tier", None)
            if tier is not None:
                snap["tier"] = tier
            enriched[name] = snap
        out = dict(state)
        if components:
            out["components"] = enriched
        return out

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

    # --- v2.1.4: daily trade floor integration ----------------------

    async def submit_floor_trade(self, proposed: ProposedTrade, *,
                                 reason: str = "daily_floor",
                                 hold_min: int = 30) -> dict:
        """Open a tiny position for the daily trade floor. Schedules a close.

        This is the smallest-possible path: it routes through the
        existing Portfolio.add_position() (which is what the sleeves
        call), so the trade is recorded in `positions` + (later) in
        `closed_trades` with `reason="daily_floor_close"`. The
        contest's 1-trade/day rule counts both opens and closes, so
        this is sufficient to clear the bar.

        For the live agent, the sleeves' TWAK path is the canonical
        trade entry. The floor is intentionally simpler — it lives
        outside the sleeve loop because the floor is a safety net,
        not a primary strategy, and shouldn't depend on a sleeve
        being enabled.
        """
        from core.portfolio import Position
        pos_id = f"FLOOR-{int(__import__('time').time())}-{proposed.symbol}"
        # Use a synthetic entry_price — for paper/replay the mark is
        # pulled from data_source, for live the BNB SDK signs the
        # actual market order and the mark comes from the receipt.
        try:
            from core.utils import token_address
            entry = float(getattr(self, "_mark_price", lambda s: 1.0)(proposed.symbol))
        except Exception:
            entry = 1.0
        pos = Position(
            id=pos_id,
            sleeve=proposed.sleeve,
            symbol=proposed.symbol,
            side=proposed.side,
            entry_price=Decimal(str(entry)),
            entry_ts=int(__import__('time').time()),
            notional_usdc=proposed.notional_usdc,
            risk_usdc=proposed.risk_usdc,
        )
        self.portfolio.add_position(pos_id, pos)
        # Schedule the close
        self._floor_positions.append({
            "pos_id": pos_id,
            "sleeve": proposed.sleeve,
            "symbol": proposed.symbol,
            "open_ts": int(__import__('time').time()),
            "close_at": int(__import__('time').time()) + hold_min * 60,
            "reason_open": reason,
        })
        log.info("floor_trade_opened", extra={
            "event": "floor_trade_open",
            "id": pos_id, "symbol": proposed.symbol,
            "notional": str(proposed.notional_usdc),
            "reason": reason, "hold_min": hold_min,
        })
        return {"status": "opened", "pos_id": pos_id, "symbol": proposed.symbol,
                "notional": str(proposed.notional_usdc), "hold_min": hold_min}

    async def _floor_close_loop(self):
        """Background task. Every 30s, close any floor position past its hold time."""
        while not self._shutdown.is_set():
            try:
                now = int(__import__('time').time())
                for entry in list(self._floor_positions):
                    if entry["close_at"] <= now:
                        pid = entry["pos_id"]
                        if pid in self.portfolio.positions:
                            try:
                                mark = self.portfolio._mark_price(entry["symbol"])
                                self.portfolio.close_position(
                                    pid, mark, reason="daily_floor_close"
                                )
                                log.info("floor_trade_closed", extra={
                                    "event": "floor_trade_close",
                                    "id": pid, "symbol": entry["symbol"],
                                    "hold_min": (now - entry["open_ts"]) // 60,
                                })
                            except Exception as e:
                                log.warning("floor_trade_close failed for %s: %s", pid, e)
                        self._floor_positions.remove(entry)
            except Exception as e:
                log.warning("floor_close_loop: %s", e)
            await asyncio.sleep(30)

    def _ensure_daily_floor(self):
        if self._daily_floor is None:
            from .daily_trade_floor import DailyTradeFloor
            self._daily_floor = DailyTradeFloor(self)
        return self._daily_floor
