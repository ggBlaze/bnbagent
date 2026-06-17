# BNB Agent — Changelog

All notable changes to this project. Versioned per the git tag.

## v2.1.7 — Readonly mode + HybridDataSource + polish (2026-06-14)

The contest-submission story: a public URL where judges can see the
agent live, interact with the chat, but cannot mutate any state.
Plus the x402 sponsor-track is now actually tradeable (HybridDataSource
fills in OHLCV from Binance so the sleeves have signals), and a round
of judge-first UI polish (capabilities strip, version display, BNB
favicon, Made by Blaze footer, green sponsor dots, hover tooltips on
locked controls).

ADDED:  `BNBAGENT_AUTH_MODE` 3-mode auth — `disabled` (local dev,
        no auth, all mutations allowed), `password` (judge + admin
        cookie gate, 2-mode behavior from v2.1.5 preserved), `readonly`
        (no password, every mutation returns 403, the contest mode).
        The legacy `BNBAGENT_AUTH_ENABLED` flag is still respected
        as a fallback; the new var wins if both are set. `/api/auth/status`
        now returns `{enabled, mode, role}` (was `{enabled, role}`).
        Login endpoint refuses with 403 in non-password modes.
        Startup log self-documents the active mode.
ADDED:  `connectors/data_source.py::HybridDataSource` — routes per-method
        to the right source. x402 for live quotes + listings (sponsor
        track, paid in USDC on Base); Binance for OHLCV (free, no key,
        full coverage). x402 has no OHLCV endpoint; without this
        hybrid, x402 mode would have made 0 sleeve trades (only the
        daily trade floor would fire, ~1 trade/day). With it, the
        sleeves actually trade in x402 mode and the BNB HACK 2026
        sponsor credit is preserved. Opt out via
        `BNBAGENT_X402_NO_BINANCE_FALLBACK=1`.
ADDED:  `connectors/binance.py` per-symbol resilience — silently
        drops unknown symbols instead of crashing the whole batch
        via `raise_for_status()`. Critical for the hybrid because
        the strategy baskets include BEP-20s that aren't all listed
        on Binance. The bulk `/ticker/price?symbols=[...]` endpoint
        now falls back to per-symbol requests on 400.
ADDED:  Wizard step 4 (🧠 LLM Brain, optional, skippable) + Ready
        card showing live LLM status. The agent now has 6 wizard
        steps: Network → Wallet → Data source → Brain → Sign → Ready.
        The wizard auto-orders the Brain step before Sign Policy
        (since the LLM is the advisor + reviewer). The skip button
        keeps the agent working without an LLM (rule-based risk only).
ADDED:  `GET /api/wallet/balances` — live on-chain balances for the
        operator wallet. BSC native (BNB) + USDT/USDC/BUSD via
        `balanceOf`; Base native (ETH) + USDC if x402 is active.
        Best-effort RPC reads, per-chain errors captured, endpoint
        never raises. Frontend: new "Wallet Holdings" panel in the
        right rail, polled on a 30s cadence.
ADDED:  `core/version.py` + `GET /api/version` — canonical version
        + git commit. Topbar + footer stamp the live build so judges
        can verify exactly which version they're looking at. Bumped
        `pyproject.toml` 2.1.6 → 2.1.7 to match.
ADDED:  `dashboard/frontend/index.html` judges-first polish:
        - BNB-themed favicon (dark square + gold diamond + B mark)
        - Capabilities strip (7 chip pills) under the data-source
          banner: 3-sleeve trading, LLM risk reviewer, ERC-8004,
          TWAK-signed txs, x402 USDC micro-data (+Binance OHLCV in
          v2.1.7), ERC-20 token launcher, BNB Chain SDK broadcast
        - LLM strip in left rail under sponsor dots: live model name
          + provider, with a green dot when the LLM is configured
        - Footer: "BNB Agent 2026 · Made by Blaze 🔥" + live
          version/commit + github.com/ggBlaze/bnbagent + BNB HACK 2026
        - All 3 sponsor dots are green with brand-color left borders
        - "Blaze 🔥" in the footer links to https://x.com/OGDegen
ADDED:  Demo mode UI: persistent `🟢 DEMO MODE — public read-only
        view. All mutations disabled.` banner under the topbar,
        `● demo` pulse badge in the topnav, and per-control English
        hover tooltips on every locked control (kill switch, sleeve
        toggles, wizard buttons, LLM key, persona editor, etc.) that
        explain what the control would have done in non-readonly mode.
CHANGED: Wizard step ordering — Network → **Wallet** → **Data source**
        → **Brain** → Sign Policy → Ready. The x402 data source
        needs the wallet to be created first (same EVM key covers
        BSC trading + Base USDC payments). Side benefit: the
        pre-existing bug where `wizardCreateWallet` advanced to
        itself (`gotoStep(3)` in the old order) is fixed naturally.
