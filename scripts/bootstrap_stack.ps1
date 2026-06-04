<#
.SYNOPSIS
    Bootstrap script: full setup + start in one command.
.DESCRIPTION
    Combines setup and start into a single script for quick launches.
    For more control, use setup_windows.ps1 and start_dashboard_stack.ps1 separately.
.EXAMPLE
    .\scripts\bootstrap_stack.ps1
#>

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SetupScript = Join-Path $RootDir "scripts\setup_windows.ps1"
$StartScript = Join-Path $RootDir "scripts\start_dashboard_stack.ps1"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  OpenCode Ad Dashboard - Bootstrap" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $SetupScript) {
    Write-Host "Running setup..." -ForegroundColor Yellow
    & $SetupScript -SkipTestRun
    Write-Host ""
} else {
    Write-Host "WARNING: setup_windows.ps1 not found, skipping setup." -ForegroundColor Yellow
    Write-Host ""
}

if (Test-Path $StartScript) {
    Write-Host "Starting dashboard stack..." -ForegroundColor Yellow
    & $StartScript
} else {
    Write-Host "ERROR: start_dashboard_stack.ps1 not found." -ForegroundColor Red
    exit 1
}
