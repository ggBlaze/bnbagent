"""Tests for core/eligibility.py and data/eligible_tokens.json.

The eligible list is the contest's contract with us — every BEP-20
traded during the live window must be in the list, or it "does not
count" toward PnL. These tests pin:

  1. The list is loaded and well-formed (size, schema version, all
     symbols uppercase ASCII, the 币安人生 (Chinese characters) entry
     round-trips).
  2. is_eligible() is case-insensitive, alias-aware, fail-closed.
  3. filter_universe() honors the strict / soft / off modes.
  4. The shipped config (basket_symbols, dex_universe_symbols,
     bsc_tokens allowlist) is a STRICT SUBSET of the eligible list —
     so a fresh clone can never accidentally trade out-of-scope.
  5. The schema_version is wired into the circuit_breaker_check
     reason string (so a stale list is visible in trade-rejection
     audit log).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make `core` importable when pytest is run from the repo root.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core import eligibility  # noqa: E402


# -- 1. The list itself ---------------------------------------------------

def test_eligible_list_loads():
    """The JSON parses, has tokens, and a schema_version."""
    assert eligibility.eligible_set(), "eligible set is empty"
    sv = eligibility.schema_version()
    assert sv and sv != "unknown", f"schema_version is {sv!r}"
    assert sv != "load-failed", "eligibility list failed to load"


def test_eligible_list_size_is_at_least_140():
    """The DoraHacks page says 149. We allow 140–160 (dedupe of
    duplicates + future additions) so a test that sees 149 or 148 or
    150 all pass — but a totally-wrong-sized list fails."""
    n = len(eligibility.eligible_set())
    assert 140 <= n <= 160, f"expected ~149 in eligible set, got {n}"


def test_eligible_list_all_uppercase_ascii_or_unicode():
    """Symbols are case-sensitive in CMC. The contest list is uppercase.
    A symbol like "eth" (lowercase) would be a config error."""
    for s in eligibility.eligible_set():
        assert s == s.upper(), f"non-uppercase symbol: {s!r}"
        # ASCII letters/digits OR the single 币安人生 entry which is
        # Chinese (4 CJK code points). Anything else is suspicious.
        is_ascii = all(c.isascii() for c in s)
        is_known_unicode = s == "\u5e01\u5b89\u4eba\u751f"
        assert is_ascii or is_known_unicode, f"unexpected unicode symbol: {s!r}"


def test_eligible_list_contains_known_in_scope_tokens():
    """Spot-check well-known tokens. If any of these go missing,
    someone updated the list and broke the universe."""
    must_have = {"ETH", "USDT", "USDC", "CAKE", "XRP", "DOGE", "ADA", "LINK",
                 "DOT", "AVAX", "SHIB", "AAVE", "UNI", "LDO", "APE", "1INCH", "SUSHI"}
    missing = must_have - eligibility.eligible_set()
    assert not missing, f"missing expected in-scope tokens: {missing}"


def test_chinese_symbol_roundtrips():
    """The 币安人生 entry must survive the JSON load and be
    in the eligible set. If a contributor normalizes the JSON to
    NFC and the editor saves as NFD, the test catches the
    normalization drift."""
    cn = "\u5e01\u5b89\u4eba\u751f"
    assert cn in eligibility.eligible_set(), f"{cn!r} not in eligible set"


# -- 2. is_eligible() ------------------------------------------------------

def test_is_eligible_uppercase_hit():
    assert eligibility.is_eligible("ETH") is True


def test_is_eligible_lowercase_hit():
    """CMC sometimes returns mixed-case. is_eligible is case-insensitive."""
    assert eligibility.is_eligible("eth") is True
    assert eligibility.is_eligible("Eth") is True


def test_is_eligible_whitespace_stripped():
    assert eligibility.is_eligible("  ETH  ") is True


def test_is_eligible_miss():
    """Tokens that are NOT on the list return False. MATIC, NEAR, APT,
    BTC, SOL, WBNB, BTCB are all common CMC symbols that are NOT in
    the contest's 149 — the universe filter must reject them."""
    for s in ("MATIC", "NEAR", "APT", "BTC", "SOL", "WBNB", "BTCB", "FOO", ""):
        assert eligibility.is_eligible(s) is False, f"unexpectedly eligible: {s!r}"


def test_is_eligible_fail_closed_on_load_failure(tmp_path, monkeypatch):
    """If the list file is missing/malformed, every symbol is rejected.
    This is the 'fail closed' contract — a deleted file must not
    silently allow out-of-scope trades."""
    # Point the module at a non-existent path by breaking the loaded
    # set directly. The module captures it on first call.
    monkeypatch.setattr(eligibility, "_ELIGIBLE", set())
    monkeypatch.setattr(eligibility, "_load_attempted", True)
    assert eligibility.is_eligible("ETH") is False
    assert eligibility.is_eligible("USDC") is False


