[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = '🧠 YATRA — Cortex Ativo'
Write-Host "🧠 Iniciando Cortex..." -ForegroundColor Cyan
python main.py