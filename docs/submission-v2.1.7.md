# BNB Agent — Hackathon Submission

> **Last updated:** 2026-06-17 (pre-submission prep) · **Version:** v2.1.7

**Hackathon:** BNB HACK 2026 (CoinMarketCap × Trust Wallet × BNB Chain) — **$36K prize pool**
**DoraHacks:** https://dorahacks.io/hackathon/bnbhack-twt-cmc/
**Track:** 1 — Autonomous Trading Agents
**Submission lock:** 2026-06-21 12:00 UTC (4 days from now)
**Live PnL-replay window:** 2026-06-22 → 2026-06-28
**Winners announced:** week of 2026-07-06

---

## Submission form fields (pre-filled, ready to paste)

### Project name
**BNB Agent**

### Tagline
> An autonomous BSC trading agent with a 3-layer LLM safety team, a Token Module, a Skills registry, and an MCP server — registered on-chain as its own AI identity, gated by one user-signed policy, and signed end-to-end with Trust Wallet.

### Category / Track
**Track 1 — Autonomous Trading Agents**

### Team
**Blaze** (solo) · github.com/ggBlaze · contact via DoraHacks DM

### GitHub repo
`https://github.com/ggBlaze/bnbagent` *(confirm public status before submit; see checklist)*

### Live dashboard
`https://<your-coolify-domain>` *(deploy before submit; placeholder)*

### Demo video
YouTube unlisted or Loom, 3 minutes, 1080p. Script at [`demo-script.md`](demo-script.md); KPI table in that file is locked to `data/reports/replay_*.json` by `tests/test_meta.py`.

### Eligibility list (already pinned)
`data/eligible_tokens.json` — 149 BEP-20 tokens, sourced from the rules page (2026-06-12 14:48 UTC). Filtered at 3 layers: sleeves (`core/sleeves/*` → `eligibility.py`), risk engine (`core/risk_engine.py`), and a belt-and-suspenders pass in the daily trade floor.

---

## Track 1 submission description (~250 words, ready to paste)

