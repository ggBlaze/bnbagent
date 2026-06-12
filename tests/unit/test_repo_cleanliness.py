"""Contract test: files the agent writes at runtime must be gitignored.

The principle: "all the user does with the repo does not affect the
repo development" (Blaze, 2026-06-12). A fresh clone should not
contain operator-specific state, and the wizard/boot/dashboard
write paths must not dirty the working tree.

This test pins the current state. If a future contributor adds a new
write path to a tracked file, this test will flag it on the next CI
run.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- Tracked files: shipped defaults + templates only ---------------------

# Files that must be TRACKED (templates the user copies from).
EXPECTED_TRACKED = [
    "config/local.yaml.example",
    "config/policy.yaml.example",
    "config/config.yaml",
    "config/policy.schema.json",
    "config/allowlist.yaml",
    "config/perps_venues.yaml",
    "config/tokens.mainnet.yaml",
    "agents/providers.yaml",      # LLM provider routing (env var NAMES only, no keys)
    "agents/personas/advisor.md", # shipped = pro default after fce4da8
    "agents/personas/chat.md",
    "agents/personas/reviewer.md",
    "agents/personas/token_module.md",
    "agents/_pro_defaults/advisor.md",
    "agents/_pro_defaults/chat.md",
    "agents/_pro_defaults/reviewer.md",
    "agents/_pro_defaults/token_module.md",
]


# --- Gitignored files: user-specific state, never committed ---------------

# Entries that MUST appear in .gitignore so the user's runtime writes
# don't land in commits.
EXPECTED_GITIGNORED = [
    "config/local.yaml",          # v2.1.1 shadow for wizard/dashboard/boot
    "config/policy.yaml",         # v2.1.2 operator-signed policy
    "agents/token_module.yaml",   # v2.1.2 dashboard Token pane config
    ".bnbagent/",                  # identity, setup.json, runtime personas, skills.json
    ".twak/",                      # v2.1.2 keystore (defense in depth)
    "data/reports/*.html",         # render-only HTML
    "data/parquet/*",
    "data/recordings/*",
    "data/jobs-*.json",
    "data/window-*-summary.json",
    "__pycache__/",
    ".venv/",
    "node_modules/",
    "logs/",
    ".env",
    ".env.local",
]


def _git_ls_files() -> set[str]:
    """Return the set of files tracked in git (relative to repo root)."""
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def _read_gitignore() -> list[str]:
    """Return the .gitignore entries (raw, one per line, comments stripped)."""
    gi = (REPO_ROOT / ".gitignore").read_text()
    out = []
    for line in gi.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


# --- Tracked-files contract -------------------------------------------------

@pytest.mark.parametrize("rel_path", EXPECTED_TRACKED)
def test_expected_file_is_tracked(rel_path: str):
    """Templates + shipped defaults + the persona/_pro_defaults must be tracked."""
    tracked = _git_ls_files()
    assert rel_path in tracked, (
        f"{rel_path} should be tracked (shipped default / template). "
        f"Re-check after git pull: is it missing? Then 'git add' it."
    )


# --- Gitignored-files contract ---------------------------------------------

@pytest.mark.parametrize("gitignore_entry", EXPECTED_GITIGNORED)
def test_expected_entry_is_gitignored(gitignore_entry: str):
    """User-specific state + the keystore + build outputs must be gitignored."""
    entries = _read_gitignore()
    # The entry must appear verbatim (or with a trailing-slash-normalized form).
    candidates = {gitignore_entry, gitignore_entry.rstrip("/") + "/"}
    if "/" in gitignore_entry and gitignore_entry.endswith("/*"):
        # Glob entry; check the directory itself is also covered
        candidates.add(gitignore_entry.rstrip("/*") + "/")
    assert any(c in entries for c in candidates), (
        f"{gitignore_entry} (or equivalent) must be in .gitignore. "
        f"Current entries: {entries}"
    )


# --- Live-write contract: paths the agent writes at runtime ---------------

# These are the files the runtime CREATES or WRITES at runtime. None of
# them should be in the tracked set after this contract test runs.
RUNTIME_WRITE_PATHS = [
    "config/local.yaml",          # v2.1.1 wizard/dashboard/boot shadow
    "config/policy.yaml",         # v2.1.2 operator-signed policy
    "agents/token_module.yaml",   # v2.1.2 dashboard Token pane config
]


@pytest.mark.parametrize("rel_path", RUNTIME_WRITE_PATHS)
def test_runtime_write_path_is_not_tracked(rel_path: str):
    """Anything the runtime writes must NOT be in the tracked set."""
    tracked = _git_ls_files()
    assert rel_path not in tracked, (
        f"{rel_path} is a runtime write target but is in the tracked "
        f"set. Add it to .gitignore and `git rm --cached` it, otherwise "
        f"operator state can land in commits."
    )


# --- Concrete check: shipped personas match pro defaults -------------------

def test_shipped_personas_match_pro_defaults():
    """All 4 shipped personas must equal the pro defaults.

    If a shipped persona diverges, a fresh clone runs the OLDER persona
    (e.g. pre-v2.0.8-M7 chat without the cmc_global_filter repeat-back
    guardrail). Sync: `cp agents/_pro_defaults/<name>.md agents/personas/`.
    """
    import hashlib
    for name in ("advisor", "chat", "reviewer", "token_module"):
        shipped = (REPO_ROOT / "agents/personas" / f"{name}.md").read_bytes()
        pro = (REPO_ROOT / "agents/_pro_defaults" / f"{name}.md").read_bytes()
        assert hashlib.sha256(shipped).hexdigest() == hashlib.sha256(pro).hexdigest(), (
            f"agents/personas/{name}.md has diverged from "
            f"agents/_pro_defaults/{name}.md. Copy the pro default over "
            f"the shipped version: "
            f"cp agents/_pro_defaults/{name}.md agents/personas/{name}.md"
        )
