<#
.SYNOPSIS
    Starts the OpenCode Ad Dashboard stack (OpenCode server + FastAPI dashboard).
.DESCRIPTION
    Launches both the OpenCode headless AI server and the FastAPI dashboard
    as background processes. Reads password from .env.dashboard if available,
    otherwise generates a random one.
.EXAMPLE
    .\scripts\start_dashboard_stack.ps1
#>

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $RootDir ".venv"
$StorageDir = Join-Path $RootDir ".local-stack"
$PidDir = Join-Path $StorageDir "pids"
$LogDir = Join-Path $StorageDir "logs"

$OpenCodeHost = "127.0.0.1"
$OpenCodePort = "4090"
$DashboardHost = "127.0.0.1"
$DashboardPort = "8787"

$OpenCodePidFile = Join-Path $PidDir "opencode.pid"
$DashboardPidFile = Join-Path $PidDir "dashboard.pid"

# Separated standard output and error log paths to prevent PowerShell crashing
$OpenCodeLog = Join-Path $LogDir "opencode.log"
$OpenCodeErrorLog = Join-Path $LogDir "opencode_error.log"
$DashboardLog = Join-Path $LogDir "dashboard.log"
$DashboardErrorLog = Join-Path $LogDir "dashboard_error.log"

# ============================================================
# Load password from .env.dashboard if available
# ============================================================
$EnvFile = Join-Path $RootDir ".env.dashboard"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^OPENCODE_SERVER_PASSWORD=(.+)$") {
            $PasswordFromFile = $Matches[1].Trim('"').Trim("'")
            break
        }
    }
}

$OpenCodePassword = if ($env:OPENCODE_SERVER_PASSWORD) {
    $env:OPENCODE_SERVER_PASSWORD
} elseif ($PasswordFromFile) {
    $PasswordFromFile
} else {
    # Native PowerShell random string generation
    $Generated = -join ((33..126) | Get-Random -Count 16 | ForEach-Object {[char]$_})
    Write-Host "Generated new password: $Generated" -ForegroundColor Yellow
    Write-Host "Save this to .env.dashboard for future use." -ForegroundColor Yellow
    $Generated
}

$env:OPENCODE_SERVER_PASSWORD = $OpenCodePassword

# ============================================================
# Create directories
# ============================================================
New-Item -ItemType Directory -Force -Path $PidDir, $LogDir | Out-Null

# ============================================================
# Helper: Check if a PID is running
# ============================================================
function Test-PidRunning($pidFile) {
    if (-not (Test-Path $pidFile)) { return $false }
    
    # FIX: Renamed $pid to $savedPid to avoid PowerShell read-only system variable conflict
    $savedPid = Get-Content $pidFile | Select-Object -First 1
    if (-not $savedPid) { return $false }
    try {
        $proc = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
        return $null -ne $proc
    } catch {
        return $false
    }
}

# ============================================================
# Check for existing processes
# ============================================================
$OpenCodeRunning = Test-PidRunning $OpenCodePidFile
$DashboardRunning = Test-PidRunning $DashboardPidFile

if ($OpenCodeRunning -and $DashboardRunning) {
    $OpenCodePid = Get-Content $OpenCodePidFile | Select-Object -First 1
    $DashboardPid = Get-Content $DashboardPidFile | Select-Object -First 1
    Write-Host "Dashboard stack already running!" -ForegroundColor Green
    Write-Host "  OpenCode:  pid $OpenCodePid (http://$OpenCodeHost`:$OpenCodePort)" -ForegroundColor Gray
    Write-Host "  Dashboard: pid $DashboardPid (http://$DashboardHost`:$DashboardPort)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To restart, run: .\scripts\stop_dashboard_stack.ps1 then start again." -ForegroundColor Yellow
    exit 0
}

if ($OpenCodeRunning) {
    Write-Host "OpenCode server already running, skipping..." -ForegroundColor Gray
}

if ($DashboardRunning) {
    Write-Host "Dashboard already running, skipping..." -ForegroundColor Gray
}

# ============================================================
# Verify prerequisites
# ============================================================
if (-not (Test-Path $VenvDir)) {
    Write-Host "ERROR: Virtual environment not found at $VenvDir" -ForegroundColor Red
    Write-Host "Run setup first: .\scripts\setup_windows.ps1" -ForegroundColor Red
    exit 1
}

$UvicornExe = Join-Path $VenvDir "Scripts\uvicorn.exe"
if (-not (Test-Path $UvicornExe)) {
    Write-Host "ERROR: uvicorn not found in virtual environment." -ForegroundColor Red
    Write-Host "Run setup first: .\scripts\setup_windows.ps1" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: opencode CLI not found in PATH." -ForegroundColor Red
    Write-Host "Install it: npm install -g opencode-ai" -ForegroundColor Red
    exit 1
}

