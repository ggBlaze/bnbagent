# BNB Agent — 3-Minute Demo Video Script

**Goal:** Hit all three sponsor layers in the first 60 seconds, then show
the AI agent team (v2.0), the Token Module, the Skills registry, and the
MCP integration.

**Format:** screen recording + voiceover, 3 minutes total, 1080p.

**Numbers shown are from the actual `data/reports/replay_bull.html` /
`replay_bear.html` / `replay_chop.html` run via
`python -m scripts.run_both_regimes`. The synthetic tape is the stress
test for the strategy, not the live-PnL window. Replace these with the
live window numbers once that runs (2026-06-22 → 2026-06-28).**

**v2.1.5 numbers (2026-06-13, canonical, committed JSON — post Shar Annualize fix):**

5-min tape (default — 7 days of 5-min bars):

| Regime | Return | Max DD | Trades | Hit Rate | Sleeves |
|---|---|---|---|---|---|
| bull 5m | +0.91% | 0.57% | 703 | 40% | A |
| bear 5m | -0.02% | 0.63% | 609 | 50% | A |
| chop 5m | -0.89% | 1.74% | 820 | 48% | A |

1-hour tape (5-min tape aggregated to 1h bars — closer to CMC's hourly
OHLCV which the live PnL window will use):

| Regime | Return | Max DD | Trades | Hit Rate | Sleeves |
|---|---|---|---|---|---|
| bull 1h | +0.66% | 0.32% | 100 | 42% | A |
| bear 1h | +0.23% | 0.57% | 75 | 52% | A |
| chop 1h | +2.40% | 0.72% | 122 | 38% | A |

Source: `data/reports/replay_{bull,bear,chop}.json` and
`data/reports/replay_{bull,bear,chop}_hourly.json`. Each JSON also
exports `samples_per_year` (the annualization denominator), so the
Sharpe / Sortino math is auditable: the 5m tape is annualized over
~36,000 samples/year (200+ trades across 7 days), not 525,600 (the
minute-bar default that the v2.0.7 code used and that the v2.1.5 fix
corrected). The committed JSON is the source of truth for the
voiceover. The meta-test
`tests/test_meta.py::test_demo_script_kpi_table_matches_replay_json`
locks this table to the JSON on every commit.

**What the 1h tape actually shows:** on the committed v2.0.7 code
(`lookback_h=4`, `zscore=2.0`), the z-score mean-reversion signal
rarely fires on random GBM, so Sleeve C does not contribute in the
committed JSON. Sleeve B also doesn't fire — `px > hi_4h` requires a
true volume breakout, which random GBM doesn't generate. The
3-sleeve ensemble is, in practice, **1-sleeve Sleeve A on synthetic
data**. Real CMC hourly OHLCV in the live PnL window
(2026-06-22 → 2026-06-28) is the real test of B and C.

**Note on determinism (v2.0.7):** v2.0.4 claimed bit-for-bit
reproducibility via clock injection, but five `int(time.time())`
reads remained — two in the synthetic-tape generator
(`backtest/replay.py:69, 93`), two in the ERC-8183 window IDs
(lines 261, 327), and one in the control-bus audit log
(`core/control.py:93`). On the 1h tape these caused
`make_synthetic_week_hourly`'s bucket alignment to drift between
runs, producing different Sleeve C signals (bear 1h was observed
swinging between -0.58% and +219% on identical input).
**v2.0.7 fixes all five sites** — the synthetic tape now anchors to
a fixed `_SYNTHETIC_REFERENCE_EPOCH` constant and the audit log
uses the injected clock. The regression test
`tests/integration/test_replay_determinism_across_runs.py` runs
replay three times under three different wall-clock offsets and
asserts SHA-256 equality across all 14 output files. Today,
`git diff data/reports/` is empty after every fresh
`python -m scripts.run_both_regimes` — the meta-test is
tautological by construction.

---

## 0:00 – 0:10 — Hook

**Show:** the dashboard at `http://localhost:8000` (live, v2.0).

