# 🤖 BNB Agent

> **Autonomous three-sleeve BSC trading agent — built to win the [BNB HACK](https://coinmarketcap.com/api/hackathon/) hackathon.**
>
> **By Blaze · MIT License**

[![CoinMarketCap](https://img.shields.io/badge/CoinMarketCap-Agent%20Hub-yellow)](https://coinmarketcap.com/api/agent-hub/)
[![Trust Wallet](https://img.shields.io/badge/Trust%20Wallet-TWAK-purple)](https://developer.trustwallet.com/)
[![BNB Chain](https://img.shields.io/badge/BNB%20Chain-AI%20Agent%20SDK-orange)](https://www.bnbchain.org/en/solutions/ai-agent)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## What it does

BNB Agent is an autonomous trading agent that runs three strategies in parallel on BNB Smart Chain:

| Sleeve | Capital | Strategy | Expected PnL | Drawdown |
|---|---|---|---|---|
| **A** — Funding carry | 70% | Long spot on PancakeSwap v3 + short equivalent notional on a BSC perps venue. Direction-neutral. Collects funding every 8h. | +0.5% APR baseline | very low |
| **B** — DEX momentum | 20% | CMC signals (volume spike + 4h breakout) → 1–4h long with ATR stop and 3% TP. Quarter-Kelly sizing. | positive alpha | capped at 1%/trade |
| **C** — Mean-reversion | 10% | Fades 1h drops >2.5σ on top-20 BSC tokens. 2% stop, 1% target. | positive alpha | capped at 0.5%/trade |

**Hard risk controls (the only UX prompt the user sees):**
- Daily loss circuit breaker: **3%**
- Per-trade risk cap: **1%**
- Max gross leverage: **2x**
- Max single position: **15%**
- Curated token allowlist (top-50 CMC + vetted BNB-chain DEX list)

Every trade is gated by a versioned, signed **User Policy** — the user signs **once** at startup.

---

## The three sponsor layers (all deeply integrated)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  L1 — CoinMarketCap Agent Hub                                           │
│      Data API + Data MCP + Skills + x402 ($0.01 USDC/request)          │
│      ↓ pays for its own data via EIP-3009 transferWithAuthorization    │
├─────────────────────────────────────────────────────────────────────────┤
│  L2 — Trust Wallet Agent SDK (TWAK)                                    │
│      Self-custody local signing (AES-256-GCM, PBKDF2)                  │
│      "Unlock once, then your agent acts without per-transaction taps"  │
│      ↓ signs every BSC tx locally; keys never leave the host           │
├─────────────────────────────────────────────────────────────────────────┤
│  L3 — BNB AI Agent SDK                                                  │
│      BSC mainnet, PancakeSwap v3, BSC perps                            │
│      ERC-8004 identity NFT + ERC-8183 job escrow                       │
│      x402 finality <200ms                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Quick start

```bash
# 1. Day-1 sanity check (validates the whole stack in <1 min)
bash scripts/first_run.sh

# 2. Sign the policy
export BNBAGENT_PRIVATE_KEY=0x...   # dev only; in prod use TWAK keystore
bash scripts/sign_policy.sh

# 3. Run a 7-day replay (the rehearsal for the live PnL-replay window)
bash scripts/replay_week.sh

# 4. Start the agent
bash scripts/start_agent.sh

# 5. In another shell, start the dashboard
bash scripts/start_dashboard.sh
# → http://localhost:8000
```

---

## Repo layout

```
bnbagent/
├── config/                  # policy.yaml (signed) + config.yaml + allowlist + perps venues
├── core/                    # agent loop, portfolio, risk engine, tick harness, logger
├── connectors/              # CMC, x402, TWAK, bnbagent-sdk, IPFS adapters
├── strategies/              # sleeve A (carry), B (momentum), C (mean-rev)
├── policy/                  # EIP-191 sign/verify + version bumping
├── identity/                # ERC-8004 registration
├── jobs/                    # ERC-8183 open/submit/finalize
├── dashboard/               # FastAPI backend + Next.js frontend (single HTML file)
├── backtest/                # fetch history + replay harness + metrics
├── tests/                   # unit + integration tests
├── scripts/                 # first_run, sign_policy, register, open_window, replay, finalize
├── docs/                    # architecture, demo script, submission
└── infra/                   # docker, systemd
```

---

## Verification — what proves it works

| Check | Command | Pass criteria |
|---|---|---|
| Policy signs & verifies | `python -m policy.policy_verify` | prints `VERIFIED` |
| Risk engine respects all rules | `pytest tests/unit/test_risk.py -v` | all 9 tests pass |
| x402 payment builds correct header | `pytest tests/unit/test_x402.py -v` | signature recovers to wallet |
| 7-day replay runs end-to-end | `bash scripts/replay_week.sh` | report generated, sleeves traded |
| Full pipeline (boot→sign→register→jobs) | `pytest tests/integration/ -v` | identity + 4 jobs in `Funded` state |
| Live dashboard | `bash scripts/start_dashboard.sh` then open `http://localhost:8000` | all sections render |

---

## Why we win the contest

1. **Track 1 PnL replay** — delta-neutral funding carry (70% of capital) is the base PnL with near-zero directional exposure. Low drawdown, high Sharpe — exactly what the judging axes reward.
2. **All three $2K special prizes** — every sponsor is visibly used:
   - **CMC**: live x402 microcharge ledger on the dashboard
   - **Trust Wallet**: TWAK-signed tx list with BscScan deep links
   - **BNB SDK**: ERC-8004 identity NFT + ERC-8183 job lifecycle
3. **Production-ready design** — 11,000+ lines of typed Python, 50+ unit tests, replay harness that runs in 30s, Docker-compose for one-command stack-up, public live dashboard.

---

## Hackathon submission

- **DoraHacks**: <https://dorahacks.io/hackathon/bnbhack-twt-cmc/>
- **Track**: 1 (Autonomous Trading Agents) — main prize + all 3 stackable specials
- **Submission lock**: 2026-06-21 12:00 UTC
- **Live PnL replay window**: 2026-06-22 → 2026-06-28
- **Winners announced**: week of 2026-07-06

See [`docs/submission.md`](docs/submission.md) for the full submission form fields and [`docs/demo-script.md`](docs/demo-script.md) for the 3-minute demo video script.

---

## License

MIT
