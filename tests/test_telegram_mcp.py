"""Tests for the telegram-mcp integration (chigwell/telegram-mcp).

token-diet setup now also registers Telegram MCP (80+ tools: chats, messages,
media, contacts, events) into every MCP client config — same philosophy as the
rest of auto_setup: one command, every tool.
"""

import json
import os

import pytest

from token_diet.auto_setup import (
    MCP_CONFIG_FILES,
    TELEGRAM_MCP_DIR,
    TELEGRAM_MCP_REPO,
    TOOLS,
    _merge_mcp_server,
    configure_telegram_mcp,
    get_telegram_mcp_env,
    telegram_mcp_env_path,
    telegram_mcp_installed,
)

from pathlib import Path


# ── ToolConfig ───────────────────────────────────────────────────────────────


def test_telegram_mcp_is_a_registered_tool():
    names = {t.name for t in TOOLS}
    assert "telegram-mcp" in names


def test_telegram_mcp_tool_has_env_and_docs():
    tool = next(t for t in TOOLS if t.name == "telegram-mcp")
    assert tool.env_var == "TELEGRAM_API_ID"
    assert tool.key_env_var == "TELEGRAM_API_HASH"
    assert "chigwell" in tool.docs_url


# ── Env handling ─────────────────────────────────────────────────────────────


def test_get_env_from_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nTELEGRAM_API_ID=123456\nTELEGRAM_API_HASH=abc123\n"
    )
    monkeypatch.setattr("token_diet.auto_setup.telegram_mcp_env_path", lambda: env_file)
    env = get_telegram_mcp_env()
    assert env["TELEGRAM_API_ID"] == "123456"
    assert env["TELEGRAM_API_HASH"] == "abc123"
    assert "TELEGRAM_SESSION_STRING" not in env


def test_get_env_file_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "token_diet.auto_setup.telegram_mcp_env_path",
        lambda: tmp_path / "does-not-exist.env",
    )
    assert get_telegram_mcp_env() == {}


def test_get_env_environment_wins_over_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_API_ID=111\n")
    monkeypatch.setattr("token_diet.auto_setup.telegram_mcp_env_path", lambda: env_file)
    monkeypatch.setenv("TELEGRAM_API_ID", "222")
    env = get_telegram_mcp_env()
    assert env["TELEGRAM_API_ID"] == "222"


# ── MCP config merge ─────────────────────────────────────────────────────────


def test_merge_creates_config_with_telegram_server(tmp_path):
    config = tmp_path / "mcp.json"
    entry = {"command": "uv", "args": ["--directory", "/x", "run", "main.py"]}
    assert _merge_mcp_server(config, entry) is True
    data = json.loads(config.read_text())
    assert data["mcpServers"]["telegram-mcp"] == entry


def test_merge_preserves_existing_servers(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"screen": {"command": "x"}}}))
    entry = {"command": "uv", "args": ["run", "main.py"]}
    assert _merge_mcp_server(config, entry) is True
    data = json.loads(config.read_text())
    assert "screen" in data["mcpServers"]
    assert "telegram-mcp" in data["mcpServers"]


def test_merge_idempotent(tmp_path):
    config = tmp_path / "mcp.json"
    entry = {"command": "uv", "args": ["run", "main.py"]}
    _merge_mcp_server(config, entry)
    # same entry -> no change
    assert _merge_mcp_server(config, entry) is False
    data = json.loads(config.read_text())
    assert data["mcpServers"]["telegram-mcp"] == entry


def test_merge_handles_corrupt_json(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text("{ not valid json")
    entry = {"command": "uv", "args": ["run", "main.py"]}
    assert _merge_mcp_server(config, entry) is True
    data = json.loads(config.read_text())
    assert data["mcpServers"]["telegram-mcp"] == entry


# ── Config file list ─────────────────────────────────────────────────────────


def test_mcp_config_files_include_standard_clients():
    joined = " ".join(MCP_CONFIG_FILES)
    assert "~/.mcp.json" in joined
    assert "~/.claude.json" in joined
    assert "cursor" in joined


# ── Constants ────────────────────────────────────────────────────────────────


def test_repo_points_at_chigwell():
    assert "chigwell" in TELEGRAM_MCP_REPO


def test_telegram_mcp_env_path_is_under_home():
    p = telegram_mcp_env_path()
    assert str(p).endswith(".telegram-mcp/.env")


def test_not_installed_by_default():
    # Repo is not cloned to the user's real home during tests
    assert telegram_mcp_installed() is False or Path(TELEGRAM_MCP_DIR).expanduser().exists()
