"""Base interface for perps venue clients.

Every BSC perps venue has its own REST API shape, auth scheme, and
quirks. The BaseVenueClient interface is the abstraction layer that
connectors/bnb_sdk.py:Perps calls into — concrete venue clients (Aster,
KiloEx, ApolloX, MUX) implement this interface and register themselves
via connectors/venues/registry.py:VenueRegistry.

The interface is intentionally minimal: 4 methods + 2 data classes.
A venue that needs more (e.g., conditional orders, batch close) should
extend this base or expose venue-specific helpers as separate methods
without breaking the base contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class VenueOrderError(RuntimeError):
    """Raised by venue clients when an order cannot be placed/closed."""


@dataclass
class VenueOrderResult:
    """Result of a successful place/close order.

    is_paper=True means the call was simulated (stubbed) — no actual
    venue position exists. The portfolio's stats use this flag to
    separate paper-trade PnL from real-trade PnL.

    venue_order_id is opaque (depends on the venue — Aster uses a hex
    string, others may use a UUID or numeric ID). Persist this so
    later close/cancel calls reference the right position.
    """
    venue_order_id: str
    symbol: str
    side: str                            # "long" | "short"
    size_usd: Decimal
    filled_price: Decimal | None = None   # None if limit order not yet filled
    is_paper: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class VenuePosition:
    """A venue-reported open position (from `get_position`)."""
    symbol: str
    side: str
    size_usd: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl_usdc: Decimal
    venue_order_id: str | None = None


class BaseVenueClient(ABC):
    """Abstract perps-venue client. One instance per venue.

    Concrete implementations live in connectors/venues/<name>.py and
    are registered via VenueRegistry.DEFAULT_CLIENTS.

    Configuration comes from config/<name>.yaml + perps.<name>.* keys
    in the merged config dict (local.yaml overrides config.yaml). The
    `__init__` receives the merged config and extracts its own keys.
    """

    venue_name: str = ""      # set by subclass

    def __init__(self, config: dict):
        self.config = config
        self.api_key = (config.get("api_key") or "").strip()
        self.api_secret = (config.get("api_secret") or "").strip()
        self.testnet = bool(config.get("testnet", False))

    # --- auth helper (override per venue) ---

    def _auth_headers(self, method: str, path: str, body: str) -> dict[str, str]:
        """Build auth headers for a request. Default: no auth. Override
        per venue (HMAC, signed payload, etc.)."""
        return {}

    # --- public interface ---

    @abstractmethod
    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: str | None = None,
    ) -> VenueOrderResult:
        """Place a market order. Returns VenueOrderResult; raises VenueOrderError
        on venue-side rejection (insufficient margin, bad symbol, etc.).
        """

    @abstractmethod
    def close_position(
        self, symbol: str, *, venue_order_id: str | None = None,
    ) -> VenueOrderResult:
        """Close an open position by symbol. If venue_order_id is given,
        close that specific order; otherwise close the entire position
        on `symbol` (most venues aggregate per symbol)."""

    @abstractmethod
    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: str | None = None,
    ) -> VenueOrderResult:
        """Reduce an open position by `factor` (e.g., 0.5 closes 50%)."""

    @abstractmethod
    def get_position(self, symbol: str) -> VenuePosition | None:
        """Return the venue's reported open position for `symbol`, or
        None if no position exists."""

    @abstractmethod
    def get_mark_price(self, symbol: str) -> Decimal | None:
        """Return the venue's current mark price for `symbol`, or None
        on error."""

    # --- helpers ---

    def _http_get(self, url: str, *, timeout: float = 5.0) -> tuple[int, dict]:
        """Shared GET helper. Returns (status_code, json_body). Raises
        VenueOrderError on connection failure. Concrete clients may
        override for venue-specific signing needs."""
        import httpx
        try:
            r = httpx.get(url, headers=self._auth_headers("GET", url, ""), timeout=timeout)
        except Exception as e:
            raise VenueOrderError(f"{self.venue_name} GET {url} failed: {e}") from e
        try:
            return r.status_code, r.json()
        except Exception as e:
            raise VenueOrderError(
                f"{self.venue_name} GET {url} returned non-JSON (status={r.status_code}): {e}"
            ) from e

    def _http_post(self, url: str, body: dict, *, timeout: float = 5.0) -> tuple[int, dict]:
        """Shared POST helper. Same return contract as _http_get."""
        import httpx
        import json
        try:
            r = httpx.post(
                url,
                headers={"Content-Type": "application/json", **self._auth_headers("POST", url, json.dumps(body))},
                json=body,
                timeout=timeout,
            )
        except Exception as e:
            raise VenueOrderError(f"{self.venue_name} POST {url} failed: {e}") from e
        try:
            return r.status_code, r.json()
        except Exception as e:
            raise VenueOrderError(
                f"{self.venue_name} POST {url} returned non-JSON (status={r.status_code}): {e}"
            ) from e