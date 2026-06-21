# Real perps execution — per-venue implementation checklist

**Status (v2.1.8):** the perps connector is fully scaffolded with a
pluggable interface (`connectors/venues/base.py` + `registry.py`), but
**none of the 4 venues have a real HTTP client yet**. Until you fill in
`connectors/venues/aster.py` (and the others), `sleeve_a_carry` will:

- In `mode=mainnet`: **raise `NotImplementedError`** the moment it tries
  to open a perp short. The portfolio's `paper_pnl_usdc` stays at 0
  because no fills happen.
- In `mode=testnet` / `mode=replay`: use the paper-stub path
  (`connectors/venues/paper_stub.py`) — every order returns immediately
  with `is_paper=True`. Stats show paper-vs-real split.

This document is the contract you'll fill in per venue.

## What's already done (no work needed)

- `connectors/venues/base.py` — `BaseVenueClient` interface with 4 abstract
  methods + 3 data classes (`VenueOrderResult`, `VenuePosition`,
  `VenueOrderError`).
- `connectors/venues/registry.py` — `VenueRegistry.register(name, cls)`
  picks the right client at call time.
- `connectors/venues/paper_stub.py` — `PaperStubClient` (used for
  testnet/replay automatically).
- 4 venue stub files: `aster.py`, `killex.py`, `apollox.py`, `mux.py`,
  each registering a class that raises `NotImplementedError` with a
  clear message pointing here.
- `connectors/bnb_sdk.py:Perps.open_short/close_short/reduce_short` —
  refactored to call into `VenueRegistry.get(venue, config)` and the
  resulting `BaseVenueClient.place_order/close_position/reduce_position`.
- Per-trade `is_paper` flag plumbed through `Position` → `closed_trades`
  → `portfolio.stats()['paper_pnl_usdc' | 'real_pnl_usdc']`.

## Per-venue checklist

For each venue (aster / killex / apollox / mux):

1. **Find the API docs.** Likely URLs (verify against the venue's
   current docs page before integrating):
   - Aster:   https://docs.aster.finance/
   - KiloEx:  https://docs.kiloex.io/
   - ApolloX: https://api.apollox.finance (Binance USD-M compatible)
   - MUX:     https://api.mux.network/

2. **Decide auth.** Aster uses HMAC-SHA256 signed payloads; ApolloX
   uses Binance-style `X-MBX-APIKEY` + signed query string; MUX uses
   bearer-token; KiloEx varies. Implement `_auth_headers(self, method,
   path, body)` in your venue client.

3. **Implement the 4 abstract methods:**
   - `place_order(symbol, side, size_usd, leverage, collateral_usdc,
     *, client_order_id=None) -> VenueOrderResult`
     - Map `size_usd` → venue's qty/size unit (most use base-asset qty
       derived from mark price × leverage).
     - Return `venue_order_id`, `filled_price` (None if not yet filled),
     `is_paper=False`, and `raw=response_dict`.
     - On venue rejection (insufficient margin, bad symbol, rate limit),
       raise `VenueOrderError(<message>)`.
   - `close_position(symbol, *, venue_order_id=None) -> VenueOrderResult`
   - `reduce_position(symbol, factor, *, venue_order_id=None) -> VenueOrderResult`
   - `get_position(symbol) -> VenuePosition | None`
   - `get_mark_price(symbol) -> Decimal | None`

4. **Add a unit test.** Pattern in `tests/unit/test_bnb_sdk.py`:
   ```python
   def test_aster_place_order(monkeypatch):
       captured = {}
       def fake_post(url, headers=None, json=None, timeout=None):
           captured["url"] = url
           captured["body"] = json
           captured["headers"] = headers
           class _R: status_code = 200
           def json(_self): return {"orderId": "0xabc", "avgPrice": "1735.93"}
           return _R()
       monkeypatch.setattr("httpx.post", fake_post)
       client = AsterVenueClient({"api_key": "k", "api_secret": "s"})
       result = client.place_order("ETH", "short", Decimal("100"), 1.0,
                                    Decimal("100"))
       assert result.venue_order_id == "0xabc"
       assert result.is_paper is False
       assert "X-Signature" in captured["headers"]  # auth header present
   ```

5. **End-to-end test against the venue's testnet.** Open a $5 short on
   ETH, verify the order appears on the venue's UI, close it, reconcile
   the fill_price against the venue's reported fill. **Do not skip this
   step on mainnet** — the venue's API can drift from its docs.

6. **Update `perps.candidates`** in `config/config.yaml` if you don't
   want this venue on the candidates list, OR set
   `perps.<venue>.enabled: false` in `config/local.yaml` to keep it
   listed but skipped at runtime.

## Configuration

Once a venue client is implemented, configure it in `config/local.yaml`:

```yaml
perps:
  candidates:
    - aster
    - killex
  aster:
    api_key: "your-aster-api-key"
    api_secret: "your-aster-api-secret"
  killex:
    api_key: "your-killex-api-key"
    api_secret: "your-killex-api-secret"
```

`VenueRegistry.get("aster", config)` will pull `api_key`/`api_secret`
from `config["aster"]` (with fallbacks to top-level `config["api_key"]`
if you prefer a single-key mode).

## What sleeve A does today

`sleeve_a_carry.py:_rebalance()` calls `self.perps.open_short(venue,
market, size_usd, leverage, collateral_usdc)`. The Perps class routes
this to the venue client registered for `venue`. In `mode=testnet` /
`mode=replay` it routes to `PaperStubClient` (always `is_paper=True`).
In `mode=mainnet` it routes to the registered venue client (raises
`NotImplementedError` until you implement it).

## What happens once a venue is implemented

1. `sleeve_a_carry` calls `perps.open_short(...)` — gets back a
   `SignedTx` wrapper containing a `VenueOrderResult` with `is_paper=False`.
2. The portfolio's `add_position(...)` is called with the new
   `Position(is_paper=False, ...)`.
3. On close, `portfolio.close_position(...)` records
   `is_paper=False` in the trade dict.
4. Stats show this PnL under `real_pnl_usdc` (not `paper_pnl_usdc`).

## Risks / things to watch

- **Settlement latency.** Perp venues settle asynchronously. A
  `close_position` call may return before the venue confirms the fill.
  Reconcile via `get_position` before trusting `is_paper=False` for
  accounting.
- **Funding accrual.** Carries accrue funding every 8h. If the venue
  has its own funding ledger (most do), `get_position` returns it
  inside `VenuePosition.unrealized_pnl_usdc`; the portfolio should NOT
  double-count it.
- **API key leakage.** The keys in `config/local.yaml` are gitignored
  but live on disk. The keystore at `~/.twak/wallet.json` is more secure.
  If you want venue keys in the keystore too, extend the keystore
  schema and the loader in `connectors/keystore.py`.