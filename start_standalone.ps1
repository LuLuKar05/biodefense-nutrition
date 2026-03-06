# ═══════════════════════════════════════════════════════════════
# start_standalone.ps1 — Option A: Standalone Telegram Bot (MVP)
# ═══════════════════════════════════════════════════════════════
#
# Launches 2 services for direct Telegram-only deployment:
#   1. Layer 3 — Threat Intelligence Backend (port 8100)
#   2. Agent Orchestrator — Telegram polling + webhook receiver (port 8200)
#
# No OpenClaw Gateway needed — talks directly to Telegram API.
# Simpler setup, but Telegram-only (no Discord, WhatsApp, etc.)
#
# Prerequisites:
#   - Python 3.12+ with dependencies installed
#   - .env file with TELEGRAM_BOT_TOKEN and FLOCK_API_KEY
#
# Usage:
#   .\start_standalone.ps1
# ═══════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  NutriShield — Option A: Standalone Telegram Bot (MVP)" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Kill any existing processes ──
Write-Host "[1/3] Cleaning up old processes..." -ForegroundColor DarkGray
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# ── Start Layer 3 ──
Write-Host "[2/3] Starting Layer 3 — Threat Backend (port 8100)..." -ForegroundColor Yellow
 $layer3 = Start-Process -FilePath "python" -ArgumentList "-m", "server" `
    -WorkingDirectory $PSScriptRoot `
    -PassThru -WindowStyle Normal
Write-Host "  PID: $($layer3.Id)" -ForegroundColor DarkGray
Start-Sleep -Seconds 3

# ── Start Orchestrator (Telegram polling) ──
Write-Host "[3/3] Starting Orchestrator — Telegram polling (port 8200)..." -ForegroundColor Yellow
$orch = Start-Process -FilePath "python" -ArgumentList "-m", "agents.orchestrator" `
    -WorkingDirectory $PSScriptRoot `
    -PassThru -WindowStyle Normal
Write-Host "  PID: $($orch.Id)" -ForegroundColor DarkGray

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Standalone mode running!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Send a message to your Telegram bot to start chatting."
Write-Host "  To stop: Get-Process python -EA 0 | Stop-Process -Force"
Write-Host ""
