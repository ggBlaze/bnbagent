"""bnbagent-sdk — BSC, PancakeV3, Perps, ERC-8004, ERC-8183."""
import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from connectors.bnb_sdk import BSCClient, PancakeV3, Perps, ERC8004, ERC8183


class TestBSCClient:
    def test_init(self):
        bsc = BSCClient(rpcs=["http://localhost:8545"], chain_id=97, mode="testnet")
        assert bsc.chain_id == 97
        assert bsc.next_nonce("0xabc") == 0
        assert bsc.next_nonce("0xabc") == 1

    def test_rotate(self):
        bsc = BSCClient(rpcs=["http://a", "http://b", "http://c"], chain_id=97, mode="testnet")
        assert bsc._idx == 0
        bsc.rotate()
        assert bsc._idx == 1
        bsc.rotate()
        bsc.rotate()
        assert bsc._idx == 0   # wraps

    def test_testnet_balances(self):
        bsc = BSCClient(rpcs=["http://x"], chain_id=97, mode="testnet")
        assert bsc.eth_balance("0xabc") == Decimal("5.0")
        assert bsc.token_balance("0xtoken", "0xholder") == Decimal("1000")

    # ---- v2.3.8: has_gas pre-flight check for broadcast path ----

    def test_has_gas_testnet_mode_always_ok(self):
        """Testnet/replay mode never blocks on gas (no real fees)."""
        bsc = BSCClient(rpcs=["http://x"], chain_id=97, mode="testnet")
        ok, reason = bsc.has_gas("0xabc", gas_units=250_000)
        assert ok is True
        assert reason == "ok"

    def test_has_gas_mainnet_sufficient(self, monkeypatch):
        """Mainnet with plenty of BNB returns ok=True."""
        bsc = BSCClient(rpcs=["http://x"], chain_id=56, mode="mainnet")
        # 0.05 BNB balance, gas_price 1 gwei, 100k gas → cost ~0.0001 BNB
        fake_w3 = MagicMock()
        fake_w3.eth.get_balance.return_value = 50 * 10**15  # 0.05 BNB
        fake_w3.eth.gas_price = 10**9  # 1 gwei
        monkeypatch.setattr(bsc, "_w3", fake_w3)
        ok, reason = bsc.has_gas("0x" + "a" * 40, gas_units=100_000)
        assert ok is True
        assert reason == "ok"

    def test_has_gas_mainnet_insufficient(self, monkeypatch):
        """Mainnet with low BNB returns ok=False + reason with numbers."""
        bsc = BSCClient(rpcs=["http://x"], chain_id=56, mode="mainnet")
        fake_w3 = MagicMock()
        # 0.0001 BNB balance vs 250k gas @ 5 gwei = 0.00125 BNB needed (×1.2 buffer = 0.0015)
        fake_w3.eth.get_balance.return_value = 10**14  # 0.0001 BNB
        fake_w3.eth.gas_price = 5 * 10**9  # 5 gwei
        monkeypatch.setattr(bsc, "_w3", fake_w3)
        ok, reason = bsc.has_gas("0x" + "a" * 40, gas_units=250_000)
        assert ok is False
        # Reason must carry human-readable numbers so an operator can see at a glance
        assert "bnb_insufficient_gas" in reason
        assert "have" in reason and "need" in reason and "overshot" in reason
        assert "250000 gas" in reason
        assert "1.20 buffer" in reason

    def test_has_gas_chain_query_failure_does_not_block(self, monkeypatch):
        """If we can't reach the chain to check, allow the broadcast to proceed
        (broadcast itself will surface a better error; circuit breaker handles
        repeat failures)."""
        bsc = BSCClient(rpcs=["http://x"], chain_id=56, mode="mainnet")
        fake_w3 = MagicMock()
        fake_w3.eth.get_balance.side_effect = RuntimeError("rpc down")
        monkeypatch.setattr(bsc, "_w3", fake_w3)
        ok, reason = bsc.has_gas("0x" + "a" * 40, gas_units=250_000)
        assert ok is True
        assert "gas_check_skipped" in reason


