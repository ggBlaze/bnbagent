"""Regression tests for the v2.0.8-H3 nonce cache resync.

H-3 was that the BSCClient._nonce_cache was a local-only counter,
never reconciled with the chain. If the agent crashed mid-tick
(after sign_transaction but before broadcast, or between broadcasts),
the cached nonce would be wrong and the next tx would either be
rejected (replay, stuck) or skip a nonce.

Fix: new BSCClient.resync_nonce(address) queries
eth_getTransactionCount(address, 'pending') and reseeds the cache.
In testnet/replay mode, the chain isn't queried (no real chain) so
the function returns the current cache value.

These tests cover:
- testnet mode: resync_nonce is a no-op (returns cache + 1)
- replay mode: same
- the API exists and is callable
- in mainnet mode, the cache is reseeded from the chain pending
"""
import pytest
from web3 import Web3

from connectors.bnb_sdk import BSCClient


def _make_client(mode: str) -> BSCClient:
    rpcs = ["https://example.com/rpc"]  # never used in testnet/replay
    return BSCClient(rpcs=rpcs, chain_id=97 if mode == "testnet" else 56, mode=mode)


class TestNonceResync:
    def test_testnet_mode_is_noop(self):
        c = _make_client("testnet")
        # prime the cache: one call to next_nonce sets cache to 0
        c.next_nonce("0x" + "a" * 40)
        # resync returns cache+1 WITHOUT mutating the cache (it's a
        # read-only operation; the operator can call it to check what
        # the next nonce would be without actually incrementing)
        assert c.resync_nonce("0x" + "a" * 40) == 1
        assert c._nonce_cache["0x" + "a" * 40] == 0  # unchanged

    def test_replay_mode_is_noop(self):
        c = _make_client("replay")
        c.next_nonce("0x" + "b" * 40)
        assert c.resync_nonce("0x" + "b" * 40) == 1
        assert c._nonce_cache["0x" + "b" * 40] == 0  # unchanged

    def test_method_exists(self):
        c = _make_client("testnet")
        assert hasattr(c, "resync_nonce")
        assert callable(c.resync_nonce)

    def test_empty_cache_resync_testnet(self):
        """Empty cache in testnet: returns 0 (default first nonce)."""
        c = _make_client("testnet")
        addr = "0x" + "c" * 40
        # cache is empty
        assert addr not in c._nonce_cache
        n = c.resync_nonce(addr)
        assert n == 0

    def test_mainnet_calls_chain(self, monkeypatch):
        """Mainnet mode queries eth_getTransactionCount and reseeds cache."""
        c = _make_client("mainnet")
        addr = "0x" + "d" * 40
        # seed a wrong cache value (e.g. agent thinks it's at 99, but
        # chain says it's actually at 142 — three txs went out from a
        # previous run that didn't update the cache)
        c._nonce_cache[addr] = 99
        # stub the chain call
        seen: dict = {}
        class FakeW3:
            class eth:
                @staticmethod
                def get_transaction_count(checksum_addr, tag):
                    seen["address"] = checksum_addr
                    seen["tag"] = tag
                    return 142
        c._w3 = FakeW3()
        # resync should query the chain and return 142
        n = c.resync_nonce(addr)
        assert n == 142
        # cache is reseeded to pending - 1 (so next_nonce returns pending)
        assert c._nonce_cache[addr] == 141
        # chain was queried for pending nonce of the checksum address
        assert seen["tag"] == "pending"
        assert seen["address"] == Web3.to_checksum_address(addr)
        # next call to next_nonce returns 142 (matches chain)
        assert c.next_nonce(addr) == 142
