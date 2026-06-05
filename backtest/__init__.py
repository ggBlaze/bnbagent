"""BNB Agent — backtest + replay harness.

The replay harness is the only way to validate the strategy against a held-out
week of market data before the live PnL-replay window opens. Strategy is
deterministic: given the same tape, the same trades will fire.

Usage:
  python -m backtest.replay --tape data/synthetic_week.json --report data/report.html
"""
