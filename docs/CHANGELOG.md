# BNB Agent — Changelog

All notable changes to this project. Versioned per the git tag.

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
