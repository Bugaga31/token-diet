@echo off
REM token-diet — one-click install (Windows CMD)
echo 🌱 token-diet installer
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ❌ Python not found. Install from: https://python.org
    exit /b 1
)

echo ✅ Python found

echo 📦 Installing token-diet...
pip install "git+https://github.com/Bugaga31/token-diet.git[server]"

echo.
echo 🔧 Auto-configuring your AI tools...
python -m token_diet.auto_setup setup

echo.
echo ✅ Done!
echo.
echo    Start proxy:   token-diet serve
echo    Check tools:   token-diet detect
echo.
echo    Then just use Claude Code, OpenCode, Cursor as usual.
echo    All requests compress automatically — 40%% fewer tokens.
