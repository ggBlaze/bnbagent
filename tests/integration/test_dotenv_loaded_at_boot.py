"""B: `.env` must be loaded into the process env at boot.

Today nothing in the boot path calls `load_dotenv()`. The dashboard has
helpers (`_get_env_var_from_dotenv`) that read `.env` on demand for
specific endpoints, but neither `core/main.py` nor
`dashboard/backend/main.py` calls `load_dotenv()` at startup. The
consequence: launching via `bash bnbagent` (which doesn't `source .env`
either) gives the agent a bare process env — no `TWAK_PWD`,
`TWAK_KEYSTORE`, `MINIMAX_API_KEY`, `BNBAGENT_ALLOW_*`. Wizard wallet
import fails, advisor LLM stays disabled, etc.

These tests spawn a child Python process that imports each module from
a tmp cwd containing a synthetic `.env`, then prints `os.environ` for
a unique probe key. If `load_dotenv()` ran, the probe leaks through.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


PROBE_KEY = "BNBAGENT_DOTENV_LOAD_PROBE"
PROBE_VAL = "loaded-at-boot-OK"


def _run_with_env(module: str, cwd: Path) -> str:
    """Spawn a child Python that imports `module` from `cwd` and prints
    the probe env var. The child inherits PYTHONPATH from this process
    so `import core.main` resolves against the repo, while `cwd` controls
    where `.env` is read from."""
    code = textwrap.dedent(f"""
        import os, sys
        # Defang the heavy side effects of importing core.main / dashboard.backend.main:
        # they kick off uvicorn / asyncio / boot() at module level under __main__,
        # but a plain `import` only triggers module-top side effects (which is what
        # we want — load_dotenv at the top runs but argparse/asyncio.run do not).
        import {module}  # noqa: F401
        print(os.environ.get({PROBE_KEY!r}, "MISSING"))
    """)
    repo_root = Path(__file__).resolve().parent.parent.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        # Make sure the parent env DOESN'T leak the probe — the only way it can
        # appear in the child's os.environ is via load_dotenv from cwd/.env.
        PROBE_KEY: "",
    }
    # Drop the probe key entirely so the only path to seeing it is dotenv.
    env.pop(PROBE_KEY)
    venv_python = repo_root / ".venv" / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable
    result = subprocess.run(
        [py, "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout.strip(), result.stderr


def test_core_main_loads_dotenv_at_import(tmp_path):
    """Importing `core.main` from a cwd with a `.env` must populate
    `os.environ`."""
    (tmp_path / ".env").write_text(f"{PROBE_KEY}={PROBE_VAL}\n")
    out, err = _run_with_env("core.main", tmp_path)
    assert out == PROBE_VAL, (
        f"core.main did not load .env at import. stdout={out!r} stderr={err[-500:]!r}"
    )


def test_dashboard_main_loads_dotenv_at_import(tmp_path):
    """Importing `dashboard.backend.main` from a cwd with a `.env` must
    populate `os.environ`. The dashboard is a sibling process to the
    agent under `bash bnbagent`, so it needs the same plumbing."""
    (tmp_path / ".env").write_text(f"{PROBE_KEY}={PROBE_VAL}\n")
    out, err = _run_with_env("dashboard.backend.main", tmp_path)
    assert out == PROBE_VAL, (
        f"dashboard.backend.main did not load .env at import. "
        f"stdout={out!r} stderr={err[-500:]!r}"
    )


def test_existing_env_vars_take_precedence_over_dotenv(tmp_path):
    """If a var is already in the shell env, dotenv must NOT overwrite
    it. This is the default python-dotenv behavior; pinning it here so
    a future refactor doesn't change semantics (e.g. someone adding
    `load_dotenv(override=True)` would silently break ops who export
    runtime overrides)."""
    (tmp_path / ".env").write_text(f"{PROBE_KEY}={PROBE_VAL}\n")
    code = textwrap.dedent(f"""
        import os
        import core.main  # noqa: F401
        print(os.environ.get({PROBE_KEY!r}, "MISSING"))
    """)
    repo_root = Path(__file__).resolve().parent.parent.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        PROBE_KEY: "shell-wins",
    }
    venv_python = repo_root / ".venv" / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable
    result = subprocess.run(
        [py, "-c", code], cwd=str(tmp_path), env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert result.stdout.strip() == "shell-wins", (
        f"expected shell env to win over .env; got {result.stdout!r}, "
        f"stderr={result.stderr[-500:]!r}"
    )
