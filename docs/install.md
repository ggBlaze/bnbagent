# BNB Agent — Install

One command. Idempotent. Safe to re-run.

```bash
git clone <your-private-repo> bnbagent && cd bnbagent
bash install.sh
```

What it does:

1. Creates `.venv/` (Python 3.10+) and installs the local package in editable mode + test extras.
2. Runs `npm install` for `@trustwallet/cli` (used by the keystore CLI path; the agent
   also ships a pure-Python keystore path so the agent runs even without Node).
3. Generates `config/policy.yaml` with an **ephemeral dev key** and signs it
   with EIP-191. In production, re-sign with your real evaluator key:
   `python -m policy.policy_sign --pk 0xYOUR_KEY`.
4. Verifies the policy signature with `python -m policy.policy_verify`.
5. Prints the next command:

```
Next step — start the agent + dashboard with:
    bash bnbagent
```

## Run

| Command | What it does |
|---|---|
| `bash bnbagent` | start the agent + dashboard on **http://localhost:8000** (single terminal, Ctrl+C stops both) |
| `bash bnbagent --replay` | run a 7-day synthetic replay; report at `data/reports/replay.html` |
| `bash bnbagent --repl` | open a Python REPL with `p = boot(...)` pre-loaded |

## Production wallet

Set the env var before starting:

```bash
export TWAK_KEYSTORE=$HOME/.twak/wallet.json
export TWAK_PWD=...
python -m policy.policy_sign       # re-sign policy with the TWAK key
bash bnbagent                       # agent signs every tx with TWAK
```

Or, for dev:
```bash
export BNBAGENT_PRIVATE_KEY=0x...   # dev only
bash bnbagent
```

## Environment variables (override config.yaml)

| Var | Default | Notes |
|---|---|---|
| `BNBAGENT_DASHBOARD_PORT` | `8000` | dashboard HTTP port |
| `BNBAGENT_EQUITY`         | `100`  | starting USDC equity |
| `BNBAGENT_LOG_LEVEL`      | `INFO` | agent log level |
| `BNBAGENT_CONTROL_FILE`   | `~/.bnbagent/control.json` | dashboard → agent IPC |
| `TWAK_KEYSTORE`           | —      | TWAK JSON keystore path |
| `TWAK_PWD`                | —      | TWAK keystore password |
| `BNBAGENT_PRIVATE_KEY`    | —      | dev-only fallback |
| `CMC_API_KEY`             | —      | optional Pro API key (x402 otherwise) |
