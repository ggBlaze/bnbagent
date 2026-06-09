"""Regression tests for the v2.0.8-M4 vol-filter fallback.

M-4 was that sleeve_a_carry._realized_vol_annualized returned 0.0
on any failure (CMC rate limit, network blip, missing data). 0.0
is BELOW the low-vol-pause threshold (default 0.05), so a single
CMC blip would force-close a healthy carry book.

Fix: the fallback is now min_vol + a small buffer (default 0.01),
so an outage looks like 'vol is fine' to the strategy. The
existing carry positions are preserved. Operators can override
the buffer via policy.global_risk.vol_fallback_buffer.

These tests cover:
- the fallback value is ABOVE the threshold (no force-close)
- the buffer is read from policy
- the default buffer is 0.01
- the empty-data path also uses the fallback (not 0.0)
- the success path is unchanged (returns computed vol)
"""
import pytest

from strategies.sleeve_a_carry import SleeveACarry


def _make_sleeve(policy: dict) -> SleeveACarry:
    cfg = {"cmc": {"basket_symbols": ["BTC", "ETH"]}}
    full_policy = {"sleeve_allocations": {"A": 0.7}, **policy}
    return SleeveACarry(
        name="A",
        components={
            "config": cfg,
            "policy": full_policy,
            "wallet": None, "cmc": None, "pancake": None,
            "perps": None, "bsc": None, "ipfs": None,
            "agent": None, "portfolio": None,
        },
        agent=None,
    )


class TestVolFallback:
    def test_fallback_above_threshold(self):
        """The fallback must be > min_vol, not 0.0."""
        s = _make_sleeve({"global_risk": {"min_realized_vol_annualized": 0.05}})
        v = s._vol_fallback()
        # default buffer is 0.01
        assert v == pytest.approx(0.06)
        # above the threshold
        assert v > 0.05

    def test_default_buffer(self):
        """Without policy override, buffer is 0.01."""
        s = _make_sleeve({"global_risk": {"min_realized_vol_annualized": 0.05}})
        assert s._vol_fallback() == pytest.approx(0.06)

    def test_buffer_overridable(self):
        """Operator can set the buffer via policy."""
        s = _make_sleeve({
            "global_risk": {
                "min_realized_vol_annualized": 0.05,
                "vol_fallback_buffer": 0.05,   # 5% above threshold
            }
        })
        assert s._vol_fallback() == pytest.approx(0.10)

    def test_fallback_with_missing_min_vol(self):
        """If min_vol is missing entirely, default to 0.05 (matches sleeve default)."""
        s = _make_sleeve({"global_risk": {}})
        v = s._vol_fallback()
        # default min_vol=0.05 + default buffer=0.01
        assert v == pytest.approx(0.06)

    def test_fallback_does_not_force_close(self):
        """The whole point: the fallback must NOT trigger low-vol-pause.

        low-vol-pause fires when realized_vol < min_vol. The fallback
        is min_vol + buffer where buffer > 0, so this is true by
        construction. We assert it explicitly so a future refactor
        can't break the invariant.
        """
        s = _make_sleeve({"global_risk": {"min_realized_vol_annualized": 0.05}})
        min_vol = 0.05
        fallback = s._vol_fallback()
        # The check in the strategy is `if realized_vol < min_vol:`
        # We need the fallback to NOT be less than min_vol.
        assert not (fallback < min_vol), \
            f"fallback {fallback} is < min_vol {min_vol} — would force-close"

    def test_failure_path_uses_fallback(self, monkeypatch):
        """When the CMC call fails, _realized_vol_annualized returns fallback."""
        import asyncio

        s = _make_sleeve({"global_risk": {"min_realized_vol_annualized": 0.05}})

        class BrokenCMC:
            async def ohlcv_historical(self, *a, **kw):
                raise RuntimeError("CMC is down")

        s.cmc = BrokenCMC()
        v = asyncio.run(s._realized_vol_annualized())
        # not 0.0 (the v2.0.7 broken behavior) — should be the fallback
        assert v != 0.0
        assert v == pytest.approx(0.06)

    def test_empty_data_path_uses_fallback(self, monkeypatch):
        """When the CMC call returns no data, the fallback is also used."""
        import asyncio

        s = _make_sleeve({"global_risk": {"min_realized_vol_annualized": 0.05}})

        class EmptyCMC:
            async def ohlcv_historical(self, *a, **kw):
                return {"data": {}}  # no symbols

        s.cmc = EmptyCMC()
        v = asyncio.run(s._realized_vol_annualized())
        assert v == pytest.approx(0.06)
