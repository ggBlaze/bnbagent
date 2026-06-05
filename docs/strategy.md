# BNB Agent — Strategy

The three sleeves that compose BNB Agent's strategy are chosen to score well
on the PnL-replay judging axes (Returns, Drawdown, Risk-Adjusted, Rule-Adherence)
and to *visibly* exercise all three sponsor layers.

## Capital allocation

```
┌──────────────────────────────────────────────────────────┐
│ Sleeve A — Funding carry            70% of capital       │
│ Sleeve B — DEX momentum             20% of capital       │
│ Sleeve C — Mean-reversion           10% of capital       │
└──────────────────────────────────────────────────────────┘
```

## Sleeve A — Funding-rate carry (70%)

**Idea:** Perps markets have a "funding rate" — a periodic payment between longs
and shorts that keeps the perp price anchored to spot. When funding is positive,
shorts collect; when negative, longs collect. The strategy is to be on whichever
side is collecting, and to hedge the directional risk with a spot position.

**Mechanics:**
1. Pick a basket of top-20 BSC tokens (curated).
2. Pick the perps venue (Aster, KiloEx, ApolloX, MUX) with the highest average
   absolute funding on the basket, over the last 7 days. Re-evaluate daily.
3. For each token in the basket:
   - **Long spot** USDC→TOKEN on PancakeSwap v3 (notional = 70% × equity / N)
   - **Short perp** equivalent notional on the selected venue
4. **Direction-neutral**: spot gains offset perp losses on price moves; PnL comes
   from funding payments every 8h.
5. **Exits:** if `|funding_8h| < 0.005%` (rate converged), if liq distance < 10%,
   if `|basis| > 0.5%`, or if the daily circuit-breaker fires.

**Why this is the base sleeve:**
- Expected PnL: +0.5% APR baseline (≈ +0.01% per 8h funding × 3 epochs/day)
- Expected drawdown: very low (direction-neutral)
- Sharpe: high (low vol, positive drift)
- This is the dominant PnL contributor and the main reason the agent can win
  the PnL-replay scoring.

**Sponsors exercised:**
- **CMC** (Data API for funding rates, OHLCV for spot prices, listings)
- **Trust Wallet** (signs every spot swap)
- **BNB SDK** (PancakeSwap v3 router interaction, perps open/close)

## Sleeve B — DEX momentum (20%)

**Idea:** On BNB-chain DEX pairs, momentum works because:
- Retail enters late on listings (FOMO)
- Volume spikes precede large price moves
- 4h breakouts are tradable because the BSC ecosystem is noisier than CEX majors

**Mechanics:**
1. Every 5 min, scan CMC OHLCV for the curated DEX universe.
2. **Signal:** `volume_5m > 2.0 × volume_ma_12h` AND `close > max(high, last 4h)`.
3. **Sizing:** quarter-Kelly with p_win=0.55 default, capped at 1% per-trade risk.
4. **Entry:** long spot via PancakeSwap.
5. **Exits:** ATR14 stop, 3% take-profit, 4h time-stop, or daily circuit breaker.

**Why 20%:** momentum can whipsaw, and we want to leave room for the carry sleeve
to compound. 20% is enough to contribute meaningfully without dominating risk.

**Sponsors exercised:**
- **CMC** (Data API for OHLCV, listings, x402 micropayments)
- **Trust Wallet** (signs the entry swap)
- **BNB SDK** (PancakeSwap v3 swap)

## Sleeve C — Mean-reversion (10%)

**Idea:** On the top-20 BSC tokens, sharp 1h drops are usually overreactions
(flash crashes, liquidation cascades, exchange-specific events). Fading them
with strict stops has a positive expected value.

**Mechanics:**
1. Every 5 min, scan CMC OHLCV for top-20 BSC tokens.
2. **Signal:** `ret_1h / realized_vol_1h ≤ -2.5` (a >2.5σ drop).
3. **Sizing:** quarter-Kelly with p_win=0.70 default, capped at 1% per-trade risk.
4. **Entry:** long spot via PancakeSwap.
5. **Exits:** +1% target, -2% stop, 6h time-stop, or daily circuit breaker.

**Why only 10%:** this is a "tail" strategy — most of the time it does nothing.
The 10% allocation caps downside if a regime change makes mean-reversion fail.

**Sponsors exercised:** same as Sleeve B.

## Why this composition wins the scoring

| Judging axis | What we do | Why we score well |
|---|---|---|
| **Returns** | 70% in carry + alpha from B + C | carry is the base, alpha stacks on top |
| **Drawdown** | 70% hedged + 1%/3% circuit breakers | low max-DD |
| **Risk-adjusted (Sharpe)** | low-vol base + Kelly-sized alpha | high Sharpe |
| **Rule adherence** | every trade gates through circuit_breaker_check against a signed YAML | trivially rule-adherent |

## Why we *also* win the 3 special prizes

- **Best Use of CMC** — every data call is paid via x402. The dashboard shows the
  full microcharge ledger. The agent has Skills from the marketplace as composable
  signal blocks.
- **Best Use of Trust Wallet** — every tx is TWAK-signed. The dashboard shows the
  full signed-tx list with BscScan deep links. Keys never leave the host.
- **Best Use of BNB AI Agent SDK** — the agent has an ERC-8004 identity NFT and
  each sleeve is an ERC-8183 job with the user as evaluator. All of this is
  visible on-chain and on the dashboard.
