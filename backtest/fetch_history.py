"""Fetch historical OHLCV from CMC into parquet for offline backtesting.

This is what builds the agent's training data. Run once before the hackathon
to populate data/parquet/ with 90d of top-20 BSC tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from connectors.cmc import CMCClient

log = logging.getLogger(__name__)


async def fetch_all(out_dir: str = "data/parquet", symbols: list[str] | None = None,
                    time_period: str = "hour", count: int = 24 * 90):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open("config/config.yaml"))
    cmc = CMCClient(x402_base=cfg["cmc"]["x402_base"], api_key=cfg["cmc"]["api_key"],
                    mode=cfg.get("mode", "testnet"))
    symbols = symbols or cfg["cmc"]["basket_symbols"][:20]
    for sym in symbols:
        try:
            r = await cmc.ohlcv_historical([sym], time_period=time_period, count=count)
            quotes = r.get("data", {}).get(sym, {}).get("quotes", [])
            if not quotes:
                log.warning(f"{sym}: no quotes")
                continue
            rows = []
            for q in quotes:
                rows.append({
                    "ts":   pd.to_datetime(q["timestamp"]),
                    "open": q["quote"]["USD"]["open"],
                    "high": q["quote"]["USD"]["high"],
                    "low":  q["quote"]["USD"]["low"],
                    "close": q["quote"]["USD"]["close"],
                    "volume": q["quote"]["USD"].get("volume", 0),
                })
            df = pd.DataFrame(rows).set_index("ts").sort_index()
            out_path = out / f"{sym}_{time_period}.parquet"
            df.to_parquet(out_path)
            log.info(f"{sym}: {len(df)} rows → {out_path}")
        except Exception as e:
            log.warning(f"{sym}: fetch failed: {e}")
    await cmc.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(fetch_all())
