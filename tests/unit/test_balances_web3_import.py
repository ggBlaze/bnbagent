"""Regression test for the web3.py 7.x compatibility bug in
core/balances._connect_first().

web3.py 7.x renamed geth_poa_middleware to ExtraDataToPOAMiddleware
and moved it to web3.middleware.proof_of_authority. The old import
`from web3.middleware import geth_poa_middleware` now raises
ImportError at runtime. The bug was silent because _connect_first
catches the exception as part of the per-RPC retry loop, returning
None for every RPC — which surfaces as "no BSC RPC reachable" on
the dashboard's Wallet Holdings panel even when the RPCs are
perfectly reachable.

This test pins the behavior: when the underlying Web3 provider
returns successfully, _connect_first must return the w3 instance
(no exception swallowing the import error). The mock here
substitutes the entire Web3 class so the test runs in any web3
version.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_connect_first_does_not_silently_swallow_import_error(monkeypatch):
    """If Web3 is installed and is_connected() returns True,
    _connect_first must return the Web3 instance. The pre-fix code
    wrapped the imports in `try: ... except ImportError: return None`
    — making every RPC look unreachable on web3.py 7.x because
    `from web3.middleware import geth_poa_middleware` fails there
    (renamed to ExtraDataToPOAMiddleware in web3.middleware.
    proof_of_authority). The fix tries both paths.

    Test strategy: patch balances._connect_first's Web3 dependency
    so is_connected() returns True, and patch _POA's middleware
    lookup. With the bug, the function returned None silently. With
    the fix, it returns the w3 instance.
    """
    fake_w3 = MagicMock()
    fake_w3.is_connected.return_value = True
    fake_w3.middleware_onion.inject = MagicMock()
    fake_web3_class = MagicMock(return_value=fake_w3)
    fake_web3_class.HTTPProvider = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("web3.Web3", fake_web3_class, raising=False)

    # Re-import balances fresh so the patched web3.Web3 takes effect.
    import importlib
    from core import balances
    importlib.reload(balances)

    result = balances._connect_first(["https://bsc.example.com"], timeout=2.0)
    assert result is fake_w3, (
        "_connect_first returned None — likely swallowing an "
        "ImportError on the web3 middleware import. See core/"
        "balances.py:_connect_first for the pre-fix bug."
    )
    assert fake_w3.is_connected.called, "w3.is_connected() should be called"


def test_connect_first_returns_none_when_web3_unreachable(monkeypatch):
    """When is_connected() returns False on every RPC (legitimately
    unreachable), _connect_first must return None — NOT because of
    an import error, but because the actual connect failed."""
    fake_w3 = MagicMock()
    fake_w3.is_connected.return_value = False
    fake_web3_class = MagicMock(return_value=fake_w3)
    fake_web3_class.HTTPProvider = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("web3.Web3", fake_web3_class, raising=False)

    import importlib
    from core import balances
    importlib.reload(balances)

    result = balances._connect_first(["https://broken.example.com"], timeout=1.0)
    assert result is None
    assert fake_w3.is_connected.called
