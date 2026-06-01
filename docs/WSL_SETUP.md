# WSL Setup Guide — OpenCode Ad Dashboard

> **Works on Windows on x86_64 and Windows on ARM (Snapdragon / Qualcomm).**
> The whole system runs **inside WSL2 (Ubuntu)**. **Chrome browser runs on
> the Windows host.** They talk to each other over a port-proxy.
> Every step below is numbered, labeled with which terminal to use, and
> marked **[once]** or **[daily]**.

**Terminal legend:**
- **[Win Admin]** = Windows PowerShell opened as Administrator
- **[Win]** = regular Windows PowerShell or normal command prompt
- **[WSL]** = Ubuntu terminal inside WSL2

---

## System architecture (read this first)

The dashboard is **two processes on two operating systems at once**:

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
│   (set once by scripts/setup_cdp_proxy.ps1)              │
│                                                          │
│   Chrome (real browser, visible on your desktop)         │
│     --remote-debugging-port=9222                        │
│     --user-data-dir=%USERPROFILE%\.config\google-       │
│                     chrome-cdp                          │
│   (launched by scripts/launch_chrome_cdp.ps1)           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

| Process | Where | How it's started |
| --- | --- | --- |
| Dashboard (FastAPI :8787) | WSL | `bash scripts/start_dashboard_stack.sh` |
| OpenCode CLI + Server (:4090) | WSL | same script + `opencode serve` |
| `gemini_web_automation.py` | WSL | dashboard button or CLI |
| `chatgpt_web_sutomation.py` | WSL | dashboard button or CLI |
| `cdp_proxy.py` (:9223) | WSL | started on demand by automation |
| **Chrome browser** | **Windows host** | PowerShell `launch_chrome_cdp.ps1` |
| Port proxy (9223→9222) | Windows host | PowerShell `setup_cdp_proxy.ps1` **[once]** |
| Firewall rule (:9223) | Windows host | PowerShell `add_cdp_firewall_rule.ps1` **[once]** |

---

# PART A — ONE-TIME SETUP (first time on a new Windows machine)

Do these steps **once** per machine. They take ~30 minutes. After this, the
**Daily Use** section (Part B) is all you need.

---

## A1. Install WSL2 **[Win Admin]** [once]

### If you're on Windows on x86_64 (regular Intel/AMD laptop):

```powershell
wsl --install -d Ubuntu
```

Reboot when prompted. Then open **Ubuntu** from the Start menu, create a UNIX
username + password, and continue to A2.

### If you're on Windows on ARM (Snapdragon / Qualcomm laptop):

WSL2 on ARM works, but the install path needs extra steps to get the **ARM64
Ubuntu** build (not the x86_64 one — that one runs everything under emulation
and is slow).