CHANGED: LLM provider dropdowns (wizard step 4 + Config pane) now
        list **MiniMax first** as the recommended default (it was
        already the default in `agents/providers.yaml` for all 4
        agents but the UI didn't surface it; the backend accepted
        it, the frontend didn't list it). Env var is `MINIMAX_API_KEY`,
        base URL is `https://api.minimaxi.chat`, model is `MiniMax-M3`.
CHANGED: `.env.example` got a new "Dashboard auth" section documenting
        all 4 auth vars (the v2.1.6 gap: only the legacy
        `BNBAGENT_AUTH_ENABLED` flag was documented; the 3 new vars
        from the v2.1.6 commit — `BNBAGENT_AUTH_MODE`,
        `BNBAGENT_AUTH_SECRET`, `JUDGE_PASSWORD`, `ADMIN_PASSWORD` —
        are now listed with full mode matrix and example values).
CHANGED: `tests/integration/test_replay_determinism_across_runs.py`
        is now `@pytest.mark.slow` and the subprocess timeout bumped
        240s → 360s. The test passes in 96s in isolation; the bump
        gives headroom for the env tax when the live bot is on the
        same CPU. New `pytest -m "not slow"` runs the fast subset
        for pre-commit / quick feedback.
CHANGED: README §17 (Dashboard auth) now documents all 3 modes
        (was 2). Production checklist updated to use
        `BNBAGENT_AUTH_MODE=readonly` as the contest recommendation.
CHANGED: `docs/operations.md` "Production env vars" now has a full
        mode matrix + the 3 minimal env-var blocks (contest URL,
        operator VPS, local dev). Was just the 2-mode block.
FIXED:    x402 mode in v2.1.6 was effectively non-trading because
        the sleeves need OHLCV (x402 has none). The
        HybridDataSource in this release routes OHLCV to Binance
        so the sleeves actually fire trades. A bug that would have
        lost 7 days of contest window to "0 trades" is now closed.
TESTS:  +37 new tests (was 438, now 475):
        - 7 in tests/unit/test_auth.py (3-mode resolution, readonly
          behavior, opt-out safety)
        - 6 in tests/integration/test_auth.py (readonly end-to-end
          via FastAPI TestClient, login refuses in readonly, no
          escalation path to admin)
        - 8 in tests/unit/test_balances.py (BSC + Base wallet balance
          reads, stablecoin USD annotation, RPC failure handling)
        - 1 in tests/integration/test_dashboard.py (wallet balances
          endpoint shape)
        - 6 in tests/unit/test_data_source.py (HybridDataSource
          per-method routing, status reports fallback, from_config
          default + opt-out)
        - 2 in tests/unit/test_binance.py (per-symbol resilience for
          both OHLCV and quotes)
        - 1 marker registration in pyproject.toml (slow)
        - 3 in tests/unit/test_setup.py (Aura, 991c123: import_wallet
          and generate_wallet both write data_source.base_address,
          stale entries are overwritten)
        - 3 in tests/integration/test_wallet_save_password.py
          (Aura, 50febf2: opt-in flag writes TWAK_PWD to .env,
          missing flag leaves .env untouched, existing line is
          replaced in place)

## v2.1.6 — Hard date-lock Token Module + 2-key wallet protection (2026-06-13)

The public Coolify deploy exposes admin routes (setup, sign, register,
kill switch, wallet import/export) and the Token Module deploy route.
Each needs defense-in-depth so a judge with the admin password (or
a misconfigured env) can't blow up the operator.

ADDED:  Token Module HARD date lock — `TokenModule.is_deploy_unlocked()`
        returns `(bool, reason)` and refuses to deploy before
        2026-07-07 00:00 UTC. After the date, STILL locked unless
        `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` is set. Belt-and-suspenders.
ADDED:  `/api/wallet/export-mnemonic` env-gated — returns 403 unless
        `BNBAGENT_ALLOW_WALLET_EXPORT=true` is in the server env. A
        judge who learns the admin password still can't dump the
        operator's seed phrase (4-factor protection: cookie + password
        + env flag + restart).
ADDED:  `/api/setup/wallet/import` env-gated — returns 403 unless
        `BNBAGENT_ALLOW_WALLET_IMPORT=true` is set. A judge with admin
        cookie still can't replace the operator's keystore with their
        own.
CHANGED: `dashboard/backend/main.py` `/api/tokens/deploy` route
        returns HTTP 423 (Locked) with `error: "token_deploy_locked"`
        and a human-readable reason. The dashboard UI shows a 🔒
        banner with the unlock date + the env flag.
CHANGED: `dashboard/frontend/index.html` Token Module deploy handler
        shows the dedicated 423 banner (no more generic "deploy failed").
CHANGED: `.env.example` got a new "Contest / safety locks" section
        documenting the 3 new env flags + their default-OFF posture.
CHANGED: README §16 (Security model) got 3 new rows for the date lock
        + 2 wallet routes.
