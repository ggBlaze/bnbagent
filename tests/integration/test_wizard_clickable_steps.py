"""UX1 (v2.1.8): the wizard's numbered step indicators must be
clickable for navigation.

Today the user finishes the wizard and is locked on step 6 ("Ready"
or the live view). To fix a misconfigured mode/RPC/wallet they'd
have to "Reset Everything" and walk the whole wizard again. The step
indicators at the top (1-Network, 2-Wallet, 3-Data source, 4-Brain,
5-Sign Policy, 6-Ready) are visually present but display-only.

Make each clickable so the operator can jump backward (always) or
forward (to any step they've already passed). gotoStep() already
toggles `.active` without losing form values.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_step_indicators_have_onclick_handlers():
    """Each #stp-N div must call wizardJump(N) on click. Pin the
    function name so a refactor doesn't silently break the wire-up."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    for n in range(1, 7):
        marker = f'id="stp-{n}"'
        assert marker in html, f"step indicator {marker} missing from HTML"
        # The step <div> that has id=stp-N must also carry an onclick
        # that calls wizardJump(N). Locate the indicator's line and
        # check it contains wizardJump.
        line = next(
            ln for ln in html.splitlines()
            if marker in ln
        )
        assert f"wizardJump({n})" in line, (
            f"#stp-{n} must have onclick=wizardJump({n}); got: {line.strip()!r}"
        )


def test_wizard_jump_function_exists_in_js():
    """The handler must exist as a callable named wizardJump."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    assert "function wizardJump" in html, (
        "expected `function wizardJump(n)` so the onclick handlers resolve"
    )


def test_step_indicators_styled_as_clickable():
    """cursor:pointer so the operator knows they're interactive."""
    from dashboard.backend import main as dash
    client = TestClient(dash.app)
    html = client.get("/").text
    # The .step or .step .num should have cursor:pointer in a CSS rule.
    assert "cursor:pointer" in html or "cursor: pointer" in html, (
        "wizard step indicators must look clickable (cursor:pointer)"
    )
