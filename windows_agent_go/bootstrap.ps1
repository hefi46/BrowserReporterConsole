# BrowserReporter Bootstrap Script
# Deploy to: \\dc\netlogon\BrowserReporter.ps1
#
# GPO Scheduled Task:
#   Trigger: At log on
#   Action:  powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \\dc\netlogon\BrowserReporter.ps1
#
# The agent runs as a daemon for the life of the user's session, collecting
# and reporting browser history on a configurable interval. This bootstrap:
#   1. Checks the console for version updates (~50 bytes)
#   2. Downloads new .exe + config only when a new version is available
#   3. Always refreshes secureconfig.json (config changes apply on next login)
#   4. Skips launch if the daemon is already running (prevents duplicates)
#   5. Starts the daemon hidden in the background

# --- CONFIGURE THESE ---
$server        = "http://browserreporter:8000"
$encryptionKey = "CHANGE-ME"
# -----------------------

$dir     = "$env:LOCALAPPDATA\BrowserReporter"
$exe     = "$dir\BrowserReporter.exe"
$cfg     = "$dir\secureconfig.json"
$verFile = "$dir\version.txt"

# Ensure local directory exists
New-Item -ItemType Directory -Path $dir -Force | Out-Null

# Check version and download exe if needed
try {
    $remote = (Invoke-RestMethod "$server/api/agent/version" -TimeoutSec 10).version
    $local  = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { "" }

    if ($remote -ne $local) {
        Invoke-WebRequest "$server/api/agent/exe"    -OutFile $exe -TimeoutSec 120
        $remote | Set-Content $verFile -NoNewline
    }
} catch {
    # Silently continue — agent will run with whatever version is cached locally
}

# Always refresh config (config changes are rare but this is a single small GET)
try {
    Invoke-WebRequest "$server/api/agent/config" -OutFile $cfg -TimeoutSec 30
} catch {
    # Continue with cached config
}

# Check if daemon is already running — don't spawn duplicates
$running = Get-Process -Name "BrowserReporter" -ErrorAction SilentlyContinue
if ($running) { exit 0 }

# Start daemon with encryption key (process-level env var, no admin needed)
if (Test-Path $exe) {
    $env:ENCRYPTION_MASTER_KEY = $encryptionKey
    Start-Process $exe -WindowStyle Hidden
}
