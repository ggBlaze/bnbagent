"""P4 (v2.1.8): the IPC snapshot must carry each component's
`.status` dict so endpoints that read `components.data_source.status`
work cross-process.

F1's `default=str` made class instances safe to JSON-encode but turned
them into useless `repr()` strings. Endpoints like /api/data-source
that do `router.tier` / `router.status` then crash with:

    AttributeError: 'str' object has no attribute 'tier'

Fix: in `_publish_dashboard_state`, walk `components` and pre-extract
each component's `.tier` and `.status` (where defined) into a plain
dict. The dashboard endpoints can then read keys from a dict or fall
back to attribute access on a live object.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest


class _FakeDataSource:
    """Mirrors the shape of connectors.data_source.DataSourceRouter for
    the purposes of serialization."""
    tier = "binance"

    @property
    def status(self) -> dict:
        return {"tier": "binance", "base": "https://api.binance.com/api/v3"}


class _FakeBSC:
    """No .status — exercises the fallback path."""
    pass


@pytest.fixture
def tmp_ipc(monkeypatch, tmp_path):
    p = tmp_path / "dashboard_state.json"
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(p))
    from core import dashboard_state as ds
    ds._clear_cache_for_tests()
    yield p
    ds._clear_cache_for_tests()


def test_publish_extracts_component_status_to_dict(tmp_ipc):
    """A component with a `.status` property is serialized as a dict
    (with `tier` + `status` keys) instead of `str(obj)`."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import dashboard_state as ds
    state = {
        "components": {
            "data_source": _FakeDataSource(),
            "bsc": _FakeBSC(),
        },
    }
    agent = Agent(policy={}, portfolio=Portfolio(starting_equity=Decimal("100")),
                  dashboard_state=state)
    agent._publish_dashboard_state()
    out = ds.read_state()
    comps = out["components"]
    # data_source should be a dict — NOT a "<__main__._FakeDataSource ...>" str.
    assert isinstance(comps["data_source"], dict), (
        f"components.data_source must be a dict in the IPC snapshot; "
        f"got {type(comps['data_source']).__name__} = {comps['data_source']!r}"
    )
    assert comps["data_source"]["tier"] == "binance"
    assert comps["data_source"]["status"] == {
        "tier": "binance", "base": "https://api.binance.com/api/v3",
    }


def test_publish_falls_back_to_str_for_components_without_status(tmp_ipc):
    """Components that don't expose a `.status` property fall back to
    the lossy str() (same as pre-P4) — those endpoints still read from
    live in-proc state when running in tests."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import dashboard_state as ds
    state = {
        "components": {"bsc": _FakeBSC()},
    }
    agent = Agent(policy={}, portfolio=Portfolio(starting_equity=Decimal("100")),
                  dashboard_state=state)
    agent._publish_dashboard_state()
    out = ds.read_state()
    # bsc is just a str (repr-style) — no crash, no enrichment.
    assert isinstance(out["components"]["bsc"], str)


def test_publish_preserves_dict_components_as_is(tmp_ipc):
    """`identity` is already a dict (per core/boot.py:200) — must NOT
    be wrapped in a tier/status envelope. Pin the regression."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    from core import dashboard_state as ds
    identity = {"token_id": 42, "cid": "bafyTEST",
                "agent_address": "0x" + "ed" * 20}
    state = {"components": {"identity": identity}}
    agent = Agent(policy={}, portfolio=Portfolio(starting_equity=Decimal("100")),
                  dashboard_state=state)
    agent._publish_dashboard_state()
    out = ds.read_state()
    assert out["components"]["identity"] == identity, (
        "identity dict must round-trip unchanged"
    )


def test_data_source_endpoint_works_with_dict_shape(tmp_ipc, monkeypatch):
    """End-to-end: after publish, the dashboard's /api/data-source
    endpoint must read the dict form correctly — no AttributeError on
    `'str' object has no attribute 'tier'`."""
    from core.tick import Agent
    from core.portfolio import Portfolio
    state = {
        "components": {"data_source": _FakeDataSource()},
        "config": {"data_source": {"tier": "binance"}},
    }
    agent = Agent(policy={}, portfolio=Portfolio(starting_equity=Decimal("100")),
                  dashboard_state=state)
    agent._publish_dashboard_state()
    # Now spin up the dashboard reading from the same IPC file.
    from fastapi.testclient import TestClient
    from dashboard.backend import main as dash
    # Clear in-proc state so the dashboard MUST read from the IPC file
    # (the actual cross-process scenario).
    dash.DASHBOARD_STATE.clear()
    client = TestClient(dash.app)
    resp = client.get("/api/data-source")
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["tier"] == "binance", (
        f"endpoint must read tier from IPC dict; got {body!r}"
    )
