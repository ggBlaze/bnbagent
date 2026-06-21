"""KiloEx perps venue client — STUB awaiting implementation.

Same checklist as connectors/venues/aster.py. KiloEx API docs:
  - https://docs.kiloex.io/
  - Endpoint style differs from Aster; check the per-venue docs.

Until implemented, every call into killex raises NotImplementedError.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .base import BaseVenueClient, VenueOrderResult, VenuePosition


class KiloExVenueClient(BaseVenueClient):
    venue_name = "killex"

    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/killex.py: place_order not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def close_position(
        self, symbol: str, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/killex.py: close_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        raise NotImplementedError(
            "connectors/venues/killex.py: reduce_position not implemented yet. "
            "See docs/venue_implement_me.md."
        )

    def get_position(self, symbol: str) -> Optional[VenuePosition]:
        return None

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        return None


from .registry import VenueRegistry
VenueRegistry.register("killex", KiloExVenueClient)