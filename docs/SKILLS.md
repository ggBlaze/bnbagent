# BNB Agent — Skills Registry

A Skill is a **discoverable, hot-toggled module** that hooks into the
agent's lifecycle. Skills are how third parties (notification services,
data feeds, etc) extend BNB Agent without forking the repo.

## Three categories

| Category | Purpose | Examples |
|---|---|---|
| `notification` | Emit side effects to external services (Telegram, Discord, etc) | telegram_alert, farcaster_post, webhook_dispatch |
| `data` | Pull alternative signals (sentiment, on-chain metrics) | x_sentiment, cmc_global_filter, glassnode_onchain |
| `strategy` | (reserved) | future use |

## Built-in Skills (6)

### `telegram_alert` (notification)

DM a Telegram chat on every trade close. Rate-limited to 60/hour.

**Env:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
**Cost:** $0
**Events:** `trade_close`

```python
# In your trade-close handler:
await skill_registry.run_hook("trade_close",
                               components=components,
                               extra={"trade": trade_dict})
```

### `farcaster_post` (notification)

Auto-post PnL updates + agent events to a Farcaster / Warpcast account.
Rate-limited to 1 post/hour.

**Env:** `WARPCAST_KEY`
**Cost:** $0
**Events:** `trade_close`, `deploy`, `advisor`

### `webhook_dispatch` (notification)

Generic webhook dispatch — POST every event to a user-configured URL.
Useful for Zapier / n8n / Discord webhook integration.

**Env:** `WEBHOOK_URL`
**Cost:** $0
**Events:** all

### `x_sentiment` (data)

Sentiment score for top BSC tokens. Uses X API as primary, CMC trending
data as fallback (no X API key required).

**Env:** *none required* (CMC fallback always works)
**Cost:** $0.01/call (when using X API)
**Output:** `{"source": "x"|"cmc_fallback", "score": -1.0..1.0, "volume": int}`

### `cmc_global_filter` (data)

**The only Skill that writes.** Pauses all sleeves when CMC's global
market metrics signal a bear regime (24h market cap change < -3%).

**Env:** *none required* (uses CMC API)
**Cost:** $0.01/call
**Side effect:** writes `~/.bnbagent/control.json` with
`_source: "skill:cmc_global_filter"` to pause sleeves A/B/C

This is the most powerful Skill. Disable it if you don't want the
agent to ever auto-pause on market conditions.

### `glassnode_onchain` (data)

Stub for the contest. Returns a deterministic score so the UI works.
In production: real Glassnode API for exchange netflow, miner flows,
etc.

**Env:** *none required*
**Cost:** $0
**Output:** `{"stub": true, "score": -1.0..1.0, "window": "1h"}`

## Skill Protocol

```python
class Skill(ABC):
    name: str
    category: str         # "notification" | "data" | "strategy"
    description: str
    version: str
    cost_per_call_usdc: float
    requires: list[str]   # env var names that must be set for enable()

    async def setup(self, components: dict) -> None: ...
    async def run(self, ctx: SkillContext, **kwargs) -> dict: ...
    async def teardown(self) -> None: ...   # optional
    def status(self) -> dict: ...            # optional
```

`SkillContext` carries the event name, the portfolio, the policy, and
event-specific `extra` data.

## Discovery

`SkillRegistry.discover()` walks `skills/notification/` and `skills/data/`,
imports every module, and instantiates any class with a non-empty `name`
class attribute and a callable `run` method. New Skills are picked up
on next heartbeat.

## Enable / disable

```bash
# From the dashboard
POST /api/skills/telegram_alert/enable
POST /api/skills/cmc_global_filter/disable

# From the chat
"enable the telegram skill"
"disable the cmc_global_filter"

# Programmatically
reg = SkillRegistry()
reg.enable("telegram_alert")
reg.disable("x_sentiment")
```

State is persisted to `~/.bnbagent/skills.json`:

```json
{"enabled": ["telegram_alert"]}
```

## Env validation

`enable()` checks that all `requires` env vars are set. If `TELEGRAM_BOT_TOKEN`
is missing, `enable("telegram_alert")` raises `RuntimeError("skill
'telegram_alert' requires env: ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']")`.
The dashboard shows this error to the user with the missing env vars.

## Adding a new Skill

1. Create the file: `skills/notification/my_skill.py` (or `skills/data/`).
2. Define the class with the protocol above.
3. Restart the agent (or wait for the next heartbeat to re-discover).
4. Add tests in `tests/unit/test_skill_registry.py`.

The chat can call your Skill's `run` method via `enable_skill(name)` +
`run_hook(event, ...)` (the chat tool dispatcher handles this).

## Tests

`tests/unit/test_skill_registry.py` — 14 tests:

- `test_registry_discover_loads_builtins` — all 6 Skills found
- `test_registry_persists_enabled_state` — state survives restart
- `test_enable_missing_env_blocks` — can't enable a Skill without its API keys
- `test_disable_round_trip`
- `test_list_returns_ready_flag` — `ready: false` when env missing
- `test_unknown_skill_raises`
- `test_skill_categories` — `notification` vs `data`
- `test_telegram_skill_skips_non_close_events`
- `test_webhook_skill_skips_without_url`
- `test_x_sentiment_returns_cmc_fallback_score`
- `test_x_sentiment_skips_without_cmc`
- `test_cmc_global_filter_status`
- `test_glassnode_is_stub`
- `test_run_hook_calls_enabled_skills`
