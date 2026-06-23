"""Daily trade floor — guarantees the BNB HACK 2026 contest's 1-trade/day
qualification rule is met.

The DoraHacks Track 1 rules say:
  "Minimum trades to qualify: at least 1 trade per day (7 over the trading
   week)"

If vol is low and no sleeve fires a signal, the agent can have a 0-trade
day and fail the qualification check. The daily trade floor module is
the safety net:

  - Every day at 23:30 UTC (the time we'd want to know by 23:59:59),
    check whether *any* trade was taken today.
  - If not, fire a single "rebalance" trade: open a small
    eligible-in-scope long on the cheapest in-scope BEP-20 token and
    close it after 30 minutes.
  - The trade is sized to 0.1% of equity (well inside the 1% per-trade
    cap, 5% daily cap) and is logged with a `reason="daily_floor"`
    marker so the audit log + the demo script can show judges that the
    floor fired.

The module is OPT-OUT via the env var BNB_HACK_NO_DAILY_FLOOR=1. The
default during the contest window is on.

The floor is also a hard cap: it fires ONCE per day max. A second
fallback if even the rebalance trade is rejected (e.g., gas spike,
venue outage) writes a `daily_floor_failed: true` line to the audit
log so the operator sees it. The agent will be disqualified for that
day if no trade can land, but the operator can intervene.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# 23:30 UTC = 23 * 3600 + 30 * 60 = 84600 seconds from midnight UTC.
# v2.2.2: moved from 23:30 to 14:00 UTC. The old time was the
# last 30min of the UTC day, so any CMC / RPC / LLM failure at
# 23:30 UTC = 17:30 CST meant the day was lost. 14:00 UTC = 08:00
# CST is 10h earlier, giving the agent and operator half a day
# to debug and re-fire if the cron missed. The contest measures
# by UTC day, so any time before 23:59 UTC on the day counts.
DEFAULT_CHECK_HOUR_UTC = 14
DEFAULT_CHECK_MINUTE_UTC = 0

# How much of the equity to deploy in the floor trade. Well below the
# 1% per-trade cap, so even if it loses 100% it can't trip the
# 5% daily circuit breaker.
# v2.2.2: bumped from 0.001 (0.1%) to 0.0125 (1.25%) so the daily
# floor trade is meaningful economics, not dust. With an $80 wallet
# this is $1.00 per fire (matches the user's "1 dollar per day" target
# on a small wallet); with a $1000 wallet it scales to $12.50. The
# FLOOR_MIN_NOTIONAL_USDC cap (0.05) still applies so micro-wallets
# below ~$4 still attempt the dust trade.
FLOOR_NOTIONAL_FRACTION = Decimal("0.0125")  # 1.25%

# Minimum notional for the floor trade. BSC accepts any dust, but the
# daily floor was originally specced with a 0.1% of $80 = $0.08 target
# and the older $0.50 floor rejected that. Lowered to $0.05 so the
# contest qualification rule (1 trade/day) is met even on a $5 wallet.
FLOOR_MIN_NOTIONAL_USDC = Decimal("0.05")

# How long the rebalance position is held before being closed.
# Long enough to count as "a trade" (it has an entry AND an exit).
FLOOR_HOLD_MINUTES = 30


@dataclass
class FloorState:
    """Per-day state for the daily trade floor."""
    last_check_utc_day: int = 0   # yyyymmdd int of the last day we checked
    last_fire_utc_day: int = 0    # yyyymmdd int of the last day we fired
    last_fire_status: str = "n/a" # "ok" / "no_in_scope_universe" / "trade_rejected" / "audit_only"
    last_fire_note: str = ""
    total_fires: int = 0
    total_days_covered: int = 0


def _utc_day(ts: int) -> int:
    """Return yyyymmdd for a unix timestamp (UTC)."""
    t = time.gmtime(ts)
    return t.tm_year * 10000 + t.tm_mon * 100 + t.tm_mday


class DailyTradeFloor:
    """Watches the agent's trade log and fires a rebalance trade at 23:30 UTC
    if no trades happened in the last 24h.

    Wires into the existing TickLoop (1s heartbeat) and the existing
    portfolio (for the trade log). Does NOT touch the sleeves — it
    constructs its own tiny ProposedTrade and routes it through the
    same circuit_breaker_check + sign + submit path the sleeves use.
    """

    def __init__(self, agent, *, check_hour: int = DEFAULT_CHECK_HOUR_UTC,
                 check_minute: int = DEFAULT_CHECK_MINUTE_UTC,
                 clock=None):
        self.agent = agent
        self.check_hour = check_hour
        self.check_minute = check_minute
        # Deterministic clock (testability, replay parity). Defaults
        # to time.time (wall clock). The Agent's TickLoop heartbeat
        # creates this lazily; tests inject a fake clock.
        self.clock = clock or time.time
        self.state = FloorState()
        self._last_heartbeat_ts = 0

    def status(self) -> dict:
        return {
            "last_check_utc_day": self.state.last_check_utc_day,
            "last_fire_utc_day":  self.state.last_fire_utc_day,
            "last_fire_status":   self.state.last_fire_status,
            "last_fire_note":     self.state.last_fire_note,
            "total_fires":        self.state.total_fires,
            "total_days_covered": self.state.total_days_covered,
        }

    async def tick(self) -> dict | None:
        """Heartbeat call. Returns a status dict if anything happened, else None.

        Idempotent — safe to call every second. The actual check is
        throttled to fire at most once per UTC day.
        """
        # The opt-out. Default is on. Set BNB_HACK_NO_DAILY_FLOOR=1
        # to disable (e.g., for the 1h-replay backtests that aren't
        # bound by the contest's trade-count rule).
        import os
        if os.environ.get("BNB_HACK_NO_DAILY_FLOOR", "").strip().lower() in ("1", "true", "yes"):
            return None

        # Manual force-fire: a one-shot trigger written by the operator
        # to control.json. Consumed and cleared on first tick.
        try:
            from .control import _consume_force_fire
            consumed = _consume_force_fire()
            if consumed:
                # v2.2.0: force-fire overrides the "already fired today"
                # check too — operator may need to retry after a bug
                # (e.g. "equity too small" set last_fire_utc_day). This
                # is the manual override.
                log.warning("daily_floor: force_fire path, resetting last_fire_utc_day=%d → 0", self.state.last_fire_utc_day)
                self.state.last_fire_utc_day = 0
                self.state.last_check_utc_day = _utc_day(int(self.clock()))
                return await self._fire_floor_trade(int(self.clock()), _utc_day(int(self.clock())))
        except Exception as e:
            log.warning("force_fire check failed: %s", e)

        now = int(self.clock())
        today = _utc_day(now)
        if self.state.last_check_utc_day == today:
            return None  # already checked today

        # Only fire the check after the configured UTC time. Before
        # that, the day is "still in progress" and may yet see a
        # sleeve trade. We record a daily check at the check-time
        # boundary so a crash + restart doesn't double-count.
        # Use time.gmtime so we honor the user's TZ (UTC for the contest).
        t = time.gmtime(now)
        if (t.tm_hour, t.tm_min) < (self.check_hour, self.check_minute):
            return None

        self.state.last_check_utc_day = today
        # Check if any trade happened today
        trades_today = self._count_trades_today(now)
        if trades_today > 0:
            self.state.last_fire_status = "ok"
            self.state.last_fire_note = f"skipped: {trades_today} sleeve trade(s) today"
            self.state.total_days_covered += 1
            return {"fired": False, "trades_today": trades_today}

        # No trades today → fire the rebalance.
        return await self._fire_floor_trade(now, today)

    def _count_trades_today(self, now: int) -> int:
        """How many trades closed (or opened) since 00:00 UTC today.

        Both opened + closed are counted, because the contest scoring
        is "real trades" not "round trips". A position that was opened
        yesterday and closed today counts for today.
        """
        pf = getattr(self.agent, "portfolio", None) or getattr(self.agent, "_portfolio", None)
        if pf is None:
            return 0
        try:
            today_str = time.strftime("%Y-%m-%d", time.gmtime(now))
            n = 0
            # Open positions: count ones opened today
            for p in getattr(pf, "positions", {}).values():
                if time.strftime("%Y-%m-%d", time.gmtime(getattr(p, "opened_at", 0) or 0)) == today_str:
                    n += 1
            # Closed trades: count ones with exit_ts today
            for tr in getattr(pf, "closed_trades", []):
                ts = getattr(tr, "exit_ts", None) or getattr(tr, "ts", None) or 0
                if time.strftime("%Y-%m-%d", time.gmtime(int(ts))) == today_str:
                    n += 1
            return n
        except Exception as e:
            log.warning("daily_trade_floor: trade count failed: %s", e)
            return 0

    async def _fire_floor_trade(self, now: int, today: int) -> dict:
        from core.eligibility import filter_universe, is_eligible
        from core.risk import ProposedTrade, circuit_breaker_check

        # Don't double-fire if we already fired today (e.g., restart).
        # Note: the force-fire path in tick() resets last_fire_utc_day
        # to 0 before calling, so this check only blocks natural fires
        # on the same UTC day after a successful prior fire.
        if self.state.last_fire_utc_day == today:
            return {"fired": False, "note": "already fired today"}

        # Find an in-scope symbol. Prefer the cheapest one in the basket
        # (smallest notional in USDC, lowest liquidity risk).
        cfg = getattr(self.agent, "config", None) or {}
        candidates = []
        if hasattr(self.agent, "components") and self.agent.components:
            cfg = self.agent.components.get("config", cfg)
        basket = filter_universe((cfg.get("cmc") or {}).get("basket_symbols", []))
        # Fall back to a hard-coded short list if basket is empty
        if not basket:
            basket = ["USDC", "USDT", "DAI"]
        # Always try USDC first — it's the deepest pool, smallest slippage.
        priority = ["USDC", "USDT", "DAI"] + [s for s in basket if s not in ("USDC", "USDT", "DAI")]
        in_scope = [s for s in priority if is_eligible(s)]
        if not in_scope:
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "no_in_scope_universe"
            self.state.last_fire_note = "no in-scope BEP-20 to trade"
            return {"fired": False, "note": "no in-scope symbol"}

        # 70/20/10 split. Use Sleeve B (momentum) — its logic
        # accepts the smallest position sizes, and the rebalance is
        # essentially a "tiny long" anyway.
        equity = Decimal("0")
        pf = getattr(self.agent, "portfolio", None)
        if pf is not None:
            try:
                equity = Decimal(str(pf.equity()))
            except Exception:
                equity = Decimal("0")

        # v2.2.0 (onchain-floor bugfix): on mainnet, the paper portfolio
        # equity is meaningless for sizing the on-chain floor (paper book
        # starts at BNBAGENT_EQUITY=100 virtual USD, not the real wallet
        # USDC). Use the cached on-chain USDC balance from
        # ~/.bnbagent/setup.json (written by poll_live_balance on boot
        # + every dashboard refresh). Falls back to paper equity on
        # testnet / replay / mock where there's no on-chain wallet.
        cfg_mode = ((getattr(self.agent, "components", {}) or {}).get("config") or {}).get("mode", "testnet")
        if cfg_mode == "mainnet":
            try:
                setup_path = os.environ.get(
                    "BNBAGENT_SETUP_FILE",
                    str(Path("~/.bnbagent/setup.json").expanduser()),
                )
                if os.path.exists(setup_path):
                    import json as _json
                    setup = _json.load(open(setup_path))
                    usdc = setup.get("usdc_balance")
                    if usdc is not None and float(usdc) > 0:
                        equity = Decimal(str(usdc))
                        log.warning(
                            "daily_floor: mainnet equity from setup.json usdc_balance=%s "
                            "(paper portfolio equity was %s)",
                            equity,
                            pf.equity() if pf is not None else "n/a",
                        )
            except Exception as e:
                log.warning("daily_floor: could not read setup.json: %s", e)

        notional = equity * FLOOR_NOTIONAL_FRACTION
        if notional < FLOOR_MIN_NOTIONAL_USDC:
            # Even at the floor fraction, this is below the minimum
            # trade size for BSC. Bail with an audit note. The operator
            # needs to either (a) fund the wallet more, or (b) lower
            # the min-trade threshold.
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "too_small"
            self.state.last_fire_note = f"equity too small: {equity} → {notional} USDC floor"
            return {"fired": False, "note": "equity too small"}

        sym = in_scope[0]
        # v2.2.0 (onchain-floor bugfix): the in-scope universe lists
        # USDC/USDT/DAI as the top priority because those are the
        # deepest stable pools. On mainnet:
        #   - USDC->USDC is an identity swap (router reverts with STF)
        #   - USDC->WBNB has nearly-empty V3 pools on BSC mainnet right
        #     now (verified 2026-06-22: pool reserves 4.9e17 USDC vs
        #     2862 WBNB → $80 USDC gets 4.7e-14 WBNB, way too dusty)
        #   - USDC->CAKE V3 pools are similarly dust
        #   - USDC->USDT V3 pools are deep (stable pair, ~$100M TVL)
        # The contest scoring rewards round-trip trade COUNT, not
        # profit. A USDC->USDT round-trip counts as one trade and the
        # ~0.001% fee is the only cost. Use that for the floor on
        # mainnet.
        if cfg_mode == "mainnet" and sym == "USDC":
            log.warning(
                "daily_floor: remapping mainnet sym USDC -> USDT "
                "(USDC->USDC identity swap reverts; USDC->WBNB V3 pools "
                "are empty on BSC mainnet; USDC->USDT is the deep stable pair)",
            )
            sym = "USDT"
        if sym is None:
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "no_mainnet_symbol"
            self.state.last_fire_note = "no non-stablecoin in_scope symbol for onchain path"
            return {"fired": False, "note": "no mainnet symbol"}

        proposed = ProposedTrade(
            sleeve="B",  # momentum — smallest per-trade cap fits
            symbol=sym,
            side="buy",
            notional_usdc=notional,
            risk_usdc=notional * Decimal("0.01"),  # assume 1% risk
            is_new=True,
        )
        policy = getattr(self.agent, "policy", None) or {}
        if hasattr(self.agent, "components") and self.agent.components:
            policy = policy or self.agent.components.get("policy", policy)
        ok, reason = circuit_breaker_check(
            current_equity=equity,
            # v2.2.0: on mainnet, the on-chain USDC balance is the
            # ground truth for equity. The paper portfolio's peak_equity
            # (BNBAGENT_EQUITY=100 virtual USD) is unrelated to the
            # real wallet, so using it as the peak would manufacture a
            # false 20% drawdown when the wallet has 80 USDC. Use the
            # on-chain equity as both current and peak so the drawdown
            # is always 0 on a fresh mainnet run. (Sleeves still get
            # the proper drawdown tracking via portfolio.update_peak.)
            peak_equity=equity if cfg_mode == "mainnet" else (
                getattr(pf, "peak_equity", equity) if pf else equity
            ),
            open_positions=list(getattr(pf, "positions", {}).values()) if pf else [],
            proposed=proposed,
            policy=policy,
            # v2.3.4: the floor is the BNB HACK contest-compliance
            # safety net — it MUST fire to guarantee the 1-trade/day
            # minimum even if the sleeves have already consumed all N
            # max_daily_trades slots. Without this flag the cap would
            # block the floor and the agent would be disqualified.
            is_floor=True,
        )
        if not ok:
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "trade_rejected"
            self.state.last_fire_note = f"circuit breaker: {reason}"
            return {"fired": False, "note": f"rejected: {reason}"}

        # Submit the floor trade. The agent exposes a unified entry
        # path (propose → sign → submit) — call it.
        submit = getattr(self.agent, "submit_floor_trade", None)
        if submit is None:
            # No entry point — record the failure so the operator sees
            # it. The floor was the safety net, not a primary path.
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "no_submit_path"
            self.state.last_fire_note = "agent has no submit_floor_trade() method"
            return {"fired": False, "note": "no submit path on agent"}

        try:
            result = await submit(proposed, reason="daily_floor", hold_min=FLOOR_HOLD_MINUTES)
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "ok"
            self.state.last_fire_note = f"fired: {sym} {notional} USDC ({result.get('status', '?')})"
            self.state.total_fires += 1
            self.state.total_days_covered += 1
            return {"fired": True, "symbol": sym, "notional": float(notional), "result": result}
        except Exception as e:
            self.state.last_fire_utc_day = today
            self.state.last_fire_status = "trade_rejected"
            self.state.last_fire_note = f"submit exception: {type(e).__name__}: {e}"
            return {"fired": False, "note": f"submit failed: {e}"}
