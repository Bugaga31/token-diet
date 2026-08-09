"""
Auto-Setup: one command configures token-diet for all your AI tools.

    token-diet setup          # detect + configure everything
    token-diet setup --proxy  # start proxy for all tools
    token-diet setup --list   # show detected tools
    token-diet setup --shell  # print shell config to source

After setup:
    Claude Code  → ANTHROPIC_BASE_URL=http://localhost:8080
    OpenCode     → OPENAI_BASE_URL=http://localhost:8080/v1
    Cursor       → OpenAI-compatible → localhost:8080/v1
    Continue.dev → config.json apiBase
    Hermes       → HERMES_BASE_URL
    Buffy        → BUFFY_BASE_URL
    Any OpenAI SDK → client = OpenAI(base_url="http://localhost:8080/v1")

Philosophy: one proxy, every tool, zero configuration per tool.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Tool definitions
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolConfig:
    """Configuration for a supported AI tool."""
    name: str
    display_name: str
    env_var: str                          # Environment variable for base URL
    proxy_path: str = "/v1"               # Path appended to proxy URL
    config_files: list[str] = field(default_factory=list)
    key_env_var: str = ""                  # API key env var
    detect_cmd: str = ""                   # Shell command to detect installation
    shell_snippet: str = ""                # Shell config snippet
    docs_url: str = ""


# All supported tools
TOOLS: list[ToolConfig] = [
    ToolConfig(
        name="claude-code",
        display_name="Claude Code (Anthropic CLI)",
        env_var="ANTHROPIC_BASE_URL",
        proxy_path="/v1",
        key_env_var="ANTHROPIC_API_KEY",
        detect_cmd="which claude 2>/dev/null || npm list -g @anthropic-ai/claude-code 2>/dev/null",
        shell_snippet="export ANTHROPIC_BASE_URL=http://localhost:8080/v1",
        docs_url="https://docs.anthropic.com/en/docs/claude-code",
    ),
    ToolConfig(
        name="opencode",
        display_name="OpenCode",
        env_var="OPENAI_BASE_URL",
        proxy_path="/v1",
        key_env_var="OPENAI_API_KEY",
        detect_cmd="which opencode 2>/dev/null || pip show opencode 2>/dev/null",
        shell_snippet="export OPENAI_BASE_URL=http://localhost:8080/v1",
        docs_url="https://github.com/opencode",
    ),
    ToolConfig(
        name="cursor",
        display_name="Cursor IDE",
        env_var="OPENAI_API_BASE",
        proxy_path="/v1",
        key_env_var="OPENAI_API_KEY",
        config_files=[
            "~/.cursor/settings.json",
            "~/.cursor/mcp.json",
        ],
        detect_cmd="ls ~/.cursor 2>/dev/null || ls /Applications/Cursor.app 2>/dev/null || ls ~/AppData/Local/Programs/Cursor 2>/dev/null",
        shell_snippet="export OPENAI_API_BASE=http://localhost:8080/v1",
        docs_url="https://cursor.sh",
    ),
    ToolConfig(
        name="continue",
        display_name="Continue.dev (VS Code / JetBrains)",
        env_var="",
        proxy_path="/v1",
        key_env_var="",
        config_files=[
            "~/.continue/config.json",
        ],
        detect_cmd="ls ~/.continue 2>/dev/null",
        shell_snippet="",
        docs_url="https://docs.continue.dev",
    ),
    ToolConfig(
        name="hermes",
        display_name="Hermes AI Assistant",
        env_var="HERMES_BASE_URL",
        proxy_path="/v1",
        key_env_var="HERMES_API_KEY",
        detect_cmd="which hermes 2>/dev/null || pip show hermes-ai 2>/dev/null",
        shell_snippet="export HERMES_BASE_URL=http://localhost:8080/v1",
        docs_url="https://github.com/hermes-ai",
    ),
    ToolConfig(
        name="buffy",
        display_name="Buffy / Freebuff",
        env_var="BUFFY_BASE_URL",
        proxy_path="/v1",
        key_env_var="BUFFY_API_KEY",
        detect_cmd="true",  # Always available (we ARE Buffy)
        shell_snippet="export BUFFY_BASE_URL=http://localhost:8080/v1",
        docs_url="https://freebuff.com",
    ),
    ToolConfig(
        name="openai-sdk",
        display_name="OpenAI Python SDK",
        env_var="OPENAI_BASE_URL",
        proxy_path="/v1",
        key_env_var="OPENAI_API_KEY",
        detect_cmd="python3 -c 'import openai' 2>/dev/null",
        shell_snippet="# In Python code:\n# client = OpenAI(base_url='http://localhost:8080/v1')",
        docs_url="https://platform.openai.com/docs",
    ),
    ToolConfig(
        name="openrouter",
        display_name="OpenRouter",
        env_var="OPENROUTER_BASE_URL",
        proxy_path="",
        key_env_var="OPENROUTER_API_KEY",
        detect_cmd="true",
        shell_snippet="export OPENROUTER_BASE_URL=http://localhost:8080",
        docs_url="https://openrouter.ai",
    ),
    ToolConfig(
        name="ollama",
        display_name="Ollama (local models)",
        env_var="OLLAMA_HOST",
        proxy_path="",
        key_env_var="",
        detect_cmd="which ollama 2>/dev/null",
        shell_snippet="# Ollama local models can be proxied through token-diet:\n# export OLLAMA_HOST=http://localhost:8080",
        docs_url="https://ollama.com",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Detector
# ═══════════════════════════════════════════════════════════════════════════════

def detect_tools() -> list[ToolConfig]:
    """Detect which AI tools are installed on this system."""
    found: list[ToolConfig] = []
    for tool in TOOLS:
        try:
            result = subprocess.run(
                tool.detect_cmd,
                shell=True, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                found.append(tool)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return found


def detect_and_report() -> dict[str, Any]:
    """Full detection report with config status."""
    detected = detect_tools()
    report: dict[str, Any] = {
        "proxy_host": os.environ.get("TOKEN_DIET_HOST", "localhost"),
        "proxy_port": int(os.environ.get("TOKEN_DIET_PORT", "8080")),
        "detected_tools": [],
        "not_detected": [],
    }

    for tool in TOOLS:
        if tool in detected:
            configured = bool(os.environ.get(tool.env_var, "") if tool.env_var else False)
            report["detected_tools"].append({
                "name": tool.name,
                "display": tool.display_name,
                "env_var": tool.env_var,
                "configured": configured,
                "shell_snippet": tool.shell_snippet if not configured else None,
            })
        else:
            report["not_detected"].append({
                "name": tool.name,
                "display": tool.display_name,
                "install_hint": tool.docs_url,
            })

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Configurator
# ═══════════════════════════════════════════════════════════════════════════════

def generate_shell_config(tools: list[ToolConfig] | None = None) -> str:
    """Generate shell config snippet to source.

    Produces a block of export statements that configure all detected tools
    to use the token-diet proxy. Source it in your .bashrc/.zshrc.
    """
    if tools is None:
        tools = detect_tools()

    host = os.environ.get("TOKEN_DIET_HOST", "localhost")
    port = os.environ.get("TOKEN_DIET_PORT", "8080")

    lines = [
        "# ═══════════════════════════════════════════════════════════",
        "#  token-diet auto-config — generated by 'token-diet setup'",
        f"#  Proxy: http://{host}:{port}",
        "# ═══════════════════════════════════════════════════════════",
        "",
    ]

    for tool in tools:
        if tool.env_var:
            value = f"http://{host}:{port}{tool.proxy_path}"
            lines.append(f"# {tool.display_name}")
            lines.append(f"export {tool.env_var}={value}")
            # Preserve original key
            if tool.key_env_var and tool.key_env_var != tool.env_var:
                lines.append(f"# Original key: export {tool.key_env_var}=your-key-here")
            lines.append("")

    lines.append("# Add to your shell: source ~/.token-diet/config.sh")
    return "\n".join(lines)


def write_shell_config(path: str | Path | None = None) -> Path:
    """Write shell config to file. Returns the file path."""
    if path is None:
        home = Path.home()
        config_dir = home / ".token-diet"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "config.sh"

    path = Path(path)
    tools = detect_tools()
    content = generate_shell_config(tools)
    path.write_text(content)
    path.chmod(0o644)
    return path


def configure_continue_dev() -> bool:
    """Configure Continue.dev to use token-diet proxy.

    Updates ~/.continue/config.json to add token-diet as a model provider.
    """
    config_path = Path.home() / ".continue" / "config.json"
    if not config_path.exists():
        return False

    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    # Add token-diet provider
    models = config.get("models", [])
    token_diet_provider = {
        "title": "token-diet (optimized)",
        "provider": "openai",
        "model": "auto",
        "apiBase": f"http://{os.environ.get('TOKEN_DIET_HOST', 'localhost')}:{os.environ.get('TOKEN_DIET_PORT', '8080')}/v1",
        "apiKey": os.environ.get("OPENAI_API_KEY", ""),
    }

    # Check if already configured
    existing = any(m.get("title") == "token-diet (optimized)" for m in models)
    if not existing:
        models.append(token_diet_provider)
        config["models"] = models
        config_path.write_text(json.dumps(config, indent=2))

    return True


def _get_proxy_url(path: str = "") -> str:
    host = os.environ.get("TOKEN_DIET_HOST", "localhost")
    port = os.environ.get("TOKEN_DIET_PORT", "8080")
    return f"http://{host}:{port}{path}"


def configure_hermes() -> bool:
    """Configure Hermes AI Assistant to use token-diet.

    Creates ~/.hermes/config.json with proxy settings.
    """
    config_dir = Path.home() / ".hermes"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    config["api_base"] = _get_proxy_url("/v1")
    config["api_key"] = config.get("api_key", os.environ.get("HERMES_API_KEY", ""))
    config["optimizer"] = "token-diet"
    config["_token_diet_configured"] = True

    config_path.write_text(json.dumps(config, indent=2))
    return True


def configure_buffy() -> bool:
    """Configure Buffy / Freebuff to use token-diet.

    Creates ~/.buffy/config.json with token-diet settings.
    Buffy is our own tool — this integrates token-diet directly.
    """
    config_dir = Path.home() / ".buffy"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    config["proxy_url"] = _get_proxy_url("/v1")
    config["token_optimizer"] = "token-diet"
    config["cache_enabled"] = True
    config["green_tracking"] = True
    config["obsidian_memory_path"] = str(Path.home() / ".buffy" / "memory.json")
    config["_token_diet_version"] = "2.5.0"
    config["_token_diet_configured"] = True

    config_path.write_text(json.dumps(config, indent=2))

    # Also create a buffy memory store for ObsidianMemory
    memory_path = Path.home() / ".buffy" / "memory.json"
    if not memory_path.exists():
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text('{"notes": {}, "version": "1.0"}')

    return True


def configure_openrouter() -> bool:
    """Configure OpenRouter to use token-diet as a middleware.

    Writes ~/.openrouter/config.sh with proxy settings.
    """
    config_dir = Path.home() / ".openrouter"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.sh"

    content = f"""# OpenRouter → token-diet proxy
