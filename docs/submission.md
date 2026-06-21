# BNB Agent — Hackathon Submission

**Hackathon:** BNB HACK (CoinMarketCap × Trust Wallet × BNB Chain)
**DoraHacks:** https://dorahacks.io/hackathon/bnbhack-twt-cmc/
**Track:** 1 (Autonomous Trading Agents)
**Version:** v2.0.0
**Submission lock:** 2026-06-21 12:00 UTC
**Live PnL-replay window:** 2026-06-22 → 2026-06-28
**Winners announced:** week of 2026-07-06

---

## Submission form fields (pre-filled)

### Project name
**BNB Agent**

### Tagline
> Autonomous three-sleeve BSC trading agent + 3-layer LLM agent team + token deploy module + skills registry + MCP server. Uses CoinMarketCap Agent Hub (x402), Trust Wallet Agent SDK, and the BNB AI Agent SDK.

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

> BNB Agent v2.0 is a production-ready, fully autonomous BSC trading agent with a 3-layer LLM agent team. The deterministic trading engine runs continuously, signs its own transactions, pays for its own data via x402, and is registered on-chain as its own AI identity.
>
> **Strategy** — three sleeves composed for the PnL-replay scoring axes:
> - 70% in delta-neutral funding carry (low drawdown, high Sharpe)
> - 20% in CMC-signal-driven DEX momentum (Kelly-sized alpha)
> - 10% in mean-reversion on top-20 BSC tokens (small, frequent wins)
>
> **Risk** — every trade is gated by a versioned, signed YAML User Policy. The user signs once (EIP-191). Per-trade risk cap 1%, daily circuit breaker 3%, max gross leverage 2x, max single position 15%, curated token allowlist, post-loss cool-off. v2.0 adds a Layer 2 LLM reviewer veto (0.5s timeout, heuristic fallback) between the circuit breaker and the sign step.
>
> **AI agent team (v2.0)** — three LLM layers with hard safety envelopes enforced in code:
> - Layer 1 (advisor, 5-min loop): can only TIGHTEN the policy
> - Layer 2 (reviewer, per-trade): can only VETO a trade
> - Layer 3 (chat, on user input): can only RECOMMEND a policy change (user must re-sign)
> Provider-agnostic: Anthropic, OpenAI, OpenRouter, OAI-compatible, local.
>
> **Token Module (v2.0)** — its own dashboard tab. ERC-20 deploy on BSC. x402-pays CMC for metadata, TWAK-signs the deploy tx, BNB SDK broadcasts. Optional single-file HTML landing page. Mainnet requires explicit confirmation + user-typed token name.
>
> **Skills registry (v2.0)** — 6 built-in Skills (3 notification, 3 data). Hot-toggled from the dashboard or the chat.
>
> **MCP server (v2.0)** — 11 tools over stdio (Claude Code / Goose) or SSE. Other agents can call bnbagent_get_pnl, bnbagent_deploy_token, bnbagent_chat, bnbagent_list_skills, etc.
>
> **Stack** — exactly the three required layers, deeply integrated:
> - **CoinMarketCap Agent Hub** (Data API + Data MCP + Skills + x402)
> - **Trust Wallet Agent SDK** (self-custody local signing, no per-tx taps)
> - **BNB AI Agent SDK** (BSC, PancakeSwap v3, BSC perps, ERC-8004, ERC-8183, x402)
>
> **On-chain evidence** — the agent has an ERC-8004 identity NFT (with persona SHA-256 hashes in the metadata, so remote MCP clients can verify the personas are stock), and each evaluation window opens 4 ERC-8183 jobs (one per sleeve + an aggregator) with the user as evaluator. Deliverables pinned to IPFS, completable on-chain.

### Special prize claims

- ✅ **Best Use of CoinMarketCap** — agent pays for all data via x402, full microcharge ledger on dashboard; Token Module enriches deploy metadata via CMC; Skills pull from CMC (x_sentiment fallback, cmc_global_filter); persona-metadata hash is auditable
- ✅ **Best Use of Trust Wallet Agent Kit** — every tx signed via TWAK, full signed-tx list with BscScan links; wallet is created/imported from the dashboard's Setup wizard with AES-256-GCM at `~/.twak/wallet.json`; the chat can deploy tokens through TWAK without ever exposing the key
- ✅ **Best Use of BNB AI Agent SDK** — ERC-8004 identity NFT (with persona hashes) + ERC-8183 job escrow per evaluation window + Token Module ERC-20 deploys + PancakeSwap v3 swaps + BSC perps + x402 finality

---

## Pre-submission checklist

- [ ] `git tag v2.0.0` pushed to GitHub
- [ ] `README.md` updated with public dashboard URL + 8004scan + BscScan links
- [ ] `docs/demo-script.md` recorded, video uploaded (YouTube unlisted or Loom)
- [ ] `data/reports/replay.html` generated (the 7-day synthetic replay)
- [ ] ERC-8004 token registered on **mainnet** (visible on 8004scan)
- [ ] At least 1 ERC-8183 job in `Completed` state on **mainnet** (visible on BscScan)
- [ ] Public dashboard URL live (deploy via `bash bnbagent` on a VPS, behind a reverse proxy with TLS)
- [ ] All three sponsor evidence sections rendered on the dashboard
- [ ] Policy signature recovers to evaluator_address (`python -m policy.policy_verify` prints `VERIFIED`)
- [ ] (v2.0) At least one LLM provider key set (e.g. `OPENROUTER_API_KEY`) so the AI agent team is live
- [ ] (v2.0) Token Module deployed at least one test token on testnet — saves the contract address + IPFS CID to the dashboard. **v2.1.6: contest-locked** — the deploy button returns 423 before 2026-07-07 UTC. Submit your testnet deploy either via the local-dev build (no auth, no env gates) or wait until 2026-07-07 + set `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` in Coolify.

---

## What happens after submission

- **2026-06-22 → 2026-06-28** — live PnL-replay window. The agent trades live on BSC.
- **2026-06-29 → 2026-07-05** — judging. Panel reviews the live PnL, the dashboard, the on-chain evidence, and (for v2.0) the AI agent team + Token Module + MCP integration.
- **Week of 2026-07-06** — winners announced.

The agent runs continuously. The dashboard is public. The on-chain evidence is permanent. The backtest report is the rehearsal; the live window is the performance.
