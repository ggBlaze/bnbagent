"""Risk engine — circuit_breaker_check, position sizing, Kelly fraction.

This is THE function called before every order. It is the only enforcement of
the user-signed policy. Every other module that wants to place a trade MUST
go through circuit_breaker_check() and respect its return.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

log = logging.getLogger(__name__)


def _eligibility_schema_version() -> str:
    """The schema_version of the loaded eligible_tokens.json.

    Lazy import to avoid a circular dep: core.eligibility imports nothing
    from core.risk, but core.risk only needs the schema version string
    for the error message, not the whole module.
    """
    from core.eligibility import schema_version
    return schema_version()


@dataclass
class ProposedTrade:
    sleeve: str
    symbol: str
    side: str
    notional_usdc: Decimal
    risk_usdc: Decimal
    is_new: bool = True


def circuit_breaker_check(
    *,
    current_equity: Decimal,
    peak_equity: Decimal,
    open_positions: list,                # list of Position-like objects
    proposed: ProposedTrade | None,
    policy: dict,
    day_start_equity: Decimal | None = None,
    day_breach_active_until: int = 0,
    now_ts: int | None = None,
    # v2.3.4: contest tuning knobs. `trades_opened_today` is the count of
    # OPENED positions since 00:00 UTC (from Portfolio.trades_opened_today_str);
    # `is_floor=True` is set by core/daily_trade_floor.py to exempt the
    # contest-compliance safety-net fire from the cap (the floor is the
    # BNB HACK 1-trade/day guarantee — if the sleeves already used all N
    # daily slots, the floor must still fire or the agent is disqualified).
    trades_opened_today: int = 0,
    is_floor: bool = False,
) -> tuple[bool, str]:
    """Returns (allow: bool, reason: str).

    Implements every rule in policy.yaml → global_risk, plus allowlist and
    sleeve-level position caps.
    """
    now_ts = now_ts or int(time.time())
    p = policy["global_risk"]

    # 0) Cooldown
    if now_ts < day_breach_active_until:
        return False, f"cooldown active until {day_breach_active_until}"

    # 0b) Hard kill-switch (set by dashboard / control endpoint)
    if policy.get("_kill_switch"):
        return False, f"kill switch engaged: {policy.get('_kill_reason', 'manual')}"

    # 0c) Live window gate (BNB HACK 2026 Track 1)
    #     If policy.global_risk.live_window_start is set, block all
    #     new orders before that timestamp. After live_window_end (if
    #     set) the agent also stops. Format: ISO-8601 in UTC, e.g.
    #     "2026-06-22T12:00:00Z". Missing fields = no gate. This is
    #     belt-and-suspenders for the kill switch: even if the kill
    #     gets accidentally disengaged, no order opens before the
    #     official live window — which is what the BNB HACK judges
    #     score against.
    live_start = p.get("live_window_start")
    if isinstance(live_start, str) and live_start:
        try:
            from datetime import datetime, timezone
            ls_ts = int(datetime.fromisoformat(live_start.replace("Z", "+00:00")).timestamp())
            if now_ts < ls_ts:
                from datetime import datetime as _dt, timezone as _tz
                ls_iso = _dt.fromtimestamp(ls_ts, tz=_tz.utc).isoformat()
                return False, f"before live window start {ls_iso}"
        except ValueError:
            log.warning("invalid live_window_start in policy: %r", live_start)
    live_end = p.get("live_window_end")
    if isinstance(live_end, str) and live_end:
        try:
            from datetime import datetime, timezone
            le_ts = int(datetime.fromisoformat(live_end.replace("Z", "+00:00")).timestamp())
            if now_ts >= le_ts:
                from datetime import datetime as _dt, timezone as _tz
                le_iso = _dt.fromtimestamp(le_ts, tz=_tz.utc).isoformat()
                return False, f"after live window end {le_iso}"
        except ValueError:
            log.warning("invalid live_window_end in policy: %r", live_end)

    # 0d) Daily-trade cap (contest tuning knob).
    # Block the proposed open if today's open count has already hit
    # policy.global_risk.max_daily_trades. Closes (`proposed.is_new=False`)
    # are not gated — closing positions doesn't consume the cap.
    # The daily-trade-floor (`is_floor=True`) bypasses this check so it
    # can guarantee the BNB HACK 1-trade/day minimum even when the sleeves
    # have already used all N slots. Missing/0 cap = no limit (the
    # policy.yaml default was 100 historically; an unset key also
    # disables the check so this is backwards-compatible).
    if (
        not is_floor
        and proposed is not None
        and proposed.is_new
    ):
        cap = p.get("max_daily_trades")
        if cap and trades_opened_today >= cap:
            return False, (f"daily trade cap reached: "
                           f"{trades_opened_today}/{cap} opened today")

    # 0e) Hard USDC notional cap per trade (v2.3.5). The per-trade-risk
    # check below only constrains risk_usdc (= notional × stop_distance);
    # it does NOT cap the trade's USDC size. On a small wallet this is
    # dangerous: sleeve A uses basis_trigger_pct=0.5 → very tight stops
    # → very small risk_usdc per $1 of notional → the per-trade-risk
    # cap (1% of equity = $1 risk on $100 paper, $0.80 on $80 wallet)
    # allows $20+ of notional through. That's how 14 simultaneous
    # spot-long buys of ETH/Cake/etc. drained the contest wallet.
    # The fix: an ABSOLUTE notional cap in USDC, separate from any %
    # of equity. Runs BEFORE per-trade-risk so it rejects over-sized
    # trades regardless of stop distance. Floor is exempt (the floor is
    # the contest-compliance safety net and is sized to 1.25% of equity,
    # so on a $100 wallet it's only $1.25 — well under the cap anyway,
    # but exempting it means a tiny-collision can't accidentally block
    # the daily-floor fire).
    if (
        not is_floor
        and proposed is not None
        and proposed.is_new
    ):
        max_notional = p.get("max_notional_usdc_per_trade")
        if max_notional is not None and float(max_notional) > 0:
            if float(proposed.notional_usdc) > float(max_notional):
                return False, (f"per-trade notional cap: "
                               f"{float(proposed.notional_usdc):.4f} USDC > "
                               f"{float(max_notional):.4f} USDC cap")

    # 1) Daily loss circuit breaker
    if day_start_equity is not None and day_start_equity > 0:
        dloss_pct = float((day_start_equity - current_equity) / day_start_equity * 100)
        if dloss_pct >= p["daily_loss_circuit_breaker_pct"]:
            return False, (f"daily loss {dloss_pct:.2f}% >= "
                           f"{p['daily_loss_circuit_breaker_pct']}% — circuit breaker")

    # 2) Max drawdown
    if peak_equity > 0:
        dd_pct = float((peak_equity - current_equity) / peak_equity * 100)
        if dd_pct >= p.get("max_drawdown_pct", 100):
            return False, f"drawdown {dd_pct:.2f}% >= {p['max_drawdown_pct']}%"

    # 3) Per-trade risk cap
    if proposed is not None and proposed.is_new and current_equity > 0:
        risk_pct = float(proposed.risk_usdc / current_equity * 100)
        if risk_pct > p["per_trade_risk_pct"]:
            return False, (f"per-trade risk {risk_pct:.2f}% > "
                           f"{p['per_trade_risk_pct']}%")

    # 4) Single-position cap (open + proposed)
    for pos in list(open_positions) + ([proposed] if proposed else []):
        if pos is None:
            continue
        if current_equity <= 0:
            break
        sz_pct = float(pos.notional_usdc / current_equity * 100)
        if sz_pct > p["max_single_position_pct"]:
            return False, (f"{getattr(pos, 'symbol', '?')} size {sz_pct:.2f}% > "
                           f"{p['max_single_position_pct']}%")

    # 5) Gross leverage
    gross = sum((abs(getattr(x, "notional_usdc", Decimal(0))) for x in open_positions), Decimal(0))
    if proposed is not None:
        gross += abs(proposed.notional_usdc)
    if current_equity > 0:
        lev = float(gross / current_equity)
        if lev > p["max_gross_leverage"]:
            return False, f"gross lev {lev:.2f}x > {p['max_gross_leverage']}x"

    # 6) Allowlist (symbol + venue) + BNB HACK 2026 eligibility
    if proposed is not None:
        al = policy["allowlist"]
        if proposed.symbol not in al["bsc_tokens"]:
            return False, f"{proposed.symbol} not in allowlist"
        # v2.1.4: defense-in-depth. Even if a sleeve forgets to call
        # filter_universe(), the risk engine is the last gate before
        # the order goes to TWAK for signing. In strict mode (the
        # default during the contest), this rejects the trade with a
        # clear reason. In soft mode, it just logs and lets it through.
        # The sleeves still do the filter at the universe level, so this
        # is a belt-and-suspenders check.
        from core.eligibility import is_eligible, _mode as _eligibility_mode
        if _eligibility_mode() == "strict" and not is_eligible(proposed.symbol):
            return False, f"{proposed.symbol} not in BNB HACK eligible 149 (schema={_eligibility_schema_version()})"

    # 7) Sleeve position cap
    if proposed is not None and proposed.sleeve in policy["sleeves"]:
        sleeve_cfg = policy["sleeves"][proposed.sleeve]
        cap = sleeve_cfg.get("max_position_pct")
        if cap and current_equity > 0:
            sz_pct = float(proposed.notional_usdc / current_equity * 100)
            if sz_pct > cap:
                return False, (f"sleeve {proposed.sleeve} cap: {sz_pct:.2f}% > {cap}%")

    # 8) Sleeve enabled
    if proposed is not None and proposed.sleeve in policy["sleeves"]:
        if not policy["sleeves"][proposed.sleeve].get("enabled", True):
            return False, f"sleeve {proposed.sleeve} disabled in policy"

    return True, "ok"


# --- Kelly + position-size helpers ---

def kelly_size(
    p_win: float,
    b_ratio: float,                         # reward / risk
    kelly_fraction: float = 0.25,
) -> float:
    """Quarter-Kelly (or any fraction) position size as fraction of bankroll.

    p_win:  estimated win probability (0..1)
    b:      reward/risk ratio (e.g. 1.5 for 3% TP on 2% stop)
    """
    if p_win <= 0 or p_win >= 1 or b_ratio <= 0:
        return 0.0
    f_full = (p_win * b_ratio - (1 - p_win)) / b_ratio
    return max(0.0, kelly_fraction * f_full)


def cap_by_risk(fraction: float, equity: Decimal, stop_distance_fraction: float,
                per_trade_risk_pct: float) -> Decimal:
    """Cap size so the per-trade risk stays within per_trade_risk_pct."""
    if stop_distance_fraction <= 0:
        return Decimal(0)
    max_size_by_risk = Decimal(str(per_trade_risk_pct / 100)) * equity / Decimal(str(stop_distance_fraction))
    return min(Decimal(str(fraction)) * equity, max_size_by_risk)


def cap_by_max_notional(size: Decimal, policy: dict) -> Decimal:
    """Cap size to the absolute per-trade USDC limit in policy.

    Layer in front of allow_trade so strategies size WITHIN safety rails
    instead of getting every proposal rejected at the risk gate with
    "per-trade notional cap: 3.5000 USDC > 1.0000 USDC cap".

    Reads policy["global_risk"]["max_notional_usdc_per_trade"], which is
    editable from the dashboard (`cfg-notional` form field, line 3086 of
    dashboard/frontend/index.html). Returns size unchanged when the cap is
    missing or zero (legacy / opt-out).
    """
    max_per_trade = policy.get("global_risk", {}).get("max_notional_usdc_per_trade")
    if max_per_trade is None:
        return size
    try:
        cap = Decimal(str(max_per_trade))
    except Exception:
        return size
    if cap <= 0:
        return size
    return min(size, cap)


def day_loss_breach_today(
    portfolio_equity: Decimal,
    day_start_equity: Decimal | None,
    threshold_pct: float,
) -> bool:
    if not day_start_equity or day_start_equity <= 0:
        return False
    dloss = float((day_start_equity - portfolio_equity) / day_start_equity * 100)
    return dloss >= threshold_pct
