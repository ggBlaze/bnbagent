"""ApolloX perps venue client — STUB awaiting implementation.

ApolloX uses Binance-style API endpoints (https://api.apollox.finance/v1/fapi/v1/...).
The shape is similar to Binance USD-M futures: HMAC-SHA256 signed,
recv_window + timestamp headers. If you've integrated Binance USD-M
futures before, the patterns apply directly.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .base import BaseVenueClient, VenueOrderResult, VenuePosition


class ApolloXVenueClient(BaseVenueClient):
    venue_name = "apollox"

    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/apollox.py: place_order not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def close_position(
        self, symbol: str, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/apollox.py: close_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/apollox.py: reduce_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def get_position(self, symbol: str) -> Optional[VenuePosition]:
        return None

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        return None


from .registry import VenueRegistry
VenueRegistry.register("apollox", ApolloXVenueClient)