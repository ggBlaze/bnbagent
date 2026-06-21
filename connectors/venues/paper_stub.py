"""Paper-trade stub venue client.

Used in mode=replay and mode=testnet so the strategy layer can exercise
its full lifecycle (open → monitor → close) without a live venue.

Every call returns immediately with a deterministic VenueOrderResult
that has is_paper=True. The portfolio uses that flag to keep paper PnL
separate from real PnL (see core/portfolio.py:paper_pnl_usdc).

This is NOT a registered venue in VenueRegistry.DEFAULT_CLIENTS — it's
instantiated on demand by Perps when mode is not "mainnet".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .base import BaseVenueClient, VenueOrderResult, VenuePosition, VenueOrderError


class PaperStubClient(BaseVenueClient):
    """Stub venue client. Every operation is a no-op that returns a
    synthetic VenueOrderResult with is_paper=True."""

    def __init__(self, venue_name: str, config: dict | None = None):
        # Bypass BaseVenueClient.__init__ — paper stub takes (name, cfg)
        # rather than just (cfg). All paper-stub state is held in the
        # `venue_name` attribute + the inherited `venue_name` class attr.
        super().__init__(config or {})
        self.venue_name = venue_name

    def place_order(
        self, symbol: str, side: str, size_usd: Decimal, leverage: float,
        collateral_usdc: Decimal, *, client_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        import time as _time, hashlib as _hl
        oid = client_order_id or _hl.sha1(
            f"paper:{self.venue_name}:{symbol}:{side}:{size_usd}:{_time.time()}".encode()
        ).hexdigest()[:16]
        log_marker = f"paper-order:{self.venue_name}:{symbol}:{side}:${size_usd}"
        return VenueOrderResult(
            venue_order_id=oid,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            filled_price=None,
            is_paper=True,
            raw={"paper_marker": log_marker},
        )

    def close_position(
        self, symbol: str, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        return VenueOrderResult(
            venue_order_id=venue_order_id or f"paper-close:{symbol}",
            symbol=symbol, side="close", size_usd=Decimal(0),
            filled_price=None, is_paper=True, raw={},
        )

    def reduce_position(
        self, symbol: str, factor: float, *, venue_order_id: Optional[str] = None,
    ) -> VenueOrderResult:
        return VenueOrderResult(
            venue_order_id=venue_order_id or f"paper-reduce:{symbol}:{factor}",
            symbol=symbol, side="reduce", size_usd=Decimal(0),
            filled_price=None, is_paper=True, raw={},
        )

    def get_position(self, symbol: str) -> Optional[VenuePosition]:
        return None  # paper stubs don't track venue positions

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        return None  # caller should fall back to Perps.mark fallback chain