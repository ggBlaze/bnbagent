"""BaseAgent: persona loader + LLM call helper used by all 3 agents.

Personas are markdown files with YAML front-matter:
  ---
  name: advisor
  version: 1.0.0
  pro_default_sha256: <auto-computed at build time>
  ---
  <system prompt body>

Two directories:
  agents/_pro_defaults/{name}.md   — canonical pro defaults (shipped, also
                                    pinned on IPFS via ERC-8004 identity)
  agents/personas/{name}.md        — live, user-editable; copied from
                                    `_pro_defaults/` on first boot
  ~/.bnbagent/personas/{name}.md   — runtime copy (always takes precedence
                                    over the in-repo `personas/` copy, so
                                    user edits survive `git pull`)

The persona loader returns a `Persona` dataclass with:
  - `system`  — the rendered system prompt
  - `diverged` — sha256(user) != sha256(pro_default)
  - `mtime`  — file mtime (used for hot-reload)
  - `sha256` — current content hash
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


PRO_DEFAULTS_DIR = Path("agents/_pro_defaults")
SHIPPED_PERSONAS_DIR = Path("agents/personas")
RUNTIME_PERSONAS_DIR = Path("~/.bnbagent/personas").expanduser()


def _read_md(path: Path) -> tuple[dict, str]:
    """Parse a markdown-with-frontmatter file. Returns (frontmatter_dict, body)."""
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        fm = {}
    body = parts[2].lstrip("\n")
    return fm, body


def _write_md(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
    path.write_text(f"---\n{fm_text}\n---\n{body}", encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Persona:
    name: str
    system: str                  # system prompt body
    path: Path                   # where it was loaded from
    pro_default_path: Path
    sha256: str
    pro_default_sha256: str
    diverged: bool
    version: str
    mtime: float

    def short(self) -> str:
        return f"Persona({self.name}, v{self.version}, diverged={self.diverged}, path={self.path})"


# --- bootstrap --------------------------------------------------------------

def bootstrap_personas() -> None:
    """On first boot, ensure runtime + shipped personas dirs exist and are
    populated from `_pro_defaults/`."""
    RUNTIME_PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    SHIPPED_PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    if not PRO_DEFAULTS_DIR.exists():
        log.warning("pro_defaults dir missing: %s", PRO_DEFAULTS_DIR)
        return
    for pro in PRO_DEFAULTS_DIR.glob("*.md"):
        for dest in (SHIPPED_PERSONAS_DIR / pro.name, RUNTIME_PERSONAS_DIR / pro.name):
            if not dest.exists():
                shutil.copy2(pro, dest)
                log.info("seeded persona %s → %s", pro.name, dest)


# --- loader -----------------------------------------------------------------

class PersonaLoader:
    """Caches loaded personas and re-reads on mtime change."""

    def __init__(self, name: str, runtime_dir: Path = RUNTIME_PERSONAS_DIR,
                 shipped_dir: Path = SHIPPED_PERSONAS_DIR,
                 pro_dir: Path = PRO_DEFAULTS_DIR):
        self.name = name
        self.runtime_dir = runtime_dir
        self.shipped_dir = shipped_dir
        self.pro_dir = pro_dir
        self._cache: Persona | None = None
        self._cache_mtime: float = 0.0

    def _resolve_path(self) -> Path:
        # runtime takes precedence over shipped
        for d in (self.runtime_dir, self.shipped_dir):
            p = d / f"{self.name}.md"
            if p.exists():
                return p
        # fall back to pro default (read-only)
        return self.pro_dir / f"{self.name}.md"

    def load(self, force: bool = False) -> Persona:
        path = self._resolve_path()
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if not force and self._cache and abs(self._cache_mtime - mtime) < 1e-6:
            return self._cache
        fm, body = _read_md(path)
        pro_path = self.pro_dir / f"{self.name}.md"
        pro_text = pro_path.read_text(encoding="utf-8") if pro_path.exists() else body
        pro_sha = _sha256(pro_text)
        cur_sha = _sha256(path.read_text(encoding="utf-8")) if path.exists() else ""
        self._cache = Persona(
            name=self.name,
            system=body.strip(),
            path=path,
            pro_default_path=pro_path,
            sha256=cur_sha,
            pro_default_sha256=pro_sha,
            diverged=cur_sha != pro_sha and path != pro_path,
            version=str(fm.get("version", "1.0.0")),
            mtime=mime(),
        )
        self._cache_mtime = mtime
        return self._cache

    def reset_to_pro(self) -> Persona:
        """Copy pro default to runtime + shipped; force re-load."""
        pro_path = self.pro_dir / f"{self.name}.md"
        if not pro_path.exists():
            raise FileNotFoundError(f"no pro default at {pro_path}")
        for d in (self.runtime_dir, self.shipped_dir):
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pro_path, d / f"{self.name}.md")
        return self.load(force=True)

    def save_user(self, body: str, version: str = "1.0.0") -> Persona:
        """Write a new user persona body to the runtime dir."""
        path = self.runtime_dir / f"{self.name}.md"
        _write_md(path, {"name": self.name, "version": version}, body.strip() + "\n")
        return self.load(force=True)


def mime() -> float:
    """current time as a float — used as a placeholder when the file doesn't exist"""
    return datetime.now().timestamp()


