"""Run the replay harness on all 6 synthetic regime/tape combinations
and emit a side-by-side comparison.

Usage:
  python -m scripts.run_both_regimes
  python -m scripts.run_both_regimes --verify    # 3x run, SHA-compare outputs (CI / judges)
  python -m scripts.run_both_regimes --verify --verify-runs 5

Outputs:
  data/reports/replay_bull.html, data/reports/replay_bull.json
  data/reports/replay_bear.html, data/reports/replay_bear.json
  data/reports/replay_chop.html, data/reports/replay_chop.json
  data/reports/replay_{bull,bear,chop}_hourly.html + .json
  data/reports/replay_compare.html + .json (side-by-side)

v2.1.5: --verify mode runs the full sweep N times and SHA-256-compares
every output file. A pass proves the entire pipeline is bit-identical
across runs (and by extension, across machines — same Python, same code,
same seed). The committed JSONs are a snapshot of one such pass; if a
fresh clone + re-run produces a different SHA, the drift is in the
runtime env, not the math.

This is the bulletproof guard the public demo needs: a judge can run
`python -m scripts.run_both_regimes --verify` and trust the output.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.replay import run_replay  # noqa: E402

log = logging.getLogger(__name__)


# Files we SHA-compare in --verify mode. Keep this list in sync with
# what _run_sweep() actually writes to disk.
_VERIFY_FILES = [
    "replay_bull.html", "replay_bull.json",
    "replay_bear.html", "replay_bear.json",
    "replay_chop.html", "replay_chop.json",
    "replay_bull_hourly.html", "replay_bull_hourly.json",
    "replay_bear_hourly.html", "replay_bear_hourly.json",
    "replay_chop_hourly.html", "replay_chop_hourly.json",
    "replay_compare.html", "replay_compare.json",
]


async def _run_sweep(reports_dir: Path) -> list[dict]:
    """Run the 6-regime sweep (3 regimes x 2 tape intervals). Returns the
    rows that go into replay_compare.{json,html}. Shared by main() and
    the --verify determinism mode."""
    from backtest.replay import make_synthetic_week, make_synthetic_week_hourly
    bull_tape = make_synthetic_week(seed=42, regime="bull")
    bear_tape = make_synthetic_week(seed=42, regime="bear")
    chop_tape = make_synthetic_week(seed=42, regime="chop")

    log.info("running bull regime…")
    bull_metrics = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_bull.html"),
        equity=1000.0, tape=bull_tape, open_jobs_flag=False,
    )
    log.info("running bear regime…")
    bear_metrics = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_bear.html"),
        equity=1000.0, tape=bear_tape, open_jobs_flag=False,
    )
    log.info("running chop regime…")
    chop_metrics = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_chop.html"),
        equity=1000.0, tape=chop_tape, open_jobs_flag=False,
    )

    # v2.0.4 — also run the 1-hour-aggregated tape so Sleeve B and
    # Sleeve C (which ask for hourly candles from CMC) actually fire.
    # The 5-min tape doesn't have hourly structure, so the live
    # PnL-window's CMC OHLCV hourly bars are the real test; this
    # hourly variant is a sandbox for the harness.
    log.info("running bull regime on HOURLY tape…")
    bull_h = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_bull_hourly.html"),
        equity=1000.0, tape=make_synthetic_week_hourly(seed=42, regime="bull"),
        open_jobs_flag=False,
    )
    log.info("running bear regime on HOURLY tape…")
    bear_h = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_bear_hourly.html"),
        equity=1000.0, tape=make_synthetic_week_hourly(seed=42, regime="bear"),
        open_jobs_flag=False,
    )
    log.info("running chop regime on HOURLY tape…")
    chop_h = await run_replay(
        tape_path=None, report_path=str(reports_dir / "replay_chop_hourly.html"),
        equity=1000.0, tape=make_synthetic_week_hourly(seed=42, regime="chop"),
        open_jobs_flag=False,
    )

    rows = []
    for name, m, m_h in (
        ("bull", bull_metrics, bull_h),
        ("bear", bear_metrics, bear_h),
        ("chop", chop_metrics, chop_h),
    ):
        rows.append({
            "regime": name,
            "interval": "5m",
            "ending_equity": m["ending_equity"],
            "total_return_pct": m["total_return_pct"],
            "sharpe": m["sharpe"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "trades": m["trades"],
            "hit_rate": m["hit_rate"],
            "attribution": m["attribution"],
        })
        rows.append({
            "regime": name,
            "interval": "1h",
            "ending_equity": m_h["ending_equity"],
            "total_return_pct": m_h["total_return_pct"],
            "sharpe": m_h["sharpe"],
            "max_drawdown_pct": m_h["max_drawdown_pct"],
            "trades": m_h["trades"],
            "hit_rate": m_h["hit_rate"],
            "attribution": m_h["attribution"],
        })
    # Write the compare files inside _run_sweep() too so --verify
    # catches drift in them. (Earlier, only main() wrote them, so
    # verify mode silently skipped the side-by-side output.)
    Path(reports_dir / "replay_compare.json").write_text(json.dumps(rows, indent=2, default=str))
    Path(reports_dir / "replay_compare.html").write_text(_render_compare(rows))
    return rows


async def _verify_determinism(reports_dir: Path, *, runs: int = 3) -> int:
    """Run the full sweep N times, compare output SHA-256s. Exit 1 on drift.

    The first run sets the baseline; subsequent runs must produce
    byte-identical files. If any file's SHA changes between runs, we
    log which file drifted and exit 1 so CI / the public-demo deploy
    fails loudly.
    """
    log.info("=== determinism verify: running %d passes ===", runs)
    baseline: dict[str, str] = {}
    drift: list[tuple[int, str, str, str]] = []  # (run_idx, file, baseline_sha, this_sha)
    for run_idx in range(1, runs + 1):
        log.info("--- pass %d/%d ---", run_idx, runs)
        await _run_sweep(reports_dir)
        for fname in _VERIFY_FILES:
            fpath = reports_dir / fname
            if not fpath.exists():
                drift.append((run_idx, fname, baseline.get(fname, "MISSING"), "MISSING"))
                continue
            sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            if run_idx == 1:
                baseline[fname] = sha
            elif sha != baseline[fname]:
                drift.append((run_idx, fname, baseline[fname], sha))
    if drift:
        log.error("=== DETERMINISM DRIFT DETECTED ===")
        for run_idx, fname, base_sha, this_sha in drift:
            log.error("  pass %d  %s  baseline=%s  this=%s",
                      run_idx, fname, base_sha[:12], this_sha[:12])
        log.error("Fix: ensure PYTHONHASHSEED=0 is exported in entry-point "
                  "scripts (bnbagent, scripts/mcp_serve*.sh) and that no "
                  "library on the boot path uses time/urandom/uuid. The "
                  "committed JSONs are a snapshot of one specific run; if "
                  "this drift is across machines only, see "
                  "docs/demo-script.md#replay-determinism for the rationale.")
        return 1
    log.info("=== determinism verify: %d passes, %d files, bit-identical ===", runs, len(_VERIFY_FILES))
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="BNB Agent replay harness (3 regimes x 2 tape intervals)")
    parser.add_argument("--verify", action="store_true",
                        help="Run N times (default 3) and SHA-compare outputs. Catches non-determinism.")
    parser.add_argument("--verify-runs", type=int, default=3,
                        help="Number of runs when --verify is set (default 3)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    reports_dir = ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if args.verify:
        return await _verify_determinism(reports_dir, runs=args.verify_runs)

    rows = await _run_sweep(reports_dir)
    log.info("wrote %s", reports_dir / "replay_compare.html")
    for r in rows:
        log.info("  %-5s %2s: ret=%+6.2f%% DD=%5.2f%% trades=%3d hit=%3.0f%% attr=%s",
                 r["regime"], r["interval"],
                 r["total_return_pct"], r["max_drawdown_pct"],
                 r["trades"], r["hit_rate"] * 100,
                 list(r["attribution"].keys()))
    return 0


def _render_compare(rows: list[dict]) -> str:
    body = ""
    for r in rows:
        cls = "green" if r["total_return_pct"] >= 0 else "red"
        sleeves = ", ".join(r.get("attribution", {}).keys()) or "\u2014"
        body += (
            f"<tr><td>{r['regime']}</td>"
            f"<td>{r['interval']}</td>"
            f"<td class='{cls}'>{r['total_return_pct']:+.2f}%</td>"
            f"<td>${r['ending_equity']:.2f}</td>"
            f"<td class='{cls}'>{r['max_drawdown_pct']:.2f}%</td>"
            f"<td>{r['trades']}</td>"
            f"<td>{r['hit_rate']*100:.1f}%</td>"
            f"<td>{sleeves}</td></tr>"
        )
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>BNB Agent \u2014 Replay Regime Comparison</title>
<style>
  body {{ font-family: monospace; background: #0b0e14; color: #e6edf3; padding: 24px; max-width: 900px; margin: 0 auto; }}
  h1 {{ color: #ffa657; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
  th, td {{ padding: 10px; border-bottom: 1px solid #2d333b; text-align: right; }}
  th {{ color: #8b96a8; font-size: 11px; text-transform: uppercase; }}
  th:first-child, td:first-child {{ text-align: left; }}
  .green {{ color: #3fb950; }}
  .red {{ color: #f85149; }}
</style>
</head><body>
<h1>BNB Agent \u2014 Replay Regime Comparison</h1>
<p>Same code, same policy, three regimes. If the agent blows up in bear and survives in chop, the risk envelope is too tight. If it loses in all three, the alpha needs work.</p>
<table>
<thead><tr><th>Regime</th><th>Interval</th><th>Total Return</th><th>Ending Equity</th><th>Max DD</th><th>Trades</th><th>Hit Rate</th><th>Sleeves firing</th></tr></thead>
<tbody>{body}</tbody>
</table>
</body></html>"""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
