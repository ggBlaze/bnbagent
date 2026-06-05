# BNB Agent — User Policy

The **User Policy** is the single file that defines everything the agent can and
cannot do. The user signs it **once** at startup. After that, the agent runs
fully autonomously — every order is checked against the policy and rejected if
it would violate it.

## The policy file (`config/policy.yaml`)

The policy is a versioned YAML file with three sections:

1. **`global_risk`** — hard caps on drawdown, leverage, per-trade risk, daily trades
2. **`sleeve_allocations`** — what fraction of equity each sleeve manages
3. **`sleeves`** — per-sleeve parameters (signals, sizing, exits)
4. **`allowlist`** — which tokens and venues the agent is allowed to use
5. **`fees`** — daily CMC spend cap, max gas price
6. **`signature`** — EIP-191 signature over `keccak256(canonical_json(body))`

See [`config/policy.yaml`](../config/policy.yaml) for a complete example and
[`config/policy.schema.json`](../config/policy.schema.json) for the JSON schema.

## Why a YAML policy?

- **Human-auditable** — judges can read it in 30 seconds
- **Versioned** — every change is a new semver version, archived automatically
- **Externally signed** — the EIP-191 signature is a real crypto commitment
- **JSON-schema validated** — invalid policies are rejected at boot

## How signing works

```bash
# Generate a private key for dev (or use TWAK keystore for prod)
export BNBAGENT_PRIVATE_KEY=0x...

# Sign the policy
bash scripts/sign_policy.sh
```

The script computes:
```
digest = keccak256(canonical_json(policy_without_signature))
sig    = eip191_personal_sign(digest, wallet)
```

And writes the signature back into `policy.yaml` under `signature:`.

## How verification works

The agent calls `verify_policy(policy, evaluator_address)` at every boot.
It re-computes the digest, recovers the signer of the signature, and refuses
to start if it doesn't match the `evaluator_address` field.

```bash
# Verify the current policy
python -m policy.policy_verify
# → prints VERIFIED or INVALID
```

## How the risk engine uses the policy

Every proposed trade goes through `circuit_breaker_check()` in
[`core/risk.py`](../core/risk.py). The function checks (in order):

1. **Cooldown** — if a previous breach put us in cooldown, refuse
2. **Daily loss** — `(day_start_equity - current) / day_start ≥ 3%` → refuse
3. **Max drawdown** — `(peak - current) / peak ≥ 8%` → refuse
4. **Per-trade risk** — `risk / equity > 1%` → refuse
5. **Single position** — `notional / equity > 15%` → refuse
6. **Gross leverage** — `gross / equity > 2x` → refuse
7. **Allowlist** — `symbol not in bsc_tokens` → refuse
8. **Sleeve position cap** — per-sleeve max
9. **Sleeve enabled** — sleeve must be enabled in policy

This is the only enforcement of the policy. Every other module that wants to
place a trade MUST call `agent.allow_trade(proposed)` and respect the result.

## Bumping a policy version

When you want to change the policy (e.g. relax a constraint), bump the version:

```bash
python -m policy.policy_version --level minor     # 1.0.0 → 1.1.0
```

The old version is archived to `config/policy-archive/policy-<old>.yaml`.
The new version is signed fresh.

## Rule-adherence score

For the hackathon's "rule adherence" judging axis, BNB Agent produces a
**rule_adherence** counter on every window's deliverable (in
[`jobs/submit_sleeve.py`](../jobs/submit_sleeve.py)):

```json
"rule_adherence": {
  "per_trade_risk_breaches": 0,
  "single_position_breaches": 0,
  "leverage_breaches": 0,
  "daily_loss_breaches": 0,
  "allowlist_violations": 0
}
```

A score of all zeros means perfect adherence — every proposed trade passed the
risk engine without modification.
