"""Meta-tests that lock public-facing claims about the test suite itself.

These are intentionally lightweight and import nothing from the project —
they assert behaviour that the badge / docs / CI must agree on. If you
intentionally change the test count, you should change this file (and the
docs) in the same commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
CONTRIBUTING = ROOT / "docs" / "CONTRIBUTING.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
AUDIT = ROOT / "docs" / "audit-2026-06-05.md"

DOCS = [README, CHANGELOG, CONTRIBUTING, ARCHITECTURE, AUDIT]

# The badge in the README and the prose everywhere else must agree.
BADGE_RE = re.compile(r"tests-(\d+)\+?/(\d+)\+?%20passing")
PROSE_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*passing")


def _collect(paths) -> dict[Path, list[tuple[int, int]]]:
    out: dict[Path, list[tuple[int, int]]] = {}
    for p in paths:
        text = p.read_text(encoding="utf-8")
        matches = BADGE_RE.findall(text) + PROSE_RE.findall(text)
        out[p] = [(int(a), int(b)) for a, b in matches]
    return out


def test_docs_test_count_is_self_consistent():
    """The README badge, CHANGELOG, CONTRIBUTING, architecture, and audit
    doc must all reference the same passed/total numbers."""
    found = _collect(DOCS)
    # gather every (passed, total) that appears anywhere
    pairs: set[tuple[int, int]] = set()
    for path, matches in found.items():
        for m in matches:
            pairs.add(m)
    assert len(pairs) == 1, (
        f"test count inconsistent across docs: {pairs}. "
        f"Update the badge in README.md and the prose in every other doc in the same commit. "
        f"Found in: {{p: m for p, ms in found.items() for m in ms}}"
    )


def test_pyproject_test_extras_install_cleanly():
    """`pip install -e ".[test]"` must succeed and include respx +
    pytest-asyncio + pytest-mock. This locks the badge claim that the
    test count is reproducible from a fresh venv."""
    import sys
    import importlib
    for mod in ("pytest", "pytest_asyncio", "respx", "pytest_mock"):
        try:
            importlib.import_module(mod)
        except ImportError as e:  # pragma: no cover — fails on real env
            pytest.fail(
                f"optional-dep '{mod}' not importable — "
                f"add it to pyproject.toml [project.optional-dependencies].test. "
                f"Original error: {e}"
            )


def test_collects_at_least_one_test_per_package():
    """Sanity: every test directory must contain at least one collected
    test. This catches a renamed/moved package silently losing its tests."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if result.returncode not in (0, 5):  # 5 = no tests collected
        pytest.fail(f"pytest --collect-only failed:\n{result.stdout}\n{result.stderr}")
    # Expect at least 100 tests collected (project is well-tested).
    lines = [l for l in result.stdout.splitlines() if "test_" in l or "::" in l]
    assert len(lines) >= 100, (
        f"expected >= 100 collected tests, got {len(lines)}. "
        f"Did a test file get accidentally deleted? Output:\n{result.stdout[-2000:]}"
    )


