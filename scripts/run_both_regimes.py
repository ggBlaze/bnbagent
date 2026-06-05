"""Run the replay harness on both bull and bear synthetic regimes and
emit a side-by-side comparison.

Usage:
  python -m scripts.run_both_regimes
Outputs:
  data/reports/replay_bull.html, data/reports/replay_bull.json
  data/reports/replay_bear.html, data/reports/replay_bear.json
  data/reports/replay_compare.html (side-by-side)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.replay import run_replay  # noqa: E402

log = logging.getLogger(__name__)


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    reports_dir = ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Use a custom in-memory tape (no on-disk JSON needed).
    from backtest.replay import make_synthetic_week
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

    rows = []
    for name, m in (("bull", bull_metrics), ("bear", bear_metrics), ("chop", chop_metrics)):
        rows.append({
            "regime": name,
            "ending_equity": m["ending_equity"],
            "total_return_pct": m["total_return_pct"],
            "sharpe": m["sharpe"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "trades": m["trades"],
            "hit_rate": m["hit_rate"],
        })

    Path(reports_dir / "replay_compare.json").write_text(json.dumps(rows, indent=2, default=str))
    Path(reports_dir / "replay_compare.html").write_text(_render_compare(rows))
    log.info("wrote %s", reports_dir / "replay_compare.html")
    for r in rows:
        log.info("  %-5s: ret=%+6.2f%% DD=%5.2f%% trades=%3d hit=%3.0f%%",
                 r["regime"], r["total_return_pct"], r["max_drawdown_pct"],
                 r["trades"], r["hit_rate"] * 100)
    return 0


def _render_compare(rows: list[dict]) -> str:
    body = ""
    for r in rows:
        cls = "green" if r["total_return_pct"] >= 0 else "red"
        body += (
            f"<tr><td>{r['regime']}</td>"
            f"<td class='{cls}'>{r['total_return_pct']:+.2f}%</td>"
            f"<td>${r['ending_equity']:.2f}</td>"
            f"<td class='{cls}'>{r['max_drawdown_pct']:.2f}%</td>"
            f"<td>{r['trades']}</td>"
            f"<td>{r['hit_rate']*100:.1f}%</td></tr>"
        )
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>BNB Agent — Replay Regime Comparison</title>
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
<h1>BNB Agent — Replay Regime Comparison</h1>
<p>Same code, same policy, three regimes. If the agent blows up in bear and survives in chop, the risk envelope is too tight. If it loses in all three, the alpha needs work.</p>
<table>
<thead><tr><th>Regime</th><th>Total Return</th><th>Ending Equity</th><th>Max DD</th><th>Trades</th><th>Hit Rate</th></tr></thead>
<tbody>{body}</tbody>
</table>
</body></html>"""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
