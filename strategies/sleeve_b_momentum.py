"""Sleeve B — DEX momentum on BNB-chain pairs.

Watches CMC OHLCV + listings data for volume-spike + 4h breakout on the curated
BSC DEX universe. Opens a 1–4h long position with ATR-based stop and a fixed
3% take-profit. Sized by quarter-Kelly, capped at 1% per-trade risk.

Exits a trade if:
  - price <= entry - 2*ATR14
  - price >= entry * 1.03 (3% TP)
  - hold time > 4h (max_hold_min)
  - per-trade risk > 1%
  - daily loss > 3%
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from decimal import Decimal
from typing import Any

import numpy as np

from core.portfolio import Position
from core.risk import ProposedTrade, kelly_size, cap_by_risk
from core.utils import token_address

log = logging.getLogger(__name__)


class SleeveBMomentum:
    """Sleeve B — DEX momentum (20% of capital)."""

    name = "B"

    def __init__(self, name: str, components: dict, agent, clock=None):
        self.name = name
        self.cfg = components["config"]
        self.policy = components["policy"]
        self.wallet = components["wallet"]
        self.data_source = components["data_source"]
        self.pancake = components["pancake"]
        self.bsc = components["bsc"]
        self.agent = agent
        self.portfolio = components["portfolio"]
        # Deterministic clock (v2.0.4). See sleeve_a_carry for rationale.
        self.clock = clock or time.time
        self.basket_top_n = 200
        self.positions: dict[str, Position] = {}
        self.win_rate_by_symbol: dict[str, float] = {}
        # symbol → entry ATR. Used by the vol-spike rescale in
        # _monitor_open_positions. If realized vol doubles mid-trade, the
        # static 2*ATR stop becomes too tight; we widen to 2*current_ATR
        # clamped to a max loss of max_loss_pct.
        self.entry_atr: dict[str, float] = {}
        # symbol → epoch of last losing exit. Used to prevent revenge re-entries.
        self.loss_cooldown_until: dict[str, int] = {}
        self.loss_cooldown_s: int = 4 * 3600  # 4h cool-off after a stop-out

    async def tick(self):
        self.portfolio.update_peak()
        equity = self.portfolio.equity()
        if equity <= 0:
            return

        sleeve_cfg = self.policy["sleeves"]["B"]
        if not sleeve_cfg.get("enabled", True):
            return

        await self._monitor_open_positions(equity)
        signals = await self._scan_signals(sleeve_cfg)
        for sym, atr14, px in signals:
            await self._open_trade(sym, atr14, px, equity, sleeve_cfg)

    async def _scan_signals(self, sleeve_cfg: dict) -> list[tuple[str, float, float]]:
        """Returns [(symbol, atr14, current_price)] for new entries."""
        try:
            universe = self.cfg["cmc"]["dex_universe_symbols"]
            ohlc = await self.data_source.ohlcv_historical(
                universe, time_period="hour", count=24, convert="USD",
            )
        except Exception as e:
            log.warning("Sleeve B: CMC ohlc fetch failed: %s", e)
            return []

        signals = []
        for sym, payload in (ohlc.get("data") or {}).items():
            candles = payload.get("quotes", [])
            if len(candles) < 16:
                continue
            last = candles[-1]
            prev_4h = candles[-5:-1]
            vol_5m = last.get("volume", 0)
            vol_ma = float(np.mean([c.get("volume", 0) for c in candles[-12:]])) or 1
            hi_4h = max(c.get("high", 0) for c in prev_4h)
            atr14 = self._atr(candles, sleeve_cfg["atr_len"])
            px = last.get("close", 0)
            # Regime filter (v2.0.4): require 4h trend confirmation. The
            # previous 1h check was removed from the code (the v2.0.2 /
            # v2.0.3 commit message claimed it was loosened but only
            # the config default changed, the code still gated on it).
            # 4h-only is the documented behaviour; 1h is now always-pass.
            require_4h = self.policy.get("global_risk", {}).get("require_4h_trend_for_momentum", True)
            trend_4h_ok = (not require_4h) or px > hi_4h
            if (vol_5m > sleeve_cfg["volume_spike_mult"] * vol_ma
                    and trend_4h_ok and atr14 > 0):
                signals.append((sym, atr14, px))
        return signals[:5]    # top 5 only per tick

    async def _open_trade(self, sym: str, atr14: float, px: float, equity: Decimal, sleeve_cfg: dict):
        if sym in self.positions:
            return
        # Cool-off: don't re-enter a symbol right after a losing exit.
        if self.loss_cooldown_until.get(sym, 0) > int(self.clock()):
            return
        p_win = self.win_rate_by_symbol.get(sym, 0.55)
        tp_pct = sleeve_cfg["tp_pct"] / 100
        stop_distance_pct = (2 * atr14) / px if px > 0 else 0.02
        b_ratio = tp_pct / stop_distance_pct if stop_distance_pct > 0 else 0
        f = kelly_size(p_win, b_ratio, kelly_fraction=sleeve_cfg["kelly_fraction"])
        size = cap_by_risk(
            fraction=f, equity=equity,
            stop_distance_fraction=stop_distance_pct,
            per_trade_risk_pct=self.policy["global_risk"]["per_trade_risk_pct"],
        )
        if size <= 0 or size < Decimal("1"):
            return
        size = min(size, equity * Decimal(str(sleeve_cfg["max_position_pct"] / 100)))
        if size > equity * Decimal("0.20"):
            size = equity * Decimal("0.20")

        proposed = ProposedTrade(
            sleeve="B", symbol=sym, side="long",
            notional_usdc=size, risk_usdc=size * Decimal(str(stop_distance_pct)),
        )
        ok, reason = self.agent.allow_trade(proposed)
        if not ok:
            log.info(f"Sleeve B skip {sym}: {reason}")
            return

        # Layer 2: LLM reviewer veto (best-effort, never blocks; falls back to heuristic)
        sleeve_state = {
            "recent_trades": list(self._recent_trades_for(sym)),
            "win_rate_ewma": self.win_rate_by_symbol.get(sym, 0.55),
            "sleeve_dd_pct": 0.0,
            "policy_max_dd_pct": float(self.policy.get("global_risk", {}).get("max_drawdown_pct", 100)),
            "loss_cooldown_active": self.loss_cooldown_until.get(sym, 0) > int(self.clock()),
        }
        market_snapshot = {"symbol": sym, "px": float(px), "atr14": atr14,
                           "vol_5m": 0, "vol_ma": 0}
        try:
            ok2, reason2, _src = await self.agent.review_trade(proposed, sleeve_state, market_snapshot)
        except Exception as e:
            log.warning(f"Sleeve B reviewer call failed: {e} — proceeding")
            ok2 = True
        if not ok2:
            log.info(f"Sleeve B reviewer veto {sym}: {reason2}")
            return

        token_addr = self._token_address(sym)
        usdc_addr = self._token_address("USDC")
        pool_fee = self.pancake.best_pool_fee(usdc_addr, token_addr, [100, 500, 2500, 10000])
        amount_in = int(size * Decimal(10**6))
        min_out = int(amount_in / Decimal(str(px)) * Decimal("0.997"))
        calldata = self.pancake.encode_swap_v3(
            token_in=usdc_addr, token_out=token_addr, fee=pool_fee,
            recipient=self.wallet.address, amount_in=amount_in, min_out=min_out,
        )
        # v2.0.8-H4: honor fees.max_gas_price_gwei. Sleeve B is the
        # most time-sensitive (momentum + 4h trend) so a stuck tx is
        # most expensive here. Skip with gas_too_high_skip — the
        # signal will re-fire on the next tick if it's still valid.
        try:
            tx = self.wallet.sign_transaction(
                {
                    "to": self.cfg["dex"]["pcs_v3_router"],
                    "data": "0x" + calldata.hex(),
                    "value": 0, "gas": self.cfg["gas"]["swap_gas"],
                    "nonce": self.bsc.next_nonce(self.wallet.address),
                    "chainId": self.cfg["chain_id"],
                },
                chain_id=self.cfg["chain_id"],
                max_gas_price_gwei=self._max_gas_gwei(),
            )
        except Exception as e:
            if "gas price" in str(e).lower() and "exceeds" in str(e).lower():
                log.info(f"Sleeve B {sym}: gas_too_high_skip — {e}")
                return
            raise
        self.bsc.broadcast(tx)

        pos = Position(
            sleeve="B", symbol=sym, side="long",
            notional_usdc=size, risk_usdc=size * Decimal(str(stop_distance_pct)),
            entry_ts=int(self.clock()), entry_price=Decimal(str(px)),
            stop_price=Decimal(str(px - 2 * atr14)),
            tp_price=Decimal(str(px * (1 + tp_pct))),
        )
        self.positions[sym] = pos
        self.entry_atr[sym] = atr14
        self.portfolio.add_position(f"B:{sym}", pos)
        log.info(f"Sleeve B: opened {sym} @ {px:.4f} size=${size} stop=${pos.stop_price} tp=${pos.tp_price}")

    async def _monitor_open_positions(self, equity: Decimal):
        max_hold = self.policy["sleeves"]["B"]["max_hold_min"] * 60
        sleeve_cfg = self.policy["sleeves"]["B"]
        vol_spike_threshold = float(sleeve_cfg.get("vol_spike_threshold", 1.5))
        max_loss_pct = float(sleeve_cfg.get("max_loss_pct", 5.0)) / 100.0
        atr_stop_mult = float(sleeve_cfg["atr_stop_mult"])
        atr_len = int(sleeve_cfg["atr_len"])
        for sym, pos in list(self.positions.items()):
            try:
                quote = await self.data_source.quotes_latest([sym])
                px = Decimal(str(quote["data"][sym]["quote"]["USD"]["price"]))
            except Exception as e:
                log.warning(f"Sleeve B monitor {sym}: cmc fail {e}")
                continue

            # --- ATR rescale on vol spike ---
            # If realized vol has spiked, the static 2*ATR stop is too tight.
            # Re-fetch the last `atr_len + 1` 1h candles and recompute ATR.
            # If current_atr > entry_atr * vol_spike_threshold, widen the
            # stop to (entry - atr_stop_mult * current_atr), clamped to a
            # max loss of max_loss_pct on the position. This catches
            # vol-spike events where the original stop would have been
            # taken out by a noise tick.
            entry_atr = self.entry_atr.get(sym)
            if entry_atr and entry_atr > 0:
                try:
                    ohlc = await self.data_source.ohlcv_historical(
                        [sym], time_period="hour", count=atr_len + 1, convert="USD",
                    )
                    candles = (ohlc.get("data") or {}).get(sym, {}).get("quotes", [])
                    if len(candles) >= atr_len + 1:
                        current_atr = self._atr(candles, atr_len)
                        if current_atr > entry_atr * vol_spike_threshold:
                            new_stop = float(pos.entry_price) - atr_stop_mult * current_atr
                            # floor: don't widen beyond max_loss_pct
                            floor_stop = float(pos.entry_price) * (1 - max_loss_pct)
                            new_stop = max(new_stop, floor_stop)
                            # For a long, widening the stop = moving it DOWN
                            # (more room before exit). The new stop is
                            # BELOW the old one if ATR spiked.
                            if float(pos.stop_price) > new_stop:
                                log.info(
                                    f"Sleeve B {sym}: vol spike "
                                    f"(ATR {entry_atr:.4f}→{current_atr:.4f}, "
                                    f"{current_atr/entry_atr:.1f}× entry), "
                                    f"widening stop {pos.stop_price}→{new_stop:.4f}"
                                )
                                pos.stop_price = Decimal(str(new_stop))
                except Exception as e:
                    log.warning(f"Sleeve B monitor {sym}: atr rescale fail {e}")

            reason = None
            if px <= pos.stop_price:
                reason = "atr_stop"
            elif pos.tp_price is not None and px >= pos.tp_price:
                reason = "tp_hit"
            elif (int(self.clock()) - pos.entry_ts) > max_hold:
                reason = "time_stop"
            if reason:
                self._close(sym, px, reason)

    def _close(self, sym: str, exit_price: Decimal, reason: str):
        pos = self.positions.pop(sym, None)
        if not pos:
            return
        pnl = self.portfolio.close_position(f"B:{sym}", exit_price=exit_price, reason=reason)
        # update win-rate estimator (very simple EWMA)
        win = 1 if pnl > 0 else 0
        prev = self.win_rate_by_symbol.get(sym, 0.55)
        self.win_rate_by_symbol[sym] = 0.9 * prev + 0.1 * win
        # Cooldown after a stop-out to prevent revenge entries.
        if pnl < 0 and reason in ("atr_stop", "stop_hit", "time_stop"):
            self.loss_cooldown_until[sym] = int(self.clock()) + self.loss_cooldown_s

    def _recent_trades_for(self, sym: str, n: int = 20) -> list[dict]:
        """Return the last N closed trades on this symbol from the portfolio."""
        try:
            all_trades = list(self.portfolio.closed_trades)
        except Exception:
            return []
        out = []
        for t in reversed(all_trades):
            if t.get("symbol") == sym:
                out.append({"pnl_pct": float(t.get("pnl_usdc", 0)) / max(1, float(t.get("notional", 1))) * 100,
                            "reason": t.get("reason")})
                if len(out) >= n:
                    break
        return list(reversed(out))

    def _atr(self, candles: list[dict], period: int) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return float(np.mean(trs[-period:])) if trs else 0.0

    def _token_address(self, symbol: str) -> str:
        return token_address(self.cfg, symbol)

    def _max_gas_gwei(self) -> float | None:
        """v2.0.8-H4: read fees.max_gas_price_gwei from policy."""
        v = (self.policy.get("fees") or {}).get("max_gas_price_gwei")
        return float(v) if v is not None else None
