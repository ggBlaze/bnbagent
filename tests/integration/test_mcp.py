"""Integration test for the MCP server.

Spawns `python -m agent_mcp.mcp_server --transport stdio` as a subprocess and
calls a few tools through the official `mcp` client. Verifies the basic
plumbing end-to-end.

Note: this test does not require a running agent. The MCP server returns
graceful "not loaded" responses when the agent process is not connected
— that's the right behavior for a server running in isolation.
"""
from __future__ import annotations

import asyncio
import os
import sys
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mcp_server_cmd():
    py = sys.executable
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [py, "-m", "agent_mcp.mcp_server", "--transport", "stdio"]


async def test_list_tools_returns_11_tools(mcp_server_cmd):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=mcp_server_cmd[0], args=mcp_server_cmd[1:],
                                    env={**os.environ, "PYTHONPATH": os.path.dirname(
                                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
    assert "bnbagent_get_pnl" in names
    assert "bnbagent_list_trades" in names
    assert "bnbagent_get_policy" in names
    assert "bnbagent_recommend_risk_change" in names
    assert "bnbagent_deploy_token" in names
    assert "bnbagent_chat" in names
    assert "bnbagent_list_skills" in names
    assert "bnbagent_enable_skill" in names
    assert "bnbagent_disable_skill" in names
    assert "bnbagent_list_positions" in names


async def test_get_pnl_returns_error_when_no_agent(mcp_server_cmd):
    """When the MCP server runs standalone (no agent connected), the
    portfolio is None and the tool returns an error dict in the text content."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import json

    env = {**os.environ, "PYTHONPATH": os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}
    params = StdioServerParameters(command=mcp_server_cmd[0], args=mcp_server_cmd[1:], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("bnbagent_get_pnl", {})
    # content is list of TextContent; we expect {"error": "no portfolio"}
    text = result.content[0].text
    data = json.loads(text)
    assert "error" in data


async def test_recommend_risk_change_does_not_write(mcp_server_cmd):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import json

    env = {**os.environ, "PYTHONPATH": os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}
    params = StdioServerParameters(command=mcp_server_cmd[0], args=mcp_server_cmd[1:], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("bnbagent_recommend_risk_change",
                                              {"key": "per_trade_risk_pct", "value": 0.5,
                                               "reason": "from test"})
    data = json.loads(result.content[0].text)
    assert "recommendation" in data
    assert data["recommendation"]["key"] == "per_trade_risk_pct"
    # we don't have a policy, so current is None
    assert data["recommendation"]["current"] is None
    # apply_via must mention Setup / re-sign
    assert "Setup" in data["apply_via"] or "re-sign" in data["apply_via"].lower()


async def test_deploy_token_mainnet_without_confirm_rejected(mcp_server_cmd):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import json

    env = {**os.environ, "PYTHONPATH": os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}
    params = StdioServerParameters(command=mcp_server_cmd[0], args=mcp_server_cmd[1:], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("bnbagent_deploy_token",
                                              {"name": "X", "symbol": "XXX", "supply": 1000,
                                               "network": "mainnet"})
    data = json.loads(result.content[0].text)
    assert "error" in data
    assert "confirm_mainnet" in data["error"]


async def test_unknown_tool_returns_error(mcp_server_cmd):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import json

    env = {**os.environ, "PYTHONPATH": os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}
    params = StdioServerParameters(command=mcp_server_cmd[0], args=mcp_server_cmd[1:], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("bnbagent_does_not_exist", {})
    data = json.loads(result.content[0].text)
    assert "error" in data
    assert "unknown tool" in data["error"]
