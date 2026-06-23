"""Tests for v2.3.5 (Option B): paper perps + Binance futures read-only.

Why this exists:
  On BSC mainnet, the perps venues in connectors/venues/ (aster, killex,
  apollox, mux) are stubs that raise NotImplementedError on every method
  except the venue-specific helper interfaces. The agent can't actually
  trade perps on BSC until those venues are wired up. Until then, sleeve
  A's carry signal still needs a real mark price + funding rate so it
  doesn't act on a canned 100.0 stub.

Option B: keep BSC as the on-chain settlement venue (so identity NFT +
daily-floor swaps work) but mark perps as paper. Orders route through
PaperStubClient (no real trade). Mark + funding come from Binance Futures
public REST API (no auth).

This test file covers:
  - BinanceFuturesReadOnlyClient: symbol mapping, mark fetch, funding
    fetch, write-path rejection
  - Perps._resolve_client: paper_perps=True → PaperStubClient even on
    mainnet; paper_perps=False → existing NotImplementedError path
  - Perps.mark / current_funding: in paper_perps mode, consult the
    read-only client first; in testnet/replay, keep the canned stub
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ------------------------------------------------------------------
# 1. BinanceFuturesReadOnlyClient — symbol mapping
# ------------------------------------------------------------------

class TestSymbolMapping:
    def test_known_symbol_maps_to_usdt_pair(self):
        from connectors.venues.binance_perp import _to_binance_symbol
        assert _to_binance_symbol("ETH") == "ETHUSDT"
        assert _to_binance_symbol("BTC") == "BTCUSDT"
        assert _to_binance_symbol("SOL") == "SOLUSDT"

    def test_unknown_symbol_falls_back_to_usdt_suffix(self):
        """Unknown symbols get the USDT suffix optimistically. The
        HTTP call will return None if the pair doesn't exist on
        Binance, which is fine — caller falls back."""
        from connectors.venues.binance_perp import _to_binance_symbol
        assert _to_binance_symbol("FOOBAR") == "FOOBARUSDT"

    def test_empty_string_returns_none(self):
        from connectors.venues.binance_perp import _to_binance_symbol
        assert _to_binance_symbol("") is None
        assert _to_binance_symbol(None) is None


# ------------------------------------------------------------------
# 2. BinanceFuturesReadOnlyClient — read methods (mocked HTTP)
# ------------------------------------------------------------------

class TestBinanceReadMethods:
    def _client(self):
        from connectors.venues.binance_perp import BinanceFuturesReadOnlyClient
        return BinanceFuturesReadOnlyClient({})

    def test_get_mark_price_parses_premiumindex_response(self):
        client = self._client()
        # Wipe the module-level cache between tests
        client._mark_cache.clear()
        mock_body = {"symbol": "ETHUSDT", "markPrice": "3492.45",
                     "indexPrice": "3492.50", "time": 1700000000000}
        with patch.object(client, "_http_get", return_value=(200, mock_body)):
            mark = client.get_mark_price("ETH")
        assert mark == Decimal("3492.45")

    def test_get_mark_price_caches(self):
        client = self._client()
        client._mark_cache.clear()
        mock_body = {"symbol": "BTCUSDT", "markPrice": "65000.00"}
        with patch.object(client, "_http_get", return_value=(200, mock_body)) as mock_get:
            client.get_mark_price("BTC")
            client.get_mark_price("BTC")  # second call should hit cache
        assert mock_get.call_count == 1

    def test_get_mark_price_returns_none_on_404(self):
        client = self._client()
        client._mark_cache.clear()
        with patch.object(client, "_http_get", return_value=(404, {"msg": "not found"})):
            assert client.get_mark_price("FOOBAR") is None

    def test_get_mark_price_returns_none_on_bad_payload(self):
        client = self._client()
        client._mark_cache.clear()
        with patch.object(client, "_http_get", return_value=(200, {"symbol": "ETHUSDT"})):
            # missing markPrice
            assert client.get_mark_price("ETH") is None

    def test_fetch_funding_rate_parses_array_response(self):
        client = self._client()
        client._funding_cache.clear()
        mock_body = [{"symbol": "ETHUSDT", "fundingRate": "0.00012",
                      "fundingTime": 1700000000000}]
        with patch.object(client, "_http_get", return_value=(200, mock_body)):
            rate = client.fetch_funding_rate("ETH")
        assert rate == pytest.approx(0.00012)

    def test_fetch_funding_rate_returns_none_on_empty(self):
        client = self._client()
        client._funding_cache.clear()
        with patch.object(client, "_http_get", return_value=(200, [])):
            assert client.fetch_funding_rate("FOOBAR") is None


# ------------------------------------------------------------------
# 3. BinanceFuturesReadOnlyClient — write paths are disabled
# ------------------------------------------------------------------

class TestBinanceReadOnlyBlocksWrites:
    def _client(self):
        from connectors.venues.binance_perp import BinanceFuturesReadOnlyClient
        return BinanceFuturesReadOnlyClient({})

    def test_place_order_raises(self):
        from connectors.venues.base import VenueOrderError
        client = self._client()
        with pytest.raises(VenueOrderError, match="read-only"):
            client.place_order(
                symbol="ETH", side="short",
                size_usd=Decimal("1"), leverage=1.0,
                collateral_usdc=Decimal("1"),
            )

    def test_close_position_raises(self):
        from connectors.venues.base import VenueOrderError
        client = self._client()
        with pytest.raises(VenueOrderError, match="read-only"):
            client.close_position("ETH")

    def test_reduce_position_raises(self):
        from connectors.venues.base import VenueOrderError
        client = self._client()
        with pytest.raises(VenueOrderError, match="read-only"):
            client.reduce_position("ETH", factor=0.5)

    def test_get_position_returns_none(self):
        client = self._client()
        assert client.get_position("ETH") is None


# ------------------------------------------------------------------
# 4. Perps._resolve_client — paper_perps routing
# ------------------------------------------------------------------

class TestPerpsResolveClient:
    def test_mainnet_paper_perps_returns_paper_stub(self):
        from connectors.bnb_sdk import Perps
        from connectors.venues.paper_stub import PaperStubClient
        perps = Perps(mode="mainnet", paper_perps=True)
        client = perps._resolve_client("aster")
        assert isinstance(client, PaperStubClient)

    def test_mainnet_no_paper_perps_routes_to_registry(self):
        """Without paper_perps, the existing mainnet behavior is
        preserved: VenueRegistry.get raises NotImplementedError
        because no live client is registered."""
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="mainnet", paper_perps=False)
        with pytest.raises(NotImplementedError):
            perps._resolve_client("aster")

    def test_testnet_returns_paper_stub_regardless_of_paper_perps(self):
        from connectors.bnb_sdk import Perps
        from connectors.venues.paper_stub import PaperStubClient
        for paper_flag in (True, False):
            perps = Perps(mode="testnet", paper_perps=paper_flag)
            client = perps._resolve_client("apollox")
            assert isinstance(client, PaperStubClient), (
                f"testnet+paper_perps={paper_flag} should give PaperStubClient"
            )

    def test_replay_returns_paper_stub(self):
        from connectors.bnb_sdk import Perps
        from connectors.venues.paper_stub import PaperStubClient
        perps = Perps(mode="replay", paper_perps=False)
        client = perps._resolve_client("killex")
        assert isinstance(client, PaperStubClient)


# ------------------------------------------------------------------
# 5. Perps.mark + current_funding — paper_perps uses read-only client
# ------------------------------------------------------------------

class TestPerpsMarkAndFunding:
    def test_mark_consults_read_only_client_in_paper_perps_mode(self):
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="mainnet", paper_perps=True)
        # Wire a mock read-only client
        mock_roc = MagicMock()
        mock_roc.get_mark_price.return_value = Decimal("3492.45")
        perps._read_only_client = mock_roc
        # mark() should consult it first and return 3492.45
        assert perps.mark("aster", "ETH") == 3492.45
        mock_roc.get_mark_price.assert_called_once_with("ETH")

    def test_mark_falls_back_to_canned_stub_when_read_only_returns_none(self):
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="mainnet", paper_perps=True)
        mock_roc = MagicMock()
        mock_roc.get_mark_price.return_value = None
        perps._read_only_client = mock_roc
        # Should not raise — falls back to _ensure() stub which
        # returns 100.0 (the historical default).
        assert perps.mark("aster", "FOO") == 100.0

    def test_mark_testnet_keeps_deterministic_stub(self):
        """testnet/replay must NOT hit Binance — the replay tape is
        the source of truth, and an HTTP call would make replays
        non-deterministic."""
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="testnet", paper_perps=False)
        mock_roc = MagicMock()
        mock_roc.get_mark_price.return_value = Decimal("9999")
        perps._read_only_client = mock_roc
        # Even though paper_perps is False here, mode=testnet means
        # we should NOT consult the read-only client.
        # The function should return the canned stub value (100.0).
        # If the function consulted mock_roc, it would return 9999.
        assert perps.mark("aster", "ETH") == 100.0
        mock_roc.get_mark_price.assert_not_called()

    def test_current_funding_consults_read_only_in_paper_perps(self):
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="mainnet", paper_perps=True)
        mock_roc = MagicMock()
        mock_roc.fetch_funding_rate.return_value = 0.00045
        perps._read_only_client = mock_roc
        rate = perps.current_funding("aster", "ETH")
        assert rate == pytest.approx(0.00045)
        mock_roc.fetch_funding_rate.assert_called_once_with("ETH")

    def test_current_funding_testnet_keeps_canned_stub(self):
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="testnet", paper_perps=False)
        mock_roc = MagicMock()
        mock_roc.fetch_funding_rate.return_value = 0.00045
        perps._read_only_client = mock_roc
        # Should NOT call the read-only client in testnet mode
        perps.current_funding("aster", "ETH")
        mock_roc.fetch_funding_rate.assert_not_called()


# ------------------------------------------------------------------
# 6. End-to-end integration: paper_perps=True, real order is paper
# ------------------------------------------------------------------

class TestPaperPerpsIntegration:
    def test_open_short_in_paper_perps_returns_paper_signed_tx(self):
        from connectors.bnb_sdk import Perps
        from connectors.bnb_sdk import SignedTx
        perps = Perps(mode="mainnet", paper_perps=True)
        result = perps.open_short(
            venue="aster", market="ETH",
            size_usd=10.0, leverage=1.0,
            collateral_usdc=10.0,
        )
        assert isinstance(result, SignedTx)
        assert result.is_paper is True, (
            "paper_perps=True must route orders through PaperStubClient "
            "so they appear in paper_pnl_usdc, not real_pnl_usdc"
        )

    def test_open_short_mainnet_no_paper_perps_raises(self):
        """Existing behavior preserved: without paper_perps, mainnet
        raises NotImplementedError because no live venue is registered."""
        from connectors.bnb_sdk import Perps
        perps = Perps(mode="mainnet", paper_perps=False)
        with pytest.raises(NotImplementedError):
            perps.open_short(
                venue="aster", market="ETH",
                size_usd=10.0, leverage=1.0,
                collateral_usdc=10.0,
            )