# Generated by token-diet setup

export OPENROUTER_BASE_URL={_get_proxy_url()}
export OPENROUTER_API_KEY=${{OPENROUTER_API_KEY:-your-key-here}}

# Source this in your shell:
#   source ~/.openrouter/config.sh
"""
    config_path.write_text(content)
    config_path.chmod(0o644)
    return True


def configure_ollama() -> bool:
    """Configure Ollama to use token-diet as a proxy layer.

    Creates ~/.ollama/token-diet.conf with proxy settings.
    Note: Ollama uses OLLAMA_HOST for remote connections.
    For local models, token-diet can proxy TO Ollama.
    """
    config_dir = Path.home() / ".ollama"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "token-diet.conf"

    content = f"""# Ollama + token-diet integration
# Generated by token-diet setup
#
# Usage: Start Ollama normally, then point token-diet to it:
#   UPSTREAM_URL=http://localhost:11434/v1 token-diet serve
#
# This proxies Ollama's local models through token-diet compression.

TOKEN_DIET_PROXY={_get_proxy_url("/v1")}
OLLAMA_UPSTREAM=http://localhost:11434
"""
    config_path.write_text(content)
    config_path.chmod(0o644)

    # Also create a convenience launcher script
    launcher = config_dir / "start-with-token-diet.sh"
    launcher_content = f"""#!/bin/bash
