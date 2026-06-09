"""Unit tests for the persona loader + personas shipped in _pro_defaults."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agents.base import (
    PersonaLoader, _read_md, _sha256, _write_md,
    PRO_DEFAULTS_DIR, SHIPPED_PERSONAS_DIR, RUNTIME_PERSONAS_DIR,
    bootstrap_personas, list_persona_names, read_persona_raw,
)


# --- v2.0.8-M7: chat persona skill-toggle discipline ----------------------

def test_chat_persona_has_skill_toggle_discipline():
    """The chat persona must contain the v2.0.8-M7 skill-toggle discipline
    section. This locks the constraint so a future edit can't silently
    remove it.
    """
    path = PRO_DEFAULTS_DIR / "chat.md"
    if not path.exists():
        pytest.skip("chat.md not in _pro_defaults")
    text = path.read_text()
    assert "Skill-toggle discipline" in text, \
        "chat.md is missing the 'Skill-toggle discipline' section (v2.0.8-M7)"
    assert "cmc_global_filter" in text, \
        "chat.md must name cmc_global_filter as the control-file-writing exception"
    # The constraint is specific: it requires a confirmation BEFORE
    # enable_skill is called for cmc_global_filter
    assert "REPEAT BACK" in text or "confirm" in text.lower(), \
        "chat.md must require an explicit confirmation before enabling cmc_global_filter"

def test_chat_persona_names_control_file_side_effect():
    """The chat persona must explicitly call out that cmc_global_filter
    writes to the control file. This is the only Skill that does."""
    path = PRO_DEFAULTS_DIR / "chat.md"
    if not path.exists():
        pytest.skip("chat.md not in _pro_defaults")
    text = path.read_text()
    assert "control file" in text.lower(), \
        "chat.md must mention that cmc_global_filter writes to the control file"
    assert "risk cap" in text.lower() or "global_risk" in text, \
        "chat.md must name what cmc_global_filter can override"


@pytest.fixture
def tmp_personas(tmp_path, monkeypatch):
    """Redirect persona directories to tmp_path for isolation."""
    pro = tmp_path / "_pro_defaults"
    ship = tmp_path / "personas"
    runtime = tmp_path / "runtime"
    for d in (pro, ship, runtime):
        d.mkdir()
    # write a pro default for advisor
    (pro / "advisor.md").write_text("---\nname: advisor\nversion: 1.0.0\n---\nPro advisor body.\n")
    (pro / "reviewer.md").write_text("---\nname: reviewer\nversion: 1.0.0\n---\nPro reviewer body.\n")
    (pro / "chat.md").write_text("---\nname: chat\nversion: 1.0.0\n---\nPro chat body.\n")
    monkeypatch.setattr("agents.base.PRO_DEFAULTS_DIR", pro)
    monkeypatch.setattr("agents.base.SHIPPED_PERSONAS_DIR", ship)
    monkeypatch.setattr("agents.base.RUNTIME_PERSONAS_DIR", runtime)
    return pro, ship, runtime


# --- front-matter parser ---------------------------------------------------

def test_read_md_with_frontmatter():
    p = Path("/tmp/_test_p.md")
    p.write_text("---\nname: x\nversion: 1\n---\nbody here\n")
    fm, body = _read_md(p)
    assert fm == {"name": "x", "version": 1}
    assert body.strip() == "body here"
    p.unlink()


def test_read_md_without_frontmatter():
    p = Path("/tmp/_test_p.md")
    p.write_text("just body\n")
    fm, body = _read_md(p)
    assert fm == {} and body == "just body\n"
    p.unlink()


def test_sha256_deterministic():
    assert _sha256("abc") == _sha256("abc")
    assert _sha256("abc") != _sha256("abcd")


def test_write_md_roundtrip(tmp_path):
    p = tmp_path / "x.md"
    _write_md(p, {"name": "x", "version": 2}, "hello body\n")
    fm, body = _read_md(p)
    assert fm == {"name": "x", "version": 2}
    assert body.strip() == "hello body"


# --- bootstrap -------------------------------------------------------------

def test_bootstrap_seeds_runtime_and_shipped(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    assert (ship / "advisor.md").exists()
    assert (runtime / "advisor.md").exists()
    assert (ship / "chat.md").exists()


def test_bootstrap_does_not_overwrite_existing(tmp_personas):
    pro, ship, runtime = tmp_personas
    (runtime / "advisor.md").write_text("USER OVERRIDE")
    bootstrap_personas()
    # runtime file is preserved (user edit)
    assert (runtime / "advisor.md").read_text() == "USER OVERRIDE"
    # shipped dir IS populated even if runtime was
    assert (ship / "advisor.md").exists()


# --- loader ----------------------------------------------------------------

def test_loader_loads_pro_default(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    p = PersonaLoader("advisor", runtime_dir=runtime, shipped_dir=ship, pro_dir=pro)
    persona = p.load()
    assert persona.system == "Pro advisor body."
    assert persona.diverged is False
    assert persona.version == "1.0.0"


def test_loader_diverged_flag(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    # user edits the runtime file
    (runtime / "advisor.md").write_text("USER EDIT\n")
    p = PersonaLoader("advisor", runtime_dir=runtime, shipped_dir=ship, pro_dir=pro)
    persona = p.load()
    assert persona.diverged is True


def test_loader_reset_restores_pro_default(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    (runtime / "advisor.md").write_text("USER EDIT\n")
    p = PersonaLoader("advisor", runtime_dir=runtime, shipped_dir=ship, pro_dir=pro)
    assert p.load().diverged is True
    persona = p.reset_to_pro()
    assert persona.diverged is False
    assert persona.system == "Pro advisor body."


def test_loader_save_user(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    p = PersonaLoader("advisor", runtime_dir=runtime, shipped_dir=ship, pro_dir=pro)
    p.save_user("my new persona", version="2.0.0")
    persona = p.load()
    assert persona.system == "my new persona"
    assert persona.version == "2.0.0"
    assert persona.diverged is True


def test_list_persona_names_returns_all(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    names = list_persona_names()
    assert "advisor" in names
    assert "reviewer" in names
    assert "chat" in names


def test_read_persona_raw_returns_text(tmp_personas):
    pro, ship, runtime = tmp_personas
    bootstrap_personas()
    text = read_persona_raw("chat")
    assert "Pro chat body" in text
