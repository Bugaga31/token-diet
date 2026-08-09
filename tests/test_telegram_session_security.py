"""Security tests: Telegram sessions must NEVER reach the repository.

Covers:
- .gitignore protects *.session, .env, TELEGRAM_* patterns
- find_local_telegram_sessions() only finds .session files
- link_local_telegram_session() copies into ~/.telegram-mcp (OUTSIDE repo),
  never into the project tree, and never prints the session content.
"""

import os
import stat
from pathlib import Path

import pytest

from token_diet.auto_setup import (
    TELEGRAM_MCP_DIR,
    find_local_telegram_sessions,
    link_local_telegram_session,
)


REPO_GITIGNORE = Path(__file__).resolve().parent.parent / ".gitignore"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── .gitignore protection ────────────────────────────────────────────────────


def test_gitignore_blocks_session_files():
    content = REPO_GITIGNORE.read_text()
    for pattern in ("*.session", "*_telethon.session", ".env", "TELEGRAM_*"):
        assert pattern in content, f".gitignore missing {pattern!r}"


def test_gitignore_blocks_tdata_and_account_json():
    content = REPO_GITIGNORE.read_text()
    assert "*.tdata*" in content
    assert "221099698*" in content


def test_no_session_or_env_files_are_tracked():
    """Nothing sensitive may be in the git index right now."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=PROJECT_ROOT
    ).stdout
    for line in out.splitlines():
        low = line.lower()
        assert not low.endswith(".session"), f"tracked session file: {line}"
        assert not low.startswith(".env"), f"tracked env file: {line}"


# ── find_local_telegram_sessions ─────────────────────────────────────────────


def test_find_sessions_only_matches_dot_session(tmp_path):
    (tmp_path / "221099698_telethon.session").write_bytes(b"\x00" * 16)
    (tmp_path / "notes.txt").write_text("not a session")
    (tmp_path / "session_notes.txt").write_text("also not a session")
    found = find_local_telegram_sessions(tmp_path)
    assert len(found) == 1
    assert found[0].name == "221099698_telethon.session"


def test_find_sessions_empty_home(tmp_path):
    assert find_local_telegram_sessions(tmp_path) == []


# ── link_local_telegram_session (private, outside repo) ──────────────────────


def test_link_copies_into_telegram_mcp_dir_not_repo(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_tg = fake_home / ".telegram-mcp"

    # Point the module's TELEGRAM_MCP_DIR constant at the fake home
    monkeypatch.setattr(
        "token_diet.auto_setup.TELEGRAM_MCP_DIR",
        str(fake_tg),
    )

    session_src = fake_home / "221099698_telethon.session"
    session_src.write_bytes(b"FAKE_SESSION_CONTENT_NEVER_LEAKS")

    result = link_local_telegram_session(session_src)
    assert result["ok"] is True

    # Session lands in ~/.telegram-mcp, NOT in the project tree
    copied = fake_tg / "telegram_default.session"
    assert copied.exists()
    assert copied.read_bytes() == b"FAKE_SESSION_CONTENT_NEVER_LEAKS"

    # .env got TELEGRAM_SESSION_NAME, no session string
    env_text = (fake_tg / ".env").read_text()
    assert "TELEGRAM_SESSION_NAME=telegram_default.session" in env_text
    assert "TELEGRAM_SESSION_STRING" not in env_text

    # Nothing sensitive was written into the project directory
    project_children = [p.name for p in PROJECT_ROOT.iterdir()]
    assert "telegram_default.session" not in project_children


def test_link_auto_discovers_session_in_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_tg = fake_home / ".telegram-mcp"
    monkeypatch.setattr(
        "token_diet.auto_setup.TELEGRAM_MCP_DIR", str(fake_tg)
    )
    monkeypatch.setattr(
        "token_diet.auto_setup.find_local_telegram_sessions",
        lambda home=None: [fake_home / "221099698_telethon.session"],
    )
    (fake_home / "221099698_telethon.session").write_bytes(b"x")

    result = link_local_telegram_session()
    assert result["ok"] is True
    assert result["label"] == "default"


def test_link_rejects_non_session_path(tmp_path):
    result = link_local_telegram_session(tmp_path / "notes.txt")
    assert result["ok"] is False


def test_link_returns_clean_error_when_no_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "token_diet.auto_setup.find_local_telegram_sessions",
        lambda home=None: [],
    )
    result = link_local_telegram_session()
    assert result["ok"] is False
    assert "не найдено" in result["reason"] or "нет" in result["reason"]


def test_copied_session_is_owner_only(tmp_path, monkeypatch):
    fake_tg = tmp_path / ".telegram-mcp"
    monkeypatch.setattr(
        "token_diet.auto_setup.TELEGRAM_MCP_DIR", str(fake_tg)
    )
    src = tmp_path / "my.session"
    src.write_bytes(b"x")
    link_local_telegram_session(src)
    mode = stat.S_IMODE((fake_tg / "telegram_default.session").stat().st_mode)
    assert mode == 0o600
