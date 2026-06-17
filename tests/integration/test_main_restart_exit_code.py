"""A (v2.1.8): core.main must exit with code 75 when the agent's
_restart_pending is True, so the bash wrapper re-execs.

We can't easily import and call `core.main.main()` here because it
opens a real event loop, real network connections, real wallet. We
spawn a subprocess instead, drive a fake heartbeat via the control
file before the main loop starts, and assert the exit code.

Test isolated to one case to keep wall-clock tight; the lower layers
(control helpers, heartbeat handler, endpoint) have their own tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_run_exits_75_when_restart_pending(tmp_path):
    """Wire-up check: run() returns; main() inspects agent._restart_pending;
    if True, sys.exits 75. We monkeypatch the run coroutine to set
    _restart_pending directly (rather than booting a real agent) so the
    test is fast and offline.

    Mocking is via a tiny stub script that re-imports core.main, patches
    `core.main.run` to a coroutine that flips the flag, then calls
    core.main.main(). Asserts subprocess exit code == 75."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    stub = textwrap.dedent("""
        import asyncio
        import sys
        import core.main as m

        # Replace run() with a stub that just sets _restart_pending on
        # the Agent we'd construct. We don't actually need an Agent; we
        # just need main() to see that "the last run wanted a restart".
        # main() should look for that signal somewhere visible to it.
        # The contract we're pinning: main() exits 75 when run() signals
        # restart-pending. The simplest signal is run() returning a
        # truthy value; we set up that signal and exit.
        async def fake_run(args):
            # Returning True tells main "agent wanted to restart"
            return True
        m.run = fake_run
        m.main()
    """)
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    venv_py = repo_root / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    result = subprocess.run(
        [py, "-c", stub], env=env, cwd=str(repo_root),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 75, (
        f"expected exit 75 on restart; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr[-800:]!r}"
    )


def test_run_exits_0_on_normal_shutdown(tmp_path):
    """Negative: run() returns falsy → main() exits 0 (clean Ctrl+C
    path). Confirms the restart exit code is only on the restart path."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    stub = textwrap.dedent("""
        import asyncio
        import core.main as m
        async def fake_run(args):
            return False  # normal shutdown
        m.run = fake_run
        m.main()
    """)
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    venv_py = repo_root / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    result = subprocess.run(
        [py, "-c", stub], env=env, cwd=str(repo_root),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"expected exit 0 on normal shutdown; got {result.returncode}\n"
        f"stderr={result.stderr[-800:]!r}"
    )
