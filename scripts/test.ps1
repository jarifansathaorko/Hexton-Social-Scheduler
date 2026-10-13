# PowerShell automated test suite runner for Hexton Scheduler
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> Running backend test suite (pytest)..." -ForegroundColor Cyan
& "$RepoRoot\backend\.venv\Scripts\pytest.exe" "$RepoRoot\backend\tests"

Write-Host "==> Running frontend e2e test suite (Playwright)..." -ForegroundColor Cyan
Push-Location "$RepoRoot\frontend"
try {
    npm run test:e2e
} finally {
    Pop-Location
}

Write-Host "All tests passed successfully!" -ForegroundColor Green