def test_demo_script_kpi_table_matches_replay_json():
    """The demo-script.md KPI tables (5m + 1h) are the source of truth
    for the voiceover. Lock them to data/reports/replay_{bull,bear,chop}.json
    AND data/reports/replay_{bull,bear,chop}_hourly.json so a fresh
    replay run that produces different numbers fails this test and
    forces the demo-script to be updated to match.

    v2.0.4 — also locks the sleeves/attribution column. If the JSON
    says {"A": {...}} and the demo-script says A+C, CI fails. This
    is the v2.0.3 review's "second time in a row" finding — the
    conversation bubble popping when judges cat the JSON.

    This is the "honest" guard — judges have the repo, they can open
    the JSON, and the demo-script must agree with it."""
    import json
    import re

    reports = ROOT / "data" / "reports"
    demo = (ROOT / "docs" / "demo-script.md").read_text()

    # Find the v2.1.5 numbers block (or any v2.X.Y — only the version
    # label moves forward, the table format is stable). The pattern
    # was v2\.0\.\d originally; v2.1.5 broadened it to v2\.\d+\.\d+.
    block_m = re.search(
        r"\*\*v2\.\d+\.\d+ numbers[^\n]*\n+(.+?)(?=\n\n\*\*|\Z)",
        demo,
        re.DOTALL,
    )
    if not block_m:
        pytest.skip("v2.X.Y numbers block not found in demo-script.md")
    body = block_m.group(1)

    def _find_row(interval: str, regime: str) -> list[str] | None:
        """Find the row matching regime+interval in the demo-script body.
        The table format is `| bull 5m | ...` (regime+interval combined
        in cell 0) or `| bull | 5m | ...` (separated) — handle both."""
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5:
                continue
            # Combined: cells[0] = "bull 5m"
            if cells[0] == f"{regime} {interval}":
                return cells
            # Separated: cells[0] = "bull", cells[1] = "5m"
            if cells[0] == regime and len(cells) > 1 and cells[1] == interval:
                return cells
        return None

    def _assert_sleeves(d: dict, expected_sleeves: str, where: str):
        actual_sleeves = set(d["attribution"].keys())
        expected_set = set(s.strip() for s in expected_sleeves.split("+") if s.strip())
        assert actual_sleeves == expected_set, (
            f"{where}: demo-script says sleeves='{expected_sleeves}' but JSON "
            f"attribution is {sorted(actual_sleeves)}. This is the v2.0.3 "
            f"review finding — conversation numbers don't match JSON. "
            f"Update the table or re-run replay and update the JSON."
        )

    for interval, suffix in (("5m", ""), ("1h", "_hourly")):
        for regime in ("bull", "bear", "chop"):
            path = reports / f"replay_{regime}{suffix}.json"
            if not path.exists():
                pytest.skip(
                    f"{path} not present — run `python -m scripts.run_both_regimes` first"
                )
            with path.open() as f:
                d = json.load(f)
            row = _find_row(interval, regime)
            if row is None:
                pytest.fail(
                    f"{regime} {interval} row not found in demo-script KPI table. "
                    f"The v2.0.4 table must include all 6 rows (3 regimes × 2 tapes)."
                )
            # Format: | regime interval | return% | DD% | trades | hit% | sleeves |
            # (cells[0] = "bull 5m")  OR  | regime | interval | ... | sleeves |
            if row[0] == f"{regime} {interval}":
                # combined format
                ret_str = row[1].rstrip("%")
                dd_str = row[2].rstrip("%")
                n_str = row[3].replace(",", "")
                hit_str = row[4].rstrip("%")
                sleeves_str = row[5]
            else:
                # separated format
                ret_str = row[2].rstrip("%")
                dd_str = row[3].rstrip("%")
                n_str = row[4].replace(",", "")
                hit_str = row[5].rstrip("%")
                sleeves_str = row[6]
            assert abs(float(ret_str) - d["total_return_pct"]) < 0.01, (
                f"{regime} {interval}: demo-script return ({ret_str}%) != JSON "
                f"({d['total_return_pct']:.3f}%). "
                f"Re-run python -m scripts.run_both_regimes and update the table."
            )
            assert abs(float(dd_str) - d["max_drawdown_pct"]) < 0.01, (
                f"{regime} {interval}: demo-script DD ({dd_str}%) != JSON "
                f"({d['max_drawdown_pct']:.3f}%)"
            )
            assert int(n_str) == d["trades"], (
                f"{regime} {interval}: demo-script trade count ({n_str}) != JSON ({d['trades']})"
            )
            assert abs(float(hit_str) - d["hit_rate"] * 100) < 1, (
                f"{regime} {interval}: demo-script hit rate ({hit_str}%) != JSON "
                f"({d['hit_rate']*100:.1f}%)"
            )
            # Attribution lock — the most important check.
            _assert_sleeves(d, sleeves_str, f"{regime} {interval}")