# ============================================================
# Start OpenCode server
# ============================================================
if (-not $OpenCodeRunning) {
    Write-Host ""
    Write-Host "Starting OpenCode server on $OpenCodeHost`:$OpenCodePort" -ForegroundColor Cyan

    # FIX: Resolve actual executable path to bypass PowerShell .ps1 execution policy issues with cmd.exe
    $OpenCodeExe = $null
    $npmGlobalPath = Join-Path $env:APPDATA "npm\node_modules\opencode-ai\bin\opencode.exe"
    if (Test-Path $npmGlobalPath) {
        $OpenCodeExe = $npmGlobalPath
    } else {
        $CmdPath = Get-Command opencode -ErrorAction SilentlyContinue
        if ($CmdPath) {
            $BaseDir = Split-Path $CmdPath.Source -Parent
            $FallbackExe = Join-Path $BaseDir "node_modules\opencode-ai\bin\opencode.exe"
            if (Test-Path $FallbackExe) { $OpenCodeExe = $FallbackExe }
        }
    }

    if ($OpenCodeExe) {
        $openCodeArgs = "serve --hostname $OpenCodeHost --port $OpenCodePort --cors http://$DashboardHost`:$DashboardPort"
        $openCodeProc = Start-Process -FilePath $OpenCodeExe -ArgumentList $openCodeArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $OpenCodeLog -RedirectStandardError $OpenCodeErrorLog
    } else {
        # Fallback: Use PowerShell Bypass if .exe not found
        $Ps1Path = (Get-Command opencode -ErrorAction SilentlyContinue).Source
        if ($Ps1Path) {
            $openCodeArgs = "-ExecutionPolicy Bypass -File `"$Ps1Path`" serve --hostname $OpenCodeHost --port $OpenCodePort --cors http://$DashboardHost`:$DashboardPort"
            $openCodeProc = Start-Process -FilePath "powershell.exe" -ArgumentList $openCodeArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $OpenCodeLog -RedirectStandardError $OpenCodeErrorLog
        } else {
            Write-Host "ERROR: Could not resolve opencode executable." -ForegroundColor Red
            exit 1
        }
    }
    
    $openCodeProc.Id | Set-Content -Path $OpenCodePidFile
    Write-Host "OpenCode started with pid $($openCodeProc.Id)" -ForegroundColor Green
}

# ============================================================
# Start Dashboard
# ============================================================
if (-not $DashboardRunning) {
    Write-Host ""
    Write-Host "Starting dashboard API/UI on $DashboardHost`:$DashboardPort" -ForegroundColor Cyan

    $uvicornArgs = "dashboard.backend.app:app --host $DashboardHost --port $DashboardPort --app-dir `"$RootDir`""
    
    # Wrapped path in quotes just in case your Windows username or path contains spaces
    $dashboardProc = Start-Process -FilePath "$UvicornExe" -ArgumentList $uvicornArgs -PassThru -WindowStyle Hidden -WorkingDirectory $RootDir -RedirectStandardOutput $DashboardLog -RedirectStandardError $DashboardErrorLog
    
    $dashboardProc.Id | Set-Content -Path $DashboardPidFile
    Write-Host "Dashboard started with pid $($dashboardProc.Id)" -ForegroundColor Green
}

# ============================================================
# Wait for dashboard to be ready
# ============================================================
Write-Host ""
Write-Host "Waiting for dashboard..." -ForegroundColor Yellow

$DashboardReady = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://$DashboardHost`:$DashboardPort/api/defaults" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($r.StatusCode -eq 200) {
            Write-Host "Dashboard is ready!" -ForegroundColor Green
            $DashboardReady = $true
            break
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}

if (-not $DashboardReady) {
    Write-Host ""
    Write-Host "WARNING: Dashboard did not become ready within 30 seconds." -ForegroundColor Yellow
    Write-Host "Check logs for errors:" -ForegroundColor Yellow
    Write-Host "  Dashboard Error Log: $DashboardErrorLog" -ForegroundColor Gray
    Write-Host "  OpenCode Error Log:  $OpenCodeErrorLog" -ForegroundColor Gray
}

# ============================================================
# Print connection info
# ============================================================
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Dashboard Stack Running" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Dashboard URL: http://$DashboardHost`:$DashboardPort" -ForegroundColor Cyan
Write-Host "OpenCode URL:  http://$OpenCodeHost`:$OpenCodePort" -ForegroundColor Cyan
Write-Host "OpenCode password: $OpenCodePassword" -ForegroundColor Cyan
Write-Host ""
Write-Host "Logs:" -ForegroundColor Gray
Write-Host "  Dashboard: $DashboardLog" -ForegroundColor Gray
Write-Host "  OpenCode:  $OpenCodeLog" -ForegroundColor Gray
Write-Host ""

# Open browser
try {
    Start-Process "http://$DashboardHost`:$DashboardPort"
} catch {
    Write-Host "Open browser manually: http://$DashboardHost`:$DashboardPort" -ForegroundColor Gray
}

Write-Host "To stop the dashboard:" -ForegroundColor Yellow
Write-Host "  .\scripts\stop_dashboard_stack.ps1" -ForegroundColor White
Write-Host ""