# Start Ollama + token-diet together
# Usage: bash ~/.ollama/start-with-token-diet.sh

echo "Starting Ollama + token-diet..."

# Start Ollama in background (if not running)
ollama serve &>/dev/null &

# Start token-diet proxy pointing to Ollama
export UPSTREAM_URL={_get_proxy_url("").replace(":8080", ":11434")}/v1
cd ~/.token-diet
token-diet serve --host 0.0.0.0 --port 8080
"""
    launcher.write_text(launcher_content)
    launcher.chmod(0o755)

    return True


def configure_all() -> dict[str, Any]:
    """Run full auto-configuration: detect, configure, report.

    Returns a report of what was done.
    """
    detected = detect_tools()
    results: dict[str, Any] = {
        "tools_detected": len(detected),
        "tools_configured": 0,
        "details": [],
        "config_files": [],
    }

    # Write shell config (env vars for all tools)
    config_path = write_shell_config()
    results["shell_config_path"] = str(config_path)
    results["config_files"].append(str(config_path))

    # Tool-specific configurators
    configurators = {
        "hermes": (configure_hermes, "~/.hermes/config.json"),
        "buffy": (configure_buffy, "~/.buffy/config.json"),
        "openrouter": (configure_openrouter, "~/.openrouter/config.sh"),
        "ollama": (configure_ollama, "~/.ollama/token-diet.conf"),
        "continue": (configure_continue_dev, "~/.continue/config.json"),
    }

    for tool in detected:
        detail: dict[str, Any] = {"name": tool.name, "action": "env_set"}

        if tool.name in configurators:
            fn, config_path_hint = configurators[tool.name]
            try:
                ok = fn()
                detail["action"] = "config_created" if ok else "config_skipped"
                if ok:
                    detail["config_file"] = config_path_hint
                    results["config_files"].append(config_path_hint)
            except Exception as e:
                detail["action"] = f"error: {e}"

        results["tools_configured"] += 1
        results["details"].append(detail)

    # Auto-source in shell profile
    shell_profile = _add_to_shell_profile()
    if shell_profile:
        results["shell_profile_updated"] = shell_profile

    # Add sourcing instructions
    results["instructions"] = (
        f"\n📋 Next steps:\n"
        f"  1. source ~/.token-diet/config.sh\n"
        f"  2. token-diet serve\n"
        f"  3. Use your AI tools — savings automatic.\n"
        f"\n💡 All config files written. Run 'token-diet detect' to verify."
    )

    return results


def _add_to_shell_profile() -> str | None:
    """Add 'source ~/.token-diet/config.sh' to .bashrc and .zshrc if not present."""
    source_line = "\n# token-diet auto-config\n[ -f ~/.token-diet/config.sh ] && source ~/.token-diet/config.sh\n"
    updated = []

    for rc_name in [".bashrc", ".zshrc", ".profile"]:
        rc_path = Path.home() / rc_name
        if rc_path.exists():
            content = rc_path.read_text()
            if "token-diet/config.sh" not in content:
                rc_path.write_text(content + source_line)
                updated.append(rc_name)

    return ", ".join(updated) if updated else None


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def list_tools() -> str:
    """Pretty-print detected vs not-detected tools."""
    report = detect_and_report()
    lines = ["", "═" * 65, "  token-diet — AI Tool Detection", "═" * 65, ""]

    # Config file statuses
    config_status = {
        "hermes": ("~/.hermes/config.json", Path.home() / ".hermes" / "config.json"),
        "buffy": ("~/.buffy/config.json", Path.home() / ".buffy" / "config.json"),
        "openrouter": ("~/.openrouter/config.sh", Path.home() / ".openrouter" / "config.sh"),
        "ollama": ("~/.ollama/token-diet.conf", Path.home() / ".ollama" / "token-diet.conf"),
        "continue": ("~/.continue/config.json", Path.home() / ".continue" / "config.json"),
        "claude-code": (None, Path.home() / ".token-diet" / "config.sh"),
        "opencode": (None, Path.home() / ".token-diet" / "config.sh"),
        "cursor": (None, Path.home() / ".token-diet" / "config.sh"),
        "openai-sdk": (None, Path.home() / ".token-diet" / "config.sh"),
        "buffy": ("~/.buffy/config.json", Path.home() / ".buffy" / "config.json"),
    }

    lines.append("  ✅ DETECTED:")
    for t in report["detected_tools"]:
        # Check if tool-specific config exists
        tool_config = config_status.get(t["name"])
        has_config = tool_config and tool_config[1].exists() if tool_config else False
        config_hint = tool_config[0] if tool_config else None

        if has_config and config_hint:
            status = f"✓ configured ({config_hint})"
        elif t["configured"]:
            status = "✓ env configured"
        else:
            status = "○ run 'token-diet setup'"

        lines.append(f"    {t['display']:<40} {status}")
        if t.get("shell_snippet") and not has_config:
            lines.append(f"      → {t['shell_snippet']}")

    if report["not_detected"]:
        lines.append("")
        lines.append("  ⬜ NOT INSTALLED (install to configure):")
        for t in report["not_detected"]:
            lines.append(f"    {t['display']:<40} → {t['install_hint']}")

    # Shell profile status
    shell_sourced = False
    for rc_name in [".bashrc", ".zshrc", ".profile"]:
        rc_path = Path.home() / rc_name
        if rc_path.exists() and "token-diet/config.sh" in rc_path.read_text():
            shell_sourced = True
            lines.append(f"\n  📋 Shell profile: {rc_name} ✓ auto-sourced")
            break
    if not shell_sourced:
        lines.append(f"\n  📋 Shell profile: ○ add 'source ~/.token-diet/config.sh' to .bashrc/.zshrc")

    proxy_url = _get_proxy_url()
    lines.append(f"\n  🌐 Proxy: {proxy_url}/v1")
    lines.append(f"  📂 Config dir: ~/.token-diet/")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point for token-diet setup."""
    import argparse

    parser = argparse.ArgumentParser(
        description="token-diet setup — auto-configure all AI tools"
    )
    parser.add_argument("command", nargs="?", default="detect",
                       choices=["detect", "setup", "list", "shell", "all"],
                       help="Action: detect, setup, list, shell, all")
    parser.add_argument("--host", default="localhost",
                       help="Proxy host (default: localhost)")
    parser.add_argument("--port", default="8080",
                       help="Proxy port (default: 8080)")
    args = parser.parse_args()

    if args.host != "localhost":
        os.environ["TOKEN_DIET_HOST"] = args.host
    if args.port != "8080":
        os.environ["TOKEN_DIET_PORT"] = args.port

    if args.command == "detect" or args.command == "list":
        print(list_tools())
    elif args.command == "shell":
        print(generate_shell_config())
    elif args.command == "setup" or args.command == "all":
        print()
        print("🔧 token-diet — Auto-Setup")
        print("═" * 65)
        result = configure_all()
        print(f"\n  ✅ {result['tools_detected']} tools detected")
        print(f"  ✅ Shell config written to: {result['shell_config_path']}")
        for d in result["details"]:
            print(f"     {d['name']}: {d['action']}")
        print(f"\n  {result['instructions']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
