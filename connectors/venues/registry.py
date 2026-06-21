"""Venue registry — maps venue names to BaseVenueClient implementations.

Per-venue clients register themselves in DEFAULT_CLIENTS. The Perps class
in connectors/bnb_sdk.py uses `VenueRegistry.get(name, config)` to obtain
the right client for a venue. If no client is registered, `get` raises a
clear NotImplementedError pointing at docs/venue_implement_me.md.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import BaseVenueClient, VenueOrderError

log = logging.getLogger(__name__)


class VenueRegistry:
    """Maps venue names to client classes. One registry per process is fine."""

    DEFAULT_CLIENTS: dict[str, type[BaseVenueClient]] = {}

    @classmethod
    def register(cls, name: str, client_cls: type[BaseVenueClient]) -> None:
        """Register a client class under a venue name (e.g. "aster")."""
        cls.DEFAULT_CLIENTS[name] = client_cls

    @classmethod
    def get(cls, name: str, config: dict | None = None) -> BaseVenueClient:
        """Return a client instance for `name`. Raises NotImplementedError
        with a clear pointer to docs/venue_implement_me.md if no client
        is registered for that venue."""
        config = config or {}
        client_cls = cls.DEFAULT_CLIENTS.get(name)
        if client_cls is None:
            raise NotImplementedError(
                f"No real venue client registered for {name!r}. "
                f"Either: (a) implement connectors/venues/{name}.py and "
                f"register it via VenueRegistry.register(); or (b) run in "
                f"mode=replay for paper trading. See docs/venue_implement_me.md."
            )
        # Per-venue config lives at config["perps"][<venue>] in the
        # merged view, with global api_key/api_secret as a fallback.
        merged = dict(config.get(name) or {})
        if not merged.get("api_key") and config.get("api_key"):
            merged["api_key"] = config["api_key"]
        if not merged.get("api_secret") and config.get("api_secret"):
            merged["api_secret"] = config["api_secret"]
        # Carry over network mode so testnet-aware clients can pick.
        if "testnet" not in merged:
            merged["testnet"] = bool(config.get("testnet", False))
        return client_cls(merged)

    @classmethod
    def known_venues(cls) -> list[str]:
        """List of venue names with a registered client."""
        return sorted(cls.DEFAULT_CLIENTS.keys())


def _paper_stub(name: str) -> "BaseVenueClient":
    """Build a venue client that simulates (paper-trades) every action.

    Used when mode=replay or mode=testnet. Returns immediately with a
    deterministic stub VenueOrderResult so the strategy layer can exercise
    its full lifecycle without a live venue.
    """
    from .paper_stub import PaperStubClient
    return PaperStubClient(name, {"venue_name": name})