**Step A1.1 — Open PowerShell as Administrator** (right-click Start → "Terminal
(Admin)" or "PowerShell (Admin)").

**Step A1.2 — Sanity check the host architecture:**
```powershell
[System.Reflection.Assembly]::ImageRuntimeArchitecture
# Arm64  -> you have a Snapdragon
# X64    -> you have Intel/AMD
```

**Step A1.3 — Check if WSL is already installed:**
```powershell
wsl --status
wsl --list --verbose
```
If you see any installed distros, note their names. If nothing is installed,
skip to A1.5.

**Step A1.4 — Check the architecture of an installed distro** (e.g. `Ubuntu`):
```powershell
wsl -d Ubuntu uname -m
```
Must print `aarch64`. If it prints `x86_64`, go to A1.6 to reinstall. If it
prints `aarch64`, skip to A1.7.

**Step A1.5 — Install WSL + Ubuntu (ARM64 build):**
```powershell
wsl --install -d Ubuntu --web-download
```
The `--web-download` flag forces WSL to fetch the latest distro package
directly from Microsoft, where it auto-picks the ARM64 build for your
Snapdragon host.

**Step A1.6 — If the wrong architecture was installed, fix it:**
```powershell
wsl --unregister Ubuntu
wsl --install -d Ubuntu --web-download
```
Reopen **Ubuntu** from the Start menu and create a UNIX username + password.

**Step A1.7 — Verify ARM64:**
```powershell
wsl -d Ubuntu uname -m
# Must print: aarch64
```

**Step A1.8 — Update Ubuntu:**
```powershell
wsl -d Ubuntu sudo apt update
wsl -d Ubuntu sudo apt upgrade -y
```

**Step A1.9 — Set WSL2 as the default version:**
```powershell
wsl --set-default-version 2
```

**Step A1.10 — If your distro shows version 1, switch it to 2:**
```powershell
wsl --set-version Ubuntu 2
```

> **Troubleshooting WSL on ARM:** if the Microsoft Store version of Ubuntu
> installed itself as x86_64 (rare, but happens), grab the manual ARM64 build
> from
> <https://learn.microsoft.com/en-us/windows/wsl/install-manual#downloading-distributions>
> — pick the `arm64` Ubuntu AppX. Then `Add-AppxPackage` it and run
> `wsl --set-version Ubuntu 2`.

---

## A2. Disable Windows PATH in WSL **[Win Admin] → [WSL]** [once]

This stops WSL from picking up Windows versions of `node`, `npm`, `python`,
etc., which would shadow the Linux ones and break the dashboard.

**A2.1** In Windows PowerShell Admin, shut down WSL:
```powershell
wsl --shutdown
```

**A2.2** Open **Ubuntu** from the Start menu, then:
```bash
echo -e "[interop]\nappendWindowsPath=false" | sudo tee /etc/wsl.conf
```

**A2.3** Back in Windows PowerShell Admin, restart WSL:
```powershell
wsl --shutdown
```
Then reopen **Ubuntu** from the Start menu.

---

## A3. Install system packages **[WSL]** [once]

Open **Ubuntu** from the Start menu and run:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl git
```

---

## A4. Install Node.js LTS (Linux ARM64) **[WSL]** [once]

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
```

**Verify it installed correctly:**
```bash
which node
# Should print: /usr/bin/node (NOT /mnt/c/...)
which npm
# Should print: /usr/bin/npm or /usr/local/bin/npm

node --version   # e.g. v22.x.x
npm --version    # e.g. 10.x.x
```

If `which node` returns a `/mnt/c/...` path, your WSL is seeing the Windows
Node.js — re-do step A2 to disable Windows PATH.

---

## A5. Clone the repo inside WSL home **[WSL]** [once]

**Important:** clone inside `~` (WSL home), **NOT** in `/mnt/c/...`. Files in
`/mnt/c` are painfully slow to access from WSL.

```bash
cd ~
git clone https://github.com/Vinay-003/ad-factory.git ad-factory
cd ad-factory
git checkout windows-setup
```

Verify:
```bash
git log --oneline -1
# Should show: 4cfeee1 docs: add WSL-on-ARM install steps + system architecture diagram
# (or whatever the current windows-setup tip is)
```

---

## A6. Run the bootstrap script **[WSL]** [once]

```bash
cd ~/ad-factory
bash scripts/setup_wsl.sh
```

This script:
1. Verifies you're on Linux (rejects Windows npm/Python).
2. Creates `.venv/` and `pip install -r requirements-dashboard.txt`.
3. Installs the `opencode-ai` CLI globally via npm.
4. Installs Playwright's bundled Chromium.
5. Creates the storage folders (`dashboard_storage/`, `runtime/`, etc.).
6. Generates `.env.dashboard` with a random 20-char server password.
7. Clears any stale OpenCode state in `~/.local/share/opencode/`.

You should see:
```
================================================
Setup complete!
================================================
Next steps:
  1) Configure AI provider: opencode providers login
  2) Verify models: opencode models
  3) Start dashboard: bash scripts/start_dashboard_stack.sh
  4) Open browser: http://127.0.0.1:8787
```

**Verify:**
```bash
.venv/bin/python --version
which opencode
opencode --version
```

---

## A7. Log in to your AI provider **[WSL]** [once]

```bash
opencode providers login
```

This is interactive — pick your provider, paste your API key.

Verify the model list:
```bash
opencode models
```

You should see at least one model listed (e.g. `gpt-4o`, `claude-sonnet-4`, etc.).

---

## A8. Set up the CDP port proxy **[Win Admin]** [once]

This creates the Windows-side bridge that lets WSL reach the Windows Chrome.

**A8.1** Find your WSL username:
```powershell
wsl -d Ubuntu whoami
# e.g. jadam
```

**A8.2** Run the port-proxy setup script from Admin PowerShell:
```powershell
$scriptPath = "\\wsl$\Ubuntu\home\jadam\ad-factory\scripts"
powershell -ExecutionPolicy Bypass -File "$scriptPath\setup_cdp_proxy.ps1"
```
(Replace `jadam` with your WSL username from A8.1.)

You should see:
```
Removing existing port proxy (if any)...
Adding port proxy: 0.0.0.0:9223 -> 127.0.0.1:9222
Active port proxies:
...
SUCCESS: Port proxy configured. WSL2 can now access Chrome CDP via the Windows host IP.
You only need to run this once. The proxy persists across reboots.
```

**A8.3** Verify:
```powershell
netsh interface portproxy show v4tov4
```
Expected output:
```
Listen on ipv4:             Connect to ipv4:

Address         Port        Address         Port
--------------- ----------  --------------- ----------
0.0.0.0         9223        127.0.0.1       9222
```

If Option A doesn't work (the `\\wsl$\...` path errors out), use Option B
(direct netsh commands):
```powershell
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1
```

---

## A9. Add the Windows Firewall rule **[Win Admin]** [once]

The port proxy won't accept connections unless the firewall allows inbound
TCP on port 9223.

**A9.1** From Admin PowerShell:
```powershell
$scriptPath = "\\wsl$\Ubuntu\home\jadam\ad-factory\scripts"
powershell -ExecutionPolicy Bypass -File "$scriptPath\add_cdp_firewall_rule.ps1"
```
(Replace `jadam` with your WSL username.)

Expected output:
```
Adding firewall rule for port 9223...
Firewall rule added.
```

**A9.2** Verify:
```powershell
Get-NetFirewallRule -DisplayName "CDP Port Proxy 9223"
```
Should show the rule with `Enabled: True`.

If Option A doesn't work, use Option B (direct command):
```powershell
New-NetFirewallRule -DisplayName "CDP Port Proxy 9223" -Direction Inbound -Protocol TCP -LocalPort 9223 -Action Allow
```

---

## A10. Install Google Chrome on Windows **[Win]** [once]

This is the **visible** browser the dashboard will use. Install it via:

1. Open Microsoft Edge or any browser and go to <https://google.com/chrome/>
2. Click **Download Chrome**
3. Run the installer
4. If asked, choose the **ARM64** build (on Snapdragon)
5. Sign in / set as default / skip — the dashboard doesn't care

**Verify the install:**
```powershell
Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe"
# Should print: True
```

If the path above is `False`, find where Chrome installed:
```powershell
Get-ChildItem "C:\Program Files*\Google\Chrome\Application\chrome.exe"
```

---

## A11. Verify the full setup **[WSL] + [Win]** [once]

Run these checks. All should pass.

**A11.1** WSL is ARM64 (or x86_64 on Intel/AMD — both are fine):
```bash
uname -m
# aarch64  (Snapdragon)
# x86_64   (Intel/AMD)
```

**A11.2** Python and Node are Linux binaries:
```bash
which python3 node npm
# All three should be /usr/bin/* or /usr/local/bin/*
# NONE should be /mnt/c/*
```

**A11.3** The dashboard starts cleanly:
```bash
cd ~/ad-factory
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh
```

You should see the script start uvicorn and opencode, then print the URL
`http://127.0.0.1:8787`.

**A11.4** Open <http://127.0.0.1:8787> in your Windows browser. You should
see the OpenCode Ad Dashboard login page.

**A11.5** Click **"Launch Visible Browser"** in the dashboard. A Chrome
window should open on your Windows desktop.

If anything fails, see the **Troubleshooting** section below.

**A11.6** Stop the stack:
```bash
bash scripts/stop_dashboard_stack.sh
```

You are done with one-time setup. From now on, only **Part B (Daily Use)** is needed.

---

# PART B — DAILY USE

These steps assume Part A is already done. Takes ~30 seconds to start, ~10
seconds to stop.

---

## B1. Start the dashboard stack **[WSL]** [daily]

```bash
cd ~/ad-factory

# Only needed if there are new commits to pull (e.g. team pushed changes):
git pull origin windows-setup

# Always needed (the password is auto-generated by setup_wsl.sh and stored in .env.dashboard):
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"

# Start the stack:
bash scripts/start_dashboard_stack.sh
```

The script will:
- Start `opencode serve` on `127.0.0.1:4090`
- Start the FastAPI dashboard on `127.0.0.1:8787`
- Print the URL

**Leave this terminal open** — the stack runs in the foreground.

---

## B2. Open the dashboard **[Win]** [daily]

Open Edge, Chrome, Firefox, or any browser and go to:
```
http://127.0.0.1:8787
```

You'll see the OpenCode Ad Dashboard.

---

## B3. Launch the visible browser (Chrome on Windows) **[Dashboard]** [daily]

In the dashboard, click the **"Launch Visible Browser"** button.

A new Chrome window opens **on your Windows desktop** (not in WSL). This
window has CDP debugging enabled on port 9222.

**Log in to the image-generation site** (ChatGPT, Gemini, etc.) the first
time you use it. The login session is saved in
`%USERPROFILE%\.config\google-chrome-cdp\` so you don't have to log in again
next time.

---

## B4. Trigger image generation **[Dashboard]** [daily]

1. Select the prompts you want to generate (checkboxes in the prompt list).
2. Click **Generate** (or the appropriate button — UI labels vary by
   dashboard version).
3. The dashboard:
   - Copies images from WSL to `C:\Users\<you>\.ad-factory-upload-temp\`
   - Connects to the Windows Chrome via `http://172.18.160.1:9223` (the
     portproxy) → netsh → Chrome on `127.0.0.1:9222`
   - Drives Chrome via CDP to upload + generate + download
