# Image Generation Runner for Windows
# Called from WSL to run ChatGPT automation on Windows side
param(
    [string]$PromptDir,
    [string]$OutDir,
    [string]$UploadDir,
    [string]$Timeout = "420",
    [string]$DownloadTimeout = "90",
    [string]$ManualLoginTimeout = "180",
    [string]$ImageSourceFile = "",
    [string]$Headless = "false"
)

# Convert WSL paths to Windows paths
function Convert-WslPath {
    param([string]$Path)
    if ($Path -match "^/mnt/([a-zA-Z])/") {
        return $Path -replace "^/mnt/([a-zA-Z])/", '$1:\' -replace "/", "\"
    }
    return $Path
}

$winPromptDir = Convert-WslPath $PromptDir
$winOutDir = Convert-WslPath $OutDir
$winUploadDir = Convert-WslPath $UploadDir
$winImageSourceFile = if ($ImageSourceFile) { Convert-WslPath $ImageSourceFile } else { "" }

# Find Windows Python
$pythonExe = "python"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $pythonExe = "python3"
} else {
    # Try common Windows Python paths
    $pythonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "C:\Program Files\Python3*\python.exe"
    )
    foreach ($pattern in $pythonPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $pythonExe = $found.FullName
            break
        }
    }
}

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$automationScript = Join-Path $scriptDir "chatgpt_web_sutomation.py"

# Build arguments
$args = @(
    $automationScript,
    "--prompt-dir", $winPromptDir,
    "--prompt-glob", "*.txt",
    "--out-dir", $winOutDir,
    "--timeout", $Timeout,
    "--download-timeout", $DownloadTimeout,
    "--manual-login-timeout", $ManualLoginTimeout,
    "--upload-dir", $winUploadDir,
    "--cdp-url", "http://127.0.0.1:9222"
)

if ($ImageSourceFile) {
    $args += "--image-source-file"
    $args += $winImageSourceFile
}

if ($Headless -eq "true") {
    $args += "--headless"
}

Write-Output "Running: $pythonExe $args"
& $pythonExe $args 2>&1
exit $LASTEXITCODE
