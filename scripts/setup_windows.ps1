<#
.SYNOPSIS
    Complete Windows setup script for OpenCode Ad Dashboard.
.DESCRIPTION
    Installs all dependencies from scratch:
    - Checks Python 3.10+ and Node.js (Auto-installs Node if missing)
    - Creates and activates a Python venv
    - Installs Python dependencies
    - Installs OpenCode CLI via npm
    - Sets up OpenCode server password
    - Creates storage directories
    - Runs a test start of the dashboard
.PARAMETER SkipOpenCodeInstall
    Skip OpenCode CLI installation.
.PARAMETER SkipTestRun
    Skip the dashboard test run after setup.
.PARAMETER Password
    Set a custom OpenCode server password (auto-generated if not provided).
.EXAMPLE
    .\scripts\setup_windows.ps1
    .\scripts\setup_windows.ps1 -Password "my-secret-pass"
    .\scripts\setup_windows.ps1 -SkipTestRun
#>

param(
    [switch]$SkipOpenCodeInstall,
    [switch]$SkipTestRun,
    [string]$Password
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $RootDir ".venv"
$ScriptsDir = Join-Path $VenvDir "Scripts"
$StorageDir = Join-Path $RootDir "dashboard_storage"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  OpenCode Ad Dashboard - Windows Setup" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# [1/8] Check Python 3.10+
# ============================================================
Write-Host "[1/8] Checking Python 3.10+" -ForegroundColor Yellow

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $PythonCmd = $cmd
        break
    }
}

if (-not $PythonCmd) {
    Write-Host "ERROR: Python not found." -ForegroundColor Red
    Write-Host "Install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Red
    exit 1
}

$PythonVersion = & $PythonCmd --version 2>&1
Write-Host "Found: $PythonVersion" -ForegroundColor Green

$VersionNumber = ($PythonVersion -replace 'Python ', '') -split '\.' | Select-Object -First 2
$Major = [int]$VersionNumber[0]
$Minor = [int]$VersionNumber[1]

if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 10)) {
    Write-Host "ERROR: Python 3.10+ required, found $Major.$Minor" -ForegroundColor Red
    exit 1
}

# ============================================================
# [2/8] Check Node.js / npm (Auto-Install if missing)
# ============================================================
Write-Host ""
Write-Host "[2/8] Checking Node.js / npm" -ForegroundColor Yellow

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js (npm) not found. Installing automatically via winget..." -ForegroundColor Yellow
    winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    
    # Refresh paths so setup can continue without restarting terminal
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

$NodeVersion = node --version
$NpmVersion = npm --version
Write-Host "Node.js: $NodeVersion" -ForegroundColor Green
Write-Host "npm: $NpmVersion" -ForegroundColor Green

# ============================================================
# [3/8] Create Virtual Environment
# ============================================================
Write-Host ""
Write-Host "[3/8] Creating virtual environment" -ForegroundColor Yellow

if (Test-Path $VenvDir) {
    Write-Host "Using existing .venv/" -ForegroundColor Gray
} else {
    & $PythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "Created .venv/" -ForegroundColor Green
}

$Pip = Join-Path $ScriptsDir "pip.exe"
$Python = Join-Path $ScriptsDir "python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "ERROR: Virtual environment python not found at $Python" -ForegroundColor Red
    exit 1
}

# ============================================================
# [4/8] Install Python Dependencies
# ============================================================
Write-Host ""
Write-Host "[4/8] Installing Python dependencies" -ForegroundColor Yellow

$ReqFile = Join-Path $RootDir "requirements-dashboard.txt"
if (-not (Test-Path $ReqFile)) {
    Write-Host "ERROR: requirements-dashboard.txt not found at $ReqFile" -ForegroundColor Red
    exit 1
}

Write-Host "Upgrading pip..." -ForegroundColor Gray
& $Python -m pip install --upgrade pip -q

Write-Host "Installing packages from requirements-dashboard.txt..." -ForegroundColor Gray
& $Pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install Python dependencies" -ForegroundColor Red
    exit 1
}
Write-Host "Python dependencies installed" -ForegroundColor Green

