"""CMC client — quotes + OHLCV + spend tracking."""
import pytest

from connectors.cmc import CMCClient
from connectors.twak import TWAKWallet
from tests.fixtures.wallets import EVALUATOR_KEY


class TestCMCClient:
    def test_init(self):
        cmc = CMCClient(x402_base="https://x", mode="testnet")
        assert cmc.spend_today == 0
        assert cmc.calls == []

    def test_replay_mode_consumes_tape(self):
        tape = [{"data": {"BTC": {"quote": {"USD": {"price": 100.0}}}}}] * 5
        cmc = CMCClient(x402_base="https://x", mode="replay", replay_tape=list(tape))
        for _ in range(5):
            r = await_sync(cmc.call("GET", "/v1/quotes", {"symbol": "BTC"}))
        with pytest.raises(RuntimeError):
            await_sync(cmc.call("GET", "/v1/quotes", {"symbol": "BTC"}))


def await_sync(coro):
    """Run a coroutine synchronously for tests."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)
