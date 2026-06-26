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
from core.eligibility import filter_universe, is_eligible
from core.portfolio import Position
from core.risk import ProposedTrade, cap_by_max_notional
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
        self.data_source = components["data_source"]
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
        the most recent 1h candles. Returns 0.0 if data is missing.

        v2.0.8-M4: the v2.0.7 fallback was 0.0, which is BELOW the
        low-vol-pause threshold (default 0.05). A single CMC blip
        (rate limit, network blip) would force-close a healthy carry
        book. The new fallback is min_vol + a small buffer, so an
        outage looks like 'vol is fine' to the strategy and the
        existing positions are preserved.

        Operators can override via policy.global_risk.
        min_realized_vol_annualized + a default 0.01 buffer.
        """
        try:
            # v2.1.4: filter to BNB HACK 2026 eligible BEP-20 universe.
            # In strict mode (default during the contest), non-eligible
            # symbols are dropped silently + logged. The carry sleeve is
            # a *delta-neutral basket* — we only need enough symbols for
            # the venue's funding comparison to be meaningful, so losing
            # a few to eligibility is fine.
            basket = filter_universe(self.cfg["cmc"]["basket_symbols"][:20])
            ohlc = await self.data_source.ohlcv_historical(
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
            return sum(vols) / len(vols) if vols else self._vol_fallback()
        except Exception as e:
            log.debug(f"Sleeve A: realized vol fetch failed: {e}")
            return self._vol_fallback()

    def _vol_fallback(self) -> float:
        """v2.0.8-M4: when the vol fetch fails or returns no data,
        return a value ABOVE the low-vol-pause threshold so the
        existing carry positions are NOT force-closed. Default buffer
        of 0.01 (1%) above the configured min_vol. Operators can
        override the buffer via policy.global_risk.vol_fallback_buffer.
        """
        min_vol = float(self.policy.get("global_risk", {}).get(
            "min_realized_vol_annualized", 0.05))
        buffer = float(self.policy.get("global_risk", {}).get(
            "vol_fallback_buffer", 0.01))
        return min_vol + buffer

    # --- core logic ---

    def _should_rebalance(self, hours: int) -> bool:
        if not self.rows:
            return True
        return (int(self.clock()) - self.last_rebalance) > hours * 3600

    async def _rebalance(self, equity: Decimal):
        cfg = self.cfg
        # v2.1.4: filter to BNB HACK 2026 eligible BEP-20 universe
        # before doing anything that depends on basket size (per-token
        # notional = equity * 70% / len(basket)). Truncating to [:20]
        # AFTER filtering would silently drop eligible symbols; do the
        # filter first, then slice.
        basket = filter_universe(cfg["cmc"]["basket_symbols"])[:20]
        if not basket:
            log.warning("Sleeve A: eligible basket is empty (all symbols filtered). Skipping rebalance.")
            return
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
        # v2.x.x: clamp per-token notional to the absolute per-trade cap
        # from policy (editable from the dashboard as `cfg-notional`).
        # Without this, the math gives 100*0.7/20 = 3.50 USDC per token
        # and every proposal dies at the risk gate with "per-trade
        # notional cap: 3.5000 USDC > 1.0000 USDC cap".
        per_token_usdc = cap_by_max_notional(per_token_usdc, self.policy)
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
                quote_data = await self.data_source.quotes_latest([sym])
                spot_price = Decimal(str(quote_data["data"][sym]["quote"]["USD"]["price"]))
            except Exception as e:
                log.warning(f"cmc quote failed for {sym}: {e}")
                continue

            token_addr = self._token_address(sym)
            usdc_addr = self._token_address("USDC")
            pool_fee = self.pancake.best_pool_fee(usdc_addr, token_addr, [100, 500, 2500, 10000])
            # v2.2.3: guard against -1 (no pool found). The fee is encoded
            # as a uint24 in the calldata; -1 throws ValueOutOfBounds and
            # kills the whole sleeve tick. The earlier v2.2.0 fix only
            # covered _submit_onchain_swap / _submit_close_swap — sleeve A
            # was still calling encode_swap_v3 with the unchecked value.
            if pool_fee is None or pool_fee < 0:
                log.info(f"Sleeve A skip {sym}: no working pool for USDC->{sym}")
                continue
            # v2.2.4 (decimals bugfix): USDC has 18 decimals on BSC
            # mainnet (was hardcoded as 6, producing dust swaps and
            # burning ~$30 of BNB in 60+ spam txs). Use the helper.
            from core.utils import token_decimals
            usdc_decimals = token_decimals("USDC", self.cfg)
            amount_in = int(per_token_usdc * Decimal(10 ** usdc_decimals))
            min_out = int(amount_in / spot_price * Decimal("0.997"))
            calldata = self.pancake.encode_swap_v3(
                token_in=usdc_addr, token_out=token_addr, fee=pool_fee,
                recipient=self.wallet.address, amount_in=amount_in, min_out=min_out,
            )
            # v2.2.3: reconcile the nonce cache from chain before signing,
            # so a fresh boot doesn't sign with nonce 0 on a wallet that
            # already has txs (the chain rejects with 'nonce too low').
            # The v2.2.0 fix only covered _submit_onchain_swap /
            # _submit_close_swap; sleeve A's rebalance does its own
            # sign_transaction so it needs the same guard.
            try:
                self.bsc.resync_nonce(self.wallet.address)
            except Exception as e:
                log.warning(f"Sleeve A resync_nonce failed: {e}")
            # v2.0.8-H4: honor fees.max_gas_price_gwei from the user-signed
            # policy. Sleeve A is the spot leg of a carry; if BSC gas spikes
            # and the tx would have to wait 30+ minutes, the funding carry
            # is already booked by both legs closing in the same tick. So
            # we wrap the sign in a try/except that logs gas_too_high_skip
            # and lets the next tick re-evaluate.
            try:
                tx_spot = self.wallet.sign_transaction(
                    {
                        "to": self.cfg["dex"]["pcs_v3_router"],
                        "data": "0x" + calldata.hex(),
                        "value": 0,
                        "gas": self.cfg["gas"]["swap_gas"],
                        "nonce": self.bsc.next_nonce(self.wallet.address),
                        "chainId": self.cfg["chain_id"],
                    },
                    chain_id=self.cfg["chain_id"],
                    max_gas_price_gwei=self._max_gas_gwei(),
                )
            except Exception as e:
                if "gas price" in str(e).lower() and "exceeds" in str(e).lower():
                    log.info(f"Sleeve A spot {sym}: gas_too_high_skip — {e}")
                    continue
                raise
            # v2.3.8: pre-flight BNB-for-gas check. Without this, every
            # tick signs and broadcasts even when the wallet can't cover
            # gas, the chain rejects with "insufficient funds for gas",
            # and we spam the journal + burn the ~0.0001 BNB the chain
            # may have charged for the failed mempool entry. The
            # 1.2× buffer absorbs in-flight gas-price spikes between
            # check and broadcast.
            try:
                ok, gas_reason = self.bsc.has_gas(
                    self.wallet.address,
                    self.cfg["gas"]["swap_gas"],
                )
            except Exception as gas_check_err:
                # Don't block trades if the gas check itself fails to
                # query the chain — broadcast will surface the real error.
                log.warning(f"Sleeve A gas check failed for {sym}: {gas_check_err}")
                ok, gas_reason = True, "gas_check_skipped"
            if not ok:
                log.info(f"Sleeve A spot {sym}: {gas_reason}")
                continue
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

    def _max_gas_gwei(self) -> float | None:
        """Read fees.max_gas_price_gwei from the user-signed policy.

        v2.0.8-H4: if not set, the wallet uses no cap (legacy behavior).
        If set, sign_transaction refuses to sign above the cap.
        """
        v = (self.policy.get("fees") or {}).get("max_gas_price_gwei")
        return float(v) if v is not None else None