# -- 3. filter_universe() --------------------------------------------------

def test_filter_universe_strict_drops_known_out_of_scope():
    """The default mode is 'strict' (BNB_HACK_TRACK1 unset or 'true').
    The shipped basket has BTC, SOL, MATIC, NEAR, APT — all out of
    scope. filter_universe must drop them."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    dropped: list[str] = []
    out = eligibility.filter_universe(
        ["ETH", "BTC", "SOL", "USDC", "MATIC", "NEAR", "APT", "CAKE"],
        on_drop=lambda s, r: dropped.append(s),
    )
    # 5 should be dropped: BTC, SOL, MATIC, NEAR, APT
    assert set(out) == {"ETH", "USDC", "CAKE"}, f"got: {out}"
    assert set(dropped) == {"BTC", "SOL", "MATIC", "NEAR", "APT"}, f"got: {dropped}"
    monkeypatch.undo()


def test_filter_universe_soft_keeps_but_logs():
    """In soft mode (BNB_HACK_TRACK1=soft), symbols are NOT dropped, but
    on_drop is called for telemetry."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("BNB_HACK_TRACK1", "soft")
    dropped: list[str] = []
    out = eligibility.filter_universe(
        ["ETH", "BTC", "MATIC", "USDC"],
        on_drop=lambda s, r: dropped.append(s),
    )
    assert set(out) == {"ETH", "BTC", "MATIC", "USDC"}, f"got: {out}"
    assert set(dropped) == {"BTC", "MATIC"}, f"got: {dropped}"
    monkeypatch.undo()


def test_filter_universe_off_returns_input_unchanged():
    """In off mode (BNB_HACK_TRACK1=off / false), the filter is bypassed.
    This is for backtest / replay use."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("BNB_HACK_TRACK1", "off")
    out = eligibility.filter_universe(["ETH", "FAKE", "WBNB"])
    assert out == ["ETH", "FAKE", "WBNB"], f"got: {out}"
    monkeypatch.undo()


def test_filter_universe_dedup_and_order():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    out = eligibility.filter_universe(["ETH", "BTC", "ETH", "USDC", "BTC"])
    assert out == ["ETH", "USDC"], f"got: {out}"
    monkeypatch.undo()


def test_filter_universe_empty_input():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    assert eligibility.filter_universe([]) == []
    monkeypatch.undo()


# -- 4. Shipped config subset check ---------------------------------------

def _load_yaml(path: Path) -> dict:
    """YAML loader that doesn't depend on PyYAML (it's a test dep, but
    not always installed in CI's default extras)."""
    import yaml
    return yaml.safe_load(path.read_text()) or {}


def test_shipped_basket_is_subset_of_eligible():
    """config/config.yaml → cmc.basket_symbols must be a subset of the
    eligible list. This is the contract that prevents a future
    contributor from sneaking a non-eligible symbol into the basket."""
    cfg = _load_yaml(ROOT / "config" / "config.yaml")
    basket = [s.upper() for s in (cfg.get("cmc") or {}).get("basket_symbols", [])]
    out_of_scope = set(basket) - eligibility.eligible_set()
    assert not out_of_scope, f"basket has out-of-scope symbols: {out_of_scope}"


def test_shipped_dex_universe_is_subset_of_eligible():
    cfg = _load_yaml(ROOT / "config" / "config.yaml")
    dex = [s.upper() for s in (cfg.get("cmc") or {}).get("dex_universe_symbols", [])]
    out_of_scope = set(dex) - eligibility.eligible_set()
    assert not out_of_scope, f"dex_universe has out-of-scope symbols: {out_of_scope}"


def test_shipped_policy_allowlist_is_subset_of_eligible():
    """config/policy.yaml.example → bsc_tokens. This is the file the
    install.sh uses as the template for the user's signed policy. The
    allowlist in the user's live policy is the LAST LINE OF DEFENSE —
    if a non-eligible symbol is in there, the agent can trade it."""
    example = ROOT / "config" / "policy.yaml.example"
    if not example.exists():
        pytest.skip("policy.yaml.example missing")
    pol = _load_yaml(example)
    al = (pol.get("allowlist") or {}).get("bsc_tokens", [])
    out_of_scope = set(s.upper() for s in al) - eligibility.eligible_set()
    assert not out_of_scope, f"policy bsc_tokens allowlist has out-of-scope: {out_of_scope}"


# -- 5. Risk engine integration ------------------------------------------

