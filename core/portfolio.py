"""Portfolio: equity, peak equity, PnL, drawdown, exposure tracking."""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
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
    # v2.1.8: paper vs real classification. A trade is `paper` if the
    # venue side was simulated (stubs in testnet/replay mode or any
    # venue whose open_short/close_short path didn't actually execute).
    # `real` means an on-chain / venue-side position was opened and
    # the matching fill was returned. Default True so existing call
    # sites that don't think about it default to "this didn't actually
    # hit a venue."
    is_paper: bool = True

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

    def __init__(self, starting_equity: Decimal = Decimal("100"), clock=None,
                 trades_persistence_path: str | None = "__default__"):
        """v2.3.5b: trades_persistence_path controls where closed
        trades are persisted as JSONL so the dashboard's /api/trades
        panel survives bot restarts.

        - omitted / default sentinel: persist to
          ~/.bnbagent/closed_trades.jsonl (the production default)
        - explicit str: persist to that path
        - explicit None: NO persistence (paper tests / replay)

        The triple-state (omitted vs None vs str) is needed because
        we can't tell apart "caller wants the default" from "caller
        explicitly disabled persistence" using a single None default.
        """
        import time as _time
        self.starting_equity = starting_equity
        self.cash_usdc = starting_equity
        # Deterministic clock (v2.0.4). See sleeve_a_carry for rationale.
        self.clock = clock or _time.time
        self.peak_equity = starting_equity
        self.positions: dict[str, Position] = {}      # id → Position
        self.closed_trades: deque = deque(maxlen=10_000)
        # v2.3.5b: persist closed trades to a JSONL file so the
        # dashboard's /api/trades panel survives bot restarts.
        # Append-only on close; loaded on init if path is set.
        # The default path lives under ~/.bnbagent/ next to the
        # control + dashboard-state files. Tests pass None to skip.
        if trades_persistence_path == "__default__":
            self.trades_persistence_path: str | None = str(
                Path("~/.bnbagent/closed_trades.jsonl").expanduser()
            )
        else:
            # explicit str OR explicit None
            self.trades_persistence_path = trades_persistence_path
        if self.trades_persistence_path:
            self._load_closed_trades_from_disk()
        self.day_start_equity: dict[str, Decimal] = {}    # YYYY-MM-DD → equity at start
        self.day_breach_active_until: int = 0
        self.equity_history: deque = deque(maxlen=86_400)  # 1 sample/sec for 1 day
        self.kill_switch: bool = False
        self.kill_reason: str = ""
        # v2.3.4: per-day counter of OPENED positions, used by the
        # max_daily_trades cap in core/risk.py::circuit_breaker_check.
        # Increments on add_position; closes don't count. Same UTC day
        # boundary as day_start_equity so the cap and the daily-loss
        # breaker agree on what "today" means.
        self.trades_opened_today: dict[str, int] = {}    # YYYY-MM-DD → count of opens
        # seed day_start for today so the daily-loss breaker is active from tick 1
        self.day_start_equity[self._today_static()] = starting_equity
        self.trades_opened_today[self._today_static()] = 0

    def _load_closed_trades_from_disk(self) -> None:
        """v2.3.5b: re-hydrate closed_trades from the JSONL file.

        Without this, /api/trades is empty after every bot restart
        because the deque lives in process memory only. The file
        is the source of truth across restarts; we re-append any
        entries that aren't already in the deque (deduped on id).
        """
        if not self.trades_persistence_path:
            return
        p = Path(self.trades_persistence_path)
        if not p.exists():
            return
        try:
            existing_ids = {t.get("id") for t in self.closed_trades}
            with open(p, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("closed_trades.jsonl: skipping malformed line: %r", line[:120])
                        continue
                    tid = trade.get("id")
                    if tid and tid not in existing_ids:
                        self.closed_trades.append(trade)
                        existing_ids.add(tid)
        except Exception as e:
            log.warning("closed_trades.jsonl: load failed (%s) — starting empty", e)

    def _append_closed_trade_to_disk(self, trade: dict) -> None:
        """v2.3.5b: append a single trade to the JSONL file with
        atomic-write semantics (mkstemp + fsync + replace). Same
        pattern as core/control.py so a concurrent reader never
        sees a half-written file.
        """
        if not self.trades_persistence_path:
            return
        try:
            p = Path(self.trades_persistence_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Append mode — each close appends one line. The file
            # is never rewritten in full, so concurrent reads see
            # a monotonically-growing log. No race because
            # O_APPEND is atomic on POSIX for small writes (< PIPE_BUF).
            with open(p, "a") as f:
                f.write(json.dumps(trade) + "\n")
                f.flush()
        except Exception as e:
            log.warning("closed_trades.jsonl: append failed: %s", e)

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
        # v2.3.4: lazy-seed the trades_opened_today counter for today
        # so the first open of a new UTC day starts from 0 even if the
        # bot has been running across midnight without a restart.
        self.trades_opened_today.setdefault(today, 0)

    # v2.3.4: how many positions were OPENED since 00:00 UTC today.
    # Used by circuit_breaker_check to enforce policy.global_risk.max_daily_trades.
    def trades_opened_today_str(self, now: int | None = None) -> int:
        """Number of position opens since 00:00 UTC today.

        Day rollover is keyed on the same YYYY-MM-DD string as
        day_start_equity so the cap and the daily-loss breaker agree.
        `now` is a wall-clock epoch for testability; default = system clock.
        """
        import time as _t
        if now is None:
            today = _t.strftime("%Y-%m-%d", _t.gmtime())
        else:
            today = _t.strftime("%Y-%m-%d", _t.gmtime(int(now)))
        self.trades_opened_today.setdefault(today, 0)
        return self.trades_opened_today[today] or 0

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
        # v2.3.4: count this open against today's max_daily_trades cap.
        # Lazy-seed today so the first open after a midnight rollover
        # starts from 0 even if add_position is the very first call
        # of the day (before _maybe_seed_day ran).
        today = self._today()
        self.trades_opened_today[today] = self.trades_opened_today.get(today, 0) + 1
        log.info("position opened", extra={
            "event": "position_open", "id": pos_id, "sleeve": pos.sleeve,
            "symbol": pos.symbol, "side": pos.side,
            "notional": str(pos.notional_usdc), "risk": str(pos.risk_usdc),
            "trades_opened_today": self.trades_opened_today[today],
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
            "is_paper": pos.is_paper,
        }
        self.closed_trades.append(trade)
        # v2.3.5b: persist to disk so /api/trades shows history
        # across restarts. Done AFTER the in-memory append so the
        # process never has a record that isn't on disk.
        self._append_closed_trade_to_disk(trade)
        log.info("position closed", extra={"event": "position_close", **trade})
        self.update_peak()
        return pnl

    # --- paper vs real aggregations (v2.1.8) ---

    def paper_pnl_usdc(self) -> Decimal:
        """Sum of realized PnL across paper (simulated) closed trades only.

        Returned alongside real_pnl_usdc so the dashboard can show
        "your real account made $X, your paper-trading sims made $Y"
        instead of conflating them.
        """
        return sum(
            (Decimal(t["pnl_usdc"]) for t in self.closed_trades if t.get("is_paper", True)),
            Decimal(0),
        )

    def real_pnl_usdc(self) -> Decimal:
        """Sum of realized PnL across real (venue-executed) closed trades only."""
        return sum(
            (Decimal(t["pnl_usdc"]) for t in self.closed_trades if not t.get("is_paper", True)),
            Decimal(0),
        )

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
            # v2.1.8: paper-vs-real PnL split so the dashboard can show
            # "real account made $X, paper sim made $Y" instead of
            # conflating them. The portfolio's total PnL is unchanged;
            # this is purely a reporting breakdown.
            "paper_pnl_usdc": float(self.paper_pnl_usdc()),
            "real_pnl_usdc":  float(self.real_pnl_usdc()),
            "paper_trades":   sum(1 for t in self.closed_trades if t.get("is_paper", True)),
            "real_trades":    sum(1 for t in self.closed_trades if not t.get("is_paper", True)),
            # v2.3.4: today's position-open count, paired with the
            # max_daily_trades cap from policy so the dashboard can
            # show "2 / 3 trades used today" without a backend call.
            "trades_opened_today": self.trades_opened_today_str(),
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
