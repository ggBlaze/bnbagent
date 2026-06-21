"""Portfolio: equity, peak equity, PnL, drawdown, exposure tracking."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass
class Position:
    sleeve: str
    symbol: str
    side: str                  # "long" | "short" | "spot_long+perp_short"
    notional_usdc: Decimal
    risk_usdc: Decimal         # |entry - stop| * size
    entry_ts: int
    entry_price: Decimal
    stop_price: Decimal
    tp_price: Decimal | None
    extra: dict = field(default_factory=dict)

    def mark_to_market(self, current_price: Decimal) -> Decimal:
        """Unrealized PnL in USDC.

        For spot_long+perp_short: PnL = funding income + spot PnL
        (perp short leg cancels the directional exposure; the residual
        exposure is to basis drift, which is captured by the funding
        income over time).
        """
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price * self.notional_usdc
        if self.side == "short":
            return (self.entry_price - current_price) / self.entry_price * self.notional_usdc
        if self.side == "spot_long+perp_short":
            # Carry PnL = funding income + spot PnL vs mark.
            # Short perp leg is delta-neutral by construction; only the
            # basis drift (mark - spot) moves PnL, captured implicitly
            # through the spot leg.
            spot_pnl = (current_price - self.entry_price) / self.entry_price * self.notional_usdc
            return self.extra.get("funding_paid_usdc", Decimal(0)) + spot_pnl
        return Decimal(0)


class Portfolio:
    """Tracks equity, peak, drawdown, open positions, sleeve exposure."""

    def __init__(self, starting_equity: Decimal = Decimal("100"), clock=None):
        import time as _time
        self.starting_equity = starting_equity
        self.cash_usdc = starting_equity
        # Deterministic clock (v2.0.4). See sleeve_a_carry for rationale.
        self.clock = clock or _time.time
        self.peak_equity = starting_equity
        self.positions: dict[str, Position] = {}      # id → Position
        self.closed_trades: deque = deque(maxlen=10_000)
        self.day_start_equity: dict[str, Decimal] = {}    # YYYY-MM-DD → equity at start
        self.day_breach_active_until: int = 0
        self.equity_history: deque = deque(maxlen=86_400)  # 1 sample/sec for 1 day
        self.kill_switch: bool = False
        self.kill_reason: str = ""
        # seed day_start for today so the daily-loss breaker is active from tick 1
        self.day_start_equity[self._today_static()] = starting_equity

    @staticmethod
    def _today_static() -> str:
        import time as _t
        return _t.strftime("%Y-%m-%d", _t.gmtime())

    # --- helpers ---

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _now(self) -> int:
        # Deterministic clock (v2.0.4). In production this is wall
        # clock; in the replay harness it's set to a callable that
        # returns the current tape ts.
        return int(self.clock())

    def _maybe_seed_day(self) -> None:
        today = self._today()
        if today not in self.day_start_equity:
            self.day_start_equity[today] = self.equity()

    # --- core ---

    def equity(self) -> Decimal:
        """Total equity = cash + sum of mark-to-market position PnL."""
        unrealized = sum(
            (p.mark_to_market(self._mark_price(p.symbol)) for p in self.positions.values()),
            Decimal(0),
        )
        return self.cash_usdc + unrealized

    def update_peak(self) -> Decimal:
        e = self.equity()
        if e > self.peak_equity:
            self.peak_equity = e
        self._maybe_seed_day()
        self.equity_history.append((self._now(), e))
        return e

    def drawdown_pct(self) -> float:
        e = self.equity()
        if self.peak_equity == 0:
            return 0.0
        return float((self.peak_equity - e) / self.peak_equity * 100)

    def day_pnl_pct(self) -> float:
        e = self.equity()
        ds = self.day_start_equity.get(self._today(), e)
        if ds == 0:
            return 0.0
        # v2.1.8: formula was (ds - e) / ds which inverts the sign —
        # an UP day showed as negative. Sign convention: gain → positive,
        # loss → negative.
        return float((e - ds) / ds * 100)

    # --- position mgmt ---

    def add_position(self, pos_id: str, pos: Position) -> None:
        self.positions[pos_id] = pos
        self.cash_usdc -= pos.notional_usdc
        log.info("position opened", extra={
            "event": "position_open", "id": pos_id, "sleeve": pos.sleeve,
            "symbol": pos.symbol, "side": pos.side,
            "notional": str(pos.notional_usdc), "risk": str(pos.risk_usdc),
        })

    def close_position(self, pos_id: str, exit_price: Decimal, reason: str = "manual") -> Decimal:
        pos = self.positions.pop(pos_id)
        pnl = pos.mark_to_market(exit_price)
        self.cash_usdc += pos.notional_usdc + pnl
        trade = {
            "id": pos_id, "sleeve": pos.sleeve, "symbol": pos.symbol,
            "side": pos.side, "notional": str(pos.notional_usdc),
            "entry": str(pos.entry_price), "exit": str(exit_price),
            "pnl_usdc": str(pnl), "reason": reason,
            "ts_open": pos.entry_ts, "ts_close": self._now(),
            "hold_min": (self._now() - pos.entry_ts) // 60,
        }
        self.closed_trades.append(trade)
        log.info("position closed", extra={"event": "position_close", **trade})
        self.update_peak()
        return pnl

    def sleeve_exposure(self, sleeve: str | None = None) -> Decimal:
        if sleeve is None:
            return sum(
                (p.notional_usdc for p in self.positions.values()),
                Decimal(0),
            )
        return sum(
            (p.notional_usdc for p in self.positions.values() if p.sleeve == sleeve),
            Decimal(0),
        )

    def sleeve_exposures(self) -> dict[str, Decimal]:
        """Return {sleeve: total_notional} for every sleeve with at least one position."""
        out: dict[str, Decimal] = {}
        for p in self.positions.values():
            out[p.sleeve] = out.get(p.sleeve, Decimal(0)) + p.notional_usdc
        return out

    def gross_exposure(self) -> Decimal:
        return sum((p.notional_usdc for p in self.positions.values()), Decimal(0))

    def mark_pct_for_pnl(self) -> dict:
        return {
            p.symbol: float(p.mark_to_market(self._mark_price(p.symbol)) / p.notional_usdc * 100)
            for p in self.positions.values()
        }

    # --- mark price (override in production with PCS Quoter / perps mark feed) ---

    def _mark_price(self, symbol: str) -> Decimal:
        # Deterministic stub: returns 100. Production overrides via
        # set_mark_provider. The previous version used random.random()
        # which made the replay non-deterministic across processes.
        return Decimal("100")

    def set_mark_provider(self, fn):
        self._mark_price = fn  # type: ignore

    # --- stats ---

    def stats(self) -> dict:
        e = self.update_peak()
        return {
            "equity":         float(e),
            "starting":       float(self.starting_equity),
            "peak":           float(self.peak_equity),
            "drawdown_pct":   self.drawdown_pct(),
            "day_pnl_pct":    self.day_pnl_pct(),
            "open_positions": len(self.positions),
            "closed_trades":  len(self.closed_trades),
            "gross_exposure": float(self.gross_exposure()),
            "sleeve_exposure": {
                s: float(self.sleeve_exposure(s)) for s in ("A", "B", "C")
            },
        }

    def sharpe(self, window: int = 200,
               samples_per_year: int = 365 * 24 * 60) -> float:
        """Annualized Sharpe from the equity history.

        `samples_per_year` defaults to minute samples (525,600) to match
        the historical convention used by the backtest report() output,
        but live callers should pass the value that matches the actual
        sample interval of equity_history (1/sec → 31,536,000) to avoid
        the annualization error the replay previously had (the original
        version hardcoded minute samples everywhere, which made live
        Sharpe numbers look 60× higher than reality).
        """
        if len(self.equity_history) < 2:
            return 0.0
        eqs = [float(e) for _, e in list(self.equity_history)[-window:]]
        rets = [(eqs[i] - eqs[i-1]) / eqs[i-1] for i in range(1, len(eqs)) if eqs[i-1] > 0]
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = var ** 0.5
        if std == 0:
            return 0.0
        return float(mean / std * (samples_per_year ** 0.5))

    def max_drawdown_pct(self) -> float:
        if not self.equity_history:
            return 0.0
        eqs = [float(e) for _, e in self.equity_history]
        peak = eqs[0]
        max_dd = 0.0
        for e in eqs:
            if e > peak:
                peak = e
            dd = (peak - e) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd
