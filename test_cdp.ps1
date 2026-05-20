# Test Chrome CDP Launch
Write-Host "Killing Chrome..."
taskkill /F /IM chrome.exe 2>$null | Out-Null
Start-Sleep -Seconds 5

Write-Host "Launching Chrome with CDP..."
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "C:\Users\jadam\.config\google-chrome-cdp"

if (!(Test-Path $userDataDir)) {
    New-Item -ItemType Directory -Path $userDataDir -Force | Out-Null
}

Start-Process $chromePath -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=$userDataDir",
    "--no-first-run",
    "--no-default-browser-check"
) -WindowStyle Normal

Write-Host "Waiting for CDP..."
Start-Sleep -Seconds 8

$cdpUrl = "http://127.0.0.1:9222/json/version"
try {
    $response = Invoke-WebRequest -Uri $cdpUrl -TimeoutSec 5 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "SUCCESS: CDP is responding"
        $response.Content.Substring(0, [Math]::Min(200, $response.Content.Length))
    }
} catch {
    Write-Host "FAILED: CDP not responding"
    Write-Host "Error: $_"
    
    # Check if Chrome is running
    $chromeProcs = Get-Process chrome -ErrorAction SilentlyContinue
    if ($chromeProcs) {
        Write-Host "Chrome is running ($($chromeProcs.Count) processes)"
    } else {
        Write-Host "Chrome is NOT running"
    }
    
    # Check port
    $portCheck = netstat -ano | Select-String "9222"
    if ($portCheck) {
        Write-Host "Port 9222 is listening:"
        $portCheck
    } else {
        Write-Host "Port 9222 is NOT listening"
    }
}
