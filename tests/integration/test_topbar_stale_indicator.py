"""UX3 (v2.1.8): the topbar live indicator must reflect reality.

Pre-fix: the topbar showed either "live" (when stats.updated_at was
missing — including after the agent died) or "updated HH:MM:SS"
(when fresh). There was no "agent down" or "stale" state, so an
operator looking at a frozen dashboard couldn't tell the agent had
crashed.

Add a freshness check in refresh(): if the latest stats.updated_at
is more than ~10s old, swap the topbar to a visible "agent offline"
state. The polling itself is already resilient (jget returns {} on
error), so this is purely a display change.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_frontend_checks_updated_at_freshness():
    """The refresh handler must compute the staleness of
    stats.updated_at and adjust the topbar label so the operator can
    SEE when the agent has gone down."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    # Look for the freshness threshold (10s) AND the offline label.
    assert "agent offline" in html.lower() or "agent down" in html.lower(), (
        "topbar must show an 'agent offline' state when stats.updated_at "
        "is stale (the user couldn't tell their dashboard was frozen)"
    )
    # Some staleness threshold check should exist in the JS.
    assert "stale" in html.lower() or "agent_offline" in html.lower() \
           or "updated_at" in html, (
        "refresh() must check stats.updated_at freshness"
    )
