"""Tests for auto_setup.py — tool detection + configuration."""

import os
import pytest
from token_diet.auto_setup import (
    ToolConfig,
    TOOLS,
    detect_tools,
    detect_and_report,
    generate_shell_config,
    list_tools,
)


class TestToolConfig:
    def test_all_tools_have_names(self):
        for tool in TOOLS:
            assert tool.name
            assert tool.display_name

    def test_all_tools_have_env_vars_or_config(self):
        for tool in TOOLS:
            assert tool.env_var or tool.config_files, f"{tool.name} has no env_var or config_files"

    def test_key_tools_present(self):
        names = {t.name for t in TOOLS}
        assert "claude-code" in names
        assert "opencode" in names
        assert "cursor" in names
        assert "buffy" in names
        assert "openai-sdk" in names


class TestDetectTools:
    def test_detect_returns_list(self):
        tools = detect_tools()
        assert isinstance(tools, list)

    def test_detect_buffy_always(self):
        """Buffy should always be detected (we ARE Buffy)."""
        tools = detect_tools()
        buffy = [t for t in tools if t.name == "buffy"]
        assert len(buffy) == 1

    def test_detect_and_report_structure(self):
        report = detect_and_report()
        assert "detected_tools" in report
        assert "not_detected" in report
        assert "proxy_host" in report
        assert "proxy_port" in report


class TestGenerateShellConfig:
    def test_generates_exports(self):
        buffy_tool = [t for t in TOOLS if t.name == "buffy"]
        config = generate_shell_config(buffy_tool)
        assert "export BUFFY_BASE_URL" in config
        assert "token-diet" in config

    def test_includes_banner(self):
        config = generate_shell_config([])
        assert "token-diet auto-config" in config


class TestListTools:
    def test_output_contains_buffy(self):
        output = list_tools()
        assert "Buffy" in output
