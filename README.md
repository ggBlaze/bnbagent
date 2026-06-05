# BNB Agent

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
- Per-symbol **post-loss cool-off** (4–6h) to prevent revenge trades

Every trade is gated by a versioned, signed **User Policy** — the user signs **once** at startup.

---

## Quick start — one command

```bash
# 1. install (idempotent, <90s on a fresh box)
bash install.sh

# 2. run (agent + live dashboard on http://localhost:8000)
bash bnbagent
```

That's it. The dashboard auto-loads in any browser pointed at port 8000.
**First-time users land in the Setup wizard** (Network → Wallet → Sign
Policy → Ready). The wizard lets you generate a wallet, import an
existing private key, set RPC endpoints, and sign the policy — all from
the browser, with the private key encrypted to disk and never echoed
back. After the wizard completes the dashboard switches to the Live pane.

The Live pane shows equity, drawdown, sleeve breakdown, **CMC x402
microcharge ledger**, **TWAK-signed tx list with BscScan links**,
**ERC-8004 identity NFT**, **ERC-8183 job escrow**, the **signed User
Policy**, a **config editor**, and a **kill switch** in the right rail.

For the live PnL-replay rehearsal:

```bash
bash bnbagent --replay    # 7-day synthetic replay; report → data/reports/replay.html
```

For production: set `TWAK_KEYSTORE` + `TWAK_PWD` (or `BNBAGENT_PRIVATE_KEY` for
dev) and re-sign the policy. See [`docs/install.md`](docs/install.md).

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

## Repo layout

```
bnbagent/
├── README.md
├── install.sh                     # one-command installer (creates venv, signs policy, etc)
├── bnbagent                       # one-command run (agent + dashboard, Ctrl+C to stop)
├── pyproject.toml                 # Python deps
├── package.json                   # Node deps (@trustwallet/cli)
│
├── config/
│   ├── config.yaml                # main config
│   ├── policy.yaml                # signed User Policy (EIP-191)
│   └── policy.schema.json
│
├── core/                          # the agent loop
│   ├── boot.py                    # load config, init wallet, register identity
│   ├── main.py                    # entry point — spawns 3 sleeve loops
│   ├── portfolio.py               # equity, peak, PnL, drawdown
│   ├── risk.py                    # circuit_breaker_check() (called before every order)
│   ├── tick.py                    # shared tick harness + heartbeat
│   ├── control.py                 # dashboard → agent IPC (kill switch, sleeve toggles)
│   └── utils.py                   # shared helpers
│
├── connectors/                    # sponsor adapters
│   ├── cmc.py                     # CMC Data API + Data MCP client (x402-aware)
│   ├── x402.py                    # EIP-3009 USDC payment flow
│   ├── twak.py                    # TWAK wrapper: sign_tx, sign_message
│   ├── bnb_sdk.py                 # BSC, PancakeV3, Perps, ERC8004, ERC8183
│   └── ipfs.py                    # local IPFS client
│
├── strategies/                    # the 3 sleeves
│   ├── sleeve_a_carry.py
│   ├── sleeve_b_momentum.py
│   └── sleeve_c_meanrev.py
│
├── policy/                        # EIP-191 sign/verify
├── identity/                      # ERC-8004 registration
├── jobs/                          # ERC-8183 open/submit/finalize
├── dashboard/                     # FastAPI backend + single-file frontend
├── backtest/                      # replay harness + metrics
├── tests/                         # unit + integration tests
├── scripts/                       # the granular scripts (sign_policy, open_window, etc)
├── docs/                          # architecture, ops, install, demo, submission, audit
└── infra/                         # docker, systemd
```

---

## Verification

| Check | Command | Pass criteria |
|---|---|---|
| Day-1 sanity (TWAK + x402 + ERC-8004) | `bash scripts/first_run.sh` | all green |
| Policy signs & verifies | `python -m policy.policy_verify` | prints `VERIFIED` |
| Risk engine respects all rules | `pytest tests/unit/test_risk.py -v` | all tests pass |
| 7-day replay | `bash bnbagent --replay` | report generated, sleeves traded |
| Full pipeline (boot→sign→register→jobs) | `pytest tests/integration/ -v` | identity + jobs in `Funded` state |
| Live dashboard | `bash bnbagent` → open `http://localhost:8000` | all sections render, charts animate |
| Kill switch | `POST /api/control {"kill": true}` | next `allow_trade` returns `"kill switch engaged"` |

---

## Why we win the contest

1. **Track 1 PnL replay** — delta-neutral funding carry (70% of capital) is
   the base PnL with near-zero directional exposure. Low drawdown, high
   Sharpe — exactly what the judging axes reward.
2. **All three $2K special prizes** — every sponsor is visibly used:
   - **CMC**: live x402 microcharge ledger on the dashboard
   - **Trust Wallet**: TWAK-signed tx list with BscScan deep links
   - **BNB SDK**: ERC-8004 identity NFT + ERC-8183 job lifecycle
3. **Production-ready design** — 11,000+ lines of typed Python, 50+ unit tests,
   1-command install + 1-command run, public live dashboard with a kill switch.

See [`docs/audit-2026-06-05.md`](docs/audit-2026-06-05.md) for the trading-logic
audit and hardening pass that was performed before the live window.

---

## Hackathon submission

- **DoraHacks**: <https://dorahacks.io/hackathon/bnbhack-twt-cmc/>
- **Track**: 1 (Autonomous Trading Agents) — main prize + all 3 stackable specials
- **Submission lock**: 2026-06-21 12:00 UTC
- **Live PnL replay window**: 2026-06-22 → 2026-06-28
- **Winners announced**: week of 2026-07-06

See [`docs/submission.md`](docs/submission.md) for the full submission form
fields and [`docs/demo-script.md`](docs/demo-script.md) for the 3-minute demo
video script.

---

## License

MIT
