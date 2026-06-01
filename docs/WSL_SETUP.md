# WSL Setup Guide - OpenCode Ad Dashboard

> **Works on both Windows on x86_64 and Windows on ARM (Snapdragon / Qualcomm).**
> All Python wheels, Node.js, Chrome for Linux, and the PowerShell scripts
> ship ARM64 builds. The only ARM-specific risk is WSL installing the
> **x86_64** Ubuntu build instead of the **ARM64** one — verify with
> `uname -m` (must print `aarch64`, not `x86_64`). See the
> [ARM / Snapdragon verification](#arm--snapdragon-verification) section.

## System architecture (read this first)

The dashboard runs **two processes on two operating systems at once**:

```
┌────────────────── WSL2 (Ubuntu 24.04) ───────────────────┐
│                                                          │
│   Dashboard (FastAPI, 127.0.0.1:8787)                    │
│   OpenCode Server (127.0.0.1:4090)                       │
│   Python automation:                                     │
│     scripts/gemini_web_automation.py                     │
│     scripts/chatgpt_web_sutomation.py                    │
│   scripts/cdp_proxy.py  (TCP proxy, listens :9223)       │
│                                                          │
└──────────────────────────┬───────────────────────────────┘
                           │  HTTP to http://172.18.160.1:9223
                           │  (172.18.160.1 = WSL2's view of
                           │   the Windows host IP)
                           ▼
┌─────────────── Windows host (your laptop) ────────────────┐
│                                                          │
│   netsh portproxy: 0.0.0.0:9223 → 127.0.0.1:9222         │
│   (set by scripts/setup_cdp_proxy.ps1, ONCE)            │
│                                                          │
│   Chrome (real browser, visible on your desktop)         │
│     --remote-debugging-port=9222                        │
│     --user-data-dir=%USERPROFILE%\.config\google-       │
│                     chrome-cdp                          │
│   (launched by scripts/launch_chrome_cdp.ps1)           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**What runs where:**

| Process | Where it runs | How it's started |
| --- | --- | --- |
| Dashboard (FastAPI :8787) | WSL | `bash scripts/start_dashboard_stack.sh` |
| OpenCode CLI + Server (:4090) | WSL | same script, also `opencode serve` |
| `gemini_web_automation.py` | WSL | dashboard button or CLI |
| `chatgpt_web_sutomation.py` | WSL | dashboard button or CLI |
| `cdp_proxy.py` (:9223) | WSL | started on demand by automation |
| **Chrome browser** | **Windows host** | PowerShell `launch_chrome_cdp.ps1` |
| Port proxy (9223→9222) | Windows host | PowerShell `setup_cdp_proxy.ps1` (once) |
| Firewall rule (:9223) | Windows host | PowerShell `add_cdp_firewall_rule.ps1` (once) |

**This is the architecture the `windows-setup` branch was designed
around**, going back to before the merge with `current_working`. All
the PowerShell scripts (`launch_chrome_cdp.ps1`, `setup_cdp_proxy.ps1`,
`add_cdp_firewall_rule.ps1`, `run_image_gen_windows.ps1`) and
`scripts/cdp_proxy.py` exist to make this WSL-host / Windows-Chrome
split work.

The launch flow when you click "Launch Visible Browser" in the
dashboard (`dashboard/backend/app.py:5820-5909`):

1. Detect WSL2 (`is_wsl` check).
2. Find the Windows host IP from `ip route | grep default` (yields
   `172.18.160.1` on default WSL2 networks).
3. Check if Chrome CDP is already responding on
   `http://<win_host_ip>:9222`; if so, return immediately.
4. Otherwise, `taskkill.exe /F /IM chrome.exe` to kill any stale Chrome.
5. Verify port 9222 is free.
6. Look for Linux Chrome (`/usr/bin/google-chrome` etc.); if not
   present, look for Windows Chrome at
   `/mnt/c/Program Files/Google/Chrome/Application/chrome.exe`.
7. **If using Windows Chrome, call PowerShell** via
   `subprocess.run(["powershell.exe", "-ExecutionPolicy", "Bypass",
   "-File", <path-to-launch_chrome_cdp.ps1>])`. This bypasses the WSL2
   localhost networking issue (Chrome binds to 127.0.0.1:9222 on
   Windows, which is *not* the same as 127.0.0.1 inside WSL2).
8. PowerShell launches Chrome with `--remote-debugging-port=9222` and
   waits for the TCP port to open.
9. WSL automation scripts then connect to the Chrome instance via
   `http://<win_host_ip>:9223` (port 9223 is the portproxy entry
   point on the Windows host that forwards to 9222).

## Prerequisites (Windows side)

### 1. Install WSL

**On Windows on x86_64 (regular Intel/AMD laptops):**

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu
```

Reboot when prompted. Then open **Ubuntu** from the Start menu, create
a UNIX username + password, and you're in.

**On Windows on ARM (Snapdragon / Qualcomm laptops):**

WSL2 supports ARM64 on Windows on ARM, but the install path is a
little different. Follow these exact steps:

1. **Open PowerShell as Administrator** (right-click Start → "Terminal
   (Admin)" or "PowerShell (Admin)").

2. **Check the host architecture** (sanity check):
   ```powershell
   [System.Reflection.Assembly]::ImageRuntimeArchitecture
   # Arm64  -> you have a Snapdragon
   # X64    -> you have Intel/AMD
   ```

3. **Check if WSL is already installed**:
   ```powershell
   wsl --status
   wsl --list --verbose
   ```
   If you see any installed distros, note their names and continue to
   step 4 to verify the architecture. If nothing is installed, jump to
   step 5.

4. **Check the architecture of an installed distro** (e.g. `Ubuntu`):
   ```powershell
   wsl -d Ubuntu uname -m
   ```
   Must print `aarch64`. If it prints `x86_64`, you have the wrong
   build — jump to step 6 to reinstall.

5. **Install WSL + Ubuntu (ARM64 build)**:
   ```powershell
   wsl --install -d Ubuntu --web-download
   ```
   The `--web-download` flag forces WSL to fetch the latest distro
   package from the Microsoft servers, where it will auto-pick the
   ARM64 build for your Snapdragon host.

6. **If the wrong architecture is installed**, fix it:
   ```powershell
   wsl --unregister Ubuntu
   wsl --install -d Ubuntu --web-download
   ```
   Reopen **Ubuntu** from the Start menu and create a UNIX username +
   password.

7. **Verify ARM64**:
   ```powershell
   wsl -d Ubuntu uname -m
   # Must print: aarch64
   ```

8. **Update Ubuntu**:
   ```powershell
   wsl -d Ubuntu sudo apt update && wsl -d Ubuntu sudo apt upgrade -y
   ```

9. **Set WSL2 as the default version** (if not already):
   ```powershell
   wsl --set-default-version 2
   ```

10. **Set the Ubuntu distro to use WSL2** (if it shows version 1):
    ```powershell
    wsl --set-version Ubuntu 2
    ```

> **Troubleshooting WSL on ARM:** if the Microsoft Store version of
> Ubuntu installed itself as x86_64 (rare, but happens), grab the
> manual ARM64 build from
> <https://learn.microsoft.com/en-us/windows/wsl/install-manual#downloading-distributions>
> — pick the `arm64` Ubuntu AppX. Then `Add-AppxPackage` it and run
> `wsl --set-version Ubuntu 2`.

### 2. Disable Windows PATH in WSL (prevents npm/CLI conflicts)

This stops WSL from picking up Windows versions of `node`, `npm`,
`python`, etc. which would shadow the Linux ones.

```powershell
wsl --shutdown
```

Then in WSL:

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

