# BNB Agent — Sale Pitch

> **An autonomous AI trading agent that lives on BNB Smart Chain, pays for its own data, signs its own transactions, and is bound by a single signature from you.**
>
> Built for the BNB HACK 2026 (CoinMarketCap × Trust Wallet × BNB Chain — $36K prize pool). The whole stack — agent, dashboard, installer, replay harness, on-chain identity — is one `git clone` and **two commands** away from running.
>
> Three strategies, one risk engine, zero per-transaction taps. The agent is delta-neutral by construction (70% in funding carry, 20% in momentum, 10% in mean-reversion) and every order is gated by a versioned, EIP-191-signed **User Policy** that you sign **once**. A 3% daily-loss circuit breaker, a 1% per-trade risk cap, and a 2× leverage cap are baked in — and judges can verify the on-disk policy still recovers to your address.
>
> The agent **pays CMC $0.01 per data call** in USDC via x402 — every microcharge is on the dashboard with a BscScan link. The agent **signs every BSC transaction with TWAK** (AES-256-GCM keystore, keys never leave the host). The agent **registers its own identity NFT on BNB Chain via ERC-8004** and **escrows its own PnL deliverables via ERC-8183 jobs** that you, the user, evaluate.
>
> No custodial risk. No black box. **You sign once, the agent runs for a week, you can kill it with one button.**

---

# Features Pitch

## 1. Three-strategy ensemble (designed for the scoring axes)

| Sleeve | Capital | Strategy | Target PnL | Drawdown |
|---|---|---|---|---|
| **A — Funding carry** | 70% | Long spot on PancakeSwap v3 + short perp on a BSC venue. Delta-neutral. | +0.5% APR baseline, near-zero directional risk | very low |
| **B — DEX momentum** | 20% | CMC signals (volume spike + 4h breakout) → 1–4h long with ATR stop and 3% TP | positive alpha, capped at 1% per trade | low |
| **C — Mean reversion** | 10% | Fades 1h drops >2.5σ on top-20 BSC tokens | positive alpha, capped at 0.5% per trade | low |

**Why this wins PnL replay:** 70% of capital is hedged — so the agent is *expected* to have low drawdown and high Sharpe, which is exactly the axes Track 1 judges reward. The 30% alpha sleeve adds upside without busting the risk caps.

## 2. Hard-coded risk engine (the only UX prompt the user sees)

- **Daily loss circuit breaker**: 3%
- **Per-trade risk cap**: 1%
- **Max gross leverage**: 2×
- **Max single position**: 15%
- **Curated token allowlist** (top-50 CMC + vetted BSC DEX list)
- **Per-symbol post-loss cool-off** (4h for momentum, 6h for mean-rev) to prevent revenge trades
- **Kill switch** in the right rail — halts all new orders, leaves open positions to TP/stop themselves
- **Signed User Policy** — `policy.yaml` is the *only* file the user signs. Every order is checked against it via `circuit_breaker_check()` before going on-chain. Signed with EIP-191. Verifiable.

## 3. All three sponsor layers, deeply integrated (stackable specials)

| Sponsor | What BNB Agent uses it for | Visible evidence |
|---|---|---|
| **CoinMarketCap** Agent Hub | Data API + Data MCP + Skills + **x402** ($0.01 USDC/request via EIP-3009 `transferWithAuthorization`) | Live **x402 microcharge ledger** on the dashboard, every cost line, BscScan-deep-linked |
| **Trust Wallet** Agent Kit (TWAK) | Self-custody local signing, AES-256-GCM keystore, PBKDF2 200k iters, at `~/.twak/wallet.json` | Live **TWAK-signed tx list** with BscScan deep links; "keys never left the host" |
| **BNB AI Agent SDK** | BSC mainnet, PancakeSwap v3, BSC perps, **ERC-8004 identity NFT**, **ERC-8183 job escrow** | Live **identity panel** (tokenId + IPFS metadata + 8004scan link) and **jobs lifecycle** (Open → Funded → Submitted → Completed) |

## 4. Premium operator dashboard (single HTML, zero JS frameworks)