4. Generated images land in `~/ad-factory/generated_images/`.

---

## B5. Kill Chrome when done **[Dashboard]** [daily]

Click **"Kill Chrome"** in the dashboard, or just close the Chrome window
manually. The CDP port will be released.

---

## B6. Stop the dashboard stack **[WSL]** [daily]

In the WSL terminal where the stack is running, press `Ctrl+C` once, or run
this in a separate WSL terminal:
```bash
bash scripts/stop_dashboard_stack.sh
```

This kills `uvicorn` and `opencode` processes and releases ports 8787 and 4090.

---

# PART C — IMAGE GENERATION WORKFLOW (details)

The visible browser flow is:

```
[Dashboard]                      [WSL]                        [Windows]
    |                               |                              |
    | click "Launch Visible         |                              |
    | Browser"                      |                              |
    |------------------------------>| detect WSL2, find host IP   |
    |                               | (ip route -> 172.18.160.1)   |
    |                               |----------------------------->|
    |                               | taskkill.exe chrome.exe      |
    |                               | powershell.exe launch_chrome_cdp.ps1
    |                               |                              | start chrome.exe
    |                               |                              |   --remote-debugging-port=9222
    |                               |                              |   --user-data-dir=...
    |                               |                              |   ... wait for :9222 ...
    |                               |<-----------------------------|
    |<--- "Chrome ready" -----------|                              |
    |                               |                              |
    | click "Generate"              |                              |
    |------------------------------>| run gemini/chatgpt           |
    |                               | _web_automation.py           |
    |                               |   --cdp-url                  |
    |                               |   http://172.18.160.1:9223   |
    |                               |                              |
    |                               | copy image to                |
    |                               | /mnt/c/Users/.../upload-temp |
    |                               |------------------------->    |
    |                               |                              | Chrome uploads
    |                               |                              | image to chat
    |                               |                              | (CDP) returns
    |                               |                              | generated image
    |                               |<-------------------------    |
    |                               | save to                      |
    |                               | ~/ad-factory/generated_images|
    |                               |                              |
    |<--- "Generation done" --------|                              |
```

