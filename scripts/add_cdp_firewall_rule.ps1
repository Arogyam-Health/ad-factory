# Add Windows Firewall rule for CDP Port Proxy
# Run as Administrator

$Port = 9223

# Check if rule already exists
$existing = Get-NetFirewallRule -DisplayName "CDP Port Proxy $Port" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule already exists."
} else {
    Write-Host "Adding firewall rule for port $Port..."
    New-NetFirewallRule -DisplayName "CDP Port Proxy $Port" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow
    Write-Host "Firewall rule added."
}