CHANGED: `docs/SECURITY.md` got a "Token Module contest lock" section
        and updates to the export/import sections for the new env gates.
CHANGED: `docs/TOKEN_MODULE.md` got a "Contest window lock" section
        with the full gate matrix.
CHANGED: `docs/operations.md` Token Module pane + a new "Production
        env vars (v2.1.6)" section at the end.
CHANGED: `docs/API.md` route table got env-gate callouts on the
        affected rows.
CHANGED: `docs/compliance.md` "No token launches" row notes that the
        lock is now enforced in code, not just in docs.
CHANGED: `docs/submission.md` "Token Module testnet deploy" checklist
        row notes the contest lock.
TESTS:  +23 new tests (438 tests passing; was 415):
        - 17 in tests/unit/test_token_lock.py (date + env boundaries)
        - 6 in tests/integration/test_auth.py (env-gated routes)

## v2.1.5 — 2-mode password wrapper + Dockerfile + Coolify deploy (2026-06-13)

The bnbagent is going on a public VPS via Coolify. The dashboard needs
a way to gate operator controls (setup, sign, register, kill switch,
wallet export, persona edit) from judge-demo controls (live state,
chat, replay, persona read).

ADDED:  `dashboard/backend/auth.py` — `BNBAGENT_AUTH_ENABLED` flag
        (default OFF) + 2 passwords (JUDGE / ADMIN) + HMAC-SHA256
        signed cookie (stdlib only, no new dep). 1-day expiry,
        httponly + samesite=strict. `current_role(request)` +
        `require_role(min_role)` FastAPI deps.
ADDED:  `dashboard/backend/main.py` — 3 new auth routes:
        GET /api/auth/status, POST /api/auth/login, POST /api/auth/logout.
CHANGED: All 20 mutation routes gated by `Depends(_auth.require_admin)`.
        Chat routes gated by `require_judge` (judges + admins).
ADDED:  `Dockerfile` — single Python 3.12 image, runs the dashboard
        on :8000 via uvicorn. Healthcheck hits /api/healthz every 30s.
ADDED:  `docker-compose.yml` — env-var driven, .env loaded automatically.
CHANGED: README §17 (Deployment) rewritten to cover local dev, the
        password wrapper, Coolify / docker-compose, reverse proxy,
        and the production checklist (now with 2 new boxes for
        AUTH_ENABLED + AUTH_SECRET).
TESTS:  +27 new auth tests (388/388 → 415/415). Local dev is unchanged:
        `bash bnbagent` works without any auth env vars set.

## v2.1.4 — BNB HACK 2026 compliance (eligible 149 + on-chain register + daily trade floor)

Blaze (2026-06-12, 08:48 CST) asked us to audit the agent against the
6 official rules from the DoraHacks Track 1 detail page
(https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail). We found
3 real gaps and fixed all of them in this release.

ADDED:  data/eligible_tokens.json — the 149-BEP-20 eligible list
        published verbatim on the contest page, pinned as
        schema_version "2026-06-12.1". The contest says "trades
        outside this list do not count" — so we filter at the
        universe level, not the trade level.
ADDED:  core/eligibility.py — filter_universe() + is_eligible(),
        with three modes: strict (default during the contest, drops
        out-of-scope symbols), soft (logs violations but keeps the
        symbol, for long-running use outside the contest window),
        off (no filter, for backtests). Mode is selected by the
        BNB_HACK_TRACK1 env var. The module fails closed if the
        list file is missing/malformed.
ADDED:  Defense-in-depth in core/risk.py::circuit_breaker_check()
        — even if a sleeve forgets to call filter_universe(), the
        risk engine is the last gate before the order reaches TWAK
        for signing. In strict mode, the order is rejected with a
        reason that includes the schema_version (so a stale list
        is visible in the trade-rejection audit log).
