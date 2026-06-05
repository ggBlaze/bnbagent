# BNB Agent — Operations

This is the live-rail manual for the dashboard. The dashboard runs on
`http://localhost:8000` when you start the system with `bash bnbagent`.

## Live pane

| Section | Source | Refresh |
|---|---|---|
| Hero strip (Equity / Day PnL / DD / Open / Sharpe) | `/api/stats` | 1.5s |
| Equity curve (SVG) | `/api/equity-series` | 1.5s |
| Sleeve allocation cards | `/api/stats` + `/api/policy` | 1.5s |
| CMC x402 microcharge ledger | `/api/cmc-charges` | 1.5s |
| TWAK-signed transactions | `/api/txs` | 1.5s |
| ERC-8004 identity | `/api/identity` | 1.5s |
| ERC-8183 jobs | `/api/jobs` | 1.5s |
| Recent trades | `/api/trades` | 1.5s |

All updates are non-blocking; the UI never freezes waiting for the agent.

## Config pane

Edits here write a **dashboard intent** to `~/.bnbagent/control.json`. The
agent's heartbeat (`core.tick.Agent._heartbeat`) reads that file once per
second and applies:

- **Sleeve toggles** — flip A/B/C on or off. Sleeve B + C respect the post-loss
  cooldown so flipping a sleeve on does not trigger a wave of stale signals.
- **Risk overrides** — `daily_loss_circuit_breaker_pct`, `per_trade_risk_pct`,
  `max_gross_leverage`, `max_single_position_pct`. Values are validated to
  match the policy schema bounds (0.1–20 / 0.1–5 / 1–5 / 1–50).

The agent never re-signs the policy on a dashboard edit — the on-disk
`policy.yaml` is still the source of truth. Dashboard edits are a **runtime
override** that is logged in `/api/control-log` and visible in the right rail.

## Logs pane

Live SSE stream of `logs/agent.log`. Capped at 500 lines in the viewport.

## Kill switch

A red **Engage Kill** button in the right rail sets `policy._kill_switch=true`.
The risk engine (`core.risk.circuit_breaker_check`) refuses every new order
with `"kill switch engaged"`. Existing positions are NOT force-closed — the
sleeve monitors still tick and will TP / stop on their own.

Press the same button (now labeled `◼ Kill Active · Resume`) to clear the
flag. The agent resumes on the next heartbeat.

## Health

```bash
curl http://localhost:8000/api/healthz
# {"status":"ok","ts":1717593600,"agent_updated_at":1717593601,"kill_switch":false}
```

Use this in your uptime monitor. The `agent_updated_at` is the timestamp of
the last agent heartbeat; if it stops advancing, the agent loop is wedged.