def test_risk_engine_rejects_non_eligible_in_strict_mode(monkeypatch):
    """The risk engine is the defense-in-depth check. Even if a sleeve
    forgets to filter, the order is rejected with a clear reason
    that includes the schema_version (so a stale list is visible)."""
    from core.risk import ProposedTrade, circuit_breaker_check
    from decimal import Decimal

    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    policy = {
        "allowlist": {"bsc_tokens": ["ETH", "USDC", "MATIC"]},  # MATIC in the user's allowlist!
        "global_risk": {
            "max_gross_leverage": 2.0,
            "per_trade_risk_pct": 1.0,
            "max_single_position_pct": 15.0,
            "daily_loss_circuit_breaker_pct": 5.0,
            "max_daily_trades": 100,
        },
        "sleeves": {
            "A": {"max_position_pct": 15.0, "enabled": True},
            "B": {"max_position_pct": 10.0, "enabled": True},
            "C": {"max_position_pct": 5.0, "enabled": True},
        },
    }
    proposed = ProposedTrade(
        sleeve="A", symbol="MATIC", side="buy",
        notional_usdc=Decimal("100"), risk_usdc=Decimal("1"),
    )
    ok, reason = circuit_breaker_check(
        current_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        open_positions=[],
        proposed=proposed,
        policy=policy,
    )
    assert ok is False, "MATIC should be rejected in strict mode"
    assert "BNB HACK eligible 149" in reason, f"reason should name the rule: {reason!r}"
    # The schema version should be in the rejection reason so the operator
    # can tell if the list is stale.
    assert "schema=" in reason, f"reason should include schema version: {reason!r}"


def test_risk_engine_allows_eligible_in_strict_mode(monkeypatch):
    from core.risk import ProposedTrade, circuit_breaker_check
    from decimal import Decimal

    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    policy = {
        "allowlist": {"bsc_tokens": ["ETH", "USDC"]},
        "global_risk": {
            "max_gross_leverage": 2.0,
            "per_trade_risk_pct": 1.0,
            "max_single_position_pct": 15.0,
            "daily_loss_circuit_breaker_pct": 5.0,
            "max_daily_trades": 100,
        },
        "sleeves": {
            "A": {"max_position_pct": 15.0, "enabled": True},
            "B": {"max_position_pct": 10.0, "enabled": True},
            "C": {"max_position_pct": 5.0, "enabled": True},
        },
    }
    proposed = ProposedTrade(
        sleeve="A", symbol="ETH", side="buy",
        notional_usdc=Decimal("100"), risk_usdc=Decimal("1"),
    )
    ok, reason = circuit_breaker_check(
        current_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        open_positions=[],
        proposed=proposed,
        policy=policy,
    )
    # May be True or False depending on other rules; the test is that
    # the rejection reason (if any) is NOT about eligibility.
    if not ok:
        assert "BNB HACK eligible" not in reason, f"unexpected eligibility reject: {reason!r}"


def test_risk_engine_allows_non_eligible_in_off_mode(monkeypatch):
    """Outside the contest, MATIC is fine. The off mode must not reject
    it on eligibility grounds."""
    from core.risk import ProposedTrade, circuit_breaker_check
    from decimal import Decimal

    monkeypatch.setenv("BNB_HACK_TRACK1", "off")
    policy = {
        "allowlist": {"bsc_tokens": ["ETH", "MATIC"]},
        "global_risk": {
            "max_gross_leverage": 2.0,
            "per_trade_risk_pct": 1.0,
            "max_single_position_pct": 15.0,
            "daily_loss_circuit_breaker_pct": 5.0,
            "max_daily_trades": 100,
        },
        "sleeves": {
            "A": {"max_position_pct": 15.0, "enabled": True},
            "B": {"max_position_pct": 10.0, "enabled": True},
            "C": {"max_position_pct": 5.0, "enabled": True},
        },
    }
    proposed = ProposedTrade(
        sleeve="A", symbol="MATIC", side="buy",
        notional_usdc=Decimal("100"), risk_usdc=Decimal("1"),
    )
    ok, reason = circuit_breaker_check(
        current_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        open_positions=[],
        proposed=proposed,
        policy=policy,
    )
    if not ok:
        assert "BNB HACK eligible" not in reason, f"off mode rejected on eligibility: {reason!r}"
    monkeypatch.undo()


# -- 6. report() / dashboard payload --------------------------------------

def test_report_shape():
    r = eligibility.report()
    assert "mode" in r
    assert "schema_version" in r
    assert "eligible_count" in r
    assert "list_path" in r
    assert "aliases" in r
    assert r["eligible_count"] >= 140
    assert r["mode"] in ("strict", "soft", "off")