**Key file locations:**

| What | Where |
| --- | --- |
| Repo source | `~/ad-factory` (in WSL) |
| Dashboard config / passwords | `~/ad-factory/.env.dashboard` |
| Generated prompts | `~/ad-factory/output/` |
| Generated images | `~/ad-factory/generated_images/` |
| Run manifests | `~/ad-factory/dashboard_storage/runs/` |
| Chrome profile (login cookies) | `C:\Users\<you>\.config\google-chrome-cdp` (in Windows) |
| Temp upload folder | `C:\Users\<you>\.ad-factory-upload-temp` (in Windows) |

---

# PART D — TROUBLESHOOTING

## D1. "Chrome binary not found"

The dashboard looks for Chrome in this order:
1. `/usr/bin/google-chrome`
2. `/usr/bin/google-chrome-stable`
3. `/snap/bin/chromium`
4. `/usr/bin/chromium-browser`
5. `/usr/bin/chromium`
6. `/mnt/c/Program Files/Google/Chrome/Application/chrome.exe`
7. `/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe`

**Fix:** install Chrome on Windows (A10) — that resolves #6. Optionally
also install Chrome in WSL with `sudo apt install -y google-chrome-stable`.

## D2. "Port 9222 is still in use" (Chrome launch)

**Fix:** wait 10 seconds, or kill stale Chrome manually:
```powershell
taskkill.exe /F /IM chrome.exe
```

