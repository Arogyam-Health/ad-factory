# WSL Setup Guide - OpenCode Ad Dashboard

## Prerequisites (Windows side)

1. **Install WSL** (PowerShell as Administrator):
   ```powershell
   wsl --install -d Ubuntu
   ```
   Reboot when prompted.

2. **Disable Windows PATH in WSL** (prevents npm/CLI conflicts):
   ```powershell
   wsl --shutdown
   ```
   Then in WSL, create `/etc/wsl.conf`:
   ```bash
   echo -e "[interop]\nappendWindowsPath=false" | sudo tee /etc/wsl.conf
   ```
   Restart WSL: `wsl --shutdown` (from Windows), then reopen Ubuntu.

## Fresh WSL Setup (one-time)

```bash
# 1) Install system dependencies
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl git

# 2) Install Node.js LTS (Linux version)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs

# 3) Verify Linux npm (NOT /mnt/c/...)
which npm
# Should show /usr/bin/npm or /usr/local/bin/npm

# 4) Clone repo inside WSL home (important: NOT /mnt/c)
cd ~
git clone <YOUR_REPO_URL> ad-factory
cd ad-factory
git checkout <YOUR_BRANCH>

# 5) Run setup script
bash scripts/setup_wsl.sh

# 6) Login to AI provider
opencode providers login

# 7) Verify models
opencode models
```

## Chrome CDP Setup (for visible browser image generation)

### Windows PowerShell (Run as Administrator)

Run these **once** in an elevated PowerShell window. Navigate to your `ad-factory` folder first:

```powershell
cd C:\path\to\your\ad-factory

# 1) Configure port proxy (WSL2 -> Windows Chrome CDP)
powershell -ExecutionPolicy Bypass -File ".\scripts\setup_cdp_proxy.ps1"

# 2) Add firewall rule for CDP port
powershell -ExecutionPolicy Bypass -File ".\scripts\add_cdp_firewall_rule.ps1"
```

**Or run the commands directly** (no script files needed):

```powershell
# Port proxy
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1

# Firewall rule
New-NetFirewallRule -DisplayName "CDP Port Proxy 9223" -Direction Inbound -Protocol TCP -LocalPort 9223 -Action Allow
```

These scripts:
- Forward port `9223` on Windows to Chrome's CDP port `9222` on localhost
- Allow inbound TCP traffic on port `9223` through Windows Firewall
- Persist across reboots (no need to run again)

### WSL (every session)

```bash
cd ~/ad-factory
git pull origin windows-setup

# Start the dashboard stack
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh
```

## Daily Usage

```bash
cd ~/ad-factory

# Pull latest changes
git pull origin windows-setup

# Start stack
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh

# Open in Windows browser: http://127.0.0.1:8787

# Stop stack
bash scripts/stop_dashboard_stack.sh
```

## Image Generation Workflow

1. **Launch visible Chrome browser** from dashboard UI
   - Click "Launch Visible Browser" button
   - Chrome opens on Windows with CDP debugging enabled
   - Log in to ChatGPT manually in the Chrome window

2. **Trigger image generation** from dashboard
   - Select prompts and click generate
   - Backend connects to Chrome via CDP on port 9223
   - Images are uploaded and generated automatically

3. **Kill Chrome** when done
   - Click "Kill Chrome" button in dashboard
   - Or close Chrome window manually

## Troubleshooting

### "Session not found" errors
```bash
pkill -f opencode
rm -rf ~/.local/share/opencode
opencode providers login
bash scripts/start_dashboard_stack.sh
```

### Chrome CDP connection fails
```bash
# Verify port proxy is active (Windows PowerShell as Admin)
netsh interface portproxy show v4tov4

# Should show: 0.0.0.0:9223 -> 127.0.0.1:9222

# Test CDP from WSL
curl -s http://172.18.160.1:9223/json/version
# Should return Chrome version info

# If not working, re-run port proxy setup (Windows PowerShell as Admin)
cd C:\path\to\your\ad-factory
powershell -ExecutionPolicy Bypass -File ".\scripts\setup_cdp_proxy.ps1"
```

### Port already in use
```bash
bash scripts/stop_dashboard_stack.sh
# Or manually:
pkill -f opencode
pkill -f uvicorn
```

### Image upload shows broken thumbnail
- Images are automatically copied to `C:\Users\jadam\.ad-factory-upload-temp\` before upload
- If upload fails, ensure Chrome has access to this Windows path
- Check that the image file exists in `~/ad-factory/input/images/`

## Architecture Notes

- **OpenCode CLI**: Runs in WSL Linux, communicates with local server
- **Dashboard Backend**: Python FastAPI in WSL, serves UI on port 8787
- **OpenCode Server**: Node.js in WSL, listens on port 4090
- **Chrome CDP**: Windows Chrome instance controlled via CDP protocol
- **Port Proxy**: Windows `netsh` forwards WSL requests (port 9223) to Chrome (port 9222)
- **Image Upload**: Images copied from WSL filesystem to Windows temp folder before CDP upload

All backend components run inside WSL. Windows hosts Chrome browser and handles port forwarding.
