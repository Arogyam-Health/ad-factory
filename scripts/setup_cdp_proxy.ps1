# Setup CDP Port Proxy for WSL2
# Run this ONCE as Administrator to allow WSL2 to access Chrome's CDP endpoint
# Chrome binds CDP to 127.0.0.1, but WSL2 needs to access via the Windows host IP

$Port = 9222

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")

if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator', then run this script." -ForegroundColor Yellow
    exit 1
}

# Remove existing port proxy if any
Write-Host "Removing existing port proxy (if any)..."
netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null | Out-Null

# Add port proxy
Write-Host "Adding port proxy: 0.0.0.0:$Port -> 127.0.0.1:$Port"
$result = netsh interface portproxy add v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=127.0.0.1 2>&1

if ($result -and $result -notmatch "Ok$") {
    Write-Host "ERROR: Failed to add port proxy: $result" -ForegroundColor Red
    exit 1
}

# Verify port proxy
Write-Host "`nActive port proxies:"
netsh interface portproxy show v4tov4

Write-Host "`nSUCCESS: Port proxy configured. WSL2 can now access Chrome CDP via the Windows host IP." -ForegroundColor Green
Write-Host "You only need to run this once. The proxy persists across reboots." -ForegroundColor Gray
