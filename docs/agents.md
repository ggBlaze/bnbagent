# BNB Agent — AI Agent Team (v2.0)

The agent is a **3-layer LLM team** that turns the deterministic trading
engine into a real *agent*. The 3 layers share a single `LLMClient`
protocol with 5 provider adapters, all driven by a single
`agents/providers.yaml` config.

## Architecture

```
agents/
  _pro_defaults/        canonical pro personas (shipped, IPFS-pinned)
    advisor.md  reviewer.md  chat.md  token_module.md
  personas/             live, user-editable (copied from _pro_defaults on boot)
  prompts/              user-prompt templates
  base.py               PersonaLoader + llm_complete + llm_stream
  providers.py          LLMClient Protocol + 5 adapters + LLMRouter
  providers.yaml        per-agent provider+model routing
  advisor.py            Layer 1 — strategy advisor (5-min loop)
  reviewer.py           Layer 2 — per-trade reviewer (veto only)
  chat.py               Layer 3 — conversational chat + tool dispatcher
  token_module.py        TokenModule (deploy + website gen)
```

## Layer 1 — StrategyAdvisor (5-min loop)

* Can only **TIGHTEN** the signed User Policy. Never loosens.
* Writes via `core.control.write_control(...)` — same audit path as the
  dashboard, so the right-rail Control Log shows advisor edits.
* Enforced in code (`_apply` compares `new < old`); the LLM is never
  trusted to constrain itself.
* Graceful no-op if no provider is configured (logs `"LLM disabled"`).

## Layer 2 — TradeReviewer (per-trade veto)

* Can only **VETO** a trade. Cannot raise risk.
* `asyncio.wait_for(..., timeout=0.5)` on every call → falls back to
  `_heuristic_veto` (always <1ms) if the LLM is slow.
* `confidence < 0.70` → veto (`source="low_confidence"`).
* Post-LLM hard guardrails (in code, not delegated): sleeve drawdown >
  50% of policy cap, win-rate < 20%, post-loss cooldown active.

## Layer 3 — ChatAgent + Chat pane

* Streams tokens via SSE.
* 9 tools: `get_pnl_summary`, `list_recent_trades`, `list_open_positions`,
  `recommend_risk_change`, `create_token`, `list_skills`, `enable_skill`,
  `disable_skill`, `sign_new_policy`.
* **Can recommend policy changes; never applies them.** The chat routes
  the user to Setup → re-sign, which requires their wallet password.

## TokenModule (its own tab)

* ERC-20 / BEP-20 / OpenZeppelin deploy on BSC.
* Testnet by default; mainnet requires `confirm_mainnet: true` and a
  user-typed token name in a dashboard modal.
* Optional landing-page website (single-file HTML, no external
  resources, sanitized against `eval`/`document.write`).

## Skills registry

* 6 built-in Skills: telegram_alert, farcaster_post, webhook_dispatch,
  x_sentiment, cmc_global_filter, glassnode_onchain.
* `SkillRegistry` discovers them, persists enable/disable state to
  `~/.bnbagent/skills.json`.
* The chat can `enable_skill(name)` / `disable_skill(name)` directly.

## MCP server (`agent_mcp/mcp_server.py`)

* Exposes the agent as 10 MCP tools over stdio or SSE.
* MCP-client integration: any stdio-compatible MCP client (Claude Code,
  Goose, Cursor, Continue, etc) can drive the BNB Agent as a tool library.
  See [`docs/MCP.md`](MCP.md) for the config snippet.
* See `scripts/mcp_serve.sh` and `scripts/mcp_serve_sse.sh`.

## Personas

* Markdown files with YAML front-matter.
* `agents/_pro_defaults/` ships the canonical pro defaults.
* `agents/personas/` is the live editable copy. The dashboard's
  "view persona" / "reset to pro" buttons operate on these files.
* `BaseAgent._load_persona` re-reads on `mtime` change, so dashboard
  edits are picked up within 1 second.
* `persona.diverged = (sha256(user) != sha256(pro_default))` → dashboard
  shows a "you've modified the pro persona" warning.
* The default pro personas are intentionally **short and strict** so the
  LLM is constrained to do the right thing out of the box. Edit freely.

## Provider-agnostic LLM config

`agents/providers.yaml`:

```yaml
default: openrouter

providers:
  anthropic:  { base: https://api.anthropic.com,  key: $ANTHROPIC_API_KEY }
  openai:     { base: https://api.openai.com,     key: $OPENAI_API_KEY }
  openrouter: { base: https://openrouter.ai/api,  key: $OPENROUTER_API_KEY }
  oai_compat: { base: $OAI_BASE,                  key: $OAI_KEY }
  local:      { base: $LOCAL_LLM_BASE,             key: "" }

agents:
  advisor:        { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 512,  temperature: 0.1 }
  reviewer:       { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 256,  temperature: 0.0 }
  chat:           { provider: openrouter, model: anthropic/claude-3.5-sonnet, max_tokens: 2048, temperature: 0.4 }
  token_module:   { provider: openrouter, model: anthropic/claude-3.5-haiku,  max_tokens: 8000, temperature: 0.7 }
```

Per-agent override: just set the `provider` and `model` under `agents.<name>`.
All 4 agents can use different providers.

## Security model

* The private key **never leaves the host process** — the dashboard only
  ever sees the address (and the encrypted keystore path).
* The LLM can only **tighten** risk (Layer 1), **veto** trades (Layer 2),
  or **recommend** policy changes (Layer 3). None of these layers can
  loosen the user's signed policy, override the circuit-breaker, or
  bypass the mainnet confirmation guard.
* Token deploy on mainnet requires the user to type the token name
  in a confirmation modal.
* Skills that post to external services (Telegram, Farcaster) require
  the user to set the relevant API keys; without them, the skill is
  disabled and the chat / dashboard show "missing_env".

## Operational run-through

```bash
bash install.sh                # creates venv, installs deps, signs a dev policy
export OPENROUTER_API_KEY=sk-or-...    # any one of the 5 providers
bash bnbagent                  # starts agent + dashboard
# → http://localhost:8000
# → Setup wizard → Live
# → Chat: "what's my PnL today?" → streams a response
# → Chat: "create a token called Mooncoin" → Token deploys on testnet
# → Tokens tab → "Deploy now" → another deploy
# → Chat: "view persona" → modal → edit → "save" → "diverged" badge
# → chat: "enable the telegram skill" → if env set, telegram gets a test msg
# → external agent (Claude Code / Goose): add bnbagent MCP server, call bnbagent_get_pnl
```

## Tests

* `tests/unit/test_providers.py` — 16 tests (5 adapters, env, router)
* `tests/unit/test_persona_loader.py` — 10 tests
* `tests/unit/test_advisor.py` — 8 tests (can-only-tighten, malformed, disabled)
* `tests/unit/test_reviewer.py` — 8 tests (low-conf, heuristic, timeout)
* `tests/unit/test_chat.py` — 10 tests (tools, recommend_does_not_write)
* `tests/unit/test_token_module.py` — 14 tests (deploy, sanitize, fallback)
* `tests/unit/test_skill_registry.py` — 14 tests
* `tests/integration/test_mcp.py` — 5 tests (subprocess, tools, mainnet guard)

Total: ~85 new tests, on top of the existing 64.
