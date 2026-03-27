# Test-Bootstrap.ps1 — run from PowerShell on this machine
$server = "http://localhost:8000"
$dir    = "$env:LOCALAPPDATA\BrowserReporter\test"
$exe    = "$dir\BrowserReporter.exe"
$cfg    = "$dir\secureconfig.json"

# Clean slate
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $dir -Force | Out-Null

# 1. Version check
Write-Host "Checking version..." -ForegroundColor Cyan
$version = (Invoke-RestMethod "$server/api/agent/version" -TimeoutSec 10).version
Write-Host "  Server version: $version" -ForegroundColor Green

# 2. Download exe
Write-Host "Downloading exe..." -ForegroundColor Cyan
Invoke-WebRequest "$server/api/agent/exe" -OutFile $exe -TimeoutSec 120
Write-Host "  Size: $((Get-Item $exe).Length / 1MB) MB" -ForegroundColor Green

# 3. Download config
Write-Host "Downloading config..." -ForegroundColor Cyan
try {
    Invoke-WebRequest "$server/api/agent/config" -OutFile $cfg -TimeoutSec 30
    Write-Host "  Config saved" -ForegroundColor Green
} catch {
    Write-Host "  Config not available (generate it on Client Config page first)" -ForegroundColor Yellow
}

# 4. Run agent (visible console for testing)
Write-Host "`nStarting agent..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop`n" -ForegroundColor Yellow
& $exe