# --- LLM call helper --------------------------------------------------------

async def llm_complete(routing, messages, **kwargs) -> str:
    """Call the LLM if the routing is enabled; return "" if disabled.

    Centralized so advisor/reviewer/chat all share the same degraded-mode behavior.

    v2.1.5: MiniMax M3 (and any other reasoning-capable model) wraps the
    actual response in a <think>...</think> block. We strip that here so
    the callers (reviewer, advisor, chat) see clean content. If the
    model returns JSON inside the think block + something else outside,
    the strip puts the second half in the caller's hand and they can
    json.loads() the tail.
    """
    if not routing.enabled or routing.client is None:
        log.info("LLM disabled for %s: %s", routing.provider_name, routing.reason)
        return ""
    try:
        raw = await routing.client.complete(
            messages,
            model=routing.model,
            max_tokens=routing.max_tokens,
            temperature=routing.temperature,
            response_format=kwargs.pop("response_format", None),
            timeout_s=kwargs.pop("timeout_s", 8.0),
        )
        if not raw:
            return ""
        # Strip a leading <think>...</think> reasoning block if present.
        # Some reasoning models (e.g. MiniMax M3) emit a think block before
        # the actual answer; downstream callers expect clean content.
        import re
        stripped = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL).strip()
        return stripped or raw  # if the strip ate everything, return the original
    except Exception as e:
        log.warning("LLM call failed (%s): %s", routing.provider_name, e)
        return ""


async def llm_stream(routing, messages, **kwargs):
    """Stream tokens from the LLM. Yields "" if disabled. Never raises."""
    if not routing.enabled or routing.client is None:
        log.info("LLM disabled for streaming (%s): %s", routing.provider_name, routing.reason)
        if False:
            yield ""  # noqa
        return
    try:
        async for chunk in routing.client.stream(
            messages,
            model=routing.model,
            max_tokens=routing.max_tokens,
            temperature=routing.temperature,
            timeout_s=kwargs.pop("timeout_s", 30.0),
        ):
            yield chunk
    except Exception as e:
        log.warning("LLM stream failed (%s): %s", routing.provider_name, e)
        return


# --- person file path helpers (for the dashboard) ---------------------------

def list_persona_names() -> list[str]:
    """Return the names of all personas we know about."""
    names = set()
    for d in (PRO_DEFAULTS_DIR, SHIPPED_PERSONAS_DIR, RUNTIME_PERSONAS_DIR):
        if d.exists():
            for p in d.glob("*.md"):
                names.add(p.stem)
    return sorted(names)


def read_persona_raw(name: str) -> str:
    """Return the raw markdown text of the user's runtime persona (or empty)."""
    p = RUNTIME_PERSONAS_DIR / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""
