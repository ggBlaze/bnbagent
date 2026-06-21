
"""v2.2.0: boot() must warn if testnet/live mode is on but the policy
has no live_window_start gate. Caught the 2026-06-21 incident where
the gate code was in place (v2.1.9) but policy.yaml didn't include
the timestamps — every pre-window trade was silently allowed."""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import yaml


def _write_cfg(tmp_path: Path, mode: str = "testnet") -> Path:
    cfg = {
        "mode": mode,
        "data_source": {"tier": "binance", "base_address": "0x" + "44" * 20},
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "rpcs": ["http://localhost:8545"],
        "chain_id": 56,
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _write_policy(tmp_path: Path, with_window: bool = True) -> Path:
    gr = (
        "  daily_loss_circuit_breaker_pct: 3.0\n"
        "  per_trade_risk_pct: 1.0\n"
        "  max_gross_leverage: 2.0\n"
        "  max_single_position_pct: 15.0\n"
        "  max_daily_trades: 100\n"
        "  max_drawdown_pct: 8.0\n"
        "  cooldown_after_breach_min: 60\n"
    )
    if with_window:
        gr += "  live_window_start: '2026-06-22T12:00:00Z'\n  live_window_end: '2026-06-28T12:00:00Z'\n"
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        "version: 1.0.0\n"
        "issued_at: 1781244077\n"
        "expires_at: 1783836077\n"
        "evaluator_address: '0xf9725D032e166E09f4d5c740046A65E36575bB0f'\n"
        "agent_address: '0xf9725D032e166E09f4d5c740046A65E36575bB0f'\n"
        "global_risk:\n" + gr +
        "sleeve_allocations: {A: 0.7, B: 0.2, C: 0.1}\n"
        "sleeves:\n"
        "  A: {enabled: true, venue_selection: highest_abs_funding_7d, rebalance_hours: 8, fund_floor_pct: 0.005, basis_trigger_pct: 0.5, max_position_pct: 15.0}\n"
        "  B: {enabled: true, volume_spike_mult: 2.0, breakout_lookback_h: 4, atr_len: 14, atr_stop_mult: 2.0, tp_pct: 3.0, max_hold_min: 240, kelly_fraction: 0.25, max_position_pct: 10.0}\n"
        "  C: {enabled: true, zscore_threshold: 2.5, stop_pct: 2.0, target_pct: 1.0, lookback_h: 1, kelly_fraction: 0.25, max_position_pct: 5.0}\n"
        "allowlist:\n"
        "  cmc_rank_max: 50\n"
        "  bsc_tokens: [WBNB, USDC, CAKE]\n"
        "  perps_venues: [aster, killex, apollox, mux]\n"
        "  dex_routers: ['0x9A489505a6B3cd73B4D6C8E6B3E8a3e7B9C8d2e1']\n"
        "fees:\n"
        "  x402_max_usdc_per_day: '10.00'\n"
        "  max_gas_price_gwei: 5\n"
        "signature: '0xababababababababababababababababababababababababababababababababababababababababababababababababababababababababababababababababab'\n"
    )
    return pol


def test_boot_warns_when_live_window_missing_in_testnet_mode(tmp_path, caplog):
    """Testnet mode + no live_window_start in policy = WARNING."""
    from core.boot import boot
    cfg = _write_cfg(tmp_path, mode="testnet")
    pol = _write_policy(tmp_path, with_window=False)
    with caplog.at_level(logging.WARNING, logger="core.boot"):
        try:
            boot(policy_path=str(pol), config_path=str(cfg), starting_equity=Decimal("100"))
        except Exception:
            pass  # boot may fail on wallet init in tests; we only care about the warning
    warnings = [r for r in caplog.records if "live_window_start" in r.message]
    assert len(warnings) >= 1, f"expected live_window warning, got: {[r.message for r in caplog.records]}"
    assert "testnet" in warnings[0].message


def test_boot_silent_when_live_window_present_in_testnet_mode(tmp_path, caplog):
    """Testnet mode + live_window_start present = no missing-window warning."""
    from core.boot import boot
    cfg = _write_cfg(tmp_path, mode="testnet")
    pol = _write_policy(tmp_path, with_window=True)
    with caplog.at_level(logging.WARNING, logger="core.boot"):
        try:
            boot(policy_path=str(pol), config_path=str(cfg), starting_equity=Decimal("100"))
        except Exception:
            pass
    warnings = [r for r in caplog.records if "live_window_start" in r.message and "WITHOUT" in r.message]
    assert warnings == [], f"expected NO live_window warning, got: {[r.message for r in warnings]}"


def test_boot_silent_in_replay_mode_without_live_window(tmp_path, caplog):
    """Replay/paper mode is exempt from the warning — it's correct to
    not have a window in a backtest."""
    from core.boot import boot
    cfg = _write_cfg(tmp_path, mode="replay")
    pol = _write_policy(tmp_path, with_window=False)
    with caplog.at_level(logging.WARNING, logger="core.boot"):
        try:
            boot(policy_path=str(pol), config_path=str(cfg), starting_equity=Decimal("100"))
        except Exception:
            pass
    warnings = [r for r in caplog.records if "live_window_start" in r.message and "WITHOUT" in r.message]
    assert warnings == [], f"replay mode should be exempt, got: {[r.message for r in warnings]}"
