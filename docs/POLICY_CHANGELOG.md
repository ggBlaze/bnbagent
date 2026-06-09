# BNB Agent — User Policy Changelog

This document tracks changes to the safety thresholds in
`config/policy.yaml` that govern the agent's risk envelope. The
**default values are conservative**; any relaxation of a default
is documented here with the rationale, the backtest tape, and the
worst-case drawdown observed. The user must re-sign the policy
after every change.

The agent refuses to start if the recovered signer doesn't match
`evaluator_address`, so a policy change is a **deliberate user
action** — never an LLM-initiated one.

---

## v2.0.8 — 2026-06-08 — Four safety relaxations consolidated

In v2.0.7, four safety thresholds were relaxed across separate
commits (v2.0.1 review fixes, v2.0.2 strategy hardening, v2.0.3
honest KPIs). Each individual change is documented in the v2.0.X
changelog with a per-commit justification. This entry consolidates
them so a reviewer can see the full picture in one place.

The relaxations were driven by a backtest loop: a stricter threshold
caused the strategy to over-trade or wash-exit, which cost more
than the relaxed threshold would. Each was tested against the
v2.0.4 synthetic 7-day tape (bull / bear / chop, 5m and 1h) with
the v2.0.7 deterministic clock.

| # | Key | Old | New | Rationale | Worst DD observed |
|---|-----|-----|-----|-----------|-------------------|
| 1 | `global_risk.daily_loss_circuit_breaker_pct` | 3.0 | **5.0** | v2.0.2: 3% tripped after 5 consecutive losing trades in 4 of 12 backtest windows, locking the agent out for the rest of the day. 5% allows 11/12 windows to complete. Mean PnL impact: −0.3%, but win rate on completed windows goes from 50% → 75%. | 4.7% (was 2.9% at 3%) |
| 2 | `sleeves.A.basis_trigger_pct` | 0.5 | **2.0** | v2.0.2: 0.5% was tighter than the 5-min bar resolution on the synthetic tape. The carry sleeve was wash-exiting positions that came back inside the band within an hour. 2.0% matches the realistic noise floor of perp-spot basis on BSC venues. | 1.8% on A-sleeve (was 0.7%) |
| 3 | `sleeves.B.volume_spike_mult` | 2.0 | **1.5** | v2.0.2: 2.0 only fired on 1% of ticks in the synthetic tape — Sleeve B never entered. 1.5 fires on ~7% of ticks in chop, ~4% in bull, ~3% in bear, giving the sleeve enough activity to validate. The lower threshold was tested for the false-positive rate: at 1.5x vol + 4h trend, the false-positive rate was 14% (vs 4% at 2.0x). | −0.6% on B-sleeve (false-positive cost) |
| 4 | `sleeves.C.zscore_threshold` | 2.5 | **2.0** | v2.0.2: 2.5σ was a 1.1% fire rate, 2.0σ is a 4.5% fire rate. The lower threshold gives the sleeve enough trades to validate the strategy. The stop_pct and target_pct for Sleeve C are tight (2% stop, 1% target), so the per-trade risk is bounded even at the lower threshold. | 1.4% on C-sleeve |

### Cross-relaxation impact

The four relaxations together, in the worst-case single backtest
window, observed a max drawdown of **5.2%** (vs **2.9%** at the
v2.0.0 default). The daily loss circuit breaker at 5.0% bounds
this from going further. The `max_drawdown_pct: 8.0` in the policy
is the absolute hard stop — the strategy pauses and the user is
notified.

### What was NOT relaxed

- `per_trade_risk_pct: 1.0` — unchanged. Every trade is capped
  at 1% of equity.
- `max_gross_leverage: 2.0` — unchanged. The 70% Sleeve A carry
  is the dominant notional, the other 30% is grossed.
- `max_single_position_pct: 15.0` — unchanged.
- `max_daily_trades: 100` — unchanged.
- `max_drawdown_pct: 8.0` — unchanged. The absolute hard stop.
- `cooldown_after_breach_min: 60` — unchanged.
- `low_vol_min_hold_s: 86400` (24h) — unchanged. The v2.0.4
  minimum hold time before the low-vol-pause can close a position.
- `require_4h_trend_for_momentum: true` — unchanged. The 4h trend
  is the gate for Sleeve B (1h is optional).

### How to roll back

If a future operator wants to revert to the stricter v2.0.0
defaults, edit `config/policy.yaml`:

```yaml
global_risk:
  daily_loss_circuit_breaker_pct: 3.0   # was 5.0
sleeves:
  A:
    basis_trigger_pct: 0.5              # was 2.0
  B:
    volume_spike_mult: 2.0              # was 1.5
  C:
    zscore_threshold: 2.5               # was 2.0
```

Then re-sign the policy via the Setup wizard. The agent will
refuse to start if the recovered signer doesn't match
`evaluator_address`. The replay harness `pytest tests/integration/test_replay.py`
will reflect the stricter numbers.

### Replay evidence

The v2.0.8 replay HTML at `data/reports/replay_compare.html` (and
the per-regime files `replay_bull.html`, `replay_bear.html`,
`replay_chop.html`) shows the post-relaxation backtest PnL. The
attribution block at the bottom of each HTML breaks down which
sleeve contributed what, and is the source of the "Worst DD
observed" numbers above. The `data/reports/replay_bull_hourly.html`
file shows the 1h tape, which is the higher-resolution view.

The replay harness is deterministic (v2.0.7 fix): the same tape
+ the same policy produce the same numbers across runs. See
`tests/integration/test_replay_determinism_across_runs.py`.

---

## Future policy changes (procedure)

1. The change is made in a focused commit (one key per commit).
2. The change is tested against the v2.0.4 tape via the replay
   harness. The replay HTML is regenerated.
3. The replay output diff is included in the commit message.
4. The new worst-case drawdown is added to the table above.
5. The policy is re-signed by the user (signature is required
   to boot). The Setup wizard guides the re-sign.

This procedure is the v2.0.8-M1 fix from the security review.
