"""bnbagent-sdk — BSC, PancakeV3, Perps, ERC-8004, ERC-8183."""
import time
from decimal import Decimal

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
        c = BSCClient(["http://x"], 97, "testnet")
        e = ERC8004(c, "0x" + "8" * 40)
        token_id, cid = e.register(agent_uri="ipfs://Qmabc")
        assert token_id > 0
        assert cid.startswith("Qm")
