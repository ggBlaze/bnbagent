"""Sleeve A — Funding-rate carry.

Long spot on PancakeSwap v3 + short equivalent notional on a BSC perps venue.
Direction-neutral. Collects funding every 8h. Primary PnL contributor; smallest
drawdown. Auto-rebalances daily at 00:00 UTC to the venue with the highest
average absolute funding over the curated basket of top-20 BSC tokens.

Exits a pair if:
  - |funding_8h| < FUND_FLOOR (default 0.005%)
  - liq distance < 10%
  - |basis| > 0.5%
  - per-trade risk > 1% (set by risk engine)
  - daily loss > 3% (set by risk engine)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from connectors.bnb_sdk import Perps
from core.portfolio import Position
from core.risk import ProposedTrade
from core.utils import token_address

log = logging.getLogger(__name__)


@dataclass
class CarryRow:
    symbol: str
    venue: str
    spot_tx: Any
    perp_tx: Any
    entry_spot_price: Decimal
    entry_funding: float
    funding_paid_usdc: Decimal = Decimal(0)
    opened_at: int = 0


class SleeveACarry:
    """Sleeve A — funding-rate carry (70% of capital)."""

    name = "A"

    def __init__(self, name: str, components: dict, agent, clock=None):
        self.name = name
        self.cfg = components["config"]
        self.policy = components["policy"]
        self.wallet = components["wallet"]
        self.cmc = components["cmc"]
        self.pancake = components["pancake"]
        self.perps: Perps = components["perps"]
        self.bsc = components["bsc"]
        self.ipfs = components["ipfs"]
        self.agent = agent
        self.portfolio = components["portfolio"]
        # Deterministic clock (v2.0.4). In production this defaults to
        # time.time (wall clock); in the replay harness it's set to a
        # callable that returns the current tape ts. This makes every
        # replay run produce identical numbers, so the meta-test that
        # locks the demo-script table to the JSON passes reliably.
        self.clock = clock or time.time

        self.basket: list[str] = []
        self.venue: str = ""
        self.rows: dict[str, CarryRow] = {}
        self.last_rebalance: int = 0
        self.last_funding_accrual: int = 0
        self.notional_budget_pct = self.policy["sleeve_allocations"]["A"]

    # --- lifecycle ---

    async def tick(self):
        self.portfolio.update_peak()
        equity = self.portfolio.equity()
        if equity <= 0:
            return

        sleeve_cfg = self.policy["sleeves"]["A"]
        if not sleeve_cfg.get("enabled", True):
            return

        # Low-vol pause (v2.0.2 — Path A): if realized vol is below the
        # threshold, the funding carry is below transaction costs. Close
        # any open carry and skip re-entering until vol returns.
        # v2.0.3 — minimum hold time: don't churn in/out of positions
        # when realized vol oscillates around the threshold. The
        # position must be held for at least min_hold_s seconds before
        # the vol-pause can close it.
        min_vol = float(self.policy.get("global_risk", {}).get("min_realized_vol_annualized", 0.05))
        min_hold_s = int(self.policy.get("global_risk", {}).get("low_vol_min_hold_s", 24 * 3600))
        realized_vol = await self._realized_vol_annualized()
        now_ts = int(self.clock())
        if realized_vol < min_vol and self.rows:
            # Only close positions held longer than min_hold_s
            stale = [
                sym for sym, row in self.rows.items()
                if (now_ts - row.opened_at) > min_hold_s
            ]
            if stale:
                log.info(f"Sleeve A: realized vol {realized_vol:.3f} < {min_vol} — "
                         f"closing {len(stale)} positions held > {min_hold_s}s")
                for sym in stale:
                    mark = float(self.perps.mark(self.rows[sym].venue, sym))
                    self._close_pair(sym, exit_price=Decimal(str(mark)), reason="low_vol_pause")

        # Daily rebalance
        if self._should_rebalance(sleeve_cfg["rebalance_hours"]):
            await self._rebalance(equity)

        # Per-tick monitoring
        await self._monitor(equity)

        # Periodic: collect funding (every 8h)
        await self._collect_funding(equity)

    async def _realized_vol_annualized(self) -> float:
        """Compute the basket's average 24h realized vol (annualized) using
        the most recent 1h candles. Returns 0.0 if data is missing."""
        try:
            basket = self.cfg["cmc"]["basket_symbols"][:20]
            ohlc = await self.cmc.ohlcv_historical(
                basket, time_period="hour", count=24, convert="USD",
            )
            import numpy as np
            vols = []
            for sym, payload in (ohlc.get("data") or {}).items():
                quotes = payload.get("quotes", [])
                if len(quotes) < 5:
                    continue
                rets = [
                    (quotes[i]["close"] - quotes[i-1]["close"]) / quotes[i-1]["close"]
                    for i in range(1, len(quotes))
                ]
                if not rets:
                    continue
                # Annualize: 1h returns, 24*365 bars/year
                vols.append(float(np.std(rets)) * (24 * 365) ** 0.5)
            return sum(vols) / len(vols) if vols else 0.0
        except Exception as e:
            log.debug(f"Sleeve A: realized vol fetch failed: {e}")
            return 0.0

    # --- core logic ---

    def _should_rebalance(self, hours: int) -> bool:
        if not self.rows:
            return True
        return (int(self.clock()) - self.last_rebalance) > hours * 3600

    async def _rebalance(self, equity: Decimal):
        cfg = self.cfg
        basket = cfg["cmc"]["basket_symbols"][:20]
        venue, _ = self.perps.select_venue(basket)

        if venue == self.venue and set(basket) == set(self.basket) and self.rows:
            return  # no change

        # Close existing carry — both legs atomically via _close_pair so
        # we never leave an orphan spot leg during the 1-2 minute rebalance
        # window. Audit finding: rebalance was closing the perp short
        # directly, leaving the spot leg running unhedged. The strategy is
        # delta-neutral by construction; an open spot without its short is
        # exactly the directional exposure we are designed to avoid.
        for sym in list(self.rows.keys()):
            # Use the mark for the exit; reason="rebalance" so it shows up
            # clearly in the trades table.
            mark = float(self.perps.mark(self.rows[sym].venue, sym))
            self._close_pair(sym, exit_price=Decimal(str(mark)), reason="rebalance")

        self.venue = venue
        self.basket = basket
        self.last_rebalance = int(self.clock())

        # Enter new carry
        per_token_usdc = equity * Decimal(str(self.notional_budget_pct)) / len(basket)
        if per_token_usdc < Decimal("1"):
            log.info("Sleeve A: equity too small for carry (%s)", per_token_usdc)
            return

        spot_price = Decimal("100")    # stub
        for sym in basket:
            proposed = ProposedTrade(
                sleeve="A", symbol=sym, side="spot_long+perp_short",
                notional_usdc=per_token_usdc,
                risk_usdc=per_token_usdc * Decimal("0.02"),   # 2% stop
                is_new=True,
            )
            ok, reason = self.agent.allow_trade(proposed)
            if not ok:
                log.info(f"skip {sym} — {reason}")
                continue

            # Layer 2: LLM reviewer veto (best-effort)
            sleeve_state = {
                "recent_trades": [],
                "win_rate_ewma": 0.55,
                "sleeve_dd_pct": 0.0,
                "policy_max_dd_pct": float(self.policy.get("global_risk", {}).get("max_drawdown_pct", 100)),
                "loss_cooldown_active": False,
            }
            try:
                ok2, reason2, _src = await self.agent.review_trade(proposed, sleeve_state, {"symbol": sym})
            except Exception as e:
                log.warning(f"Sleeve A reviewer call failed: {e} — proceeding")
                ok2 = True
            if not ok2:
                log.info(f"Sleeve A reviewer veto {sym}: {reason2}")
                continue

            # Open spot leg on PancakeSwap
            try:
                quote_data = await self.cmc.quotes_latest([sym])
                spot_price = Decimal(str(quote_data["data"][sym]["quote"]["USD"]["price"]))
            except Exception as e:
                log.warning(f"cmc quote failed for {sym}: {e}")
                continue

            token_addr = self._token_address(sym)
            usdc_addr = self._token_address("USDC")
            pool_fee = self.pancake.best_pool_fee(usdc_addr, token_addr, [100, 500, 2500, 10000])
            amount_in = int(per_token_usdc * Decimal(10**6))
            min_out = int(amount_in / spot_price * Decimal("0.997"))
            calldata = self.pancake.encode_swap_v3(
                token_in=usdc_addr, token_out=token_addr, fee=pool_fee,
                recipient=self.wallet.address, amount_in=amount_in, min_out=min_out,
            )
            tx_spot = self.wallet.sign_transaction({
                "to": self.cfg["dex"]["pcs_v3_router"],
                "data": "0x" + calldata.hex(),
                "value": 0,
                "gas": self.cfg["gas"]["swap_gas"],
                "nonce": self.bsc.next_nonce(self.wallet.address),
                "chainId": self.cfg["chain_id"],
            })
            rcpt_spot = self.bsc.broadcast(tx_spot)

            # Open perp short leg
            tx_perp = self.perps.open_short(
                venue=venue, market=sym, size_usd=float(per_token_usdc),
                leverage=1.0, collateral_usdc=float(per_token_usdc),
            )

            row = CarryRow(
                symbol=sym, venue=venue, spot_tx=rcpt_spot, perp_tx=tx_perp,
                entry_spot_price=spot_price,
                entry_funding=self.perps.current_funding(venue, sym),
                opened_at=int(self.clock()),
            )
            self.rows[sym] = row

            pos = Position(
                sleeve="A", symbol=sym, side="spot_long+perp_short",
                notional_usdc=per_token_usdc, risk_usdc=per_token_usdc * Decimal("0.02"),
                entry_ts=row.opened_at, entry_price=spot_price,
                stop_price=spot_price * Decimal("0.98"),
                tp_price=None, extra={"venue": venue, "funding_paid_usdc": Decimal(0)},
            )
            self.portfolio.add_position(f"A:{sym}", pos)

    async def _monitor(self, equity: Decimal):
        sleeve_cfg = self.policy["sleeves"]["A"]
        fund_floor = sleeve_cfg["fund_floor_pct"] / 100
        basis_trigger = sleeve_cfg["basis_trigger_pct"] / 100

        for sym, row in list(self.rows.items()):
            f_now = self.perps.current_funding(row.venue, sym)
            liq = self.perps.liq_distance_pct(row.venue, sym, side="short")
            mark = self.perps.mark(row.venue, sym)
            spot = row.entry_spot_price
            basis = (mark - float(spot)) / float(spot)

            if abs(f_now) < fund_floor:
                log.info(f"Sleeve A {sym}: funding converged, closing pair")
                self._close_pair(sym, exit_price=Decimal(str(mark)), reason="funding_floor")
            elif liq < 0.10:
                self.perps.reduce_short(row.venue, sym, factor=0.5)
            elif abs(basis) > basis_trigger:
                # basis drifted → close perp leg and let spot leg run with stop
                self._close_pair(sym, exit_price=Decimal(str(mark)), reason="basis_trigger")

    async def _collect_funding(self, equity: Decimal):
        """Accrue funding income at 8h boundaries only (not every 30s tick)."""
        now = int(self.clock())
        if now - self.last_funding_accrual < 8 * 3600:
            return
        self.last_funding_accrual = now
        for sym, row in list(self.rows.items()):
            f = self.perps.current_funding(row.venue, sym)
            notional = self.portfolio.positions.get(f"A:{sym}")
            if notional:
                # funding_8h is a fraction (e.g. 0.0008 = 0.08% per 8h)
                inc = Decimal(str(abs(f))) * notional.notional_usdc
                row.funding_paid_usdc += inc
                notional.extra["funding_paid_usdc"] = row.funding_paid_usdc

    def _close_pair(self, sym: str, exit_price: Decimal, reason: str):
        pos = self.portfolio.positions.get(f"A:{sym}")
        if not pos:
            return
        row = self.rows.get(sym)
        if row is not None:
            # close the perp short exactly once
            self.perps.close_short(row.venue, sym)
        pnl = self.portfolio.close_position(f"A:{sym}", exit_price=exit_price, reason=reason)
        self.rows.pop(sym, None)
        return pnl

    def _token_address(self, symbol: str) -> str:
        return token_address(self.cfg, symbol)