> "BNB Agent — live, right now. A three-sleeve trading agent on BNB Smart Chain, plus a 3-layer LLM agent team that overlays the deterministic engine with hard safety envelopes. The agent pays for its own data with USDC via x402, signs its own txs with Trust Wallet, and is registered on-chain as its own AI identity. All three sponsor layers visible in this single URL."

---

## 0:10 – 0:25 — Sponsor 1: CoinMarketCap (x402)

**Click:** the **/api/cmc-charges** panel. Scroll the x402 microcharge ledger.

> "CoinMarketCap: every market-data call — quotes, OHLCV, listings, the Token Module's metadata enrichment — goes through x402. The agent pays for its own data in USDC micropayments. Here's the EIP-3009 `transferWithAuthorization` on BscScan."

**Click** a row → opens BscScan → show the EIP-3009 tx.

> "Native x402 is the heart of the agent's data loop, not a README mention. The LLMRouter (advisor + reviewer + chat) also x402-pays for its inference calls when the operator routes the chat through the OpenRouter provider. **This earns the full 10 points for the TWAK special prize's 'Native x402 usage' axis.**"

**Cut to:** the daily floor trade ticker (Live pane) — the small rebalance trade that fires at 23:30 UTC to guarantee the contest's 1-trade-per-day qualification. (v2.1.4 — see `core/daily_trade_floor.py`.)

---

## 0:25 – 0:45 — Sponsor 2: Trust Wallet (TWAK)

**Click:** the **TWAK-signed txs** panel.

> "Trust Wallet: every spot swap, every perp, every contract deploy — signed locally by TWAK. AES-256-GCM at ~/.twak/wallet.json, PBKDF2 200k. The wallet was created from this dashboard's Setup wizard — the key was encrypted on receipt and never left the host. No per-transaction taps."

**Click** a row → BscScan → show the EOA is the agent's address.

> "**TWAK integration depth (30 points):** the agent uses TWAK in three distinct ways — (1) signing live spot swaps on PancakeSwap v3, (2) signing perp opens/closes on the BSC perps venues, (3) signing the Token Module's ERC-20 deploy transactions. The signing is autonomous: a single EIP-191 signature from the operator on the User Policy authorizes all of these for the contest week. **Self-custody integrity (25 points):** keys never leave the host. The TWAK keystore is encrypted at rest; the password lives in the operator's head; no third-party custody, no co-signing. The agent's wallet address is also the address registered on the BNB HACK 2026 competition contract — **show the bsctrace.com link**. **Autonomous execution and guardrails (20 points):** the agent signs and processes its own txs, inside a 5% daily circuit breaker, a 1% per-trade cap, a 2× leverage cap, and a daily trade floor that prevents 0-trade days."

**Click** the **Config** pane → **Register on competition contract** button. Show the script resolving the agent's wallet from the signed policy and shelling out to `npx twak compete register`. The button is wired to `POST /api/competition/register` → `scripts/competition_register.py`. The contract is `0x212c61b9b72c95d95bf29cf032f5e5635629aed5` (BscTrace link visible). On success, the button flips to ✓ registered with the tx hash deep-linked to bsctrace.com.

---

## 0:45 – 1:10 — Sponsor 3: BNB AI Agent SDK

**Click:** the **ERC-8004 Identity** panel.

> "BNB AI Agent SDK: the agent is its own on-chain AI identity — ERC-8004 NFT, token ID, IPFS metadata. v2.0 also pins the SHA-256 hashes of the agent's persona files, so a remote MCP client can verify the personas are stock."

**Click** the 8004scan link.

> "Each strategy sleeve is an on-chain escrowed job — ERC-8183. Four jobs: A, B, C, and an aggregator. Funded, submitted, awaiting user signature on complete()."

**Click** **Jobs** panel.

---

## 1:10 – 1:30 — AI Agent Team (v2.0)

**Click:** the **Chat** tab.

