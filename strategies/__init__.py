"""BNB Agent — strategies.

The three sleeves that compose BNB Agent's strategy:

  - SleeveACarry    (70% capital) — funding-rate carry on BSC perps
  - SleeveBMomentum (20% capital) — DEX momentum on BNB-chain pairs
  - SleeveCMeanRev  (10% capital) — mean-reversion on top-20 BSC tokens

Each sleeve exposes a `tick()` async method. The Agent's TickLoop calls it on
its schedule (30s / 300s / 300s). Every order is gated by `agent.allow_trade()`,
which calls the risk engine. The risk engine is the only enforcement of the
user-signed policy.
"""
