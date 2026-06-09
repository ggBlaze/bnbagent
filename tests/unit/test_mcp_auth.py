"""Tests for the v2.0.8-M3 MCP server token auth.

M-3 was that the MCP server (when run with --transport sse) bound
0.0.0.0 with no auth, exposing 10 tools (incl. skill toggles that
write to the control file) to anyone who reached the port.

Fix:
- Default --host is now 127.0.0.1 (was 0.0.0.0).
- Optional BNBAGENT_MCP_TOKEN env var enforces Bearer auth. If set,
  every SSE / messages request must carry a matching
  Authorization: Bearer <token> header. If unset, a WARNING is
  logged on startup and the server accepts unauthenticated
  requests (safe for 127.0.0.1).

These tests cover the _TokenAuthMiddleware class directly.
"""
import pytest
from starlette.requests import Request
from starlette.responses import Response

from agent_mcp.mcp_server import _TokenAuthMiddleware


def _build_request(auth_header: str | None) -> Request:
    headers = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/sse",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


@pytest.mark.asyncio
class TestTokenAuth:
    async def _run(self, token, auth_header):
        mw = _TokenAuthMiddleware(app=None, token=token)
        request = _build_request(auth_header)
        called_next = False

        async def fake_next(req):
            nonlocal called_next
            called_next = True
            return Response("ok", status_code=200)

        result = await mw.dispatch(request, fake_next)
        return result, called_next

    async def test_no_token_passes(self):
        """Unset token → all requests pass through (localhost is safe)."""
        result, called = await self._run(None, None)
        assert result.status_code == 200
        assert called is True

    async def test_correct_bearer_passes(self):
        result, called = await self._run("secret", "Bearer secret")
        assert result.status_code == 200
        assert called is True

    async def test_wrong_bearer_401(self):
        result, called = await self._run("secret", "Bearer wrong")
        assert result.status_code == 401
        assert called is False

    async def test_missing_auth_401(self):
        result, called = await self._run("secret", None)
        assert result.status_code == 401
        assert called is False

    async def test_basic_auth_401(self):
        """Basic auth is not Bearer auth, must reject."""
        result, called = await self._run("secret", "Basic dXNlcjpwYXNz")
        assert result.status_code == 401
        assert called is False

    async def test_empty_bearer_401(self):
        """'Bearer ' with no token must reject."""
        result, called = await self._run("secret", "Bearer ")
        assert result.status_code == 401
        assert called is False

    async def test_bearer_no_space_401(self):
        """'BearerX' (no space) must reject."""
        result, called = await self._run("secret", "BearerXsecret")
        assert result.status_code == 401
        assert called is False