- **4 panes**: Live, Config, Logs (SSE stream), Replay
- **Mission-control aesthetic**: acid-lime accent on near-black, Inter + JetBrains Mono, hand-drawn SVG equity curve with gradient fill, animated live-status pulse
- **Hero strip**: Equity, Day PnL, Drawdown, Open Positions, live Sharpe — all updating 1.5s
- **Sleeve cards**: A / B / C with live capital, target alloc, and tick cadence
- **Ledgers**: CMC microcharges + TWAK signed txs + ERC-8004 identity + ERC-8183 jobs + recent trades
- **Right rail**: kill switch, sleeve toggles, control log
- **Config pane**: live editor for risk overrides (daily cap, per-trade, leverage, position size)
- **Logs pane**: SSE stream of `logs/agent.log`, capped at 500 lines, color-coded

## 5. Setup wizard — go from zero to running in the browser

4 steps, all in the dashboard, all in under two minutes:
1. **Network** — pick testnet / mainnet / replay; set RPC URLs; CMC key (optional)
2. **Wallet** — generate a new wallet (or import existing private key) + encryption password
3. **Sign policy** — unlock the wallet, sign `policy.yaml` with EIP-191
4. **Done** — summary, jump to Live

**Security:** the private key is encrypted to disk on receipt; the browser only ever sees the address. The keystore format is TWAK-compatible, so the CLI fallback path (`npx twak sign message`) works on the same file.

## 6. One-command install + one-command run

```bash
bash install.sh      # creates venv, installs deps, signs a dev policy
bash bnbagent        # starts agent + dashboard, Ctrl+C stops both
bash bnbagent --replay    # 7-day synthetic replay, HTML report
bash bnbagent --repl      # Python REPL with components pre-loaded
```

**No `cd`-and-tail logs.** **No 10-step README.** **One terminal, one command.**

## 7. Production hardening (audited, documented)

A 10-item trading-logic audit pass before the live window, all fixes applied:

- Sleeve A funding accrual fixed (was every 30s, now on 8h boundaries — wasn't over-crediting 960×)
- Sleeve A basis exit closes both legs atomically
- Sleeves B & C: post-loss cool-off to prevent revenge trades
- Portfolio mark-to-market for carry now includes spot PnL
- Daily loss breaker active from tick 1 (was skipped on day 0)
- Kill switch in `circuit_breaker_check` (dashboard-driven)
- Shared `core/utils.py::token_address` (was duplicated 3×)
- Replay lambda default-arg binding fix
- Risk check guards on `proposed.is_new`
- Per-leg exposure cap consolidation

Full audit in `docs/audit-2026-06-05.md`.

## 8. Replay harness + metrics

- 7-day synthetic tape generator
- Drives all 3 sleeves through the same code paths as production
- Emits a full HTML report: Sharpe, Sortino, Calmar, max DD, hit rate, sleeve attribution
- **No policy breaches** is the submission gate
- Reproducible from `data/synthetic_week.json`

## 9. The story the demo tells (3 minutes)

| Time | Pane hit | Sponsor |
|---|---|---|
| 0:00 | "Open dashboard, here's the live agent" | (hook) |
| 0:15 | CMC x402 ledger: 47 calls, $0.47 spent, click → BscScan → EIP-3009 USDC transfer | **CMC** |
| 0:30 | TWAK signed txs: 30 swaps, click → BscScan, "keys never left the host" | **Trust Wallet** |
| 0:50 | ERC-8004 NFT (tokenId + IPFS + 8004scan) | **BNB SDK** |
| 1:10 | Signed policy, IPFS, recovery to evaluator | (rule-adherence) |
| 1:30 | Sleeve breakdown | (PnL) |
| 2:00 | 4 ERC-8183 jobs (A/B/C/ALL), funded → submitted → complete | (on-chain) |
| 2:30 | Backtest vs last week, Sharpe X, max DD Y% | (close) |

## 10. Numbers at submission

- **149+ / 149** unit + integration tests pass
- **15,000+** lines of typed Python
- **1** install command, **1** run command
- **3** sponsor layers visibly used
- **4** ERC-8183 jobs per evaluation window (A / B / C / aggregator)
- **0** per-transaction taps (sign once at startup)
- **0** keys leave the host
- **0** policy breaches in the replay harness
- **3** LLM layers (advisor, reviewer, chat) — provider-agnostic, per-agent configurable
- **6** Skills out of the box (telegram, farcaster, webhook, x-sentiment, cmc-global-filter, glassnode)
- **1** Token Module with optional single-file website generation
- **10** MCP tools exposed — other agents can drive the whole stack
- **4** persona .md files (pro defaults + user-editable live copies)
- **5** LLM provider adapters (Anthropic, OpenAI, OpenRouter, OAI-compat, local)
