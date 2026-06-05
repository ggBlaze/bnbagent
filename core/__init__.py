"""BNB Agent — core agent loop.

Modules:
  - boot.py        → load policy, init TWAK, init SDK, register identity
  - main.py        → entry point: spawn sleeve loops + dashboard
  - portfolio.py   → equity, peak equity, PnL, drawdown, exposure tracking
  - risk.py        → circuit_breaker_check (called before every order)
  - tick.py        → shared tick harness (asyncio)
  - logger.py      → structured JSON logging
"""
