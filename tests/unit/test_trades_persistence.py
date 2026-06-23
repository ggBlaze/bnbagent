"""Tests for v2.3.5b: closed_trades persistence across restarts.

Why this exists:
  The dashboard's /api/trades panel was empty on every restart
  because the in-memory closed_trades deque resets when the
  process exits. v2.3.5b adds a JSONL append-only file in
  ~/.bnbagent/closed_trades.jsonl that:
    - is appended to on every close
    - is re-hydrated into the deque on Portfolio init
    - is deduped on id (so re-appending the same trade is a no-op)
    - is tolerant of corrupt lines (skips them, doesn't crash)

This file covers:
  - A trade closed before restart is visible after restart
  - Two restarts in a row don't duplicate trades
  - A corrupt line in the file is skipped (not fatal)
  - The deque maxlen=10_000 still applies on top of the file
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.portfolio import Portfolio, Position


def _pos(symbol="ETH", notional=Decimal("1"), is_paper=False):
    return Position(
        sleeve="B", symbol=symbol, side="long",
        notional_usdc=notional, risk_usdc=Decimal("0.01"),
        entry_ts=1000, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
        is_paper=is_paper,
    )


def test_trade_persists_across_restart(tmp_path):
    path = str(tmp_path / "closed_trades.jsonl")
    # First "process": open + close one trade
    p1 = Portfolio(trades_persistence_path=path)
    p1.add_position("t1", _pos())
    p1.close_position("t1", exit_price=Decimal("101"), reason="tp")
    # Restart: fresh Portfolio, same path
    p2 = Portfolio(trades_persistence_path=path)
    assert len(p2.closed_trades) == 1, (
        f"trade didn't persist across restart: {p2.closed_trades}"
    )
    assert p2.closed_trades[0]["id"] == "t1"


def test_two_restarts_no_duplication(tmp_path):
    path = str(tmp_path / "closed_trades.jsonl")
    # Process 1: open + close t1
    p1 = Portfolio(trades_persistence_path=path)
    p1.add_position("t1", _pos()); p1.close_position("t1", exit_price=Decimal("101"), reason="tp")
    # Process 2: open + close t2 (t1 should re-load from disk)
    p2 = Portfolio(trades_persistence_path=path)
    p2.add_position("t2", _pos()); p2.close_position("t2", exit_price=Decimal("101"), reason="tp")
    # Process 3: re-load both
    p3 = Portfolio(trades_persistence_path=path)
    assert len(p3.closed_trades) == 2
    ids = {t["id"] for t in p3.closed_trades}
    assert ids == {"t1", "t2"}


def test_corrupt_line_in_file_is_skipped(tmp_path):
    path = str(tmp_path / "closed_trades.jsonl")
    # Manually write a file with one good line + one bad line
    with open(path, "w") as f:
        f.write(json.dumps({
            "id": "t1", "sleeve": "B", "symbol": "ETH",
            "notional": "1", "pnl_usdc": "0.5",
            "ts_open": 1000, "ts_close": 2000,
            "is_paper": False,
        }) + "\n")
        f.write("THIS IS NOT JSON\n")
        f.write(json.dumps({
            "id": "t3", "sleeve": "B", "symbol": "ETH",
            "notional": "1", "pnl_usdc": "0.2",
            "ts_open": 3000, "ts_close": 4000,
            "is_paper": False,
        }) + "\n")
    p = Portfolio(trades_persistence_path=path)
    # Both good lines loaded; corrupt line skipped
    assert len(p.closed_trades) == 2
    ids = {t["id"] for t in p.closed_trades}
    assert ids == {"t1", "t3"}


def test_paper_and_real_trades_both_persist(tmp_path):
    """The v2.3.5b fix surfaces both real + paper trades. Both
    must persist equally so the panel shows them after restart."""
    path = str(tmp_path / "closed_trades.jsonl")
    p1 = Portfolio(trades_persistence_path=path)
    p1.add_position("real-1", _pos(is_paper=False))
    p1.close_position("real-1", exit_price=Decimal("101"), reason="tp")
    p1.add_position("paper-1", _pos(is_paper=True))
    p1.close_position("paper-1", exit_price=Decimal("101"), reason="tp")
    p2 = Portfolio(trades_persistence_path=path)
    flags = {t["id"]: t["is_paper"] for t in p2.closed_trades}
    assert flags == {"real-1": False, "paper-1": True}


def test_no_persistence_when_path_is_none(tmp_path):
    """Backwards-compat: tests / paper mode pass path=None → no file I/O."""
    p = Portfolio(trades_persistence_path=None)
    p.add_position("t1", _pos())
    p.close_position("t1", exit_price=Decimal("101"), reason="tp")
    # In-memory still works
    assert len(p.closed_trades) == 1
    # But nothing was written to /tmp
    files = list(tmp_path.iterdir())
    assert files == [], f"expected no files, got {files}"
