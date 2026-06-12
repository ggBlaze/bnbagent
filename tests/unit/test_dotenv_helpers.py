"""Unit tests for the .env helpers used by the LLM API key UI."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dashboard.backend.main import (
    _get_env_var_from_dotenv,
    _set_env_var_in_dotenv,
    _DOTENV_PATH,
)


# --- _get_env_var_from_dotenv ----------------------------------------------

def test_get_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _get_env_var_from_dotenv("NOT_SET") == ""


def test_get_no_dotenv_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No .env file at all
    assert not _DOTENV_PATH.exists()
    assert _get_env_var_from_dotenv("ANYTHING") == ""


def test_get_simple_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text("FOO=bar\n")
    assert _get_env_var_from_dotenv("FOO") == "bar"


def test_get_quoted_value_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text('FOO="bar baz"\n')
    assert _get_env_var_from_dotenv("FOO") == "bar baz"


def test_get_skips_comments_and_blanks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text(
        "# comment line\n"
        "\n"
        "FOO=bar\n"
        "  # indented comment\n"
    )
    assert _get_env_var_from_dotenv("FOO") == "bar"


# --- _set_env_var_in_dotenv -------------------------------------------------

def test_set_appends_to_empty_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _set_env_var_in_dotenv("FOO", "bar")
    assert _DOTENV_PATH.read_text() == "FOO=bar\n"


def test_set_replaces_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text("FOO=old\nBAR=keep\n")
    _set_env_var_in_dotenv("FOO", "new")
    content = _DOTENV_PATH.read_text()
    assert "FOO=new" in content
    assert "BAR=keep" in content
    assert "FOO=old" not in content


def test_set_appends_to_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text("FOO=bar\n")
    _set_env_var_in_dotenv("NEW", "value")
    content = _DOTENV_PATH.read_text()
    assert "FOO=bar" in content
    assert "NEW=value" in content
    # Original line is preserved
    assert content.startswith("FOO=bar\n")


def test_set_preserves_comments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _DOTENV_PATH.write_text(
        "# My config\n"
        "\n"
        "FOO=bar\n"
        "# Another comment\n"
        "BAZ=qux\n"
    )
    _set_env_var_in_dotenv("FOO", "new")
    content = _DOTENV_PATH.read_text()
    assert "# My config" in content
    assert "# Another comment" in content
    assert "BAZ=qux" in content
    assert "FOO=new" in content
    assert "FOO=bar" not in content


def test_set_creates_parent_dir(tmp_path, monkeypatch):
    """If .env's parent doesn't exist, set should create it."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    monkeypatch.chdir(sub)
    _set_env_var_in_dotenv("FOO", "bar")
    assert (sub / ".env").exists()
    assert "FOO=bar" in (sub / ".env").read_text()


def test_set_value_with_special_chars(tmp_path, monkeypatch):
    """Values can contain / + - _ (typical API key chars). No escaping needed."""
    monkeypatch.chdir(tmp_path)
    _set_env_var_in_dotenv("API_KEY", "sk-abc_DEF.123+/xyz==")
    content = _DOTENV_PATH.read_text()
    assert "API_KEY=sk-abc_DEF.123+/xyz==" in content
    # Round-trip
    assert _get_env_var_from_dotenv("API_KEY") == "sk-abc_DEF.123+/xyz=="
