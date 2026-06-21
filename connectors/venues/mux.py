"""MUX Protocol perps venue client — STUB awaiting implementation.

MUX Protocol (https://api.mux.network) uses a slightly different API
shape than Aster/KiloEx/ApolloX — typically REST with bearer-token auth.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .base import BaseVenueClient, VenueOrderResult, VenuePosition


class MuxVenueClient(BaseVenueClient):
    venue_name = "mux"

    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/mux.py: place_order not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def close_position(
        self, symbol: str, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/mux.py: close_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/mux.py: reduce_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def get_position(self, symbol: str) -> Optional[VenuePosition]:
        return None

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        return None


from .registry import VenueRegistry
VenueRegistry.register("mux", MuxVenueClient)