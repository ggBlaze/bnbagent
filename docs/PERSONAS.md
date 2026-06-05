# BNB Agent — Personas

A **persona** is a markdown file that becomes the **system prompt** for
one of the 3 LLM layers (or the Token Module). Personas are:

- **Human-editable** — the user can edit them from the dashboard
- **Resettable** — one click restores the canonical pro default
- **Versioned** — frontmatter has a `version` field; mismatches show
  in the dashboard
- **Hot-reloaded** — the agent re-reads the file on `mtime` change
  (within 1 second of the dashboard's "save")
- **IPFS-pinned** — the pro default SHA-256 is recorded in the
  ERC-8004 identity metadata, so a remote MCP client can verify the
  personas are stock

## File format

Each persona is a markdown file with YAML front-matter:

```markdown
---
name: advisor
version: 1.0.0
pro_default_sha256: a4f8...c2d1   # auto-computed at build time
description: Layer 1 strategy advisor — can only TIGHTEN risk, never loosen.
---

You are the strategy advisor of an autonomous BSC trading agent called BNB Agent.

You run every 5 minutes. You observe the agent's state and the recent market, and
you return a small JSON object describing TIGHTENING actions (or `no_op`).

**HARD CONSTRAINT — the most important rule in your prompt:**

> The user has signed a `policy.yaml` that defines the agent's hard limits.
> You CANNOT raise any of those limits. You can only **tighten** them: ...
```

The body is the system prompt. Frontmatter is metadata for the
persona loader.

## Two directories

```
agents/
  _pro_defaults/
    advisor.md
    reviewer.md
    chat.md
    token_module.md
  personas/
    advisor.md       (seeded from _pro_defaults on first boot)
    reviewer.md
    chat.md
    token_module.md
```

The **pro defaults** are the canonical personas. They are also pinned
to IPFS via the ERC-8004 identity metadata (`personas_pro_sha256`) and
shipped in the repo at `v1.0.0`.

The **live personas** are user-editable copies. On first boot, the
agent copies `_pro_defaults/*` → `personas/*`. The dashboard always
reads from the live copies.

There is also a **runtime override** path at `~/.bnbagent/personas/` —
the user can edit there too, and that copy takes precedence (survives
`git pull`).

## Dashboard UX

**Chat pane → "view persona"** → modal opens with the current persona
.md. User can edit in a textarea, click "save", and the change is
written to `agents/personas/<name>.md`. The modal shows:

- The current sha256 vs the pro_default sha256
- A "diverged" badge if the user has changed anything
- A "reset to pro" button that copies `_pro_defaults/<name>.md` →
  `personas/<name>.md`

**Chat pane → "reset persona"** → same as "reset to pro" but
auto-confirmed (no dialog).

## Persona loader (technical)

```python
from agents.base import PersonaLoader
loader = PersonaLoader("advisor")
persona = loader.load()        # reads from runtime → shipped → pro (in that order)
print(persona.system)         # system prompt body
print(persona.diverged)       # sha256(user) != sha256(pro_default)
print(persona.sha256)         # current content hash
print(persona.pro_default_sha256)  # pro default hash
```

The loader caches `(path, mtime, sha256)` and re-reads only when the
file's mtime changes. Editor saves are detected within 1 second.

## Per-agent personas

| Agent | Persona file | Constraints |
|---|---|---|
| `advisor` | `agents/personas/advisor.md` | Can only TIGHTEN; JSON output enforced by schema + code |
| `reviewer` | `agents/personas/reviewer.md` | Can only VETO; confidence ≥ 0.70 required to allow |
| `chat` | `agents/personas/chat.md` | Read-only; tools it can call are limited to the 9 listed in `chat.py:TOOL_SPECS` |
| `token_module` | `agents/personas/token_module.md` | Must follow the module's own config (network, protocol, theme); mainnet requires explicit confirmation |

## Writing a good persona

A good persona:

1. **States the hard constraint up front** — the most important rule is
   the first one. The LLM is most attentive to early instructions.
2. **Defines the output format explicitly** — JSON schema, length limits,
   no-prose rule.
3. **Lists veto conditions or no-go behaviors** — the LLM needs a
   positive list of when to refuse.
4. **Tone and length matter** — short, direct personas produce shorter,
   more deterministic responses. The pro defaults are intentionally
   <300 words each.
5. **Avoid "loosen" or "raise" words** in the advisor's persona — the
   LLM is sensitive to lexical cues. The pro advisor persona says
   "tighten" 6 times and "loosen" 1 time (as a warning).

## Reset

```bash
# CLI
python -c "from agents.base import PersonaLoader; PersonaLoader('advisor').reset_to_pro()"

# Dashboard
Chat pane → view persona → reset to pro

# Or manually
cp agents/_pro_defaults/advisor.md agents/personas/advisor.md
```

The pro defaults are also in the git repo, so a `git checkout -- agents/personas/`
restores them too.
