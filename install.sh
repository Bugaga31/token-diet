#!/bin/bash
# token-diet — one-click install (Linux / macOS / WSL)
# curl -sSL https://raw.githubusercontent.com/Bugaga31/token-diet/main/install.sh | bash

set -e

GREEN='\033[32m'
NC='\033[0m'

echo "🌱 token-diet installer"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ python3 not found. Install Python 3.10+: https://python.org"
    exit 1
fi

echo "✅ Python $(python3 --version)"

# Install
echo "📦 Installing token-diet..."
# Try multiple install strategies for different systems
PKG='git+https://github.com/Bugaga31/token-diet.git#egg=token-diet[server]'
if pip install "$PKG" --break-system-packages 2>/dev/null; then
    :
elif pip install "$PKG" --user 2>/dev/null; then
    :
elif pip3 install "$PKG" 2>/dev/null; then
    :
else
    echo "❌ Install failed. Try: pip install 'git+https://github.com/Bugaga31/token-diet.git[server]' --break-system-packages"
    exit 1
fi

echo ""
echo "🔧 Auto-configuring your AI tools..."
python3 -m token_diet.auto_setup setup 2>/dev/null || token-diet-setup setup 2>/dev/null || true

echo ""
echo "${GREEN}✅ Done!${NC}"
echo ""
echo "   Start the proxy:  token-diet serve"
echo "   Check tools:      token-diet doctor"
echo "   Quick test:       token-diet self-test"
echo "   Shell config:     source ~/.token-diet/config.sh"
echo ""
echo "   Then just use Claude Code, OpenCode, Hermes as usual."
echo "   All requests compress automatically — 40% fewer tokens."