## D3. "CDP connection refused" / curl http://172.18.160.1:9223/json/version fails

The port proxy or firewall rule is missing. Re-run A8 and A9.

**Verify from WSL:**
```bash
curl -sv http://172.18.160.1:9223/json/version 2>&1 | head -20
```
Should return Chrome's `webSocketDebuggerUrl` JSON.

If 172.18.160.1 doesn't work, find your actual Windows host IP from WSL:
```bash
ip route | grep default
# default via 172.18.160.1 dev eth0 ...
```
The IP after "via" is the Windows host IP as seen from WSL2. Use that in
place of `172.18.160.1`.

## D4. "Session not found" (opencode errors)

```bash
pkill -f opencode
rm -rf ~/.local/share/opencode
opencode providers login
bash scripts/start_dashboard_stack.sh
```

## D5. Dashboard unreachable from Windows browser

WSL2 sometimes drops port forwarding. Restart:
```powershell
wsl --shutdown
```
Then reopen Ubuntu and run `bash scripts/start_dashboard_stack.sh` again.

## D6. Port 8787 or 4090 already in use

```bash
bash scripts/stop_dashboard_stack.sh
# Or manually:
pkill -f opencode
pkill -f uvicorn
```

## D7. Image upload shows broken thumbnail in Chrome

Images are copied to `C:\Users\<you>\.ad-factory-upload-temp\` before upload.
If the path has a space or non-ASCII character, copy fails. Check:
```powershell
Test-Path "$env:USERPROFILE\.ad-factory-upload-temp"
# Should be True after a generation run
```

If `False`, check that the WSL user has permission to write to your Windows
user folder (rare; usually it's a Windows Defender controlled-folder-access
issue — allow Chrome through Controlled Folder Access in Windows Security).

## D8. WSL2 architecture is wrong (x86_64 instead of aarch64 on Snapdragon)

See **A1.6** — unregister and reinstall with `--web-download`.

---

# PART E — ARM / SNAPDRAGON REFERENCE

## What works on ARM out-of-the-box

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

## ARM verification commands

```bash
# 1. WSL is ARM64
uname -m
# aarch64   <-- correct on Snapdragon
# x86_64    <-- WSL is emulating x86; reinstall with --web-download (see A1.6)

# 2. Python is the Linux/ARM64 build
python3 -c "import platform; print(platform.machine())"
# aarch64

# 3. Node.js is the Linux/ARM64 build
node -e "console.log(process.arch)"
# arm64

# 4. Chrome in WSL is the ARM64 build (if installed)
file "$(which google-chrome)" | head -1
# ELF 64-bit LSB pie executable, ARM aarch64 ...

# 5. All pip packages resolved as manylinux / py3-none-any
.venv/bin/pip list --format=columns | head
```

## ARM-specific gotchas

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

---

# Quick reference card

| Task | Command | Terminal |
| --- | --- | --- |
| Start dashboard | `bash scripts/start_dashboard_stack.sh` | WSL |
| Stop dashboard | `bash scripts/stop_dashboard_stack.sh` | WSL |
| Set password (needed every start) | `export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard \| cut -d'=' -f2)"` | WSL |
| Pull latest | `git pull origin windows-setup` | WSL |
| Re-login to AI | `opencode providers login` | WSL |
| Re-run setup (broken venv) | `bash scripts/setup_wsl.sh` | WSL |
| Reset port proxy | `powershell -File "\\wsl$\Ubuntu\home\<user>\ad-factory\scripts\setup_cdp_proxy.ps1"` | Win Admin |
| Reset firewall rule | `powershell -File "\\wsl$\Ubuntu\home\<user>\ad-factory\scripts\add_cdp_firewall_rule.ps1"` | Win Admin |
| Test CDP | `curl -s http://172.18.160.1:9223/json/version` | WSL |
| Open dashboard | <http://127.0.0.1:8787> | Win browser |
| Restart WSL | `wsl --shutdown` (then reopen Ubuntu) | Win Admin |
| Check WSL arch | `wsl -d Ubuntu uname -m` | Win |
