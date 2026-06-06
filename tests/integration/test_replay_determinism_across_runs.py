"""Regression test for v2.0.7. Prior to this commit, replay was not
deterministic: int(time.time()) was used in 5 places (backtest/replay.py
lines 69, 93, 261, 327 and core/control.py:93), causing the synthetic
tape's absolute timestamps to vary between runs. This caused the
1h-tape attribution to swing between 'A only' and 'A + C', and the
bear 1h return to swing between -0.58% and +219% on identical input.

The bug mechanism: backtest.replay.make_synthetic_week anchors candle
timestamps to `int(time.time())`; make_synthetic_week_hourly then
floor-buckets those into hour windows via `c["ts"] // 3600 * 3600`.
The number of 5-min bars that fall into each hour depends on
`epoch mod 3600`, so a different wall-clock at run time produces a
different hourly OHLCV series → different z-scores for Sleeve C →
different attribution. The 5m tape is alignment-invariant (sleeves
read returns/z-scores over candle counts, not absolute times), which
is why 5m metrics were stable before the fix while 1h metrics
weren't.

This test runs the replay 3 times in subprocesses under three
different injected wall-clock offsets that are each > 3600 seconds
apart, guaranteeing each run lands on a different `epoch mod 3600`
and therefore a different hourly bucket alignment. With the bug
present, the three runs produce different 1h JSON and HTML; with the
fix in place, all 14 output files (7 JSON + 7 HTML) are bit-identical
across all 3 runs (SHA-256 hashes match).

Subprocesses run with -B (PYTHONDONTWRITEBYTECODE=1) so stale .pyc
caches can't mask a regression.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
HELPER = ROOT / "tests" / "integration" / "_replay_runner.py"

# Three offsets chosen so that each `(offset mod 3600)` lands in a
# different 300-second bin. The bug is in
# `make_synthetic_week_hourly`: it buckets 5m bars by
# `ts // 3600 * 3600`, so the bar-count per hour only changes when
# `epoch mod 3600` crosses a multiple of 300. (Empirically verified:
# offsets like 0/3601/7259 — mod 3600 = 0/1/59 — all fall in the
# same bin and produce identical bucketing despite naively-large
# spacing.) These offsets give mod-3600 values of 2800/2000/1200,
# guaranteeing 4+ crossings between any pair. After the v2.0.7 fix
# the code never reads wall-clock, so the offset is irrelevant and
# all three runs produce identical files.
OFFSETS = (10_000, 20_000, 30_000)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_all(out_dir: Path) -> dict[str, str]:
    return {p.name: _sha256(p) for p in sorted(out_dir.glob("replay_*"))}


def test_replay_is_deterministic_across_runs(tmp_path):
    runs: list[tuple[int, dict[str, str]]] = []
    for offset in OFFSETS:
        out = tmp_path / f"run_offset_{offset}"
        env = os.environ.copy()
        env["TEST_TIME_OFFSET"] = str(offset)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        r = subprocess.run(
            [sys.executable, "-B", str(HELPER), str(out)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if r.returncode != 0:
            pytest.fail(
                f"helper subprocess failed (offset={offset}):\n"
                f"--- STDOUT ---\n{r.stdout[-3000:]}\n"
                f"--- STDERR ---\n{r.stderr[-3000:]}"
            )
        h = _hash_all(out)
        assert h, (
            f"helper produced no replay_* files in {out} "
            f"(offset={offset}). stdout tail:\n{r.stdout[-1000:]}"
        )
        runs.append((offset, h))

    # All runs must produce the same file set.
    base_files = set(runs[0][1])
    for offset, hashes in runs[1:]:
        sym_diff = set(hashes) ^ base_files
        assert not sym_diff, (
            f"run offset={offset} produced a different file set:\n"
            f"  only in this run: {sorted(set(hashes) - base_files)}\n"
            f"  missing here:     {sorted(base_files - set(hashes))}"
        )

    # Pivot on file name: collect each file's hash from each run, find
    # the files that drift.
    drift: dict[str, list[tuple[int, str]]] = {}
    for fname in sorted(base_files):
        per_run = [(off, h[fname]) for off, h in runs]
        if len({h for _, h in per_run}) > 1:
            drift[fname] = per_run

    if drift:
        lines = [
            f"Replay output differs across {len(runs)} runs "
            f"(non-deterministic). Drifted files:",
        ]
        for fname, per in drift.items():
            lines.append(f"  {fname}")
            for off, h in per:
                lines.append(f"    offset={off:5d}  sha256={h[:16]}…")
        lines.append("")
        lines.append(
            "Root cause: wall-clock reads in backtest/replay.py and "
            "core/control.py make the synthetic tape's absolute "
            "timestamps depend on `int(time.time())` at run time. The "
            "1h-aggregation buckets 5m bars by `ts // 3600`, so "
            "different epochs produce different hourly OHLCV → "
            "different Sleeve C signals → different attribution. "
            "Fix: replace int(time.time()) at the 5 sites "
            "(backtest/replay.py:69,93,261,327 + core/control.py:93)."
        )
        pytest.fail("\n".join(lines))
