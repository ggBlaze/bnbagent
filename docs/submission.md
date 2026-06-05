# BNB Agent — Hackathon Submission

**Hackathon:** BNB HACK (CoinMarketCap × Trust Wallet × BNB Chain)
**DoraHacks:** https://dorahacks.io/hackathon/bnbhack-twt-cmc/
**Track:** 1 (Autonomous Trading Agents)
**Submission lock:** 2026-06-21 12:00 UTC
**Live PnL-replay window:** 2026-06-22 → 2026-06-28
**Winners announced:** week of 2026-07-06

---

## Submission form fields (pre-filled)

### Project name
**BNB Agent**

### Tagline
> Autonomous three-sleeve BSC trading agent — funding carry, DEX momentum, mean-reversion. Uses CoinMarketCap Agent Hub (x402), Trust Wallet Agent SDK, and the BNB AI Agent SDK.

### Category
**Track 1 — Autonomous Trading Agents**

### Team
Solo / team name: (fill in)

### GitHub repo
`https://github.com/<your-org>/bnbagent`

### Live dashboard
`https://bnbagent.example/dashboard` (deploy to a public host before submission)

### Demo video
YouTube unlisted / Loom link, 3 minutes, 1080p. See [`demo-script.md`](demo-script.md).

### Track 1 submission description

> BNB Agent is a production-ready, fully autonomous three-sleeve BSC trading agent. It runs continuously, signs its own transactions, pays for its own data via x402, and is registered on-chain as its own AI identity.
>
> **Strategy** — three sleeves composed for the PnL-replay scoring axes:
> - 70% in delta-neutral funding carry (low drawdown, high Sharpe)
> - 20% in CMC-signal-driven DEX momentum (Kelly-sized alpha)
> - 10% in mean-reversion on top-20 BSC tokens (small, frequent wins)
>
> **Risk** — every trade is gated by a versioned, signed YAML User Policy. The user signs once (EIP-191). Per-trade risk cap 1%, daily circuit breaker 3%, max gross leverage 2x, max single position 15%, curated token allowlist.
>
> **Stack** — exactly the three required layers, deeply integrated:
> - **CoinMarketCap Agent Hub** (Data API + Data MCP + Skills + x402)
> - **Trust Wallet Agent SDK** (self-custody local signing, no per-tx taps)
> - **BNB AI Agent SDK** (BSC, PancakeSwap v3, BSC perps, ERC-8004, ERC-8183, x402)
>
> **On-chain evidence** — the agent has an ERC-8004 identity NFT, and each evaluation window opens 4 ERC-8183 jobs (one per sleeve + an aggregator) with the user as evaluator. Deliverables pinned to IPFS, completable on-chain.

### Special prize claims

- ✅ **Best Use of CoinMarketCap** — agent pays for all data via x402, full microcharge ledger on dashboard
- ✅ **Best Use of Trust Wallet Agent Kit** — every tx signed via TWAK, full signed-tx list with BscScan links
- ✅ **Best Use of BNB AI Agent SDK** — ERC-8004 identity NFT + ERC-8183 job escrow per evaluation window

---

## Pre-submission checklist

- [ ] `git tag v1.0.0` pushed to GitHub
- [ ] `README.md` updated with public dashboard URL + 8004scan + BscScan links
- [ ] `docs/demo-script.md` recorded, video uploaded (YouTube unlisted or Loom)
- [ ] `data/reports/replay.html` generated (the 7-day synthetic replay)
- [ ] ERC-8004 token registered (visible on 8004scan)
- [ ] At least 1 ERC-8183 job in `Completed` state (visible on BscScan)
- [ ] Public dashboard URL live (deploy via `bash scripts/start_dashboard.sh` on a VPS)
- [ ] All three sponsor evidence sections rendered on the dashboard
- [ ] Policy signature recovers to evaluator_address (`python -m policy.policy_verify` prints `VERIFIED`)

---

## What happens after submission

- **2026-06-22 → 2026-06-28** — live PnL-replay window. The agent trades live on BSC.
- **2026-06-29 → 2026-07-05** — judging. Panel reviews the live PnL, the dashboard, the on-chain evidence.
- **Week of 2026-07-06** — winners announced.

The agent runs continuously. The dashboard is public. The on-chain evidence is permanent. The backtest report is the rehearsal; the live window is the performance.
