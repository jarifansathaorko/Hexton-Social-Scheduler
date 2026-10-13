# PowerShell build script to compile standalone portable Hexton.exe
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> Step 1: Building frontend production assets..." -ForegroundColor Cyan
Push-Location "$RepoRoot\frontend"
try {
    npm run build
} finally {
    Pop-Location
}

Write-Host "==> Step 2: Compiling standalone executable with PyInstaller..." -ForegroundColor Cyan
& "$RepoRoot\backend\.venv\Scripts\pyinstaller.exe" --noconfirm --clean "$RepoRoot\hexton.spec"

if (Test-Path "$RepoRoot\dist\Hexton.exe") {
    $ExeSize = (Get-Item "$RepoRoot\dist\Hexton.exe").Length / 1MB
    Write-Host ("==> Success! Standalone executable compiled: dist\Hexton.exe ({0:N1} MB)" -f $ExeSize) -ForegroundColor Green
} else {
    Write-Error "Build failed: dist\Hexton.exe not found."
}
