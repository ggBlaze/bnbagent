"""Sleeve C — Mean-reversion on top-20 BSC tokens.

Fades sharp 1h drops on the top-20 CMC tokens that trade on BSC. Uses a 1h
z-score threshold of -2.5σ to detect dislocations. Sized by quarter-Kelly,
capped at 1% per-trade risk. 2% stop, 1% target, 6h time stop.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

import numpy as np

from core.portfolio import Position
from core.risk import ProposedTrade, kelly_size, cap_by_risk
from core.utils import token_address

log = logging.getLogger(__name__)


class SleeveCMeanRev:
    """Sleeve C — mean-reversion (10% of capital)."""

    name = "C"

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
        self.positions: dict[str, Position] = {}
        self.win_rate_by_symbol: dict[str, float] = {}
        self.loss_cooldown_until: dict[str, int] = {}
        self.loss_cooldown_s: int = 6 * 3600  # 6h cool-off after a stop-out

    async def tick(self):
        self.portfolio.update_peak()
        equity = self.portfolio.equity()
        if equity <= 0:
            return

        sleeve_cfg = self.policy["sleeves"]["C"]
        if not sleeve_cfg.get("enabled", True):
            return

        await self._monitor_open_positions(equity, sleeve_cfg)
        signals = await self._scan_signals(sleeve_cfg)
        for sym, ref_price, sigma in signals:
            await self._open_mean_rev(sym, ref_price, sigma, equity, sleeve_cfg)

    async def _scan_signals(self, sleeve_cfg: dict) -> list[tuple[str, float, float]]:
        try:
            universe = self.cfg["cmc"]["basket_symbols"][:20]
            ohlc = await self.cmc.ohlcv_historical(
                universe, time_period="hour", count=24 * 7, convert="USD",
            )
        except Exception as e:
            log.warning("Sleeve C: CMC ohlc fetch failed: %s", e)
            return []

        z = sleeve_cfg["zscore_threshold"]
        out = []
        for sym, payload in (ohlc.get("data") or {}).items():
            quotes = payload.get("quotes", [])
            if len(quotes) < 5:
                continue
            ret_1h = (quotes[-1]["close"] - quotes[-2]["close"]) / quotes[-2]["close"]
            rets = [
                (quotes[i]["close"] - quotes[i-1]["close"]) / quotes[i-1]["close"]
                for i in range(-sleeve_cfg["lookback_h"], 0)
            ]
            sigma = float(np.std(rets)) if rets else 0
            if sigma > 0 and ret_1h / sigma <= -z:
                out.append((sym, quotes[-1]["close"], sigma))
        return out[:3]

    async def _open_mean_rev(self, sym: str, ref_price: float, sigma: float, equity: Decimal, sleeve_cfg: dict):
        if sym in self.positions:
            return
        if self.loss_cooldown_until.get(sym, 0) > int(time.time()):
            return
        p_win = self.win_rate_by_symbol.get(sym, 0.70)
        stop_pct = sleeve_cfg["stop_pct"] / 100
        target_pct = sleeve_cfg["target_pct"] / 100
        b = target_pct / stop_pct
        f = kelly_size(p_win, b, kelly_fraction=sleeve_cfg["kelly_fraction"])
        size = cap_by_risk(
            fraction=f, equity=equity,
            stop_distance_fraction=stop_pct,
            per_trade_risk_pct=self.policy["global_risk"]["per_trade_risk_pct"],
        )
        if size <= 0 or size < Decimal("1"):
            return
        size = min(size, equity * Decimal(str(sleeve_cfg["max_position_pct"] / 100)))

        proposed = ProposedTrade(
            sleeve="C", symbol=sym, side="long",
            notional_usdc=size, risk_usdc=size * Decimal(str(stop_pct)),
        )
        ok, reason = self.agent.allow_trade(proposed)
        if not ok:
            log.info(f"Sleeve C skip {sym}: {reason}")
            return

        # Layer 2: LLM reviewer veto (best-effort)
        sleeve_state = {
            "recent_trades": list(self._recent_trades_for(sym)),
            "win_rate_ewma": self.win_rate_by_symbol.get(sym, 0.70),
            "sleeve_dd_pct": 0.0,
            "policy_max_dd_pct": float(self.policy.get("global_risk", {}).get("max_drawdown_pct", 100)),
            "loss_cooldown_active": self.loss_cooldown_until.get(sym, 0) > int(time.time()),
        }
        market_snapshot = {"symbol": sym, "px": float(ref_price), "sigma": float(sigma)}
        try:
            ok2, reason2, _src = await self.agent.review_trade(proposed, sleeve_state, market_snapshot)
        except Exception as e:
            log.warning(f"Sleeve C reviewer call failed: {e} — proceeding")
            ok2 = True
        if not ok2:
            log.info(f"Sleeve C reviewer veto {sym}: {reason2}")
            return

        token_addr = self._token_address(sym)
        usdc_addr = self._token_address("USDC")
        pool_fee = self.pancake.best_pool_fee(usdc_addr, token_addr, [100, 500, 2500, 10000])
        amount_in = int(size * Decimal(10**6))
        min_out = int(amount_in / Decimal(str(ref_price)) * Decimal("0.997"))
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
            sleeve="C", symbol=sym, side="long",
            notional_usdc=size, risk_usdc=size * Decimal(str(stop_pct)),
            entry_ts=int(time.time()), entry_price=Decimal(str(ref_price)),
            stop_price=Decimal(str(ref_price * (1 - stop_pct))),
            tp_price=Decimal(str(ref_price * (1 + target_pct))),
        )
        self.positions[sym] = pos
        self.portfolio.add_position(f"C:{sym}", pos)
        log.info(f"Sleeve C: opened {sym} @ {ref_price:.4f} size=${size} z={-ref_price/sigma:.2f}")

    async def _monitor_open_positions(self, equity: Decimal, sleeve_cfg: dict):
        max_hold = 6 * 3600
        for sym, pos in list(self.positions.items()):
            try:
                quote = await self.cmc.quotes_latest([sym])
                px = Decimal(str(quote["data"][sym]["quote"]["USD"]["price"]))
            except Exception as e:
                log.warning(f"Sleeve C monitor {sym}: cmc fail {e}")
                continue

            reason = None
            if px >= pos.tp_price:
                reason = "tp_hit"
            elif px <= pos.stop_price:
                reason = "stop_hit"
            elif (int(time.time()) - pos.entry_ts) > max_hold:
                reason = "time_stop"
            if reason:
                self._close(sym, px, reason)

    def _close(self, sym: str, exit_price: Decimal, reason: str):
        pos = self.positions.pop(sym, None)
        if not pos:
            return
        pnl = self.portfolio.close_position(f"C:{sym}", exit_price=exit_price, reason=reason)
        win = 1 if pnl > 0 else 0
        prev = self.win_rate_by_symbol.get(sym, 0.70)
        self.win_rate_by_symbol[sym] = 0.9 * prev + 0.1 * win
        if pnl < 0 and reason in ("stop_hit", "time_stop"):
            self.loss_cooldown_until[sym] = int(time.time()) + self.loss_cooldown_s

    def _token_address(self, symbol: str) -> str:
        return token_address(self.cfg, symbol)

    def _recent_trades_for(self, sym: str, n: int = 20) -> list[dict]:
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
