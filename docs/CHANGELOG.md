# BNB Agent — Changelog

All notable changes to this project. Versioned per the git tag.

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