# ============================================================
# [5/8] Install OpenCode CLI
# ============================================================
Write-Host ""
Write-Host "[5/8] Installing OpenCode CLI" -ForegroundColor Yellow

if ($SkipOpenCodeInstall) {
    Write-Host "Skipping OpenCode installation (--SkipOpenCodeInstall)" -ForegroundColor Gray
} else {
    $OpenCodeFound = $false
    try {
        $null = Get-Command opencode -ErrorAction Stop
        $OpenCodeFound = $true
    } catch {
        $OpenCodeFound = $false
    }

    if ($OpenCodeFound) {
        $OCVersion = opencode --version 2>$null
        Write-Host "OpenCode already installed: $OCVersion" -ForegroundColor Green
    } else {
        Write-Host "Installing opencode-ai globally via npm..." -ForegroundColor Gray
        # FIX: Correct npm package name
        npm install -g opencode-ai
        
        # Refresh paths immediately
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: npm install -g opencode-ai failed." -ForegroundColor Yellow
            Write-Host "You may need to run PowerShell as Administrator or fix npm global prefix." -ForegroundColor Yellow
            Write-Host "Alternative: install from https://open-code.ai/docs/cli" -ForegroundColor Yellow
        } else {
            Write-Host "OpenCode CLI installed" -ForegroundColor Green
        }
    }
}

$OpenCodeAvailable = $false
try {
    $null = Get-Command opencode -ErrorAction Stop
    $OpenCodeAvailable = $true
} catch {
    $OpenCodeAvailable = $false
}

if (-not $OpenCodeAvailable) {
    Write-Host "WARNING: OpenCode CLI not found in PATH." -ForegroundColor Yellow
    Write-Host "Install manually: npm install -g opencode-ai" -ForegroundColor Yellow
    Write-Host "Or download from: https://open-code.ai/docs/cli" -ForegroundColor Yellow
} else {
    opencode --version 2>$null
}

# ============================================================
# [6/7] Setup OpenCode Password and Auth
# ============================================================
Write-Host ""
Write-Host "[6/7] Setting up OpenCode server password" -ForegroundColor Yellow

if (-not $Password) {
    # FIX: Replaced System.Web.Security with native PowerShell generation
    $Password = -join ((33..126) | Get-Random -Count 16 | ForEach-Object {[char]$_})
    Write-Host "Generated random password: $Password" -ForegroundColor Cyan
}

$env:OPENCODE_SERVER_PASSWORD = $Password

$PasswordFile = Join-Path $RootDir ".env.dashboard"
$PasswordContent = @"
# OpenCode Ad Dashboard - Auto-generated by setup_windows.ps1
# Do not share this file. Add it to .gitignore.
OPENCODE_SERVER_PASSWORD=$Password
OPENCODE_API_URL=http://127.0.0.1:4090
"@

if (Test-Path $PasswordFile) {
    $Existing = Get-Content $PasswordFile -Raw
    if ($Existing -match "OPENCODE_SERVER_PASSWORD=") {
        Write-Host "Password already set in .env.dashboard (keeping existing)" -ForegroundColor Gray
        $ExistingPassword = ($Existing | Select-String "OPENCODE_SERVER_PASSWORD=(.+)").Matches.Groups[1].Value
        $env:OPENCODE_SERVER_PASSWORD = $ExistingPassword
        $Password = $ExistingPassword
    } else {
        Add-Content -Path $PasswordFile -Value "`nOPENCODE_SERVER_PASSWORD=$Password"
        Add-Content -Path $PasswordFile -Value "OPENCODE_API_URL=http://127.0.0.1:4090"
        Write-Host "Password appended to .env.dashboard" -ForegroundColor Green
    }
} else {
    Set-Content -Path $PasswordFile -Value $PasswordContent
    Write-Host "Created .env.dashboard with password" -ForegroundColor Green
}

