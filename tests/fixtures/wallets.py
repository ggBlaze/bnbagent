"""Shared test wallets and policies."""
from __future__ import annotations

from connectors.twak import TWAKWallet

# Hard-coded dev keys (testnet only). DO NOT use on mainnet.
EVALUATOR_KEY = "0x" + "a" * 64
AGENT_KEY     = "0x" + "b" * 64

EVALUATOR_ADDRESS = TWAKWallet.from_private_key(EVALUATOR_KEY).address
AGENT_ADDRESS     = TWAKWallet.from_private_key(AGENT_KEY).address

TEST_POLICY = {
    "version": "1.0.0",
    "issued_at": 1717593600,
    "expires_at": 1718284800,
    "evaluator_address": EVALUATOR_ADDRESS,
    "agent_address":     AGENT_ADDRESS,
    "global_risk": {
        "daily_loss_circuit_breaker_pct": 3.0,
        "per_trade_risk_pct":             1.0,
        "max_gross_leverage":             2.0,
        "max_single_position_pct":       15.0,
        "max_daily_trades":             100,
        "max_drawdown_pct":              8.0,
        "cooldown_after_breach_min":     60,
    },
    "sleeve_allocations": {"A": 0.70, "B": 0.20, "C": 0.10},
    "sleeves": {
        "A": {"enabled": True, "venue_selection": "highest_abs_funding_7d",
              "rebalance_hours": 8, "fund_floor_pct": 0.005,
              "basis_trigger_pct": 0.50, "max_position_pct": 15.0},
        "B": {"enabled": True, "volume_spike_mult": 2.0, "breakout_lookback_h": 4,
              "atr_len": 14, "atr_stop_mult": 2.0, "tp_pct": 3.0,
              "max_hold_min": 240, "kelly_fraction": 0.25, "max_position_pct": 10.0},
        "C": {"enabled": True, "zscore_threshold": 2.5, "stop_pct": 2.0,
              "target_pct": 1.0, "lookback_h": 1, "kelly_fraction": 0.25,
              "max_position_pct": 5.0},
    },
    "allowlist": {
        "cmc_rank_max": 50,
        "bsc_tokens": ["WBNB", "USDC", "CAKE", "BTCB", "ETH", "SOL", "XRP", "DOGE",
                       "ADA", "AVAX", "LINK", "DOT", "MATIC", "SHIB", "LTC", "BCH",
                       "NEAR", "ATOM", "UNI", "APT"],
        "perps_venues": ["aster", "killex", "apollox", "mux"],
        "dex_routers":  ["0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"],
    },
    "fees": {
        "x402_max_usdc_per_day": "10.00",
        "max_gas_price_gwei":    5,
    },
    "signature": "0x" + "00" * 65,
}