class TestPancakeV3:
    def test_encode_swap_returns_calldata(self):
        bsc = BSCClient(rpcs=["http://x"], chain_id=97, mode="testnet")
        pcv3 = PancakeV3(bsc, "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40)
        calldata = pcv3.encode_swap_v3(
            token_in="0x" + "a" * 40, token_out="0x" + "b" * 40, fee=2500,
            recipient="0x" + "c" * 40, amount_in=10**18, min_out=10**17,
        )
        assert len(calldata) > 4

    def test_quote_stub(self):
        bsc = BSCClient(rpcs=["http://x"], chain_id=97, mode="testnet")
        pcv3 = PancakeV3(bsc, "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40)
        out = pcv3.quote("0x" + "a" * 40, "0x" + "b" * 40, 2500, 10**18)
        assert out == int(10**18 * 0.997)

    def test_best_pool_fee_stub(self):
        bsc = BSCClient(rpcs=["http://x"], chain_id=97, mode="testnet")
        pcv3 = PancakeV3(bsc, "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40)
        fee = pcv3.best_pool_fee("0x" + "a" * 40, "0x" + "b" * 40, [100, 500, 2500, 10000])
        assert fee == 2500


class TestPerpsMarkFetch:
    """v2.1.8: Perps.mark() in production mode fetches the real mark from
    the venue's mark_endpoint, caches it for `mark_cache_ttl_s`, and
    falls back gracefully on network/parse errors."""

    def test_mark_fetches_from_endpoint_in_mainnet(self, monkeypatch):
        captured = {}

        def fake_get(url, timeout=None):
            captured["url"] = url
            class _Resp:
                status_code = 200
                def json(self):
                    return {"ETH": 1735.93, "BTC": 60000.0}
                def raise_for_status(self): pass
            return _Resp()

        monkeypatch.setattr("connectors.bnb_sdk.httpx.get", fake_get)
        # mainnet mode + no _mark_provider → must hit the HTTP path
        p = Perps(mode="mainnet", mark_cache_ttl_s=60)
        mark = p.mark("aster", "ETH")
        assert mark == 1735.93
        assert "aster" in captured["url"]

    def test_mark_caches_for_ttl(self, monkeypatch):
        calls = {"n": 0}

        def fake_get(url, timeout=None):
            calls["n"] += 1
            class _Resp:
                status_code = 200
                def json(self): return {"ETH": 1735.93}
                def raise_for_status(self): pass
            return _Resp()

        monkeypatch.setattr("connectors.bnb_sdk.httpx.get", fake_get)
        p = Perps(mode="mainnet", mark_cache_ttl_s=60)
        p.mark("aster", "ETH")
        p.mark("aster", "ETH")
        p.mark("aster", "ETH")
        assert calls["n"] == 1, f"expected 1 HTTP call (cached), got {calls['n']}"

    def test_mark_falls_back_to_cached_on_error(self, monkeypatch):
        # First call succeeds; cache fills.
        state = {"fail_next": False}
        def fake_get(url, timeout=None):
            if state["fail_next"]:
                raise RuntimeError("simulated RPC outage")
            class _Resp:
                status_code = 200
                def json(self): return {"ETH": 1735.93}
                def raise_for_status(self): pass
            return _Resp()

        monkeypatch.setattr("connectors.bnb_sdk.httpx.get", fake_get)
        p = Perps(mode="mainnet", mark_cache_ttl_s=0)  # 0 TTL = never expire cache
        assert p.mark("aster", "ETH") == 1735.93  # fills cache
        # Now simulate outage; mark() must return the cached value.
        state["fail_next"] = True
        # Force a re-fetch (TTL=0 forces expiry, but on None return we
        # fall back to the last-known cached value).
        assert p.mark("aster", "ETH") == 1735.93

    def test_mark_falls_back_to_stub_on_no_cache(self, monkeypatch):
        def fake_get(url, timeout=None):
            raise RuntimeError("never reachable")
        monkeypatch.setattr("connectors.bnb_sdk.httpx.get", fake_get)
        p = Perps(mode="mainnet", mark_cache_ttl_s=60)
        # No prior cache → returns the historical stub 100.0 (matches
        # the existing _ensure() default). This preserves the current
        # behavior for first-boot before the venue API responds.
        mark = p.mark("aster", "ETH")
        assert mark == 100.0

    def test_parse_mark_handles_common_shapes(self):
        from connectors.bnb_sdk import _parse_mark_payload  # type: ignore
        # 1. Dict {symbol: price}
        assert _parse_mark_payload({"ETH": 1735.93}, "ETH") == 1735.93
        # 2. List of dicts (Binance-style)
        assert _parse_mark_payload(
            [{"symbol": "ETHUSDT", "markPrice": "1735.93"}], "ETH"
        ) == 1735.93
        # 3. Wrapped {"data": [...]}
        assert _parse_mark_payload(
            {"data": [{"symbol": "ETH", "markPrice": "1735.50"}]}, "ETH"
        ) == 1735.50
        # 4. Wrapped {"result": {...}}
        assert _parse_mark_payload(
            {"result": {"ETH": 1735.40}}, "ETH"
        ) == 1735.40
        # 5. Symbol mismatch → None
        assert _parse_mark_payload({"BTC": 60000}, "ETH") is None
        # 6. Junk → None
        assert _parse_mark_payload(None, "ETH") is None
        assert _parse_mark_payload({}, "ETH") is None


class TestPerps:
    def test_venue_selection(self):
        p = Perps(mode="testnet")
        markets = ["BTC", "ETH", "SOL"]
        venue, scores = p.select_venue(markets)
        assert venue in ["aster", "killex", "apollox", "mux"]
        assert set(scores.keys()) == set(markets)

    def test_funding_convergence(self):
        p = Perps(mode="testnet")
        f = p.current_funding("aster", "BTC")
        assert -0.05 < f < 0.05

    def test_funding_within_realistic_8h_band(self):
        """Lock the calibration: real BSC venues (Aster / KiloEx / ApolloX /
        MUX) settle 8h at 0.01%–0.05%, widened slightly for tail events.
        If you change the band, you're making a business decision, not a
        test tweak — update the docstring in bnb_sdk._ensure as well."""
        p = Perps(mode="testnet")
        # Sample 50 fresh (venue, market) pairs.
        venues = ["aster", "killex", "apollox", "mux"]
        markets = [f"SYM{i}" for i in range(50)]
        for v in venues:
            for m in markets:
                f = p.current_funding(v, m)
                # 8h band: -0.05% to +0.15% (centi-percent to ~0.15%)
                assert -0.0005 <= f <= 0.0015, (
                    f"funding out of band: venue={v} market={m} f={f}. "
                    f"Real BSC venues settle 8h at 0.01%-0.05%; widen the band "
                    f"only after reading the venue's actual settlement history."
                )

    def test_mark_uses_provider_when_set(self):
        """Audit #21: the old stub returned a constant 100, which caused
        sleeve_a._monitor's basis_trigger to fire on every tick (basis =
        (100 - entry) / entry, often > 0.5%), producing thousands of
        spurious trade-closes per replay run. Now the perps mark uses a
        provider (the live spot tape) plus a small deterministic basis
        noise (±0.05%, 5 bps) that matches the real perp-spot spread."""
        p = Perps(mode="testnet")
        p.set_mark_provider(lambda s: 250.0)  # spot tape says 250 for any symbol
        # Each (venue, market) has a deterministic basis noise in [-0.05%, +0.05%]
        for venue in ("aster", "killex", "apollox", "mux"):
            for market in ("BTC", "ETH", "SOL"):
                mark = p.mark(venue, market)
                # Within ±0.05% of 250
                assert 249.875 <= mark <= 250.125, (
                    f"mark {mark} for {venue}/{market} outside ±5bps of 250"
                )
        # Without a provider set, falls back to the cached value (100).
        p2 = Perps(mode="testnet")
        assert p2.mark("aster", "BTC") == 100.0

    def test_open_close_short(self):
        p = Perps(mode="testnet")
        tx_open = p.open_short("aster", "BTC", size_usd=100, leverage=1, collateral_usdc=100)
        assert tx_open.tx_hash.startswith("0x")
        tx_close = p.close_short("aster", "BTC")
        assert tx_close.tx_hash.startswith("0x")

    def test_liq_distance_safe(self):
        p = Perps(mode="testnet")
        d = p.liq_distance_pct("aster", "BTC", "short")
        assert d > 0.20


class TestERC8183:
    def test_lifecycle(self):
        c = ERC8183(BSCClient(["http://x"], 97, "testnet"), "0x" + "9" * 40)
        job_id = c.create_job(
            provider="0x" + "a" * 40, evaluator="0x" + "b" * 40,
            deliverable_spec=b"\x00" * 32, budget=25_000_000,
            token="0x" + "c" * 40,
        )
        assert c.get(job_id)["status"] == "Open"
        c.fund(job_id, 25_000_000)
        assert c.get(job_id)["status"] == "Funded"
        c.submit(job_id, "Qmtest")
        assert c.get(job_id)["status"] == "Submitted"
        c.complete(job_id)
        assert c.get(job_id)["status"] == "Completed"

    def test_fund_requires_open(self):
        c = ERC8183(BSCClient(["http://x"], 97, "testnet"), "0x" + "9" * 40)
        job_id = c.create_job(
            provider="0x" + "a" * 40, evaluator="0x" + "b" * 40,
            deliverable_spec=b"\x00" * 32, budget=25_000_000, token="0x" + "c" * 40,
        )
        c.fund(job_id, 25_000_000)
        with pytest.raises(ValueError):
            c.fund(job_id, 100)


class TestERC8004:
    def test_register_stub(self):
        """v2.3.0: testnet/replay stub returns (token_id, agent_uri).
        The agent_uri is what the IdentityRegistry would receive as
        `register(string)` — pinning produces either an ipfs:// URL
        or a public gateway HTTPS URL. The token_id is a deterministic
        stub derived from keccak(agent_uri) so it stays stable across
        test runs without broadcasting."""
        c = BSCClient(["http://x"], 97, "testnet")
        e = ERC8004(c, "0x" + "8" * 40)
        token_id, agent_uri = e.register(agent_uri="ipfs://Qmabc")
        assert token_id > 0
        # v2.3.0: the second return value is now the agent_uri
        # (the string passed to the IdentityRegistry's register(string))
        assert agent_uri == "ipfs://Qmabc"
        # cid is still exposed as an attribute for backwards compat
        assert e._cid.startswith("Qm")


class TestPancakeV3Mainnet:
    """v2.1.8: encode_swap_v3 in mainnet mode must not raise
    Web3ValueError when building the transaction. The contract binding
    already supplies 'to'; passing it via build_transaction kwargs is
    a web3.py violation that crashes every sleeve-A rebalance tick."""

    def test_encode_swap_v3_mainnet_does_not_set_to(self, monkeypatch):
        """Mock the BSC web3 client so we never hit a real RPC."""
        bsc = BSCClient(rpcs=["http://x"], chain_id=56, mode="mainnet")

        # Build a fake web3 + eth.contract that records the kwargs passed
        # to build_transaction, then returns a fake data blob.
        captured = {}

        class _FakeTx:
            def build_transaction(self, kwargs):
                captured["kwargs"] = dict(kwargs)
                return {"data": b"\xab\xcd\xef"}
        class _FakeFn:
            def __call__(self, params):
                return _FakeTx()
        # web3.py exposes `Contract.functions` as an attribute (a
        # ContractFunctions instance), not a method. Reproduce that here so
        # `router.functions.exactInputSingle(...)` resolves like the real
        # library does.
        class _FakeContractFunctions:
            def exactInputSingle(self_inner, params):
                return _FakeFn()(params)
        class _FakeContract:
            functions = _FakeContractFunctions()
        class _FakeEth:
            def contract(self, address=None, abi=None):
                return _FakeContract()
        class _FakeW3:
            def __init__(self): self.eth = _FakeEth()
            @staticmethod
            def to_checksum_address(a): return a
        monkeypatch.setattr(bsc, "w3", lambda: _FakeW3())

        pcv3 = PancakeV3(
            bsc,
            "0x" + "1" * 40,
            "0x" + "2" * 40,
            "0x" + "3" * 40,
        )
        calldata = pcv3.encode_swap_v3(
            token_in="0x" + "a" * 40, token_out="0x" + "b" * 40, fee=2500,
            recipient="0x" + "c" * 40, amount_in=10**18, min_out=10**17,
        )
        assert calldata == b"\xab\xcd\xef"
        # The contract binding already supplies `to`; passing it via
        # build_transaction kwargs is a web3.py violation.
        assert "to" not in captured["kwargs"], (
            f"build_transaction was called with 'to'={captured['kwargs'].get('to')!r}; "
            "the contract binding already supplies it and web3.py rejects the redundancy."
        )
