# BNB Agent — Twitter Thread Draft

> Launch thread for the BNB HACK 2026 submission. Target audience: BSC CT, agent builders, judges. Post as one thread, image in the first tweet, video in the last.
> Length: 10 tweets, ~240 chars each, lots of line breaks for scannability.
> Hashtags: #BNBHack2026 #BNBChain #CoinMarketCap #TrustWallet #BuildOnBNB

---

## Tweet 1/10 (hook + image)

just shipped BNB Agent for #BNBHack2026 🏴

an autonomous BSC trading agent where:
→ you sign ONE policy
→ the agent signs every tx
→ a 3-layer LLM safety team gates every trade
→ an ERC-8004 identity NFT + 8183 job escrow record every move

live PnL-replay window: jun 22 → jun 28 🧵👇

[image: dashboard screenshot, top half of thread image]

---

## Tweet 2/10 (the autonomy story)

the whole point: most "AI agents" are prompts with a wallet attached.

BNB Agent is a runtime.

deterministic Python code on BSC.
user signs a YAML policy once.
the agent runs for a week, kills itself on a 3% daily DD, and ships an HTML backtest report.

you don't babysit it.

---

## Tweet 3/10 (the strategy)

3 sleeves, composed for the PnL-replay scoring axes:

• 70% delta-neutral funding carry (sleeve A) — low DD, high Sharpe
• 20% CMC-driven DEX momentum (sleeve B) — Kelly-sized, ATR-stops
• 10% mean-reversion on top-20 BSC tokens (sleeve C) — small, frequent

149-token allowlist pinned from the contest page.
no off-list trades. ever.

---

## Tweet 4/10 (the safety team)

the 3-layer LLM team is a SAFETY architecture, not a novelty.

→ Layer 1 (advisor, every 5min): can only TIGHTEN the policy. never loosen.
→ Layer 2 (reviewer, per-trade): can only VETO. with a hard timeout + heuristic fallback if the model is slow.
→ Layer 3 (chat, on user input): can only RECOMMEND. every recommendation needs a fresh user signature.

the LLM has the keys to a suggestion box. not the keys to the wallet.

---

## Tweet 5/10 (the wallet story)

every tx is signed by Trust Wallet's Agent Kit (TWAK).

→ wallet created/imported in the setup wizard
→ encrypted with AES-256-GCM at ~/.twak/wallet.json
→ the chat can deploy tokens through TWAK without ever exposing the key
→ every signed tx has a BscScan link on the dashboard

self-custody. no per-tx taps. no browser extension popups.

---

## Tweet 6/10 (the data story)

the agent pays for its own market data.

→ every CMC quote is an x402 microcharge in USDC
→ full microcharge ledger on the dashboard
→ hybrid data source (v2.1.7): x402 for live, Binance OHLCV for the historical path
→ it never goes offline, even when x402 is rate-limiting

it spends real money on data, so the data is real.

---

## Tweet 7/10 (the on-chain story)

this is the part judges will check first.

→ ERC-8004 identity NFT on mainnet
  (persona SHA-256 hashes in metadata, so the personas are auditable as stock)
→ ERC-8183 job escrow per evaluation window
  (1 job per sleeve + 1 aggregator = 4 jobs open at any time)
→ deliverables pinned to IPFS
→ user is the evaluator. you sign once, the jobs close themselves.

no "trust me bro" — verify on BscScan.

---

## Tweet 8/10 (the Token Module)

yes, it can also LAUNCH tokens.

→ ERC-20 deploy on BSC from the dashboard
→ CMC-enriched metadata (paid for via x402)
→ TWAK-signs the deploy tx
→ generates a single-file HTML landing page
→ contest-locked until jul 7 UTC (no rogue deploys during judging)

so BNB Agent is also a launchpad. with a built-in guard.

---

## Tweet 9/10 (the MCP story)

other agents can call BNB Agent.

→ MCP server: 10 tools, stdio + SSE
→ `bnbagent_get_pnl` — read-only PnL snapshot
→ `bnbagent_deploy_token` — full Token Module flow
→ `bnbagent_chat` — Layer 3 chat
→ `bnbagent_list_skills` — hot-toggle the Skills registry
→ `competition_register` — on-chain register for BNB HACK

plug it into Claude Code or Goose in 5 minutes. your agent can now trade BSC with the same policy gate.

---

## Tweet 10/10 (CTA + video)

live PnL-replay window: jun 22 → jun 28 (BSC mainnet, 7 days).

3-min demo video in the replies 👇
code (MIT): github.com/ggBlaze/bnbagent
track 1: dorahacks.io/hackathon/bnbhack-twt-cmc/

if you're a judge, hit me up on DoraHacks — i'll spin up a sandbox for you.
if you're a builder, fork it. it's designed to be forked.

gm #BuildOnBNB ✨

---

## Posting notes

- Image for tweet 1: dashboard screenshot, ideally with the 3 sleeves and the live PnL chart visible. Crop to 16:9 for X, 3:4 for IG crosspost.
- Video in tweet 10: 3-min YouTube unlisted link (matches the contest demo video).
- Best time to post: Tue/Wed evening (US) or Wed morning (EU). Avoid Friday — judges are reading the queue.
- Reply to tweet 1 with the GitHub link separately (more discoverable).
- Pin a follow-up tweet 24h later with the live PnL link once the window opens (2026-06-22 12:00 UTC).
- Quote-tweet a notable judge or sponsor with a short demo clip on day 2.
