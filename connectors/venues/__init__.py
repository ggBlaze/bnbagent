"""Perps venue clients.

Each BSC perps venue (aster, killex, apollox, mux) has its own REST API for
opening/closing positions, querying marks, and reconciling fills. This
package defines a `BaseVenueClient` interface and a `VenueRegistry` that
maps venue names to implementations.

To add a new venue:
  1. Create connectors/venues/<name>.py implementing BaseVenueClient.
  2. Add it to VenueRegistry.DEFAULT_CLIENTS.
  3. Set perps.<name>.api_key + api_secret in config/local.yaml.
  4. Run the venue against a small test order before going live.

Until you do step 1 for a venue, calls into that venue raise
`NotImplementedError` with a message pointing at docs/venue_implement_me.md.
"""
from .base import (
    BaseVenueClient,
    VenueOrderResult,
    VenueOrderError,
    VenuePosition,
)
from .registry import VenueRegistry

__all__ = [
    "BaseVenueClient",
    "VenueOrderResult",
    "VenueOrderError",
    "VenuePosition",
    "VenueRegistry",
]