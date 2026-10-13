# PowerShell quality-check script for Hexton Scheduler
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> Checking backend formatting (ruff format)..." -ForegroundColor Cyan
& "$RepoRoot\backend\.venv\Scripts\ruff.exe" format --check backend

Write-Host "==> Checking backend linting (ruff check)..." -ForegroundColor Cyan
& "$RepoRoot\backend\.venv\Scripts\ruff.exe" check backend

Write-Host "==> Checking frontend TypeScript types..." -ForegroundColor Cyan
Push-Location "$RepoRoot\frontend"
try {
    npm run typecheck
} finally {
    Pop-Location
}

Write-Host "==> Checking frontend ESLint..." -ForegroundColor Cyan
Push-Location "$RepoRoot\frontend"
try {
    npm run lint
} finally {
    Pop-Location
}

Write-Host "All quality checks passed!" -ForegroundColor Green
