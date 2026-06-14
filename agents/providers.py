"""LLM provider layer.

Single LLMClient Protocol; 5 adapter implementations (Anthropic, OpenAI,
OpenRouter, OAI-compatible, local); per-agent provider+model routing via
`agents/providers.yaml`. No third-party SDKs — all calls go through
`httpx.AsyncClient` to keep the dep surface tiny.

Public surface:
    LLMClient            — Protocol
    AnthropicClient      — Anthropic Messages API
    OpenAIClient         — OpenAI Chat Completions API
    OpenRouterClient     — OpenRouter (Anthropic-format headers, OAI body)
    GenericOAICompatClient — any OAI-compatible endpoint (Mistral, etc)
    LocalLLMClient       — local llama.cpp / ollama
    PROVIDERS            — name → class registry
    build_provider(name, cfg) — instantiate + resolve env
    LLMRouter            — per-agent provider+model resolver with semaphore
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable

import httpx
import yaml

log = logging.getLogger(__name__)


# --- protocol ---------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        response_format: dict | None = None,
        timeout_s: float = 8.0,
    ) -> str: ...

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout_s: float = 30.0,
    ) -> AsyncIterator[str]: ...


# --- env substitution -------------------------------------------------------

def _resolve_env(value: Any) -> Any:
    """Recursively expand $VAR and ${VAR} in strings, leaves other types alone."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
        if value.startswith("$"):
            return os.environ.get(value[1:], "")
        return value
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


# --- shared helpers ---------------------------------------------------------

def _system_text(messages: list[dict]) -> str:
    parts = [m["content"] for m in messages if m.get("role") == "system"]
    return "\n\n".join(parts)


def _history_without_system(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") != "system"]


# --- Anthropic adapter ------------------------------------------------------

class AnthropicClient:
    name = "anthropic"

    def __init__(self, base: str, api_key: str, default_model: str = "claude-3-5-haiku-latest"):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self._client = httpx.AsyncClient(timeout=30.0)

    async def complete(
        self, messages, *, model=None, max_tokens=1024, temperature=0.2,
        response_format=None, timeout_s=8.0,
    ) -> str:
        model = model or self.default_model
        system = _system_text(messages) or None
        body: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": _history_without_system(messages),
        }
        if system:
            body["system"] = system
        if response_format and response_format.get("type") == "json_object":
            # Anthropic doesn't have a native JSON mode; we just instruct in the
            # system message (the advisor's persona already does this).
            pass
        r = await self._client.post(
            f"{self.base}/v1/messages", json=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        # concatenate text blocks
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )

    async def stream(self, messages, *, model=None, max_tokens=1024, temperature=0.2, timeout_s=30.0):
        model = model or self.default_model
        system = _system_text(messages) or None
        body: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": _history_without_system(messages), "stream": True,
        }
        if system:
            body["system"] = system
        async with self._client.stream(
            "POST", f"{self.base}/v1/messages", json=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            timeout=timeout_s,
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except Exception:
                    continue
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")


# --- OpenAI adapter ---------------------------------------------------------

class OpenAIClient:
    name = "openai"

    def __init__(self, base: str, api_key: str, default_model: str = "gpt-4o-mini"):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self._client = httpx.AsyncClient(timeout=30.0)

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.2,
                      response_format=None, timeout_s=8.0) -> str:
        model = model or self.default_model
        body: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages,
        }
        if response_format and response_format.get("type") == "json_object":
            body["response_format"] = {"type": "json_object"}
        r = await self._client.post(
            f"{self.base}/v1/chat/completions", json=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "content-type": "application/json"},
            timeout=timeout_s,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def stream(self, messages, *, model=None, max_tokens=1024, temperature=0.2, timeout_s=30.0):
        model = model or self.default_model
        body = {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages, "stream": True,
        }
        async with self._client.stream(
            "POST", f"{self.base}/v1/chat/completions", json=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "content-type": "application/json"},
            timeout=timeout_s,
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except Exception:
                    continue
                for chunk in evt.get("choices", []):
                    delta = chunk.get("delta", {}).get("content")
                    if delta:
                        yield delta


# --- OpenRouter adapter -----------------------------------------------------

