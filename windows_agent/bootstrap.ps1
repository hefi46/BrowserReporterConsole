# BrowserReporter Bootstrap Script
# Deploy to: \\dc\netlogon\BrowserReporter.ps1
#
# GPO Scheduled Task:
#   Trigger: At log on
#   Action:  powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \\dc\netlogon\BrowserReporter.ps1
#
# This script checks the console for updates, downloads the agent .exe and
# config only when a new version is available, then runs the agent silently.
# Traffic per login is ~50 bytes (version check) unless an update is needed.

# --- CONFIGURE THIS ---
$server = "http://browserreporter.yourdomain.local:8000"
# ----------------------

$dir     = "$env:LOCALAPPDATA\BrowserReporter"
$exe     = "$dir\BrowserReporter.exe"
$cfg     = "$dir\secureconfig.json"
$verFile = "$dir\version.txt"

# Ensure local directory exists
New-Item -ItemType Directory -Path $dir -Force | Out-Null

# Check version and download if needed
try {
    $remote = (Invoke-RestMethod "$server/api/agent/version" -TimeoutSec 10).version
    $local  = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { "" }

    if ($remote -ne $local) {
        Invoke-WebRequest "$server/api/agent/exe"    -OutFile $exe -TimeoutSec 120
        Invoke-WebRequest "$server/api/agent/config" -OutFile $cfg -TimeoutSec 30
        $remote | Set-Content $verFile -NoNewline
    }
} catch {
    # Silently continue — agent will run with whatever version is cached locally
}

# Run the agent
if (Test-Path $exe) {
    Start-Process $exe -WindowStyle Hidden
}
