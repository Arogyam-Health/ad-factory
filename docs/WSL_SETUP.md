# WSL Setup Guide - OpenCode Ad Dashboard

> **Works on both Windows on x86_64 and Windows on ARM (Snapdragon / Qualcomm).**
> All Python wheels, Node.js, Chrome for Linux, and the PowerShell scripts
> ship ARM64 builds. The only ARM-specific risk is WSL installing the
> **x86_64** Ubuntu build instead of the **ARM64** one — verify with
> `uname -m` (must print `aarch64`, not `x86_64`). See the
> [ARM / Snapdragon verification](#arm--snapdragon-verification) section.

## Prerequisites (Windows side)

1. **Install WSL** (PowerShell as Administrator):
   ```powershell
   wsl --install -d Ubuntu
   ```
   Reboot when prompted.

   > **On Snapdragon / Windows on ARM:** the command above installs the
   > ARM64 build of Ubuntu by default. If you ever see `x86_64` from
   > `uname -m` inside WSL, force the ARM64 build with:
   > ```powershell
   > wsl --unregister Ubuntu
   > wsl --install -d Ubuntu --web-download
   > ```
   > then pick the `arm64` variant from the download page.

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

**Option A: Run the script files** (recommended)

Access the scripts via the WSL network path from Windows:

```powershell
# Replace <username> with your WSL username (e.g., jadam)
$scriptPath = "\\wsl$\Ubuntu\home\<username>\ad-factory\scripts"

# 1) Configure port proxy
powershell -ExecutionPolicy Bypass -File "$scriptPath\setup_cdp_proxy.ps1"

# 2) Add firewall rule
powershell -ExecutionPolicy Bypass -File "$scriptPath\add_cdp_firewall_rule.ps1"
```

**Option B: Run commands directly** (if Option A doesn't work)

```powershell
# 1) Configure port proxy
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1

# 2) Add firewall rule
New-NetFirewallRule -DisplayName "CDP Port Proxy 9223" -Direction Inbound -Protocol TCP -LocalPort 9223 -Action Allow
```

**Verify it worked:**
```powershell
netsh interface portproxy show v4tov4
# Should show: 0.0.0.0:9223 -> 127.0.0.1:9222
```

These commands:
- Forward port `9223` on Windows to Chrome's CDP port `9222` on localhost
- Allow inbound TCP traffic on port `9223` through Windows Firewall
- Persist across reboots (run once only)

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
$scriptPath = "\\wsl$\Ubuntu\home\<username>\ad-factory\scripts"
powershell -ExecutionPolicy Bypass -File "$scriptPath\setup_cdp_proxy.ps1"

# Or run directly:
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1
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

## ARM / Snapdragon verification

The `windows-setup` branch is arch-agnostic. These checks confirm you're
running natively on ARM64, not under x86_64 emulation:

```bash
# 1. WSL is ARM64 (must say aarch64, NOT x86_64)
uname -m
# aarch64   <-- correct on Snapdragon
# x86_64    <-- WSL is emulating x86; reinstall with --web-download (see step 1 above)

# 2. Python is the Linux/ARM64 build
python3 -c "import platform; print(platform.machine())"
# aarch64

# 3. Node.js is the Linux/ARM64 build
node -e "console.log(process.arch)"
# arm64

# 4. Chrome in WSL is the ARM64 build (if installed via setup_wsl.sh)
file "$(which google-chrome)" | head -1
# ELF 64-bit LSB pie executable, ARM aarch64 ...

# 5. Inside the venv, all pip packages resolved as manylinux / py3-none-any
.venv/bin/pip list --format=columns | head
```

### What works on ARM out-of-the-box

| Component | ARM64 availability |
| --- | --- |
| `psutil 7.2.2` | manylinux `aarch64` wheel |
| `Pillow 12.2.0` | manylinux `aarch64` wheel |
| `playwright 1.59.0` | `manylinux_2_17_aarch64` wheel + ARM64 Chromium |
| `selenium 4.32.0` | pure Python (`py3-none-any`) |
| `fastapi`, `uvicorn`, `openpyxl`, `opencode-ai`, `python-multipart` | pure Python |
| Node.js LTS (20.x / 22.x) | ARM64 Linux binary via nodesource |
| Google Chrome stable (Linux) | ARM64 `.deb` from dl.google.com |
| `netsh portproxy`, `New-NetFirewallRule` (Windows host) | arch-agnostic — identical on x64 and ARM |
| Windows host Chrome | native ARM64 build auto-installed by Chrome installer |

### ARM-specific gotchas

- **Slow first-time `pip install`**: ARM64 wheels for some packages
  (especially `playwright`) are larger than x86_64 equivalents. Allow
  extra time on first run.
- **Playwright Chromium download**: the `playwright install chromium`
  command downloads the correct ARM64 build automatically; no extra
  flags needed.
- **Windows Chrome under emulation**: if you accidentally install the
  x86_64 Chrome on Windows on ARM, it runs under Prism emulation (slow).
  Always pick the ARM64 installer from <https://google.com/chrome/> when
  offered.
- **No code changes needed**: the dashboard's `run_opencode`,
  `gemini_web_automation.py`, `chatgpt_web_sutomation.py` all use
  hardcoded Linux paths (`/usr/bin/google-chrome`, etc.) that are
  identical on x64 and ARM64 Ubuntu.