class OpenRouterClient(OpenAIClient):
    """OpenRouter is OAI-compatible but requires extra headers."""
    name = "openrouter"

    def __init__(self, base: str, api_key: str, default_model: str = "anthropic/claude-3.5-haiku"):
        super().__init__(base=base, api_key=api_key, default_model=default_model)
        self._extra_headers = {
            "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://bnbagent.local"),
            "X-Title":      os.environ.get("OPENROUTER_TITLE", "BNB Agent"),
        }

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.2,
                      response_format=None, timeout_s=8.0) -> str:
        model = model or self.default_model
        body: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages,
        }
        if response_format and response_format.get("type") == "json_object":
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        headers.update(self._extra_headers)
        r = await self._client.post(
            f"{self.base}/v1/chat/completions", json=body, headers=headers, timeout=timeout_s,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def stream(self, messages, *, model=None, max_tokens=1024, temperature=0.2, timeout_s=30.0):
        model = model or self.default_model
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": messages, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        headers.update(self._extra_headers)
        async with self._client.stream(
            "POST", f"{self.base}/v1/chat/completions", json=body,
            headers=headers, timeout=timeout_s,
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except Exception:
                    continue
                for chunk in evt.get("choices", []):
                    delta = chunk.get("delta", {}).get("content")
                    if delta:
                        yield delta


# --- Generic OAI-compatible -------------------------------------------------

class GenericOAICompatClient(OpenAIClient):
    name = "oai_compat"


# --- Local LLM (OAI-compatible) --------------------------------------------

class LocalLLMClient(OpenAIClient):
    name = "local"

    def __init__(self, base: str = "http://127.0.0.1:8080", api_key: str = "", default_model: str = "local"):
        super().__init__(base=base, api_key=api_key or "no-key", default_model=default_model)


# --- registry ---------------------------------------------------------------

PROVIDERS: dict[str, type[LLMClient]] = {
    "anthropic":  AnthropicClient,
    "openai":     OpenAIClient,
    "openrouter": OpenRouterClient,
    "oai_compat": GenericOAICompatClient,
    "minimax":    OpenAIClient,  # v2.1.5+: MiniMax (OpenAI-compatible endpoint at api.minimaxi.chat)
    "local":      LocalLLMClient,
}


def build_provider(name: str, cfg: dict) -> LLMClient:
    """Instantiate a provider from a config dict (already env-resolved)."""
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(f"unknown provider: {name!r}; choose from {list(PROVIDERS)}")
    base = cfg.get("base", "")
    api_key = cfg.get("key", "")
    if name != "local" and not api_key:
        raise ValueError(f"provider {name!r} requires an API key in providers.yaml or env")
    return cls(base=base, api_key=api_key, default_model=cfg.get("default_model", ""))


def load_providers_config(path: str | Path = "agents/providers.yaml") -> dict:
    """Load and env-resolve `agents/providers.yaml`. Returns the resolved dict."""
    p = Path(path)
    if not p.exists():
        return {"default": "openrouter", "providers": {}, "agents": {}}
    raw = yaml.safe_load(p.read_text()) or {}
    return _resolve_env(raw)


# --- router -----------------------------------------------------------------

@dataclass
class AgentRouting:
    provider_name: str
    client: LLMClient | None
    model: str
    max_tokens: int = 1024
    temperature: float = 0.2
    enabled: bool = False
    reason: str = ""


class LLMRouter:
    """Per-agent provider+model resolution with a shared concurrency gate."""

    def __init__(self, config: dict | None = None, max_concurrency: int = 4):
        raw = config if config is not None else load_providers_config()
        self.config = _resolve_env(raw) if isinstance(raw, dict) else raw
        self._cache: dict[str, LLMClient] = {}
        self._sem = asyncio.Semaphore(max_concurrency)

    def for_agent(self, name: str) -> AgentRouting:
        agents = self.config.get("agents") or {}
        agent_cfg = agents.get(name) or {}
        provider_name = agent_cfg.get("provider") or self.config.get("default") or "openrouter"
        model = agent_cfg.get("model", "")
        max_tokens = int(agent_cfg.get("max_tokens", 1024))
        temperature = float(agent_cfg.get("temperature", 0.2))

        providers = self.config.get("providers") or {}
        prov_cfg = providers.get(provider_name) or {}
        key = prov_cfg.get("key", "") or ""
        base = prov_cfg.get("base", "") or ""

        if not base:
            return AgentRouting(provider_name=provider_name, client=None, model=model,
                                max_tokens=max_tokens, temperature=temperature,
                                enabled=False, reason="no base url")
        if provider_name != "local" and not key:
            return AgentRouting(provider_name=provider_name, client=None, model=model,
                                max_tokens=max_tokens, temperature=temperature,
                                enabled=False, reason="no api key")
        try:
            client = self._cache.get(provider_name)
            if client is None:
                client = build_provider(provider_name, {"base": base, "key": key})
                self._cache[provider_name] = client
            return AgentRouting(provider_name=provider_name, client=client, model=model,
                                max_tokens=max_tokens, temperature=temperature,
                                enabled=True, reason="ok")
        except Exception as e:
            return AgentRouting(provider_name=provider_name, client=None, model=model,
                                max_tokens=max_tokens, temperature=temperature,
                                enabled=False, reason=f"build failed: {e}")

    def status(self) -> dict:
        """For the dashboard — which providers are configured and which agents are enabled."""
        agents = self.config.get("agents") or {}
        out = {
            "default": self.config.get("default"),
            "providers": {},
            "agents": {},
        }
        for pname, pcfg in (self.config.get("providers") or {}).items():
            key = pcfg.get("key", "")
            out["providers"][pname] = {
                "base": pcfg.get("base", ""),
                "has_key": bool(key) and key != "no-key",
            }
        for aname in agents:
            r = self.for_agent(aname)
            out["agents"][aname] = {
                "provider": r.provider_name,
                "model":    r.model,
                "enabled":  r.enabled,
                "reason":   r.reason,
            }
        return out

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._sem


# --- per-call convenience (used by BaseAgent and tests) ---------------------

async def call_with_semaphore(client: LLMClient, router: LLMRouter, method: str, **kw) -> Any:
    async with router.semaphore:
        if method == "complete":
            return await client.complete(**kw)
        raise ValueError(f"unknown method: {method}")
