"""Live metrics — Sharpe, Sortino, max DD, exposure by sleeve."""
from __future__ import annotations

import math
from collections import deque
from decimal import Decimal


def sharpe(returns: list[float], rf: float = 0.0, annualize: int = 525_600) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns) - rf
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return mean / std * (annualize ** 0.5)


def sortino(returns: list[float], rf: float = 0.0, annualize: int = 525_600) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns) - rf
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    var = sum(r ** 2 for r in downside) / len(downside)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return mean / std * (annualize ** 0.5)


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100 if peak else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def calmar(returns: list[float], equity_curve: list[float]) -> float:
    if not returns:
        return 0.0
    total = equity_curve[-1] / equity_curve[0] - 1 if equity_curve[0] else 0
    days = max(1, len(returns))
    annualized = (1 + total) ** (365 / days) - 1
    mdd = max_drawdown(equity_curve)
    return annualized / (mdd / 100) if mdd > 0 else 0.0
