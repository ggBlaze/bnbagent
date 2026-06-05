"""WebSocket stream helper — broadcasts dashboard state to all connected clients."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class StreamBus:
    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.add(ws)
        log.info("client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket):
        self._clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]):
        dead = []
        msg = json.dumps(payload, default=str)
        for c in list(self._clients):
            try:
                await c.send_text(msg)
            except Exception:
                dead.append(c)
        for d in dead:
            self.disconnect(d)


bus = StreamBus()
