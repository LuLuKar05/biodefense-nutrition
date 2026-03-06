# ═══════════════════════════════════════════════════════════════
# start_gateway.ps1 — Option B: Self-Hosted with OpenClaw Gateway
# ═══════════════════════════════════════════════════════════════
#
# Launches all 3 services for multi-channel deployment:
#   1. Layer 3 — Threat Intelligence Backend (port 8100)
#   2. Gateway Bridge — OpenClaw ↔ Orchestrator (port 18790)
#   3. OpenClaw Gateway — Multi-channel connector (port 18789)
#
# Prerequisites:
#   - Python 3.12+ with dependencies installed (pip install -r requirements.txt)
#   - Node.js 22+ with OpenClaw installed (npm install -g openclaw@latest)
#   - .env file configured with tokens (TELEGRAM_BOT_TOKEN, FLOCK_API_KEY, etc.)
#
# Usage:
#   .\start_gateway.ps1
#
# To stop all services:
#   Get-Process python, node -ErrorAction SilentlyContinue | Stop-Process -Force
# ═══════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  NutriShield — Option B: Self-Hosted with OpenClaw Gateway" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  This launches 3 services:" -ForegroundColor White
Write-Host "    1. Layer 3 — Threat Backend       (port 8100)"  -ForegroundColor Yellow
Write-Host "    2. Gateway Bridge                  (port 18790)" -ForegroundColor Yellow
Write-Host "    3. OpenClaw Gateway                (port 18789)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Channels: Telegram, Discord, WhatsApp, Slack, WebChat" -ForegroundColor Green
Write-Host "  Privacy:  User data stays LOCAL — zero-knowledge backend" -ForegroundColor Green
Write-Host ""

# ── Kill any existing processes ──
Write-Host "[1/4] Cleaning up old processes..." -ForegroundColor DarkGray
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "  Done." -ForegroundColor DarkGray

# ── Start Layer 3: Threat Intelligence Backend ──
Write-Host ""
Write-Host "[2/4] Starting Layer 3 — Threat Intelligence Backend (port 8100)..." -ForegroundColor Yellow
$layer3 = Start-Process -FilePath "python" -ArgumentList "-m", "threat_backend" `
    -WorkingDirectory $PSScriptRoot `
    -PassThru -WindowStyle Normal
Write-Host "  PID: $($layer3.Id)" -ForegroundColor DarkGray
Start-Sleep -Seconds 3

# ── Start Gateway Bridge ──
Write-Host ""
Write-Host "[3/4] Starting Gateway Bridge (port 18790)..." -ForegroundColor Yellow
$bridge = Start-Process -FilePath "python" -ArgumentList "gateway_bridge.py" `
    -WorkingDirectory $PSScriptRoot `
    -PassThru -WindowStyle Normal
Write-Host "  PID: $($bridge.Id)" -ForegroundColor DarkGray
Start-Sleep -Seconds 2

# ── Start OpenClaw Gateway ──
Write-Host ""
Write-Host "[4/4] Starting OpenClaw Gateway (port 18789)..." -ForegroundColor Yellow

# Check if openclaw is installed
$openclawPath = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclawPath) {
    Write-Host ""
    Write-Host "  WARNING: 'openclaw' not found in PATH." -ForegroundColor Red
    Write-Host "  Install it with: npm install -g openclaw@latest" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Layer 3 and Gateway Bridge are running." -ForegroundColor Yellow
    Write-Host "  You can start OpenClaw manually:" -ForegroundColor Yellow
    Write-Host "    cd openclaw" -ForegroundColor White
    Write-Host "    openclaw gateway --port 18789 --verbose" -ForegroundColor White
    Write-Host ""
} else {
    $gateway = Start-Process -FilePath "openclaw" `
        -ArgumentList "gateway", "--port", "18789", "--verbose" `
        -WorkingDirectory (Join-Path $PSScriptRoot "openclaw") `
        -PassThru -WindowStyle Normal
    Write-Host "  PID: $($gateway.Id)" -ForegroundColor DarkGray
}

# ── Summary ──
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Endpoints:" -ForegroundColor White
Write-Host "    Layer 3 health:    http://127.0.0.1:8100/health"
Write-Host "    Bridge health:     http://127.0.0.1:18790/health"
Write-Host "    OpenClaw WebChat:  http://127.0.0.1:18789"
Write-Host ""
Write-Host "  Users can now chat on any connected channel:" -ForegroundColor White
Write-Host "    - Telegram: message your bot directly"
Write-Host "    - Discord: mention the bot in any server channel"
Write-Host "    - WhatsApp: message the linked number"
Write-Host "    - WebChat: open http://127.0.0.1:18789 in a browser"
Write-Host ""
Write-Host "  To stop all services:" -ForegroundColor DarkGray
Write-Host "    Get-Process python, node -EA 0 | Stop-Process -Force" -ForegroundColor DarkGray
Write-Host ""