CHANGED: strategies/sleeve_a_carry.py + sleeve_b_momentum.py +
         sleeve_c_meanrev.py — all three sleeves call
         filter_universe() before fetching OHLCV (saves a paid x402
         microcharge per dropped symbol AND keeps us on the
         contest's scored list).
CHANGED: config/config.yaml — replaced 5 out-of-scope tokens in
         basket_symbols (BTC, SOL, MATIC, NEAR, APT) and 5 in
         dex_universe_symbols (WBNB, BTCB, SOL, MATIC, NEAR, APT)
         with 5 in-scope replacements (USDT, DAI, INJ, AAVE, FIL).
CHANGED: config/policy.yaml.example — replaced 5 out-of-scope
         tokens in bsc_tokens allowlist + removed WBNB, BTCB.
ADDED:  scripts/competition_register.py — the on-chain registration
        wrapper. Resolves the agent's wallet from policy.yaml or
        BNBAGENT_PRIVATE_KEY or ~/.twak/wallet.json, shells out to
        `npx twak compete register --network mainnet --contract
        0x212c61b9b72c95d95bf29cf032f5e5635629aed5`, captures the
        tx hash, deep-links to bsctrace.com, caches the result in
        data/competition_register.json (gitignored). Flags: --check
        (status only), --emit-mcp (print the MCP action JSON so any
        MCP client can drive it), --dry-run (resolve address + emit
        MCP, no tx).
ADDED:  agent_mcp/mcp_server.py — competition_register MCP tool.
        The action name matches the rules page verbatim.
ADDED:  dashboard/backend/main.py — 3 new endpoints:
        GET  /api/competition/register/status   (cached state + contract)
        POST /api/competition/register          (triggers the script)
        POST /api/competition/register/emit-mcp (offline prep)
        GET  /api/eligibility                   (filter mode + count)
ADDED:  dashboard/frontend/index.html — BNB HACK 2026 card on the
        Live pane with: contract address (BscTrace deep-link),
        registration status, agent wallet, tx hash, daily-floor
        status, eligible-token count. 3 buttons: Register / Refresh
        / Show MCP action. Card auto-refreshes when the user opens
        the Live pane.
ADDED:  core/daily_trade_floor.py — 1-trade-per-day safety net.
        At 23:30 UTC every day, the heartbeat checks if any trade
        happened. If not, fires a 0.1%-of-equity rebalance on the
        cheapest in-scope BEP-20 (USDC preferred), holds for 30
        minutes, then closes. Goes through the same circuit breaker
        as a sleeve trade. Once per UTC day max (idempotent on
        restart). Opt-out via BNB_HACK_NO_DAILY_FLOOR=1.
CHANGED: core/tick.py — Agent.start() now also creates the floor
         close loop (closes the floor position after hold_min).
         Agent._heartbeat() now also calls floor.tick() once a
         second. Agent has a new submit_floor_trade() method that
         wraps Portfolio.add_position() with the same audit
         logging as a sleeve trade.
ADDED:  docs/compliance.md — the full audit trail. Every rule from
        the contest page is mapped to: the code that enforces it
        (if mechanical), the operator step (if ops), the test that
        pins it (if any), the demo segment that shows it (if
        visible). Includes the special-prize scoring breakdown,
        target: 100/100 TWAK + full marks on CMC and BNB SDK.
ADDED:  docs/demo-script.md — explicit x402 segment ("Native x402
        is the heart of the agent's data loop, not a README
        mention") + explicit TWAK segment with the 3-surface
        breakdown (signing + autonomous mode + x402) +
        competition-register button segment.
ADDED:  Tests: 47 new tests (22 eligibility + 12 daily floor + 13
        register). Test count: 330 → 377. The eligibility tests
        pin the shipped config (basket + dex_universe + allowlist)
        to be a strict subset of the 149 — a future contributor
        sneaking in an out-of-scope symbol will fail CI.

## v2.1.3 — UI gaps in the dashboard (LLM key + Personas + Token form)

Blaze (2026-06-12, 07:07 CST) flagged three real UI gaps in the
dashboard that the v2.1.0/v2.1.1/v2.1.2 series didn't address. All
fixed in this release.

ADDED:  POST /api/llm/key + POST /api/llm/test. The LLM provider
        keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY,
        OAI_KEY) used to require a manual edit of .env + an agent
        restart. Now there's a Config pane section with a masked
        key field, a Set button, and a Test button. The endpoint
        writes/updates the env var in .env (gitignored, atomic-ish)
        and the test endpoint reads .env directly (not os.environ,
        so the user can verify their key BEFORE restarting the
        agent — the in-process router has env vars cached from boot).
        The Set response is honest about the restart requirement.
ADDED:  Tests for the dotenv helpers (11 tests) + the LLM key
        endpoint (8 tests): replaces/appends, preserves comments +
        unrelated lines, atomic write, special chars in API keys,
        missing/invalid/valid key detection, oai_compat requires
        OAI_BASE, local provider is n/a.
ADDED:  Personas section in the Config pane. Lists all 4 personas
        (advisor / reviewer / chat / token_module) with their
        status (pro default vs diverged) and View / Edit / Reset
        links. The runtime copy lives at ~/.bnbagent/personas/
        (gitignored, takes precedence over the shipped copy in
        agents/personas/). The chat persona keeps its existing
        view/edit links in the Chat pane too — minimal disruption.
ADDED:  Inline form fields for token name + symbol in the Token
        Module pane. The old code used window.prompt() dialogs (bad
        UX). The new form has dedicated <input> fields, validation
        (3-5 uppercase chars for symbol, non-empty for name), and
        a prominent network notice that updates in real-time when
        the user changes the Network dropdown:
        - Green for testnet: "BSC Testnet (chain 97, free, recommended)"
        - Red for mainnet: "BSC MAINNET — real BNB, IRREVERSIBLE"
CHANGED: agents/_pro_defaults/chat.md + agents/personas/chat.md
         now point the chat LLM at the new Config pane → LLM API
         key section (the old instruction said "Setup → re-enter
         the API key" but Setup never had an LLM step; the pro
         default was lying to the user).
CHANGED: Chat banner copy now says "open the Config pane → LLM API
         key section" (was: "add a key in .env and restart").
CHANGED: badge 311/311 → 330/330.

## v2.1.2 — repo cleanliness (the other write paths)

CHANGED: config/policy.yaml is now gitignored. The shipped
         config/policy.yaml was expired (issued/expires from 2024)
         AND its signature didn't recover to evaluator_address —
         the agent booted with a "proceeding in dev mode" warning
         on every run. Rather than ship a stale signed policy,
         the file is now a gitignored runtime artifact generated
         on first `bash install.sh` (via `policy_sign --dev`) and
         overwritten by the Setup wizard's "Sign Policy" step when
         the operator signs with their TWAK keystore. A new
         config/policy.yaml.example (tracked, a template with
         __SIG__/__EVAL__ placeholders) serves as the
         "what does a policy look like" reference for new readers.
CHANGED: agents/token_module.yaml is now gitignored. The Token
         Module's update_config() writes here; without the
         gitignore entry the file would appear as untracked on
         first dashboard use and could land in `git add .`
         commits.
CHANGED: ~/.twak/ is now gitignored (defense in depth). The TWAK
         keystore is at ~/.twak/wallet.json by default (outside
         the repo) and AES-256-GCM-encrypted, but the gitignore
         entry protects against a future install path that puts
         it inside the repo.
ADDED:  tests/unit/test_repo_cleanliness.py (36 contract tests):
        - 16 tests that the templates / shipped defaults /
          persona files are tracked
        - 17 tests that the user-specific state / build outputs
          are gitignored
        - 3 tests that the runtime write paths
          (config/local.yaml, config/policy.yaml,
          agents/token_module.yaml) are NOT in the tracked set
        - 1 test that the shipped personas match the pro defaults
          (catches the v2.0.8-M7 divergence I found last turn
          before the next stale shipped persona slips through)
CHANGED: badge 275/275 → 311/311.

## v2.1.1 — config/local.yaml shadow pattern

CHANGED: The Setup wizard, dashboard data-source endpoints, and
         core/boot.py no longer write to the tracked
         config/config.yaml. All user-specific state (tier choice,
         CMC Pro API key, custom Base RPCs, the base_address boot
         auto-writes) now lives in config/local.yaml, which is
         gitignored. config/config.yaml is the shipped defaults
         file, immutable at runtime. See core/config_paths.py for
         the merge semantics. See config/local.yaml.example for
         the file shape and the security rationale (the CMC Pro
         API key was at risk of accidental commit before this
         refactor).

ADDED:  config/local.yaml.example (tracked, copied to
        config/local.yaml on first `bash install.sh`).
ADDED:  core/config_paths.py: load_config() (deep-merge), write_local()
        (atomic-ish), ensure_local_example_copied() (bootstrap).
ADDED:  tests/unit/test_config_paths.py (15 tests: shipped-only /
        local-only / both / nested-override / list-replace /
        round-trip / write-atomics / ensure-copies-example /
        ensure-skips-when-exists / ensure-noop-without-example).
CHANGED: badge 260/260 → 275/275.
CHANGED: install.sh copies local.yaml.example on first install.
CHANGED: tests/unit/test_boot.py and tests/integration/test_dashboard.py
         updated to use the new tmp_path/config/config.yaml layout.
         The pre-refactor prereq-400 tests were false positives
         (the endpoint fell through to cfg={} because the fixture
         was at the wrong path); now they actually exercise the
         merged-view prereq check.

## v2.1.0 — 3-tier CMC data source

ADDED: 3-tier data-source selection (CMC Pro / x402 on Base / Binance
       fallback) via the Setup wizard + a 'Change data source' button
       in the Config pane.
ADDED: Persistent data-source banner in the Live pane.
ADDED: Secret-phrase export button in the Wallet step +
       /api/wallet/export-mnemonic endpoint.
ADDED: Base RPC config (3 defaults, add/remove, rotation) in the
       x402 wizard step.
CHANGED: x402 now settles on Base (chain 8453) with native USDC at
         0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913. The retry
         header is now PAYMENT-SIGNATURE (was X-PAYMENT).
FIXED:   The 404 on https://api.coinmarketcap.com/agent-hub — the
         correct x402 base is https://pro-api.coinmarketcap.com/x402.
CHANGED: The CMC integration is now a MarketDataSource Protocol with
         4 concrete clients behind a DataSourceRouter.

## v2.0.8 — 2026-06-08 — Security hardening (H1-H4 + M3 + M4)

**Security.** A focused security review of the wallet, BSC RPC, gas,
MCP server, and CMC-fallback paths was performed in v2.0.8. The
**full review (private)** is at
`~/.openclaw/workspace-hax/Projects/audits/bnbagent-security-review-2026-06-08.md`
and is **not** committed to this public repo. A summary lives in
`docs/SECURITY.md`.

Six commits in this release:

### v2.0.8-H4 — gas-price cap from policy, refuse stuck-tx window

**Before:** `sign_transaction` hardcoded `maxFeePerGas = 5 gwei` and
accepted any caller-supplied `gasPrice`. On BSC mainnet, gas spikes
push required fees to 10-20+ gwei. A tx signed at 5 gwei sits in the
mempool indefinitely and the trade signal is gone by the time the tx
lands.

**After:** `sign_transaction` accepts an OPTIONAL `max_gas_price_gwei`
kwarg. If the resulting fee would exceed the cap, raises
`GasPriceTooHigh` BEFORE signing. The 4 call sites (3 sleeves +
token module) read `fees.max_gas_price_gwei` from the user-signed
policy and pass it. A failed cap raises, the sleeve logs
`gas_too_high_skip`, the next tick re-evaluates the signal. The
default 5 gwei is at the cap boundary → replay + testnet unchanged.
Added 7 tests.

### v2.0.8-H3 — resync_nonce: reconcile local cache from chain

**Before:** `BSCClient._nonce_cache` was a local-only counter, never
reconciled with the chain. Crash mid-tick (after sign, before
broadcast) left the cache wrong, causing the next tx to be rejected.

**After:** New `BSCClient.resync_nonce(address)` queries
`eth_getTransactionCount(address, 'pending')` on mainnet and
reseeds the cache. In testnet/replay, the function is a no-op
(read-only inspection of the in-memory cache). Added 5 tests.

### v2.0.8-H2 — add pycryptodome dep + hoist AES imports to module level

**Before:** `from Crypto.Cipher import AES` was inside the function
body of every decrypt path, AND `pycryptodome` was not declared in
`pyproject.toml`. A fresh `pip install bnbagent` would install
cleanly then fail at first decrypt with a confusing warning.

**After:** `pycryptodome>=3.18` declared in deps. AES hoisted to
module level in `connectors/keystore.py` and `connectors/twak.py`.
A missing dep now fails at module import (loud, traceable). Added
3 tests for the round-trip + module-level invariant.

### v2.0.8-H1 — gate BNBAGENT_PRIVATE_KEY env var behind explicit opt-in

**Before:** If `BNBAGENT_PRIVATE_KEY` was set anywhere in the env
(`.env`, docker-compose, shell history, systemd), the keystore
encryption was silently bypassed and the raw key sat in process
memory. No warning, no prompt, no audit entry.

**After:** The PK env var path is opt-in via `BNBAGENT_ALLOW_PK_ENV=1`.
Without the opt-in, refuses to load and raises `RuntimeError`. A
`CRITICAL` log line is emitted on every keystore bypass, regardless
of opt-in. Added 4 tests.

### v2.0.8-M3 — MCP SSE default bind 127.0.0.1 + optional Bearer auth

**Before:** `python -m mcp.server --transport sse` bound `0.0.0.0`
with no auth, exposing 10 tools (incl. skill toggles that write
to the control file) to anyone on the network.

**After:** Default `--host` is now `127.0.0.1`. Optional
`BNBAGENT_MCP_TOKEN` env var enforces Bearer auth. If unset, a
WARNING is logged on startup and unauthenticated requests are
accepted (safe for localhost). The token is never logged, never
written to disk, never exposed in any response. Added 7 tests.

### v2.0.8-M4 — vol filter fallback above pause threshold

**Before:** `SleeveACarry._realized_vol_annualized` returned `0.0`
on any failure (CMC rate limit, network blip). `0.0 < 0.05` (the
default threshold), so a single CMC blip force-closed a healthy
carry book.

**After:** The fallback is `min_vol + buffer` (default buffer 0.01),
so a CMC outage looks like "vol is fine" and the existing positions
stay open. The buffer is overridable via
`policy.global_risk.vol_fallback_buffer`. Added 7 tests.

### Test count

194 → 212 → 226. The H1-H4 + M3-M4 commits added 18 tests
(14 unit + 4 integration). The follow-up L1-L5 + M5-M7 +
chat-persona-confirm + keystore-smoke commits added 14 more.
All 226 pass.

### Files

- `connectors/keystore.py` — hoisted AES
- `connectors/twak.py` — hoisted AES, gated PK env, gas cap
- `connectors/bnb_sdk.py` — `resync_nonce`
- `strategies/sleeve_a_carry.py` — cap + vol fallback
- `strategies/sleeve_b_momentum.py` — cap
- `strategies/sleeve_c_meanrev.py` — cap
- `agents/token_module.py` — cap
- `agent_mcp/mcp_server.py` — host default + middleware
- `pyproject.toml` — pycryptodome dep
- `tests/unit/test_keystore_smoke.py` — new (3)
- `tests/unit/test_twak_pk_env_gate.py` — new (4)
- `tests/unit/test_twak_gas_cap.py` — new (7)
- `tests/unit/test_nonce_resync.py` — new (5)
- `tests/unit/test_sleeve_a_vol_fallback.py` — new (7)
- `tests/unit/test_mcp_auth.py` — new (7)
- `docs/SECURITY.md` — review summary

## v2.0.7 — 2026-06-06 — Real bit-for-bit replay determinism

**Bug fix.** The v2.0.4 "deterministic replay" claim was incomplete:
five `int(time.time())` reads remained — two in the synthetic-tape
generator, two in the ERC-8183 window IDs, one in the control-bus
audit log. The 5m metrics were stable because sleeves read returns
and z-scores over candle counts (alignment-invariant). The 1h
metrics were not: `make_synthetic_week_hourly` buckets 5m bars into
hours via `ts // 3600 * 3600`, so the bar count per hour depends on
`epoch mod 3600`. Three runs at three different wall-clocks would
land in three different 5-min bins and produce three different
hourly OHLCV → three different Sleeve C signals → three different
attributions. Bear 1h has been observed swinging between -0.58% and
+219% on identical input.

### Added

- `tests/integration/test_replay_determinism_across_runs.py` —
  subprocess-runs replay 3 times under 3 wall-clock offsets whose
  `mod 3600` values are guaranteed to fall in different 5-min bins,
  SHA-256-hashes all 14 output files per run, asserts identical
  across all 3. Runs the helper with `python -B` so stale `.pyc`
  caches can't mask a regression.
- `tests/integration/_replay_runner.py` — test helper that monkey-
  patches `time.time` at module-import time via `TEST_TIME_OFFSET`
  env var and writes to a custom output dir. Test-only mechanism;
  no production code knows about it.

### Changed

- `backtest/replay.py` — added `_SYNTHETIC_REFERENCE_EPOCH` constant
  (`1_780_722_354`, the unix-time bin one 5-min slot before commit
  fdf5c62, empirically pinned so the deterministic output reproduces
  the v2.0.5.1 canonical replay_*.json numbers bit-for-bit).
  Replaced `int(time.time())` at:
  - line 69 (5-min candle ts) → `_SYNTHETIC_REFERENCE_EPOCH - (minutes - i) * 300`
  - line 93 (funding ts) → same anchor
  - line 261 (open-jobs `window_id`) → `f"replay-{regime}-{seed}-open"`
  - line 327 (finalize `window_id`) → `f"replay-{regime}-{seed}-final"`
  - Removed now-unused `import time`.
- `core/control.py` — `_applied_at` now uses `portfolio._now()`
  (which reads the injected clock) instead of `int(time.time())`.
  In the replay harness the clock is the current tape ts; in
  production it's wall-clock. Removed now-unused `import time`.

### Verified

- `pytest -q`: 179 tests passing (178 pre-existing + the new
  determinism test).
- `git diff data/reports/` is empty after a fresh
  `python -m scripts.run_both_regimes` — the meta-test
  `test_demo_script_kpi_table_matches_replay_json` is now
  tautological by construction. The v2.0.5.1 demo-script numbers
  (bull_hourly +0.99% / 87 trades / A-only, bear_hourly -0.56% / 99
  / A-only, chop_hourly +0.62% / 135 / A-only) are unchanged.
- Three sequential `scripts/run_both_regimes.py` invocations
  produce SHA-256-identical output for all 14 files.

## v2.0.0 — 2026-06-05 — AI Agent Team + Skills + Token Module + MCP

**Major upgrade.** BNB Agent graduates from a deterministic bot to a real
**AI agent team**. The underlying BSC trading engine is unchanged; the
LLM layers are an additive, safe, bounded extension.

### Added

- **3-LLM agent team** (advisor / reviewer / chat)
  - `agents/advisor.py` — Layer 1: 5-min tightening loop. Can only TIGHTEN the policy.
  - `agents/reviewer.py` — Layer 2: per-trade veto (0.5s timeout → heuristic fallback). Can only VETO.
  - `agents/chat.py` — Layer 3: conversational interface with 9 tools. Can only RECOMMEND.
- **Provider-agnostic LLM** (`agents/providers.py`)
  - 5 adapters: Anthropic, OpenAI, OpenRouter, generic OAI-compatible, local (llama.cpp)
  - Per-agent provider+model routing via `agents/providers.yaml`
  - Pure `httpx` — no third-party SDKs
- **Personas** (`agents/_pro_defaults/`, `agents/personas/`)
  - 4 pro defaults (advisor, reviewer, chat, token_module)
  - Live user-editable copies in `agents/personas/`
  - Dashboard: "view persona" modal, "reset to pro" button
  - `persona.diverged` flag = (sha256(user) != sha256(pro_default))
- **Token Module** (`agents/token_module.py`)
  - ERC-20 / BEP-20 / OpenZeppelin deploy on BSC
  - x402-pays CMC for token metadata
  - TWAK-signs the contract-creation tx
  - BNB SDK broadcasts; deterministic `contract_address` in testnet
  - Optional single-file HTML landing-page generation (sanitized against eval/document.write)
  - Mainnet requires `confirm_mainnet: true` + user-typed token name in dashboard modal
- **Skills registry** (`skills/`)
  - 6 built-in Skills: telegram_alert, farcaster_post, webhook_dispatch, x_sentiment, cmc_global_filter, glassnode_onchain
  - Hot-toggled from dashboard or chat
  - State persisted to `~/.bnbagent/skills.json`
  - `cmc_global_filter` is the only Skill that writes (pauses sleeves on bear regime)
- **MCP server** (`agent_mcp/mcp_server.py`)
  - 10 tools over stdio (Claude Code / Goose / Cursor) or SSE (port 8765)
  - `bnbagent_get_pnl`, `bnbagent_list_positions`, `bnbagent_list_trades`,
    `bnbagent_get_policy`, `bnbagent_recommend_risk_change`,
    `bnbagent_deploy_token`, `bnbagent_chat`, `bnbagent_list_skills`,
    `bnbagent_enable_skill`, `bnbagent_disable_skill`
  - Integration test: `tests/integration/test_mcp.py`
- **Dashboard panes**
  - **Chat** pane (Layer 3): message log + input + persona controls + recent decisions
  - **Tokens** pane: config form + deploy button + result card with BscScan link + website download
- **Backend endpoints** (20+ new)
  - `/api/chat`, `/api/chat/tools`, `/api/chat/tool`
  - `/api/agent/advisor`, `/api/agent/reviewer`
  - `/api/agent/personas/{name}` (GET/POST/reset)
  - `/api/llm/status`, `/api/llm/config`
  - `/api/tokens/config`, `/api/tokens/deploy`
  - `/api/skills`, `/api/skills/{name}/enable`, `/api/skills/{name}/disable`
- **20+ new launch scripts**
  - `scripts/mcp_serve.sh`, `scripts/mcp_serve_sse.sh`

### Changed

- `core/main.py` — wires `LLMRouter`, `StrategyAdvisor`, 3× `TradeReviewer`, `ChatAgent`,
  `SkillRegistry`, `TokenModule` into the boot.
- `core/tick.py` — `Agent.review_trade(proposed, sleeve_state, market_snapshot)` method + `reviewers` dict.
- `core/portfolio.py` — `sleeve_exposures()` helper for the advisor's context.
- `strategies/{a,b,c}.py` — per-sleeve reviewer hook (between `allow_trade` and `sign_transaction`).
- `connectors/bnb_sdk.py` — testnet `BSCClient.broadcast` now returns a deterministic `contract_address`
  for contract-create txs (so the token deploy demo works end-to-end).
- `dashboard/frontend/index.html` — +chat pane, +tokens pane, +LLM config UI. ~1800 lines.
- `install.sh` — friendlier error if MCP SDK not installed.

### Tests

- 80+ new tests: `test_providers.py`, `test_persona_loader.py`, `test_advisor.py`,
  `test_reviewer.py`, `test_chat.py`, `test_token_module.py`,
  `test_skill_registry.py`, `test_mcp.py` (integration).
- **172/172** tests passing. CI-enforced.

### Documentation

- 8 new docs: `agents.md`, `API.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `MCP.md`,
  `PERSONAS.md`, `SECURITY.md`, `SKILLS.md`, `TOKEN_MODULE.md`
- 1 new top-level file: `salepitch.md`
- `README.md` fully rewritten as the canonical entry point

---

## v1.2.0 — 2026-06-05 — Dashboard Setup wizard

First-time users now land in a 4-step Setup wizard (Network → Wallet →
Sign Policy → Ready) that completes in under two minutes. New: AES-256-GCM
TWAK keystore at `~/.twak/wallet.json`; private key encrypted on disk
on receipt and never echoed back. 5 setup-related endpoints, 8 new tests.

---

## v1.1.0 — 2026-06-05 — Production hardening + 1-command install/run

- `install.sh` — idempotent 1-command installer
- `bnbagent` — 1-command runner (boots agent + dashboard in one terminal)
- 10 trading-logic hardening fixes (see `docs/audit-2026-06-05.md`)
- Premium Operations Bridge dashboard (acid-lime accent, SVG sparklines)
- Dashboard SSE log stream + control log
- Kill switch in the right rail
- Docs: `install.md`, `operations.md`, `audit-2026-06-05.md`

---

## v1.0.0 — 2026-06-05 — Initial submission for BNB HACK 2026

First tagged release. Three-sleeve BSC trading agent. 64 unit + integration
tests. Full sponsor integration (CMC x402 + TWAK + BNB SDK). ERC-8004
identity + ERC-8183 jobs. Replay harness.
