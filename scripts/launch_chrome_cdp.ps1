# Chrome CDP Launcher for Windows
# Called from WSL via powershell.exe
param(
    [string]$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [string]$UserDataDir = "$env:USERPROFILE\.config\google-chrome-cdp",
    [int]$Port = 9222
)

# Kill existing Chrome
Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 5

# Ensure data dir exists
if (!(Test-Path $UserDataDir)) {
    New-Item -ItemType Directory -Path $UserDataDir -Force | Out-Null
}

# Launch Chrome
$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$UserDataDir",
    "--no-first-run",
    "--no-default-browser-check"
)
Start-Process $ChromePath -ArgumentList $arguments -WindowStyle Normal

# Wait for CDP using TCP socket check (avoids CLOSE_WAIT connection leak)
for ($i = 0; $i -lt 30; $i++) {
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $tcpClient.Connect("127.0.0.1", $Port)
        $tcpClient.Close()
        Write-Output "SUCCESS"
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    }
}

Write-Output "FAILED"
exit 1
