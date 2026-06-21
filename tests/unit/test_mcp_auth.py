"""Tests for the v2.0.8-M3 MCP server token auth.

M-3 was that the MCP server (when run with --transport sse) bound
0.0.0.0 with no auth, exposing 11 tools (incl. skill toggles that
write to the control file) to anyone who reached the port.

Fix:
- Default --host is now 127.0.0.1 (was 0.0.0.0).
- Optional BNBAGENT_MCP_TOKEN env var enforces Bearer auth. If set,
  every SSE / messages request must carry a matching
  Authorization: Bearer *** header. If unset, a WARNING is
  logged on startup and the server accepts unauthenticated
  requests (safe for 127.0.0.1).

v2.1.6 (Aura): The middleware is now a PURE ASGI callable (no
BaseHTTPMiddleware) to avoid the body-stream assertion bug that
fires when a streaming SSE response is followed by a 404 on a
different request. These tests exercise the ASGI call surface
directly (scope / receive / send), not the old dispatch API.
"""
import pytest

from agent_mcp.mcp_server import _TokenAuthMiddleware


def _build_scope(auth_header: str | None) -> dict:
    headers = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode()))
    return {
        "type": "http",
        "method": "GET",
        "path": "/sse",
        "headers": headers,
        "query_string": b"",
    }


class _CaptureSend:
    """Captures the ASGI messages a middleware sends.

    Mimics the ASGI `send` callable; records the http.response.start
    status/headers and the http.response.body chunks so tests can
    assert on them.
    """

    def __init__(self):
        self.start = None
        self.bodies = []

    async def __call__(self, message):
        if message["type"] == "http.response.start":
            self.start = {"status": message["status"], "headers": message["headers"]}
        elif message["type"] == "http.response.body":
            self.bodies.append(message.get("body", b""))


@pytest.mark.asyncio
class TestTokenAuth:
    async def _run(self, token, auth_header):
        """Invoke the pure-ASGI middleware with a fake downstream.

        Returns (downstream_called, send_capture). The downstream is a
        tiny coroutine that flips a flag if reached; the middleware
        either calls it (pass-through) or sends a 401 response itself
        (reject).
        """
        downstream_called = False

        async def fake_app(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = _TokenAuthMiddleware(app=fake_app, token=token)
        scope = _build_scope(auth_header)
        send = _CaptureSend()

        async def empty_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await mw(scope, empty_receive, send)
        return downstream_called, send

    async def test_no_token_passes(self):
        """Unset token → all requests pass through (localhost is safe)."""
        called, capture = await self._run(None, None)
        assert called is True
        assert capture.start is not None
        assert capture.start["status"] == 200

    async def test_correct_bearer_passes(self):
        called, capture = await self._run("secret", "Bearer secret")
        assert called is True
        assert capture.start is not None
        assert capture.start["status"] == 200

    async def test_wrong_bearer_401(self):
        called, capture = await self._run("secret", "Bearer wrong")
        assert called is False
        assert capture.start["status"] == 401
        # Body is a JSON error message
        body = b"".join(capture.bodies)
        assert b"Bearer" in body or b"error" in body

    async def test_missing_auth_401(self):
        called, capture = await self._run("secret", None)
        assert called is False
        assert capture.start["status"] == 401

    async def test_basic_auth_401(self):
        """Basic auth is not Bearer auth, must reject."""
        called, capture = await self._run("secret", "Basic dXNlcjpwYXNz")
        assert called is False
        assert capture.start["status"] == 401

    async def test_empty_bearer_401(self):
        """'Bearer ' with no token must reject."""
        called, capture = await self._run("secret", "Bearer ")
        assert called is False
        assert capture.start["status"] == 401

    async def test_bearer_no_space_401(self):
        """'BearerX' (no space) must reject."""
        called, capture = await self._run("secret", "BearerXsecret")
        assert called is False
        assert capture.start["status"] == 401


@pytest.mark.asyncio
class TestTokenAuthASGIPassThrough:
    """Verify the middleware doesn't break non-HTTP scopes (lifespan, ws)."""

    async def test_lifespan_passes_through(self):
        called = False

        async def fake_app(scope, receive, send):
            nonlocal called
            called = True
            assert scope["type"] == "lifespan"

        mw = _TokenAuthMiddleware(app=fake_app, token="secret")
        await mw({"type": "lifespan"}, lambda: None, lambda m: None)
        assert called is True
