"""Skill abstract base + context object passed to `run()`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SkillContext:
    """Information about the event that triggered this Skill invocation."""
    event: str                    # "trade_open" | "trade_close" | "tick" | "deploy" | "advisor" | ...
    portfolio: Any = None         # core.portfolio.Portfolio
    policy: dict | None = None
    components: dict | None = None  # full boot() result
    extra: dict | None = None     # event-specific (e.g. trade dict for trade_close)


class Skill(ABC):
    """A discoverable, toggleable agent module.

    Subclasses set the class attrs and implement setup/run/teardown/status.
    The registry uses `issubclass(cls, Skill)` (which is fine because Skill
    is a plain ABC, not a typing.Protocol).
    """
    name: str = ""
    category: str = ""        # "strategy" | "notification" | "data"
    description: str = ""
    version: str = "0.0.0"
    cost_per_call_usdc: float = 0.0
    requires: list[str] = []

    @abstractmethod
    async def setup(self, components: dict) -> None: ...

    @abstractmethod
    async def run(self, ctx: SkillContext, **kwargs) -> dict: ...

    async def teardown(self) -> None:
        pass

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "category": self.category}
