"""SkillRegistry — discovers, enables, disables Skills. Persists state."""
from __future__ import annotations

import importlib
import json
import logging
import os
import pkgutil
from pathlib import Path
from typing import Any

from .base import Skill, SkillContext

log = logging.getLogger(__name__)


DEFAULT_STATE_PATH = Path("~/.bnbagent/skills.json").expanduser()


class SkillRegistry:
    def __init__(self, state_path: Path = DEFAULT_STATE_PATH):
        self.state_path = state_path
        self._skills: dict[str, Skill] = {}
        self._enabled: set[str] = set()
        self._load_state()

    # --- state persistence ----------------------------------------------

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self._enabled = set(data.get("enabled", []) or [])
        except Exception:
            self._enabled = set()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"enabled": sorted(self._enabled)}, indent=2))

    # --- discovery ------------------------------------------------------

    def discover(self) -> None:
        """Import every module under skills/notification and skills/data."""
        import skills
        for category in ("notification", "data"):
            try:
                pkg = importlib.import_module(f"skills.{category}")
            except ImportError as e:
                log.info("skills.%s not importable: %s", category, e)
                continue
            for mod_info in pkgutil.iter_modules(pkg.__path__):
                full = f"skills.{category}.{mod_info.name}"
                try:
                    mod = importlib.import_module(full)
                except Exception as e:
                    log.warning("skill %s failed to import: %s", full, e)
                    continue
                for attr in dir(mod):
                    cls = getattr(mod, attr)
                    if not isinstance(cls, type):
                        continue
                    if cls is Skill or cls.__module__ != mod.__name__:
                        continue
                    # duck-type: must have a `name` class attr and a `run` method
                    if not getattr(cls, "name", ""):
                        continue
                    if not callable(getattr(cls, "run", None)):
                        continue
                    try:
                        instance = cls()
                        self._skills[instance.name] = instance
                        log.info("discovered skill: %s (%s)", instance.name, instance.category)
                    except Exception as e:
                        log.warning("skill %s.%s failed to instantiate: %s", full, attr, e)

    # --- enable / disable ----------------------------------------------

    def _missing_env(self, skill: Skill) -> list[str]:
        return [v for v in (skill.requires or []) if not os.environ.get(v)]

    def enable(self, name: str, components: dict | None = None) -> dict:
        skill = self._skills.get(name)
        if not skill:
            raise ValueError(f"unknown skill: {name}")
        missing = self._missing_env(skill)
        if missing:
            raise RuntimeError(f"skill {name!r} requires env: {missing}")
        self._enabled.add(name)
        self._save_state()
        if components is not None:
            try:
                import asyncio
                asyncio.get_event_loop().run_until_complete(skill.setup(components))
            except RuntimeError:
                # no event loop yet; setup will be called lazily on first run
                pass
        return {"enabled": True, "name": name, "status": skill.status()}

    def disable(self, name: str) -> dict:
        self._enabled.discard(name)
        self._save_state()
        return {"enabled": False, "name": name}

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[dict]:
        out = []
        for name, skill in sorted(self._skills.items()):
            missing = self._missing_env(skill)
            out.append({
                "name": name,
                "category": skill.category,
                "description": skill.description,
                "version": skill.version,
                "cost_per_call_usdc": float(skill.cost_per_call_usdc),
                "requires": list(skill.requires or []),
                "enabled": name in self._enabled,
                "ready": not missing,
                "missing_env": missing,
                "status": skill.status(),
            })
        return out

    def list_enabled(self) -> list[Skill]:
        return [self._skills[n] for n in self._enabled if n in self._skills]

    async def run_hook(self, event: str, components: dict | None = None,
                       extra: dict | None = None) -> None:
        """Call run() on every enabled notification skill for an event."""
        ctx = SkillContext(
            event=event, portfolio=(components or {}).get("portfolio"),
            policy=(components or {}).get("policy"),
            components=components, extra=extra or {},
        )
        for skill in self.list_enabled():
            try:
                await skill.run(ctx)
            except Exception as e:
                log.warning("skill %s failed: %s", skill.name, e)
