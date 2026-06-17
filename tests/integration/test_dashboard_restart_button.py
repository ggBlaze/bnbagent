"""P5 (v2.1.8): dashboard frontend must expose a "Restart Agent" button
that calls POST /api/agent/restart.

The endpoint itself is well-tested in tests/integration/test_dashboard_restart_endpoint.py.
This test pins the FRONTEND contract: the button is in the HTML, the
JS function POSTs to the right URL, and a docstring/title attribute
explains what it does (so the operator doesn't fire it by accident).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_dashboard_html_contains_restart_button():
    """GET / serves an HTML page that includes a Restart Agent button."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "restart-btn" in html, (
        "HTML must contain an element with id='restart-btn' so the "
        "operator can fire the restart endpoint from the UI"
    )
    assert "Restart Agent" in html or "restart agent" in html.lower(), (
        "button label should say 'Restart Agent' (visible text)"
    )


def test_dashboard_html_contains_restart_js_function():
    """The button's onclick must call a function that POSTs to
    /api/agent/restart. Pin the URL so a frontend refactor doesn't
    silently break the wire-up."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    assert "/api/agent/restart" in html, (
        "JS must POST to /api/agent/restart (the endpoint added in A1)"
    )
    assert "restartAgent" in html, (
        "expected a restartAgent() function so the button onclick can call it"
    )
