# ==============================================================================
# Hexton Scheduler — Windows x64 Portable Production Build Pipeline
# ==============================================================================

param (
    [switch]$SkipTests = $false,
    [switch]$SkipPyInstaller = $false,
    [switch]$PackOnly = $false,
    [switch]$Publish = $false
)

$ErrorActionPreference = 'Stop'
$sw = [System.Diagnostics.Stopwatch]::StartNew()

Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host '   HEXTON SCHEDULER — PORTABLE PRODUCTION BUILD PIPELINE  ' -ForegroundColor Yellow
Write-Host '==========================================================' -ForegroundColor Cyan

$rootDir = $PSScriptRoot
$venvPython = Join-Path $rootDir 'backend\.venv\Scripts\python.exe'
$pyinstallerExe = Join-Path $rootDir 'backend\.venv\Scripts\pyinstaller.exe'
$frontendDir = Join-Path $rootDir 'frontend'
$distDir = Join-Path $rootDir 'dist'
$portableDistDir = Join-Path $distDir 'portable'

# STEP 1: Pre-flight Environment Checks
Write-Host "`n[1/6] Verifying Build Environment Prerequisites..." -ForegroundColor Cyan

if (-not (Test-Path $venvPython)) {
    Write-Error "Python virtual environment not found at $venvPython."
}
$pyVer = & $venvPython --version
Write-Host "  Found Python: $pyVer" -ForegroundColor Gray

$nodeVer = & node --version
$npmVer = & npm --version
Write-Host "  Found Node: $nodeVer, npm: $npmVer" -ForegroundColor Gray

# Ensure node_modules junction or folder exists
if (-not (Test-Path (Join-Path $rootDir 'node_modules'))) {
    Write-Host "  Creating node_modules link..." -ForegroundColor Gray
    cmd /c mklink /J (Join-Path $rootDir 'node_modules') "E:\project\Clipxton\src\App.UI\node_modules"
}

# STEP 2: Automated Testing Battery
if (-not $SkipTests) {
    Write-Host "`n[2/6] Running Automated Verification Battery..." -ForegroundColor Cyan

    Write-Host "  Running frontend TypeScript check..." -ForegroundColor Gray
    Push-Location $frontendDir
    try {
        & npm run typecheck
        if ($LASTEXITCODE -ne 0) { throw "Frontend TypeScript check failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }

    Write-Host "  Running backend domain and API test suite..." -ForegroundColor Gray
    $env:PYTHONPATH = "backend"
    & $venvPython -m pytest backend/tests/test_domain_scheduling.py backend/tests/test_api.py -o cache_dir="backend/.pytest_cache" -q
    if ($LASTEXITCODE -ne 0) { throw "Backend pytest battery failed with exit code $LASTEXITCODE" }

    Write-Host "  All test batteries passed cleanly!" -ForegroundColor Green
} else {
    Write-Host "`n[2/6] Skipping test suites (-SkipTests specified)..." -ForegroundColor Yellow
}

# STEP 3: Compile Frontend Production Bundle
Write-Host "`n[3/6] Building Frontend Production Distribution..." -ForegroundColor Cyan
Push-Location $frontendDir
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
Write-Host "  Frontend assets compiled into frontend/dist" -ForegroundColor Green

# STEP 4: Compile Python Backend with PyInstaller
Write-Host "`n[4/6] Checking / Compiling Python Server Executable..." -ForegroundColor Cyan
$serverExe = Join-Path $distDir 'Hexton.exe'

if ($SkipPyInstaller -and (Test-Path $serverExe)) {
    Write-Host "  Using existing server binary: $serverExe (-SkipPyInstaller specified)" -ForegroundColor Gray
} else {
    if (-not (Test-Path $pyinstallerExe)) {
        Write-Error "PyInstaller not found at $pyinstallerExe."
    }
    Write-Host "  Compiling Hexton.exe via PyInstaller..." -ForegroundColor Gray
    & $pyinstallerExe hexton.spec --distpath dist --workpath build/pyinstaller --noconfirm
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller compilation failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path $serverExe)) {
    throw "Compiled server executable not found at $serverExe"
}
$serverSizeMb = [Math]::Round((Get-Item $serverExe).Length / 1MB, 2)
Write-Host "  Server executable ready: $serverExe ($serverSizeMb MB)" -ForegroundColor Green

# STEP 5: Patch NSIS Portable Template
Write-Host "`n[5/6] Optimizing NSIS Portable Extraction Template..." -ForegroundColor Cyan
& node electron/scripts/optimize_portable_nsis.js

# STEP 6: Package Electron Portable Application
Write-Host "`n[6/6] Packaging Standalone Electron Application..." -ForegroundColor Cyan
if ($PackOnly) {
    Write-Host "  Building unpacked directory (npx electron-builder --dir)..." -ForegroundColor Gray
    & npx electron-builder --dir
} else {
    Write-Host "  Building Standalone Portable Executable (npx electron-builder --win portable)..." -ForegroundColor Gray
    & npx electron-builder --win portable
}

if ($LASTEXITCODE -ne 0) {
    throw "electron-builder failed with exit code $LASTEXITCODE"
}

# STEP 7: Verify Release Artifacts & Generate Checksums
Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host '                RELEASE ARTIFACTS READY                   ' -ForegroundColor Green
Write-Host '==========================================================' -ForegroundColor Green

$artifacts = @()
$packageJson = Get-Content (Join-Path $rootDir 'package.json') -Raw | ConvertFrom-Json
$appVersion = $packageJson.version

if ($PackOnly) {
    $unpackedExe = Join-Path $portableDistDir 'win-unpacked\Hexton Scheduler.exe'
    if (Test-Path $unpackedExe) { $artifacts += Get-Item $unpackedExe }
} else {
    $portableExe = Join-Path $portableDistDir "Hexton-Scheduler-$appVersion-Portable.exe"
    if (Test-Path $portableExe) {
        $artifacts += Get-Item $portableExe
    } else {
        # Fallback to search portable dist dir
        $found = Get-ChildItem -Path $portableDistDir -Filter "*Portable*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $artifacts += $found }
    }
}

$checksumFile = Join-Path $portableDistDir 'SHA256SUMS.txt'
$checksumLines = @()

foreach ($art in $artifacts) {
    $sizeMb = [Math]::Round($art.Length / 1MB, 2)
    $hash = (Get-FileHash -Path $art.FullName -Algorithm SHA256).Hash
    Write-Host "Artifact: $($art.Name)" -ForegroundColor Yellow
    Write-Host "  Path:     $($art.FullName)" -ForegroundColor Gray
    Write-Host "  Size:     $sizeMb MB" -ForegroundColor Gray
    Write-Host "  SHA-256:  $hash" -ForegroundColor Gray
    $checksumLines += "$hash  $($art.Name)"
}

if ($checksumLines.Count -gt 0) {
    Set-Content -Path $checksumFile -Value ($checksumLines -join "`r`n") -Encoding utf8
    Write-Host "`nChecksum manifest written to: $checksumFile" -ForegroundColor Cyan
}

# STEP 8: Generate OTA Update Metadata & UI Patch
Write-Host "`n[7/7] Generating OTA Update Metadata & UI Patch..." -ForegroundColor Cyan
& node tools/generate_latest_yml.js
& node tools/create_ui_patch.js

# Optional: Publish release to GitHub
if ($Publish) {
    Write-Host "`n[PUBLISH] Uploading release assets to GitHub Releases..." -ForegroundColor Cyan
    & node tools/publish_release.js
}

$sw.Stop()
$totalSec = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
Write-Host "`nPipeline completed successfully in $totalSec seconds!" -ForegroundColor Green
