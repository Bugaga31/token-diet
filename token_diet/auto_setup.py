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


def configure_all() -> dict[str, Any]:
    """Run full auto-configuration: detect, configure, report.

    Returns a report of what was done.
    """
    detected = detect_tools()
    results: dict[str, Any] = {
        "tools_detected": len(detected),
        "tools_configured": 0,
        "details": [],
    }

    # Write shell config
    config_path = write_shell_config()
    results["shell_config_path"] = str(config_path)

    # Configure Continue.dev if present
    for tool in detected:
        detail = {"name": tool.name, "action": "env_var_set"}
        if tool.name == "continue":
            ok = configure_continue_dev()
            detail["action"] = "continue_config_updated" if ok else "continue_config_skipped"
        results["tools_configured"] += 1
        results["details"].append(detail)

    # Add sourcing instructions
    results["instructions"] = (
        f"Add to your shell profile:\n"
        f"  echo 'source ~/.token-diet/config.sh' >> ~/.bashrc\n"
        f"  source ~/.token-diet/config.sh\n\n"
        f"Then start the proxy:\n"
        f"  token-diet serve\n\n"
        f"All your AI tools now route through token-diet.\n"
        f"Savings: automatic. Planet: healthier. People: happier."
    )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def list_tools() -> str:
    """Pretty-print detected vs not-detected tools."""
    report = detect_and_report()
    lines = ["", "═" * 65, "  token-diet — AI Tool Detection", "═" * 65, ""]

    lines.append("  ✅ DETECTED:")
    for t in report["detected_tools"]:
        status = "✓ configured" if t["configured"] else "○ not configured"
        lines.append(f"    {t['display']:<40} {status}")
        if t.get("shell_snippet"):
            lines.append(f"      → {t['shell_snippet']}")

    if report["not_detected"]:
        lines.append("")
        lines.append("  ⬜ NOT INSTALLED (can be configured):")
        for t in report["not_detected"]:
            lines.append(f"    {t['display']:<40} → {t['install_hint']}")

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
