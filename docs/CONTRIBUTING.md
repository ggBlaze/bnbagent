# Contributing to BNB Agent

Thanks for your interest. PRs welcome.

## Dev setup

```bash
git clone <repo> bnbagent && cd bnbagent
bash install.sh                  # creates .venv, installs deps
source .venv/bin/activate        # or: /tmp/venv/bin/activate
export PYTHONPATH=$PWD
```

Run the tests:

```bash
pytest -q                        # 767/779 passing
pytest tests/unit/ -v            # verbose unit
pytest tests/integration/ -v     # MCP end-to-end
```

## Code style

- **Python 3.10+** syntax (PEP 604 unions, `match` is fine, no walrus abuse)
- **Type hints** on all public functions (`mypy` strict mode in CI is a goal, not enforced today)
- **Docstrings** on every public class and function (Google style)
- **Ruff** for linting (`pyproject.toml [tool.ruff]`, line-length 100)
- **No third-party SDKs** for LLM calls — use `httpx`. Keeps the dep surface tiny and the providers swappable.
- **Module-level constants** in SCREAMING_SNAKE_CASE; functions/variables in snake_case; classes in PascalCase.
- **Async-first**: anything that hits the network or the LLM is `async`. Sync helpers are fine for crypto / portfolio math.

## Architecture rules (please respect)

- **The risk engine (`core/risk.py`) is the only enforcer of the policy.** No code path may sign a tx without going through `agent.allow_trade()` (or `agent.review_trade()` then `sign_transaction`).
- **The LLM can only TIGHTEN / VETO / RECOMMEND.** Never loosen. Never bypass. The `agents/advisor.py` `_apply`, `agents/reviewer.py` review, and `agents/chat.py` `_tool_recommend` are the three safety envelopes.
- **The private key never leaves the host process.** Don't add features that need to send the key over the wire.
- **All config is externalized to YAML.** Don't hardcode RPC URLs, gas prices, token addresses, or perps venues. The runtime config is split into two files (v2.1.1): `config/config.yaml` (shipped defaults, tracked, immutable at runtime) and `config/local.yaml` (user-specific overrides, gitignored). Read via `core.config_paths.load_config()`, write via `core.config_paths.write_local()`. The shipped file is never mutated at runtime — only `git pull` or a fresh clone can change it.
- **Every runtime write must land in a gitignored location.** The principle: *all the user does with the repo does not affect the repo development.* Files the runtime creates or mutates (`config/local.yaml`, `config/policy.yaml`, `agents/token_module.yaml`, `~/.twak/`, `~/.bnbagent/`) are gitignored. The contract is pinned by `tests/unit/test_repo_cleanliness.py` — a new tracked write path will fail the test on CI. If you're adding a new write path, also add a test entry to `EXPECTED_GITIGNORED` and `RUNTIME_WRITE_PATHS` in that file.
- **The dashboard is a single HTML file.** Keep it that way (vendored js is fine; a build step is not). Add `// SPLIT-ME:` comments if a section is getting big — we can split after the contest.
- **Tests mirror the source layout.** New module under `agents/` → `tests/unit/test_agents_<name>.py`. New connector → `tests/unit/test_<connector>.py`.

## Test conventions

- **Class-based grouping** in test files (e.g. `class TestRisk:`)
- **One test per behavior.** Don't combine "can only tighten" + "hostile LLM doesn't raise" + "malformed JSON doesn't crash" into one big test.
- **Use the `FakeLLMClient`** in `tests/fixtures/llm.py` for any LLM-touching code. Don't hit real APIs in tests.
- **Replay-tape fixtures** for connector tests. `mode="replay"` short-circuits all network calls in `BSCClient.broadcast`, `CMCClient.call`, `PancakeV3.quote`, etc.
- **Hard-coded dev keys** in `tests/fixtures/wallets.py`. Never use real keys.
- **Test critical invariants explicitly.** For example: "the advisor's `_apply` must refuse a `new >= current` value" gets its own test that locks the invariant.

## Adding a new LLM layer (or persona)

1. Create the persona file: `agents/_pro_defaults/<name>.md` with the YAML front-matter (`name`, `version`).
2. Create the live copy: `agents/personas/<name>.md` (same content; will diverge over time).
3. Add a method or class to `agents/<role>.py` that:
   - Loads the persona via `agents.base.PersonaLoader`
   - Calls `llm_complete` / `llm_stream` from `agents.base`
   - Enforces a hard safety envelope in code (never trust the LLM)
4. Register the new agent in `agents/providers.yaml` under `agents.<name>`.
5. Add `tests/unit/test_<role>.py` with `FakeLLMClient` fixtures.

## Adding a new Skill

1. Create the file under `skills/notification/<name>.py` or `skills/data/<name>.py`.
2. Class with class attrs `name`, `category`, `description`, `version`, `cost_per_call_usdc`, `requires` (list of env var names).
3. Implement `async setup(components)`, `async run(ctx, **kwargs) -> dict`, `def status() -> dict`.
4. Add `tests/unit/test_skill_registry.py` cases for the new skill.

The `SkillRegistry.discover()` picks up any class with a non-empty `name` class attribute and a callable `run` method.

## Adding a new MCP tool

1. Add a `Tool(...)` entry to the `list_tools` handler in `agent_mcp/mcp_server.py`.
2. Add a `call_tool` branch for it.
3. Add a test in `tests/integration/test_mcp.py` (spawn the server, call the tool, assert the result).

## PR process

1. **Open an issue first** for non-trivial changes. Bug fixes and one-liners are fine without.
2. **Branch from `main`**. Branch name: `feat/...` or `fix/...`.
3. **Tests must pass** locally before pushing. CI will run the same set.
4. **Don't bump the version** in `pyproject.toml` or `agents/_pro_defaults/*/version` — that's a release-tag job.
5. **Squash-merge** for a clean history.

## Reporting a security issue

**Do not open a public issue.** Email `security@bnbagent.example` (or your
fork's equivalent) with a description. We aim to respond within 24h
during the contest window.
