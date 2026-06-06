"""Test helper — run the replay equivalent of scripts.run_both_regimes
into an arbitrary output directory under an arbitrary wall-clock offset.

Used by tests/integration/test_replay_determinism_across_runs.py to
force three subprocess runs onto three different `epoch mod 3600`
values (so the buggy hourly-bucket alignment in
backtest.replay.make_synthetic_week_hourly drifts across runs even
within a single CI minute). Without this, three back-to-back
subprocesses would land within ~90 seconds and might share a 5-min
bucket boundary, masking the bug.

Reads two env vars:
  TEST_TIME_OFFSET    integer seconds added to time.time() before any
                      project import (default 0). The monkey-patch is
                      applied at module import time so every read of
                      time.time() inside backtest.replay sees the
                      shifted clock.
  (sys.argv[1])       output directory for the 14 files
                      (6 5m+1h JSON × HTML pairs + compare JSON+HTML).

NOT a test module — the leading underscore keeps pytest from
collecting it. The test subprocesses this script directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time as _time
from pathlib import Path

# 1. Monkey-patch time.time BEFORE importing the project. Any module
#    that captures `time.time` at import time (none currently, but we
#    don't depend on that) sees the shifted clock.
_OFFSET = float(os.environ.get("TEST_TIME_OFFSET", "0"))
if _OFFSET != 0.0:
    _real_time = _time.time
    _time.time = lambda _r=_real_time, _o=_OFFSET: _r() + _o  # type: ignore[assignment]

# 2. sys.path so `import backtest.replay` resolves to the repo's copy.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backtest.replay import (  # noqa: E402
    make_synthetic_week,
    make_synthetic_week_hourly,
    run_replay,
)


async def _run(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)
    rows: list[dict] = []
    for regime in ("bull", "bear", "chop"):
        # 5m
        await run_replay(
            tape_path=None,
            report_path=str(out_dir / f"replay_{regime}.html"),
            equity=1000.0,
            tape=make_synthetic_week(seed=42, regime=regime),
            open_jobs_flag=False,
        )
        # 1h
        await run_replay(
            tape_path=None,
            report_path=str(out_dir / f"replay_{regime}_hourly.html"),
            equity=1000.0,
            tape=make_synthetic_week_hourly(seed=42, regime=regime),
            open_jobs_flag=False,
        )
    # compare files — content shape mirrors scripts.run_both_regimes
    # closely enough that a future drift in either path is visible,
    # but not a verbatim render (the determinism test only cares about
    # bit-stability across runs of this helper).
    for regime in ("bull", "bear", "chop"):
        for interval, suffix in (("5m", ""), ("1h", "_hourly")):
            with (out_dir / f"replay_{regime}{suffix}.json").open() as f:
                m = json.load(f)
            rows.append({
                "regime": regime,
                "interval": interval,
                "ending_equity": m["ending_equity"],
                "total_return_pct": m["total_return_pct"],
                "sharpe": m["sharpe"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "trades": m["trades"],
                "hit_rate": m["hit_rate"],
                "attribution": m["attribution"],
            })
    (out_dir / "replay_compare.json").write_text(
        json.dumps(rows, indent=2, default=str)
    )
    # Minimal stable HTML — content is fully determined by `rows`
    # above, so its hash is exactly as deterministic as the underlying
    # metrics.
    body = "\n".join(
        f"<tr><td>{r['regime']}</td><td>{r['interval']}</td>"
        f"<td>{r['total_return_pct']:+.4f}%</td>"
        f"<td>{r['ending_equity']:.4f}</td>"
        f"<td>{r['max_drawdown_pct']:.4f}%</td>"
        f"<td>{r['trades']}</td>"
        f"<td>{r['hit_rate']*100:.4f}%</td>"
        f"<td>{','.join(sorted(r['attribution'].keys())) or '-'}</td></tr>"
        for r in rows
    )
    (out_dir / "replay_compare.html").write_text(
        f"<!doctype html><table>{body}</table>"
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _replay_runner.py <output_dir>", file=sys.stderr)
        return 2
    asyncio.run(_run(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
