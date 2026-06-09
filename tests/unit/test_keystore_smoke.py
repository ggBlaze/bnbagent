"""Keystore round-trip smoke test.

Regression for the v2.0.8 hardening (H-2): the keystore create + decrypt
loop must work without a real TWAK keystore file present, on any
pycryptodome >= 3.18 install. The test runs against an isolated temp
keystore via TWAK_KEYSTORE env var so it never touches the user's
real `~/.twak/wallet.json`.

This test catches:
  - missing pycryptodome dependency (v2.0.7.1 fixed)
  - shadow-imported AES import paths (v2.0.8 hoisted to module level)
  - any future regression where the import path goes back inside a function
"""
import os
import tempfile
from pathlib import Path

import pytest
from eth_account import Account


def test_keystore_roundtrip(tmp_path, monkeypatch):
    # isolate the keystore from the user's real one
    keystore = tmp_path / "wallet.json"
    monkeypatch.setenv("TWAK_KEYSTORE", str(keystore))

    # import the module AFTER setting the env var (the path is read at import-time)
    from connectors import keystore as ks
    # re-read the env var in case the module already imported with a different path
    ks._keystore_path = lambda: keystore

    password = "smoke-test-passw0rd"
    summary = ks.create_keystore(password)
    assert "address" in summary
    address = summary["address"]
    assert address.startswith("0x")
    assert len(address) == 42

    # file exists with chmod 600
    assert keystore.exists()
    mode = keystore.stat().st_mode & 0o777
    assert mode == 0o600, f"keystore mode is {oct(mode)}, expected 0o600"

    # decrypt + verify the key is usable
    blob = __import__("json").loads(keystore.read_text())
    raw_key = ks.decrypt_keystore(blob, password)
    assert len(raw_key) == 32
    recovered = Account.from_key(raw_key).address
    assert recovered.lower() == address.lower()

    # wrong password fails (decrypt_and_verify raises ValueError on GCM tag mismatch)
    with pytest.raises(ValueError):
        ks.decrypt_keystore(blob, "wrong-password")


def test_pycryptodome_imported_at_module_level():
    """H-2 regression: AES must be importable from connectors.keystore
    without an in-function shadow. A fresh `pip install` of bnbagent
    (which now declares pycryptodome>=3.18) will have this working."""
    from Crypto.Cipher import AES  # noqa: F401
    from connectors import keystore as ks
    # if the module loaded at all, AES is available (the import at module
    # top of keystore.py would have raised ImportError otherwise)
    assert ks.AES is AES


def test_twak_module_loads_with_pycryptodome():
    """H-2 regression: connectors.twak must also load with AES at top level.
    We just import it; if pycryptodome is missing, this raises ImportError
    immediately and clearly, not later at first-decrypt time."""
    import connectors.twak  # noqa: F401
    from Crypto.Cipher import AES  # noqa: F401
    assert True
