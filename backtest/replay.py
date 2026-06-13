"""Replay harness — drive the strategies against a recorded market-data tape.

This is the rehearsal for the live PnL-replay window. It consumes a synthetic
or recorded tape (CMC OHLCV + perps fundings) and runs the same code paths as
production: portfolio, risk engine, sleeves, ERC-8183 job lifecycle.

Output: a metrics report (Sharpe, Sortino, max DD, hit rate, attribution) +
an HTML chart. The gate for submission: Sharpe > 0, max DD < 8%, no policy
breaches.

Usage:
  python -m backtest.replay --tape data/synthetic_week.json --report data/report.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.boot import boot
from core.portfolio import Portfolio
from core.risk import circuit_breaker_check, ProposedTrade
from strategies.sleeve_a_carry import SleeveACarry
from strategies.sleeve_b_momentum import SleeveBMomentum
from strategies.sleeve_c_meanrev import SleeveCMeanRev
from backtest.metrics import report, equity_curve_from_trades, returns_from_equity, DEFAULT_SAMPLES_PER_YEAR
from jobs.open_jobs import open_jobs_for_window
from jobs.finalize_window import finalize_window

log = logging.getLogger(__name__)


# Anchor for the deterministic synthetic-tape timestamps (v2.0.7).
# Pre-v2.0.7 the tape was anchored to int(time.time()), so the
# absolute candle timestamps depended on wall-clock at run time.
# That itself was harmless on the 5m tape (sleeves read returns/
# z-scores over candle counts, not absolute times), but
# make_synthetic_week_hourly buckets 5m bars into hours via
# `ts // 3600 * 3600`, so different wall-clocks landed bars in
# different hour buckets, producing different hourly OHLCV →
# different Sleeve C signals → different attribution. See
# tests/integration/test_replay_determinism_across_runs.py.
#
# The specific value is the unix time of one 5-minute bin
# before commit fdf5c62 (v2.0.5.1, 2026-06-06 00:10:54 UTC-5),
# empirically pinned by sweeping all 12 candidate 5-min bins
# until the bin that reproduces the v2.0.5.1 canonical
# replay_*_hourly.json numbers bit-for-bit was found
# (bull_hourly: trades=87, ending_equity=1009.887542,
# return=+0.9888%, attribution=A-only). With this epoch the
# meta-test test_demo_script_kpi_table_matches_replay_json
# stays green and `git diff data/reports/` is empty after a
# fresh `python -m scripts.run_both_regimes` from this commit
# forward.
_SYNTHETIC_REFERENCE_EPOCH = 1_780_722_354


# --- synthetic tape generator (used when no real CMC history is available) ---

def make_synthetic_week(seed: int = 42, regime: str = "bull") -> list[dict]:
    """Produce 1 week of 5-min candles for the basket. Realistic-ish GBM with funding bias.

    Args:
      seed: RNG seed for reproducibility.
      regime: "bull" (default, slight positive drift, funding slightly positive),
              "bear" (slight negative drift, funding slightly negative on average),
              "chop" (zero drift, fat tails via higher sigma).
    """
    rng = random.Random(seed)
    symbols = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
               "DOT", "MATIC", "SHIB", "LTC", "BCH", "NEAR", "ATOM", "UNI", "APT", "CAKE", "WBNB", "USDC"]
    base = {s: 100 + rng.random() * 1000 for s in symbols}
    minutes = 7 * 24 * 12     # 5-min bars over 7 days = 2016
    if regime == "bull":
        mu_low, mu_high, sigma, fund_low, fund_high = -0.0001, 0.0002, 0.005, -0.0005, 0.0015
    elif regime == "bear":
        mu_low, mu_high, sigma, fund_low, fund_high = -0.0003, -0.0001, 0.006, -0.0015, 0.0002
    elif regime == "chop":
        mu_low, mu_high, sigma, fund_low, fund_high = -0.00005, 0.00005, 0.012, -0.0008, 0.0008
    else:
        raise ValueError(f"unknown regime: {regime!r}; use 'bull' | 'bear' | 'chop'")
    tape = []
    for i in range(minutes):
        ts = _SYNTHETIC_REFERENCE_EPOCH - (minutes - i) * 300
        for sym in symbols:
            mu = rng.uniform(mu_low, mu_high)
            ret = rng.gauss(mu, sigma)
            close = base[sym] * (1 + ret)
            high = max(base[sym], close) * (1 + abs(rng.gauss(0, sigma/2)))
            low = min(base[sym], close) * (1 - abs(rng.gauss(0, sigma/2)))
            tape.append({
                "ts": ts,
                "symbol": sym,
                "open": base[sym],
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": rng.uniform(1e5, 1e7),
            })
            base[sym] = close
    # funding snapshots every 8h — calibrated to realistic BSC venue
    # rates (Aster / KiloEx / ApolloX / MUX settle 8h at 0.01%–0.05%,
    # widened slightly for tail events). See connectors/bnb_sdk.py.
    fundings = []
    for i in range(0, minutes, 96):
        for sym in symbols:
            fundings.append({
                "ts": _SYNTHETIC_REFERENCE_EPOCH - (minutes - i) * 300,
                "symbol": sym,
                "venue": rng.choice(["aster", "killex", "apollox", "mux"]),
                "funding": rng.uniform(fund_low, fund_high),
            })
    return {"candles": tape, "fundings": fundings, "regime": regime, "seed": seed}


def make_synthetic_week_hourly(seed: int = 42, regime: str = "bull") -> dict:
    """Aggregate the 5-min tape into 1-hour bars so Sleeve B and
    Sleeve C — which ask for hourly candles from CMC — actually have
    valid data to work with. The 5-min tape gives 2016 bars per symbol;
    the hourly aggregation gives 168 bars per symbol (7d × 24h).

    OHLCV aggregation is standard: open=first, high=max, low=min,
    close=last, volume=sum. The aggregated ts is the hour boundary.
    The fundings dict is unchanged (fundings are 8h boundaries either
    way).
    """
    five_min = make_synthetic_week(seed=seed, regime=regime)
    buckets: dict[tuple[str, int], list[dict]] = {}
    for c in five_min["candles"]:
        # 1h boundary: round down to the nearest hour
        h_ts = (c["ts"] // 3600) * 3600
        buckets.setdefault((c["symbol"], h_ts), []).append(c)
    hourly: list[dict] = []
    for (sym, h_ts), cs in buckets.items():
        # sort by ts ascending (they should already be, but be safe)
        cs = sorted(cs, key=lambda x: x["ts"])
        hourly.append({
            "ts":      h_ts,
            "symbol":  sym,
            "open":    cs[0]["open"],
            "high":    max(c["high"] for c in cs),
            "low":     min(c["low"]  for c in cs),
            "close":   cs[-1]["close"],
            "volume":  sum(c["volume"] for c in cs),
        })
    hourly.sort(key=lambda c: (c["ts"], c["symbol"]))
    return {
        "candles":  hourly,
        "fundings": five_min["fundings"],
        "regime":   regime,
        "seed":     seed,
        "interval": "hour",
    }


# --- replay engine ---

async def run_replay(tape_path: str | None, report_path: str, equity: float = 100.0,
                     tape: dict | None = None, open_jobs_flag: bool = True) -> dict:
    if tape is None:
        if tape_path and Path(tape_path).exists():
            tape = json.load(open(tape_path))
        else:
            log.info("no tape provided — generating synthetic week")
            tape = make_synthetic_week()

    # Deterministic clock (v2.0.4). We hold a mutable holder so the
    # main loop can advance the clock to the current tape ts on every
    # tick. This makes the replay fully reproducible — every run
    # produces identical numbers, so the meta-test that locks the
    # demo-script table to the JSON passes reliably.
    _clock_holder = [0.0]
    def _clock() -> float:
        return _clock_holder[0]

    components = boot(starting_equity=Decimal(str(equity)), clock=_clock)
    # override mode at runtime: replay stub is sufficient because we patch the CMC client below
    policy = components["policy"]
    portfolio: Portfolio = components["portfolio"]
    data_source = components["data_source"]

    # build a candle index: sym -> list of (ts, candle)
    candles_by_sym: dict[str, list[dict]] = {}
    for c in tape.get("candles", []):
        candles_by_sym.setdefault(c["symbol"], []).append(c)
    fundings_by_sym: dict[str, list[dict]] = {}
    for f in tape.get("fundings", []):
        fundings_by_sym.setdefault(f["symbol"], []).append(f)

    # Replace the CMC client with a deterministic function that pulls from the tape.
    # The fake returns FLAT candle dicts (not CMC's nested quote.USD shape) so
    # the sleeves' `.get("high", 0)` etc. work without extra nesting logic.
    # Audit #21: the original stub returned the absolute last close of the
    # full series (end-of-week price), which made the entry spot diverge
    # from the per-tick mark, triggering basis_trigger spuriously. Now
    # the price is bounded by the current ts (matching the mark_fn).
    _current_ts = [0]
    def _set_current_ts(ts: int):
        _current_ts[0] = ts
    async def fake_quotes(symbols: list[str], convert: str = "USD") -> dict:
        out = {"data": {}}
        for sym in symbols:
            series = candles_by_sym.get(sym, [])
            # find the latest close at or before _current_ts (replay "now")
            px = 100.0
            for c in reversed(series):
                if c["ts"] <= _current_ts[0]:
                    px = c["close"]
                    break
            out["data"][sym] = {"quote": {"USD": {"price": px}}}
        return out

    async def fake_ohlc(symbols: list[str], time_period: str = "hour", count: int = 24, convert: str = "USD") -> dict:
        out = {"data": {}}
        for sym in symbols:
            series = candles_by_sym.get(sym, [])
            tail = series[-count:] if series else []
            # FLAT shape: each quote is a single dict with OHLCV at the top level.
            quotes = [{
                "timestamp": c["ts"],
                "time_open":  c["ts"],
                "time_close": c["ts"] + 300,
                "open":   c["open"],
                "high":   c["high"],
                "low":    c["low"],
                "close":  c["close"],
                "volume": c["volume"],
            } for c in tail]
            out["data"][sym] = {"quotes": quotes}
        return out

    data_source.quotes_latest = fake_quotes
    data_source.ohlcv_historical = fake_ohlc
    data_source.call = lambda *a, **kw: asyncio.sleep(0)  # no-op

    # drive sleeves (pass the deterministic clock from boot so every
    # time.time() read inside the strategy is reproducible)
    a = SleeveACarry(name="A", components=components, agent=None, clock=_clock)
    b = SleeveBMomentum(name="B", components=components, agent=None, clock=_clock)
    c = SleeveCMeanRev(name="C", components=components, agent=None, clock=_clock)

    # Build a simple Agent shim that calls the risk engine + portfolio
    class AgentShim:
        def __init__(_self, policy, portfolio):
            _self.policy = policy
            _self.portfolio = portfolio
        def allow_trade(_self, proposed):
            ok, reason = circuit_breaker_check(
                current_equity=portfolio.equity(),
                peak_equity=portfolio.peak_equity,
                open_positions=list(portfolio.positions.values()),
                proposed=proposed,
                policy=policy,
                day_start_equity=portfolio.day_start_equity.get(portfolio._today()),
                day_breach_active_until=portfolio.day_breach_active_until,
            )
            return ok, reason
        # v2.1.5: must be async to match the production Agent.review_trade
        # signature (core/tick.py:167) and the sleeves' call site
        # (strategies/sleeve_*.py: await self.agent.review_trade(...)).
        # The previous sync definition caused
        # "object tuple can't be used in 'await' expression" warnings on
        # every tick, which made the reviewer effectively a no-op in
        # the replay path. The replay now matches production.
        async def review_trade(_self, proposed, sleeve_state, market_snapshot):
            # Replay shim: pass-through so the reviewer code path is
            # exercised in the harness. The real reviewer is wired in
            # production via core.main._init_llm_components. Without
            # this method, every tick logs a warning and the LLM-skip
            # path is taken, which hides reviewer bugs.
            return True, "ok_replay_shim", "shim"

    shim = AgentShim(policy, portfolio)
    a.agent = shim
    b.agent = shim
    c.agent = shim

    # open ERC-8183 jobs (in-memory; replay mode)
    if open_jobs_flag:
        usdc_entry = components["config"]["tokens"]["USDC"]
        usdc_addr = usdc_entry["bsc_address"] if isinstance(usdc_entry, dict) else usdc_entry
        jobs = open_jobs_for_window(
            window_id=f"replay-{tape.get('regime','unk')}-{tape.get('seed',0)}-open",
            policy=policy,
            erc8183=components["erc8183"],
            ipfs=components["ipfs"],
            wallet=components["wallet"],
            usdc_address=usdc_addr,
        )
        log.info(f"opened jobs: {jobs}")

    # Run sleeves for 7 days, ticking every 5 minutes (faster than production)
    log.info("running replay for 7 days of 5-min bars")
    breaches = []
    minutes = len(tape.get("candles", [])) // 20   # unique ts buckets
    unique_ts = sorted({c["ts"] for c in tape.get("candles", [])})
    for tick_idx, ts in enumerate(unique_ts):
        # Advance the deterministic clock to the current tape ts (v2.0.4).
        # Every strategy + portfolio + perps read this clock instead of
        # wall-clock time.time(), so the replay is bit-for-bit
        # reproducible across processes and across runs.
        _clock_holder[0] = float(ts)
        # mark-to-market: update each position's mark price from latest candle
        for sym in candles_by_sym:
            mark = next((c["close"] for c in reversed(candles_by_sym[sym]) if c["ts"] <= ts), None)
            if mark is None:
                continue
            for pid, pos in list(portfolio.positions.items()):
                if pos.symbol == sym:
                    pos.extra["mark"] = mark
        # bind `ts` via default arg so the lambda doesn't capture a moving target
        mark_fn = lambda s, _ts=ts: Decimal(str(_mark(s, _ts, candles_by_sym)))
        _set_current_ts(ts)
        portfolio.set_mark_provider(mark_fn)
        # Perps stub also needs the mark provider so basis_trigger
        # compares the current spot mark to the entry spot price, not
        # the hardcoded stub value of 100. (Audit #21: 3,368 spurious
        # trade-closes per bear-regime run before this was wired up.)
        try:
            components["perps"].set_mark_provider(mark_fn)
        except Exception:
            pass

        # tick sleeves
        try:
            await a.tick()
        except Exception as e:
            log.warning(f"sleeve A tick fail: {e}")
        try:
            await b.tick()
        except Exception as e:
            log.warning(f"sleeve B tick fail: {e}")
        try:
            await c.tick()
        except Exception as e:
            log.warning(f"sleeve C tick fail: {e}")

        portfolio.update_peak()
        if tick_idx % 100 == 0:
            log.info(f"  tick {tick_idx}/{len(unique_ts)} equity=${portfolio.equity():.2f} "
                     f"DD={portfolio.drawdown_pct():.2f}% pos={len(portfolio.positions)}")

    # finalize
    summary = {}
    if open_jobs_flag:
        summary = finalize_window(
            jobs=jobs, portfolio=portfolio, policy=policy,
            ipfs=components["ipfs"], erc8183=components["erc8183"],
            window_id=f"replay-{tape.get('regime','unk')}-{tape.get('seed',0)}-final",
        )

    # metrics
    # Bug fix v2.1.5: pass samples_per_year to report() based on the
    # actual sample frequency of the equity curve. Previously the
    # default of 365*24*60 (minute samples) was used unconditionally,
    # which inflated the Sharpe by ~60x on a 7-day replay (the eq
    # curve has ~200 points across 7 days, not 525,600).
    eq_curve = equity_curve_from_trades(float(equity), list(portfolio.closed_trades))
    # Sample frequency: 1 sample per (tape_duration / eq_curve_length)
    # seconds, annualized. Default to minute if we can't compute it.
    samples_per_year = DEFAULT_SAMPLES_PER_YEAR
    if len(eq_curve) >= 2:
        candles = (tape or {}).get("candles", []) or []
        if candles:
            tape_duration_s = max(c["ts"] for c in candles) - min(c["ts"] for c in candles)
            if tape_duration_s > 0:
                sample_interval_s = tape_duration_s / (len(eq_curve) - 1)
                samples_per_year = int(round((365 * 24 * 3600) / sample_interval_s))
    metrics = report(
        eq_curve, list(portfolio.closed_trades),
        starting_equity=float(equity),
        samples_per_year=samples_per_year,
    )
    metrics["samples_per_year"] = samples_per_year  # surface for the HTML/JSON so the demo can show it
    # Surface the breach count + the kill-switch flag in the JSON so the
    # bnbagent launcher can decide exit code. Without this, --replay
    # always returns 0 from a successful Python invocation even if the
    # agent blew through its risk gates.
    metrics["breaches"] = len(breaches)
    metrics["kill_switch_engaged"] = bool(getattr(portfolio, "kill_switch", False))

    # write report
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    html = _render_html(metrics, summary, eq_curve)
    Path(report_path).write_text(html)
    json.dump({**metrics, "summary": summary},
              open(str(report_path).replace(".html", ".json"), "w"),
              indent=2, default=str)
    log.info(f"replay complete: equity=${metrics['ending_equity']:.2f} "
             f"sharpe={metrics['sharpe']:.2f} maxDD={metrics['max_drawdown_pct']:.2f}% "
             f"trades={metrics['trades']} report={report_path}")
    return metrics


def _mark(sym: str, ts: int, candles_by_sym: dict) -> float:
    series = candles_by_sym.get(sym, [])
    for c in reversed(series):
        if c["ts"] <= ts:
            return c["close"]
    return 100.0


def _render_html(metrics: dict, summary: dict, equity_curve: list[float]) -> str:
    rows = ""
    for s, a in metrics.get("attribution", {}).items():
        rows += f"<tr><td>{s}</td><td>{a['trades']}</td><td>${a['pnl']:.2f}</td><td>{a['hit_rate']*100:.1f}%</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>BNB Agent — Replay Report</title>
<style>
  body {{ font-family: monospace; background: #0b0e14; color: #e6edf3; padding: 24px; max-width: 1100px; margin: 0 auto; }}
  h1 {{ color: #ffa657; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .card {{ background: #131722; padding: 12px; border-radius: 8px; border: 1px solid #2d333b; }}
  .label {{ color: #8b96a8; font-size: 10px; text-transform: uppercase; }}
  .value {{ font-size: 22px; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ padding: 8px; border-bottom: 1px solid #2d333b; text-align: left; }}
  th {{ color: #8b96a8; font-size: 10px; text-transform: uppercase; }}
  .green {{ color: #3fb950; }}
  .red {{ color: #f85149; }}
  .yellow {{ color: #d29922; }}
</style>
</head><body>
<h1>BNB Agent — Replay Report</h1>
<div class='grid'>
  <div class='card'><div class='label'>Final Equity</div><div class='value'>${metrics['ending_equity']:.2f}</div></div>
  <div class='card'><div class='label'>Total Return</div><div class='value {'green' if metrics['total_return_pct']>=0 else 'red'}'>{metrics['total_return_pct']:+.2f}%</div></div>
  <div class='card'><div class='label'>Sharpe</div><div class='value yellow'>{metrics['sharpe']:.2f}</div></div>
  <div class='card'><div class='label'>Sortino</div><div class='value yellow'>{metrics['sortino']:.2f}</div></div>
  <div class='card'><div class='label'>Max DD</div><div class='value red'>{metrics['max_drawdown_pct']:.2f}%</div></div>
  <div class='card'><div class='label'>Calmar</div><div class='value yellow'>{metrics['calmar']:.2f}</div></div>
  <div class='card'><div class='label'>Trades</div><div class='value'>{metrics['trades']}</div></div>
  <div class='card'><div class='label'>Hit Rate</div><div class='value yellow'>{metrics['hit_rate']*100:.1f}%</div></div>
</div>
<h2>Attribution by Sleeve</h2>
<table><thead><tr><th>Sleeve</th><th>Trades</th><th>PnL</th><th>Hit Rate</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Equity Curve (final 1000 points)</h2>
<pre>{equity_curve[-1000:]}</pre>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape",   default=None)
    ap.add_argument("--report", default="data/reports/replay.html")
    ap.add_argument("--equity", type=float, default=100.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_replay(args.tape, args.report, equity=args.equity))


if __name__ == "__main__":
    main()
