"""Config path resolution — the local.yaml shadow pattern.

`config/config.yaml` is **tracked** in git and holds the shipped defaults
(testnet, mock tier, empty API key, 3 default Base RPCs). It is the
fallback for every read.

`config/local.yaml` is **gitignored** and holds user-specific state
(wizard-picked tier, CMC Pro API key, custom Base address + RPCs, the
base_address that boot auto-writes). Created on first run by copying
`config/local.yaml.example`. Never committed.

Read resolution: `local.yaml` (override) is deep-merged on top of
`config.yaml` (base). Lists in `local.yaml` replace lists in `config.yaml`.
The merged view is what the rest of the agent sees.

Write resolution: every wizard/dashboard/boot write goes to `local.yaml`.
The shipped `config.yaml` is treated as immutable from runtime code — it
is updated only by `git pull` or a fresh clone.

Why: before this refactor, every Setup-wizard run and every boot
auto-wrote to `config/config.yaml` (a tracked file), so the working
tree accumulated the operator's local state. Worse, the CMC Pro API
key landed in a file that could be accidentally committed. The
shadow pattern makes the safety boundary explicit and matches the
convention used by tools like git (`config` vs `config.local`),
vim (`vimrc` vs `vimrc.local`), and many others.

Backwards compat: callers that pass an explicit `--config path` to
`policy/policy_sign.py` still work; only the default path is
shadowed. Anyone reading `Path("config/config.yaml")` directly is
reading the shipped defaults only (no user overrides).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# These are intentionally module-level so tests can monkeypatch.chdir and
# have the helper resolve paths relative to the new cwd. The shape is
# "config/<file>.yaml" relative to cwd, matching every call site that
# currently uses `Path("config/config.yaml")`.
DEFAULT_CONFIG = Path("config/config.yaml")
LOCAL_CONFIG = Path("config/local.yaml")
LOCAL_EXAMPLE = Path("config/local.yaml.example")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base` in place. Lists in
    `override` replace lists in `base` (not deep-merged).
    """
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(*, base_dir: Path | None = None) -> dict:
    """Read the merged config: `local.yaml` on top of `config.yaml`.

    Returns {} if neither file exists. Returns the merged view if only
    `config.yaml` exists. Returns just the local config if only
    `local.yaml` exists (the user can run the agent with a local-only
    config; the shipped defaults are optional).

    The `base_dir` kwarg is for tests that want to point the helper at
    a tmp directory; production callers leave it None and the paths
    resolve relative to cwd (which is the repo root when running via
    `bash bnbagent` or `pytest` from the repo).
    """
    base = base_dir or Path.cwd()
    default = base / DEFAULT_CONFIG
    local = base / LOCAL_CONFIG
    cfg: dict = {}
    if default.exists():
        cfg = yaml.safe_load(default.read_text()) or {}
    if local.exists():
        override = yaml.safe_load(local.read_text()) or {}
        _deep_merge(cfg, override)
    return cfg


def write_local(cfg: dict, *, base_dir: Path | None = None) -> None:
    """Write the user-specific config to `local.yaml` (atomic-ish).

    Creates the parent dir if missing. The write is `.tmp` + rename so
    a crash mid-write doesn't leave a half-written file.

    `cfg` is the FULL desired state of the local file, not a delta —
    callers load the merged config, mutate, and pass the result here.
    The shadow semantics (local overrides base) mean the user will see
    the merged view of (this file) + (tracked config.yaml).
    """
    base = base_dir or Path.cwd()
    local = base / LOCAL_CONFIG
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_suffix(local.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
    tmp.replace(local)


def ensure_local_example_copied(*, base_dir: Path | None = None) -> bool:
    """Copy `local.yaml.example` → `local.yaml` if `local.yaml` is missing.

    Returns True if a copy happened, False if `local.yaml` already
    existed (or the example was missing). Called by `install.sh` and
    by boot when a wizard write is requested but no local file exists.
    """
    base = base_dir or Path.cwd()
    example = base / LOCAL_EXAMPLE
    local = base / LOCAL_CONFIG
    if local.exists() or not example.exists():
        return False
    write_local({}, base_dir=base)  # create the file with empty {}
    # Actually copy the example contents so the user can see the shape.
    local.write_text(example.read_text())
    return True


def config_dir(*, base_dir: Path | None = None) -> Path:
    """Return the config directory (parent of the two config files)."""
    base = base_dir or Path.cwd()
    return base / "config"
