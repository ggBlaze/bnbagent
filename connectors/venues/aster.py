"""Aster perps venue client — STUB awaiting implementation.

To implement:
  1. Find Aster's order-placement API endpoints at https://docs.aster.finance/
     (HTTP REST, JSON request/response, HMAC-SHA256 signed).
  2. Fill in `place_order`, `close_position`, `reduce_position`,
     `get_position`, `get_mark_price` with real API calls.
  3. Add a unit test in tests/unit/test_venues_aster.py that mocks the
     HTTP responses (httpx) and verifies the request shape, signed
     headers, and response parsing.
  4. Run an end-to-end test against Aster's testnet (or a tiny mainnet
     position) to verify fill_price and position reconciliation.

When `perps.aster.api_key` + `api_secret` are set in config/local.yaml
AND this module is implemented, sleeve A will execute real orders on
Aster instead of the paper-stub path.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .base import BaseVenueClient, VenueOrderError, VenueOrderResult, VenuePosition


class AsterVenueClient(BaseVenueClient):
    venue_name = "aster"

    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/aster.py: place_order not implemented yet. "
            "See docs/venue_implement_me.md for the per-venue checklist."
        )

    def close_position(
        self, symbol: str, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/aster.py: close_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/aster.py: reduce_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def get_position(self, symbol: str) -> Optional[VenuePosition]:
        return None

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        return None


# Register so VenueRegistry.get("aster", config) returns this class.
from .registry import VenueRegistry
VenueRegistry.register("aster", AsterVenueClient)