if ($OpenCodeAvailable) {
    Write-Host "Initializing OpenCode config..." -ForegroundColor Gray

    $OpencodeDataDir = if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA "opencode"
    } else {
        Join-Path (Join-Path $env:USERPROFILE "AppData") "Local\opencode"
    }

    $AuthFile = Join-Path $OpencodeDataDir "auth.json"
    if (-not (Test-Path $OpencodeDataDir)) {
        New-Item -ItemType Directory -Force -Path $OpencodeDataDir | Out-Null
    }

    if (-not (Test-Path $AuthFile)) {
        Write-Host "Creating OpenCode auth file..." -ForegroundColor Gray
        $AuthJson = @{
            server_password = $Password
        } | ConvertTo-Json -Depth 10
        Set-Content -Path $AuthFile -Value $AuthJson -Encoding UTF8
        Write-Host "Created auth.json" -ForegroundColor Green
    } else {
        Write-Host "OpenCode auth.json already exists" -ForegroundColor Gray
    }
}

# ============================================================
# [7/7] Create Storage Directories
# ============================================================
Write-Host ""
Write-Host "[7/7] Creating storage directories" -ForegroundColor Yellow

foreach ($sub in @("pids", "logs", "runs")) {
    $dir = Join-Path $StorageDir $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "  Created $sub/" -ForegroundColor Gray
    }
}

$InputDir = Join-Path $RootDir "input"
foreach ($sub in @("docs", "images")) {
    $dir = Join-Path $InputDir $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "  Created input/$sub/" -ForegroundColor Gray
    }
}

$RuntimeDir = Join-Path $RootDir "runtime"
foreach ($sub in @("opencode_queue", "gemini_selected_prompts", "chatgpt_selected_prompts", "conversion_916_prompts", "generation_logs")) {
    $dir = Join-Path $RuntimeDir $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "  Created runtime/$sub/" -ForegroundColor Gray
    }
}

foreach ($sub in @("output", "generated_images")) {
    $dir = Join-Path $RootDir $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "  Created $sub/" -ForegroundColor Gray
    }
}

# ============================================================
# Summary
# ============================================================
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "OpenCode server password: $Password" -ForegroundColor Cyan
Write-Host "Password saved to: $PasswordFile" -ForegroundColor Gray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Configure AI provider: opencode providers login" -ForegroundColor White
Write-Host "  2. Verify models: opencode models" -ForegroundColor White
Write-Host "  3. Place your product master doc in: input/docs/" -ForegroundColor White
Write-Host "  4. Place images in: input/images/" -ForegroundColor White
Write-Host ""

# ============================================================
# Test Run
# ============================================================
if (-not $SkipTestRun) {
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  Starting Dashboard Test Run..." -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""

    $StartScript = Join-Path $RootDir "scripts\start_dashboard_stack.ps1"
    if (Test-Path $StartScript) {
        Write-Host "Launching start_dashboard_stack.ps1..." -ForegroundColor Yellow
        Write-Host ""

        $env:OPENCODE_SERVER_PASSWORD = $Password

        & $StartScript

        Write-Host ""
        Write-Host "Test run initiated!" -ForegroundColor Green
        Write-Host "Dashboard should be available at: http://127.0.0.1:8787" -ForegroundColor Cyan
        Write-Host "OpenCode server should be available at: http://127.0.0.1:4090" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "To stop the dashboard:" -ForegroundColor Yellow
        Write-Host "  .\scripts\stop_dashboard_stack.ps1" -ForegroundColor White
    } else {
        Write-Host "ERROR: start_dashboard_stack.ps1 not found at $StartScript" -ForegroundColor Red
    }
} else {
    Write-Host "Test run skipped (--SkipTestRun)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To start the dashboard:" -ForegroundColor Yellow
    Write-Host "  .\scripts\start_dashboard_stack.ps1" -ForegroundColor White
}

Write-Host ""
Write-Host "To stop the dashboard:" -ForegroundColor Yellow
Write-Host "  .\scripts\stop_dashboard_stack.ps1" -ForegroundColor White
Write-Host ""