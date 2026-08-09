# token-diet — one-click install (Windows PowerShell)
# irm https://raw.githubusercontent.com/Bugaga31/token-diet/main/install.ps1 | iex

Write-Host "🌱 token-diet installer" -ForegroundColor Green

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Python not found. Install from: https://python.org" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Python $(python --version)"

Write-Host "📦 Installing token-diet..."
pip install "git+https://github.com/Bugaga31/token-diet.git[server]"

Write-Host ""
Write-Host "🔧 Auto-configuring your AI tools..."
python -m token_diet.auto_setup setup 2>$null

Write-Host ""
Write-Host "✅ Done!" -ForegroundColor Green
Write-Host ""
Write-Host "   Start proxy:   token-diet serve"
Write-Host "   Check tools:   token-diet detect"
Write-Host "   Shell config:  $env:USERPROFILE\.token-diet\config.sh"
Write-Host ""
Write-Host "   Then just use Claude Code, OpenCode, Cursor as usual."
Write-Host "   All requests compress automatically — 40% fewer tokens."