> BNB Agent v2.1.7 is a production-ready, fully autonomous BSC trading agent built on a 3-layer LLM safety team. The deterministic trading engine runs continuously, signs its own transactions, pays for its own data via x402, and registers itself on-chain as its own AI identity (ERC-8004).
>
> **Strategy** — three sleeves composed for the PnL-replay scoring axes:
> - 70% in delta-neutral funding carry (low DD, high Sharpe) — Sleeve A
> - 20% in CMC-signal-driven DEX momentum (Kelly-sized alpha) — Sleeve B
> - 10% in mean-reversion on top-20 BSC tokens (small, frequent wins) — Sleeve C
>
> **Risk** — every trade is gated by a versioned, signed YAML User Policy. The user signs once (EIP-191). Per-trade risk cap 1%, daily circuit breaker 3%, max gross leverage 2x, max single position 15%, curated token allowlist, post-loss cool-off. v2.0 added a Layer-2 LLM reviewer veto (with timeout + heuristic fallback) between the circuit breaker and the sign step.
>
> **AI agent team (v2.0+)** — three LLM layers with hard safety envelopes enforced in code:
> - Layer 1 (advisor, 5-min loop): can only TIGHTEN the policy
> - Layer 2 (reviewer, per-trade): can only VETO a trade
> - Layer 3 (chat, on user input): can only RECOMMEND a policy change (user must re-sign)
> Provider-agnostic: Anthropic, OpenAI, OpenRouter, OAI-compatible, local, **and MiniMax M3** (the agent's primary reviewer; reasoning-tier model with auto-default timeout by family).
>
> **Token Module (v2.0+)** — its own dashboard tab. ERC-20 deploy on BSC. x402-pays CMC for metadata, TWAK-signs the deploy tx, BNB SDK broadcasts. Optional single-file HTML landing page. **v2.1.6: contest-locked** (deploy button returns 423 before 2026-07-07 UTC + requires `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` env opt-in even after unlock).
>
> **Skills registry (v2.0+)** — 6 built-in Skills (3 notification, 3 data). Hot-toggled from the dashboard or the chat.
>
> **MCP server (v2.0+)** — 10 tools over stdio (Claude Code / Goose) or SSE. Other agents can call `bnbagent_get_pnl`, `bnbagent_deploy_token`, `bnbagent_chat`, `bnbagent_list_skills`, and `competition_register`.
>
> **Stack** — exactly the three required layers, deeply integrated:
> - **CoinMarketCap Agent Hub** (Data API + Data MCP + Skills + x402 microcharges)
> - **Trust Wallet Agent Kit (TWAK)** (self-custody local signing, no per-tx taps)
> - **BNB AI Agent SDK** (BSC, PancakeSwap v3, BSC perps, ERC-8004, ERC-8183, x402)
>
> **On-chain evidence** — the agent has an ERC-8004 identity NFT (with persona SHA-256 hashes in the metadata, so remote MCP clients can verify the personas are stock), and each evaluation window opens 4 ERC-8183 jobs (one per sleeve + an aggregator) with the user as evaluator. Deliverables pinned to IPFS, completable on-chain.
>
> **Reproducibility** — `bash bnbagent --replay` produces `data/reports/replay.html` from 3 regimes × 2 tape intervals, all 6 JSONs committed and pinned by a meta-test. The numbers in `docs/demo-script.md` are the source of truth for the demo video voiceover.

---

## Special prize claims

- ✅ **Best Use of CoinMarketCap** — agent pays for all data via x402, full microcharge ledger on dashboard; Token Module enriches deploy metadata via CMC; Skills pull from CMC (x_sentiment fallback, cmc_global_filter); persona-metadata hash is auditable. **v2.1.7: HybridDataSource** — x402 quotes for live mode, Binance OHLCV for the historical path, so the data layer never goes offline.
- ✅ **Best Use of Trust Wallet Agent Kit (TWAK)** — every tx signed via TWAK, full signed-tx list with BscScan links; wallet is created/imported from the dashboard's Setup wizard with AES-256-GCM at `~/.twak/wallet.json`; the chat can deploy tokens through TWAK without ever exposing the key. **`npx twak compete register`** is the path to on-chain registration; the script `scripts/competition_register.py` wraps it with checks + receipt verification.
- ✅ **Best Use of BNB AI Agent SDK** — ERC-8004 identity NFT (with persona hashes) + ERC-8183 job escrow per evaluation window + Token Module ERC-20 deploys + PancakeSwap v3 swaps + BSC perps + x402 finality.

---

## Pre-submission checklist

### Code & repo
- [ ] `git tag v2.1.7` pushed to GitHub
- [ ] Repo is **public** (or you've added the DoraHacks judges as collaborators)
- [ ] `README.md` has live dashboard URL + 8004scan + BscScan links filled in

### On-chain
- [ ] **ERC-8004 token registered on mainnet** (visible on 8004scan) — agent identity NFT
- [ ] **`competition_register` tx sent to `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`** — the participant list
- [ ] At least 1 ERC-8183 job in `Completed` state on mainnet (visible on BscScan) — proof of live PnL job execution
- [ ] Policy signature recovers to evaluator_address (`python -m policy.policy_verify` prints `VERIFIED`)

### Demo
- [ ] `docs/demo-script.md` recorded, video uploaded (YouTube unlisted or Loom)
- [ ] `data/reports/replay.html` generated and committed
- [ ] Public dashboard URL live (deploy via `bash bnbagent` on a VPS, behind a reverse proxy with TLS, `BNBAGENT_AUTH_MODE=password` for the public deploy)
- [ ] All three sponsor evidence sections rendered on the dashboard

### Token Module
- [ ] (Optional) Token Module deployed at least one test token on testnet — saves the contract address + IPFS CID to the dashboard. **v2.1.6: contest-locked** — the deploy button returns 423 before 2026-07-07 UTC. Submit your testnet deploy either via the local-dev build (no auth, no env gates) or wait until 2026-07-07 + set `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` in Coolify.

---

## What happens after submission

- **2026-06-22 → 2026-06-28** — live PnL-replay window. The agent trades live on BSC.
- **2026-06-29 → 2026-07-05** — judging. Panel reviews the live PnL, the dashboard, the on-chain evidence, and (for v2.0+) the AI agent team + Token Module + MCP integration.
- **Week of 2026-07-06** — winners announced.

The agent runs continuously. The dashboard is public. The on-chain evidence is permanent. The backtest report is the rehearsal; the live window is the performance.
