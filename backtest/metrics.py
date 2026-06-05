"""Backtest metrics: Sharpe, Sortino, Calmar, max DD, hit rate, profit factor."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable


def equity_curve_from_trades(starting_equity: float, trades: list[dict]) -> list[float]:
    eq = starting_equity
    out = [eq]
    for t in trades:
        eq += float(t.get("pnl_usdc", 0))
        out.append(eq)
    return out


def returns_from_equity(equity: list[float]) -> list[float]:
    return [(equity[i] - equity[i-1]) / equity[i-1] for i in range(1, len(equity)) if equity[i-1]]


def sharpe(returns: list[float], annualize: int = 365 * 24 * 60) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(annualize)


def sortino(returns: list[float], annualize: int = 365 * 24 * 60) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    var = sum(r ** 2 for r in downside) / len(downside)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(annualize)


def max_drawdown_pct(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100 if peak else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def calmar(equity: list[float]) -> float:
    if len(equity) < 2 or equity[0] == 0:
        return 0.0
    total = equity[-1] / equity[0] - 1
    days = max(1, len(equity) / (24 * 60))
    annualized = (1 + total) ** (365 / days) - 1
    mdd = max_drawdown_pct(equity)
    return annualized / (mdd / 100) if mdd > 0 else 0.0


def hit_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if float(t.get("pnl_usdc", 0)) > 0)
    return wins / len(trades)


def profit_factor(trades: list[dict]) -> float:
    gross_profit = sum(float(t.get("pnl_usdc", 0)) for t in trades if float(t.get("pnl_usdc", 0)) > 0)
    gross_loss = abs(sum(float(t.get("pnl_usdc", 0)) for t in trades if float(t.get("pnl_usdc", 0)) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def attribution_by_sleeve(trades: list[dict]) -> dict[str, dict]:
    out: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        out[t.get("sleeve", "?")].append(t)
    return {s: compute(s) for s, c in out.items() for compute in [{**{
        "trades": len(c),
        "pnl": sum(float(t["pnl_usdc"]) for t in c),
        "hit_rate": hit_rate(c),
    }}] if False} or {
        s: {
            "trades":   len(c),
            "pnl":      sum(float(t["pnl_usdc"]) for t in c),
            "hit_rate": hit_rate(c),
        }
        for s, c in out.items()
    }


def report(equity: list[float], trades: list[dict], starting_equity: float = 100.0) -> dict:
    eq = equity or [starting_equity]
    rets = returns_from_equity(eq)
    return {
        "starting_equity":   starting_equity,
        "ending_equity":     eq[-1],
        "total_return_pct":  (eq[-1] / starting_equity - 1) * 100,
        "trades":            len(trades),
        "hit_rate":          hit_rate(trades),
        "profit_factor":     profit_factor(trades),
        "sharpe":            sharpe(rets),
        "sortino":           sortino(rets),
        "max_drawdown_pct":  max_drawdown_pct(eq),
        "calmar":            calmar(eq),
        "attribution":       attribution_by_sleeve(trades),
    }
