"""Skills package — discoverable, hot-toggled modules that hook into the agent's lifecycle.

A Skill is anything that:
  - has a stable name + version
  - declares required env vars
  - has a `run(ctx, **kwargs)` async method called by the agent on events
  - can be enabled/disabled from the chat or dashboard

Categories: strategy | notification | data
"""
from .base import Skill, SkillContext
from .registry import SkillRegistry

__all__ = ["Skill", "SkillContext", "SkillRegistry"]
