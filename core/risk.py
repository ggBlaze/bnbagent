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


def day_loss_breach_today(
    portfolio_equity: Decimal,
    day_start_equity: Decimal | None,
    threshold_pct: float,
) -> bool:
    if not day_start_equity or day_start_equity <= 0:
        return False
    dloss = float((day_start_equity - portfolio_equity) / day_start_equity * 100)
    return dloss >= threshold_pct
