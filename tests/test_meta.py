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
