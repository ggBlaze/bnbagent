"""Test fixture: FakeLLMClient that returns scripted responses."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class FakeLLMClient:
    """Drop-in replacement for any LLMClient. Records calls, returns scripted text.

    Scripts are popped in order; if exhausted, returns "" for complete and yields
    nothing for stream. Use `raise_on_call` to simulate API errors.
    """

    def __init__(self, scripts: list[str] | None = None, *, raise_on_call: Exception | None = None,
                 stream_chunks: list[str] | None = None):
        self.scripts = list(scripts or [])
        self.stream_chunks = list(stream_chunks or [])
        self._idx = 0
        self._stream_idx = 0
        self.calls: list[dict[str, Any]] = []
        self.raise_on_call = raise_on_call

    @property
    def name(self) -> str:
        return "fake"

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.2,
                      response_format=None, timeout_s=8.0) -> str:
        self.calls.append({
            "messages": list(messages), "model": model, "max_tokens": max_tokens,
            "temperature": temperature, "response_format": response_format,
        })
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self._idx < len(self.scripts):
            out = self.scripts[self._idx]
            self._idx += 1
            return out
        return ""

    async def stream(self, messages, *, model=None, max_tokens=1024, temperature=0.2, timeout_s=30.0):
        self.calls.append({
            "messages": list(messages), "model": model, "max_tokens": max_tokens,
            "temperature": temperature, "stream": True,
        })
        if self.raise_on_call is not None:
            raise self.raise_on_call
        chunks = self.stream_chunks or (self.scripts[self._idx].split(" ") if self._idx < len(self.scripts) else [])
        self._idx += 1
        for c in chunks:
            yield c
            await asyncio.sleep(0)  # cooperative yield

    def call_count(self) -> int:
        return len(self.calls)

    def last_messages(self) -> list[dict]:
        return self.calls[-1]["messages"] if self.calls else []


def make_fake(scripts: list[str] | None = None, **kw) -> FakeLLMClient:
    """Factory used in fixtures."""
    return FakeLLMClient(scripts=scripts, **kw)
