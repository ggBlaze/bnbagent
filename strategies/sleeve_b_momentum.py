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

    def __init__(self, name: str, components: dict, agent):
        self.name = name
        self.cfg = components["config"]
        self.policy = components["policy"]
        self.wallet = components["wallet"]
        self.cmc = components["cmc"]
        self.pancake = components["pancake"]
        self.bsc = components["bsc"]
        self.agent = agent
        self.portfolio = components["portfolio"]
        self.basket_top_n = 200
        self.positions: dict[str, Position] = {}
        self.win_rate_by_symbol: dict[str, float] = {}
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
            ohlc = await self.cmc.ohlcv_historical(
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
            if vol_5m > sleeve_cfg["volume_spike_mult"] * vol_ma and px > hi_4h and atr14 > 0:
                signals.append((sym, atr14, px))
        return signals[:5]    # top 5 only per tick

    async def _open_trade(self, sym: str, atr14: float, px: float, equity: Decimal, sleeve_cfg: dict):
        if sym in self.positions:
            return
        # Cool-off: don't re-enter a symbol right after a losing exit.
        if self.loss_cooldown_until.get(sym, 0) > int(time.time()):
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

        token_addr = self._token_address(sym)
        usdc_addr = self._token_address("USDC")
        pool_fee = self.pancake.best_pool_fee(usdc_addr, token_addr, [100, 500, 2500, 10000])
        amount_in = int(size * Decimal(10**6))
        min_out = int(amount_in / Decimal(str(px)) * Decimal("0.997"))
        calldata = self.pancake.encode_swap_v3(
            token_in=usdc_addr, token_out=token_addr, fee=pool_fee,
            recipient=self.wallet.address, amount_in=amount_in, min_out=min_out,
        )
        tx = self.wallet.sign_transaction({
            "to": self.cfg["dex"]["pcs_v3_router"],
            "data": "0x" + calldata.hex(),
            "value": 0, "gas": self.cfg["gas"]["swap_gas"],
            "nonce": self.bsc.next_nonce(self.wallet.address),
            "chainId": self.cfg["chain_id"],
        })
        self.bsc.broadcast(tx)

        pos = Position(
            sleeve="B", symbol=sym, side="long",
            notional_usdc=size, risk_usdc=size * Decimal(str(stop_distance_pct)),
            entry_ts=int(time.time()), entry_price=Decimal(str(px)),
            stop_price=Decimal(str(px - 2 * atr14)),
            tp_price=Decimal(str(px * (1 + tp_pct))),
        )
        self.positions[sym] = pos
        self.portfolio.add_position(f"B:{sym}", pos)
        log.info(f"Sleeve B: opened {sym} @ {px:.4f} size=${size} stop=${pos.stop_price} tp=${pos.tp_price}")

    async def _monitor_open_positions(self, equity: Decimal):
        max_hold = self.policy["sleeves"]["B"]["max_hold_min"] * 60
        for sym, pos in list(self.positions.items()):
            try:
                quote = await self.cmc.quotes_latest([sym])
                px = Decimal(str(quote["data"][sym]["quote"]["USD"]["price"]))
            except Exception as e:
                log.warning(f"Sleeve B monitor {sym}: cmc fail {e}")
                continue

            reason = None
            if px <= pos.stop_price:
                reason = "atr_stop"
            elif pos.tp_price is not None and px >= pos.tp_price:
                reason = "tp_hit"
            elif (int(time.time()) - pos.entry_ts) > max_hold:
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
            self.loss_cooldown_until[sym] = int(time.time()) + self.loss_cooldown_s

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