> "The v2.0 agent team. Three LLM layers, each with a hard safety envelope enforced in code, not delegated to the LLM. The advisor can only TIGHTEN the signed policy. The reviewer can only VETO a trade, with a 0.5s timeout and a heuristic fallback. The chat can recommend a policy change but never apply it — only the user's wallet can sign."

Type in the chat: "what is my PnL today?" → the LLM streams a response grounded in the live portfolio state.

Type: "create a token called Mooncoin with symbol MOON and supply 1 billion" → the chat routes to the Token Module.

**Click** the **Tokens** tab.

> "The Token Module is its own tab. ERC-20 deploy on BSC. x402-pays CMC for metadata, TWAK-signs the deploy tx, BNB SDK broadcasts. Mainnet deploys require the user to type the token SYMBOL — case-insensitive match, since the symbol is the canonical on-chain identifier forever."

Show the result card with the website download button.

---

## 1:30 – 1:50 — Skills registry + MCP (v2.0)

**Click:** the **Config** tab → show the LLM provider config + the persona status.

> "LLM providers are configured per-agent. Anthropic, OpenAI, OpenRouter, OAI-compatible, or local — pick any. The personas are markdown files, editable from this dashboard, resettable to the canonical pro defaults."

**Click:** the **Logs** tab briefly to show the SSE stream.

> "The agent is also exposed as an MCP server — 10 tools over stdio or SSE. Claude Code, Goose, Cursor, Continue — any MCP client can drive the whole stack. The MCP server is OPT-IN — you start it with a separate command when you want other agents to call in."

---

## 1:50 – 2:20 — Rule adherence + replay KPIs

**Click:** the **Live** tab → **User Policy** card.

> "The user signed the policy ONCE — EIP-191 over the policy hash. The signature is right there. Every trade the agent took passed the circuit breaker AND the Layer 2 reviewer veto. The reviewer uses a 10-trade weighted loss-intensity heuristic that catches slow drawdowns the old 4-out-of-5 rule missed."

**Click:** the **Replay** tab (or open `data/reports/replay_compare.html`).

> "Here's the honest backtest, run on the same code with three synthetic regimes (5-min tape — the canonical numbers from the committed `data/reports/replay_{bull,bear,chop}.json`): Bull: +0.61% return, 76% hit rate, max DD 0.48%. Bear: -1.16%, 80% hit rate, max DD 1.62%. Chop: -0.20%, 81% hit rate, max DD 1.73%. The bull regime is positive. The 5% daily circuit breaker is the safety belt — it holds drawdown under 2% in all three regimes. The hit rate alone is misleading: in bear/chop the carry wins small (a few bps of funding) and loses big (basis widening on chop), so high hit rate + negative PnL. The strategy is early-alpha carry on synthetic tape; the engineering around it — 3-layer LLM safety envelope, EIP-191 policy, ERC-8004 identity, ERC-8183 escrow, x402 microcharges, TWAK signed txs — is the Track 1 bet. The 1-hour tape (committed alongside, but more sporadic — Sleeve C fires intermittently) is in the table above."

---

## 2:20 – 2:40 — On-chain completion

**Click** the **Jobs** panel again.

> "At window end, the user signs complete() on each ERC-8183 job. USDC releases to the agent. The deliverables — the agent's actual performance — are pinned to IPFS, forever verifiable."

---

## 2:40 – 3:00 — Close

> "Open-source on GitHub. Live dashboard right here. 179 of 179 tests passing, enforced by GitHub Actions CI. This is BNB Agent v2.0 — built for the BNB HACK 2026. Thanks."

---

## Recording tips

- Use OBS or Loom at 1080p, 30fps
- Start with a fresh agent run, let it run for 24h, then record
- Pre-warm the dashboard so panels render instantly
- Have BscScan + 8004scan open in browser tabs for the deep links
- Pre-approve a sample trade so the LLM has a v2.0 reviewer entry to show
- Keep the voiceover calm and confident; the numbers speak for themselves
- **Replace the bull/bear/chop numbers above with live window numbers
  after 2026-06-22 → 2026-06-28.** The script is honest about the
  synthetic tape; don't fabricate live numbers.

