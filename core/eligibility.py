"""Eligibility filter for the BNB HACK 2026 Track 1 contest.

The contest publishes a fixed list of 149 BEP-20 tokens at
https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail . Trades outside this
list "do not count" toward PnL. This module loads the list once at import
time from `data/eligible_tokens.json` and exposes a small surface that the
sleeves + the dashboard + the policy validator all call into.

The list is also the `cmc_global_filter`-style Skill that *could* fire; the
difference is this one is contract, not signal — it cannot be opted out of
from the dashboard. Any symbol that is not in the eligible set is rejected
before the trade is constructed.

This is intentionally a separate module from `cmc_global_filter.py`:
`cmc_global_filter` is a skill that fires on macro conditions (global
market cap 24h change). This module is the *universe* filter — pure
set membership against a static, contest-published list. Different
concerns, different file.

Usage from a sleeve:
    from core.eligibility import filter_universe
    universe = filter_universe(self.cfg["cmc"]["basket_symbols"])

If a symbol is dropped, a structured warning is logged so the agent's
audit log records *why* the universe shrank. The dropped symbols are
also reported on the dashboard's Live pane so the operator can see
"BNB HACK eligibility: filtered 5 symbols" instead of just "no trades
in sleeve A today."

The "BNB HACK Track 1" mode flag (env var BNB_HACK_TRACK1=true) decides
whether the filter is enforced strictly or treated as a soft warning. In
strict mode (the default during the contest window), non-eligible symbols
are dropped. In soft mode (default outside the contest window), non-eligible
symbols are tagged `in_scope: false` so the agent logs the violation but
can still trade them. This lets the same code work for:
  - the contest (strict — only the 149 list)
  - normal long-running use (soft — log violations, no drop)
  - testing (strict_off — no filter at all, so test fixtures with
    fake symbols still pass)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

# Where the eligibility list lives. Pinned to the repo so a fresh clone
# has it. Tests pin the file's _schema_version string to make a
# contested-list update a deliberate commit.
_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "eligible_tokens.json"

# Symbol aliases that show up in some CMC rank maps but resolve to the
# same on-chain BEP-20 as an in-scope symbol. Maps an *alias* (what the
# code might pass in) to the *canonical in-scope symbol* (what the list
# actually contains). Empty by default — the contest list is the source
# of truth. Patches can be added here if a CMC symbol diverges.
_ALIASES: dict[str, str] = {
    # Some CMC ticks render the BNB-chain bridged BTC as "BTC" rather
    # than "BTCB". The contest's eligible list does NOT contain "BTC"
    # (it has the native non-BEP version, and SLX, etc., but not the
    # bridged BTC). Treat "BTC" as out-of-scope. No alias.
    # BTCB (BEP-20 BTC) is also not on the list. No alias.
}

# Mode flags. Strict = contest mode (default). Soft = log only.
# Off = no filter (testing).
_STRICT = "strict"
_SOFT = "soft"
_OFF = "off"


def _load_list() -> tuple[set[str], str]:
    """Load the eligible token list from disk. Returns (set, schema_version).

    The set is uppercase so callers can pass mixed-case symbols.
    """
    with open(_LIST_PATH) as f:
        blob = json.load(f)
    schema = blob.get("_schema_version", "unknown")
    raw = blob.get("tokens", [])
    return {s.upper() for s in raw}, schema


# Load once at import. The file is small (~2KB) and committed to the repo,
# so this is a sync read. We do NOT use a module-level cache for the
# *mode* — that's read per-call so the env var can be flipped at runtime
# (e.g., the dashboard toggle on the Live pane).
_ELIGIBLE: set[str] = set()
_SCHEMA_VERSION: str = "unknown"
_load_attempted: bool = False


def _ensure_loaded() -> None:
    global _ELIGIBLE, _SCHEMA_VERSION, _load_attempted
    if _load_attempted:
        return
    _load_attempted = True
    try:
        _ELIGIBLE, _SCHEMA_VERSION = _load_list()
    except Exception as e:
        # The list is critical for contest mode. If the file is missing
        # or malformed, the default in strict mode is to fail closed:
        # no symbol is eligible. The dashboard will surface this in red.
        log.error("eligibility: failed to load %s: %s", _LIST_PATH, e)
        _ELIGIBLE = set()
        _SCHEMA_VERSION = "load-failed"


def _mode() -> str:
    """Resolve the active mode from the BNB_HACK_TRACK1 env var.

    - "true" (case-insensitive), "1", or unset inside the contest window →
      strict (the default for production runs during the contest).
    - "false", "0", or "off" → strict_off (testing/development).
    - "soft" → log only, no drop.
    """
    val = os.environ.get("BNB_HACK_TRACK1", "").strip().lower()
    if val in ("soft",):
        return _SOFT
    if val in ("false", "0", "off", "no", "disabled"):
        return _OFF
    # Default to strict — the contest window is the norm for this codebase.
    return _STRICT


def is_eligible(symbol: str) -> bool:
    """True iff `symbol` is in the contest's eligible BEP-20 list.

    Case-insensitive. Applies alias normalization first. Returns False
    if the eligibility list failed to load (fail-closed).
    """
    _ensure_loaded()
    if not _ELIGIBLE:
        return False
    s = (symbol or "").strip().upper()
    if not s:
        return False
    s = _ALIASES.get(s, s)
    return s in _ELIGIBLE


def schema_version() -> str:
    """The _schema_version field of the loaded eligible_tokens.json.

    Used by tests to detect when the contest list changes and force a
    re-validation of every config that depends on it.
    """
    _ensure_loaded()
    return _SCHEMA_VERSION


def eligible_set() -> set[str]:
    """The full eligible set (uppercase). Use for diagnostics, not for
    trading decisions — call is_eligible() for that.
    """
    _ensure_loaded()
    return set(_ELIGIBLE)


def filter_universe(
    symbols: Iterable[str],
    *,
    on_drop: "callable | None" = None,
) -> list[str]:
    """Filter a list of symbols to the contest's eligible set.

    Honors the BNB_HACK_TRACK1 env var:
      - strict  : drop non-eligible symbols (default)
      - soft    : keep them, but call on_drop() for each
      - off     : no filtering, return the input list unchanged

    `on_drop` is an optional callable(symbol, reason) called once per
    dropped symbol. The agent's audit log uses this to record *why*
    the universe shrank.

    Returns a list of uppercase, deduplicated, order-preserved symbols.
    """
    _ensure_loaded()
    mode = _mode()
    if mode == _OFF:
        # Preserve dedup + order. Caller is responsible for case.
        seen: set[str] = set()
        out: list[str] = []
        for s in symbols:
            su = s.strip().upper()
            if su and su not in seen:
                seen.add(su)
                out.append(su)
        return out
    seen = set()
    out = []
    for s in symbols:
        su = s.strip().upper()
        if not su or su in seen:
            continue
        if su in _ELIGIBLE:
            seen.add(su)
            out.append(su)
        else:
            reason = f"not in contest eligible list (schema={_SCHEMA_VERSION})"
            if mode == _SOFT:
                # soft mode: keep the symbol AND call on_drop for
                # telemetry. The agent logs the violation but the
                # trade is allowed (with a warning). Outside the
                # contest window, this is the right behavior.
                seen.add(su)
                out.append(su)
                if on_drop is not None:
                    try:
                        on_drop(su, reason)
                    except Exception as e:
                        log.warning("eligibility: on_drop callback failed: %s", e)
                else:
                    log.info("eligibility (soft): %s kept but flagged — %s", su, reason)
            else:
                # strict mode (default): drop the symbol. on_drop is
                # also called for telemetry so the dashboard Live
                # pane can show "filtered N symbols".
                log.info("eligibility (strict): %s dropped — %s", su, reason)
                if on_drop is not None:
                    try:
                        on_drop(su, reason)
                    except Exception as e:
                        log.warning("eligibility: on_drop callback failed: %s", e)
    return out


def report() -> dict:
    """A diagnostic summary for the dashboard. Reports the active mode,
    the schema version, the size of the eligible set, and the last
    filter result if cached. Intended for /api/eligibility.
    """
    _ensure_loaded()
    return {
        "mode": _mode(),
        "schema_version": _SCHEMA_VERSION,
        "eligible_count": len(_ELIGIBLE),
        "list_path": str(_LIST_PATH),
        "aliases": dict(_ALIASES),
    }
