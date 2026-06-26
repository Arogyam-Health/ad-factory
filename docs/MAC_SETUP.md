# Mac Setup Guide — OpenCode Ad Dashboard

> Everything runs **natively on macOS**. No WSL, no VMs, no port proxies.
> Chrome is a native Mac app at `/Applications/Google Chrome.app/...`.
> Python and Node.js come via Homebrew. CDP is on `127.0.0.1:9222`
> directly — no netsh or portproxy needed.

**Terminal legend:**
- **[Terminal]** = macOS Terminal.app or iTerm2
- **[Browser]** = any browser on this Mac

---

## System architecture

Unlike the WSL/Windows setup where the system and browser are on two
operating systems connected by a port proxy, on macOS **everything**
runs on the same machine:

```
┌────────────────── macOS ─────────────────────────────────────┐
│                                                             │
│   Dashboard (FastAPI, 127.0.0.1:8787)                       │
│   OpenCode Server (127.0.0.1:4090)                          │
│   Python automation:                                        │
│     scripts/gemini_web_automation.py                        │
│     scripts/chatgpt_web_sutomation.py                       │
│                                                             │
│   Chrome browser (visible, native Mac app)                  │
│     --remote-debugging-port=9222                            │
│     --user-data-dir=~/.config/google-chrome-cdp              │
│                                                             │
│   Chrome listens on 127.0.0.1:9222                          │
│   Automation connects directly to 127.0.0.1:9222            │
│   NO port proxy, NO netsh, NO firewall rules needed.        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

| Process | Where | How it's started |
| --- | --- | --- |
| Dashboard (FastAPI :8787) | native macOS | `bash scripts/start_dashboard_stack.sh` |
| OpenCode CLI + Server (:4090) | native macOS | same script |
| `gemini_web_automation.py` | native macOS | dashboard button or CLI |
| `chatgpt_web_sutomation.py` | native macOS | dashboard button or CLI |
| **Chrome browser** | **native macOS** | dashboard launches it directly via subprocess |

---

# PART A — ONE-TIME SETUP

Do these steps **once** per machine. Takes ~20 minutes.

---

## A1. Install Homebrew **[Terminal]** [once]

Homebrew is the package manager for macOS. We use it to install Python and
Node.js.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the on-screen instructions. At the end, it will tell you to run
something like:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Run those commands. Then verify:

```bash
brew --version
# Homebrew 4.x.x
```

---

## A2. Install Python 3.12+ **[Terminal]** [once]

macOS ships with Python 3.9 (or older). The dashboard needs 3.10+. Install
the latest via Homebrew:

```bash
brew install python@3.12
```

Verify:

```bash
python3 --version
# Python 3.12.x
```

---

## A3. Install Node.js LTS **[Terminal]** [once]

```bash
brew install node@22
```

Or via nvm (if you manage multiple Node versions):

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.zshrc
nvm install --lts
nvm use --lts
```

Verify:

```bash
node --version   # v22.x.x
npm --version    # 10.x.x
```

---

## A4. Install Google Chrome **[Browser]** [once]

If you don't already have Chrome installed:

1. Go to <https://google.com/chrome/>
2. Click Download → the auto-detected Mac (Apple Silicon or Intel)
3. Open the `.dmg` and drag Chrome to Applications
4. Launch it once from Applications to complete the install

**Check the binary exists:**

```bash
ls "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Should show: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

On Apple Silicon (M1/M2/M3/M4) the binary is native ARM64. On Intel Macs
it's x86_64. Both work identically for the dashboard.

---

## A5. Clone the repo **[Terminal]** [once]

```bash
cd ~
git clone https://github.com/Vinay-003/ad-factory.git ad-factory
cd ad-factory
git checkout windows-setup
```

Verify:

```bash
git log --oneline -1
# Should show the current windows-setup tip commit
```

---

## A6. Set up Python virtualenv **[Terminal]** [once]

```bash
cd ~/ad-factory
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dashboard.txt
```

Verify:

```bash
.venv/bin/python --version
```

---

## A7. Install OpenCode CLI **[Terminal]** [once]

```bash
npm install -g opencode-ai
```

Verify:

```bash
which opencode
opencode --version
```

---

## A8. Generate the server password **[Terminal]** [once]

Create `.env.dashboard` in the repo root:

```bash
cd ~/ad-factory
python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits + '!@#\$%^&*()-_=+?'
password = ''.join(secrets.choice(alphabet) for _ in range(20))
with open('.env.dashboard', 'w') as f:
    f.write(f'OPENCODE_SERVER_PASSWORD={password}\n')
    f.write('OPENCODE_API_URL=http://127.0.0.1:4090\n')
print(f'Created .env.dashboard with password: {password}')
"
```

---

## A9. Log in to your AI provider **[Terminal]** [once]

```bash
opencode providers login
```

Interactive — pick your provider, paste your API key.

Verify:

```bash
opencode models
# Should list at least one model
```

---

## A10. Verify the full setup **[Terminal]** [once]

**A10.1** Start the dashboard:

```bash
cd ~/ad-factory
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh
```

**A10.2** Open <http://127.0.0.1:8787> in your browser.

**A10.3** Click **"Launch Visible Browser"** in the dashboard. A Chrome
window should open on your desktop.

**A10.4** Verify Chrome CDP is reachable from a separate Terminal tab:

```bash
curl -s http://127.0.0.1:9222/json/version | head -5
# Should return Chrome version JSON
```

**A10.5** Stop the stack:

```bash
bash scripts/stop_dashboard_stack.sh
```

---

# PART B — DAILY USE

Takes ~30 seconds to start, ~10 seconds to stop.

---

## B1. Start the dashboard **[Terminal]** [daily]

```bash
cd ~/ad-factory
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh
```

Leave the terminal open. The dashboard runs at `http://127.0.0.1:8787`.

---

## B2. Open the dashboard in your browser **[Browser]** [daily]

Go to <http://127.0.0.1:8787>.

---

## B3. Launch the visible browser (Chrome) **[Dashboard]** [daily]

Click **"Launch Visible Browser"**. Chrome opens on your Mac desktop. Log in
to ChatGPT / Gemini the first time (session persists in
`~/.config/google-chrome-cdp`).

---

## B4. Generate images **[Dashboard]** [daily]

Select prompts → click Generate. The automation connects to Chrome via
`127.0.0.1:9222` (no port proxy needed).

---

## B5. Kill Chrome when done **[Dashboard]** [daily]

Click **"Kill Chrome"** or close the Chrome window.

---

## B6. Stop the dashboard **[Terminal]** [daily]

```bash
bash scripts/stop_dashboard_stack.sh
```

---

# PART C — MAC-SPECIFIC NOTES

## What's different from the Windows/WSL setup

| Aspect | Windows/WSL | Mac |
| --- | --- | --- |
| Chrome location | `/mnt/c/Program Files/Google/Chrome/Application/chrome.exe` (on Windows host) | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| Chrome launch | PowerShell script via WSL interop | direct `subprocess.Popen` (dashboard does this) |
| Port proxy | `netsh portproxy 9223→9222` needed | **None** — CDP is directly on `127.0.0.1:9222` |
| Firewall rule | `New-NetFirewallRule` needed | **None** — loopback is always open |
| WSL detection | `Path("/mnt/c").exists()` → `True` | `False` (all WSL code is skipped) |
| Process kill | `taskkill.exe` (Windows) | `pkill -f Google Chrome` or `kill` |
| Package manager | `apt` (in WSL) | `brew install` (macOS) |
| Browser open | `xdg-open` (not found → prints URL) | `open` (macOS native) |
| Python | `apt install python3` | `brew install python@3.12` |

## What doesn't work on Mac (and workarounds)

| Feature | What's broken | Workaround |
| --- | --- | --- |
| `copy_to_windows_temp()` in ChatGPT automation | Original code hardcoded `/mnt/c/Users/...` — **FIXED** in this branch to use `Path.home()` when `/mnt/c` doesn't exist. | None needed, the fix is already applied. |
| `xdotool` keystroke injection (gemini_web_automation.py) | `xdotool` is Linux-only; calling it fails on macOS. | Use **CDP paste mode** instead (the default). The automation scripts use `page.keyboard` / CDP clipboard API, which works on macOS — `xdotool` is only a fallback for file-upload dialogs. If you hit file-upload code paths, the automation will fall back to a CDP-based approach. |
| `powershell.exe` / `taskkill.exe` | These don't exist on macOS. | The dashboard guards these with `Path("/mnt/c").exists()` and `shutil.which()` — on macOS they're never called. On Mac, the dashboard uses `subprocess.Popen` directly for Chrome launch and `process.terminate()` / `process.kill()` for shutdown. |

## Where files live on Mac

| What | Path |
| --- | --- |
| Repo source | `~/ad-factory` |
| Dashboard config / password | `~/ad-factory/.env.dashboard` |
| Python venv | `~/ad-factory/.venv/` |
| Generated prompts | `~/ad-factory/output/` |
| Generated images | `~/ad-factory/generated_images/` |
| Run manifests | `~/ad-factory/dashboard_storage/runs/` |
| Chrome CDP user data (login cookies) | `~/.config/google-chrome-cdp` |
| Upload temp (CDP image uploads) | `~/.ad-factory-upload-temp` |

---

# PART D — TROUBLESHOOTING

## D1. "Chrome binary not found"

The dashboard searches in this order:

```
/usr/bin/google-chrome
/usr/bin/google-chrome-stable
/snap/bin/chromium
/usr/bin/chromium-browser
/usr/bin/chromium
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Chromium.app/Contents/MacOS/Chromium
```

If Chrome is installed at a non-standard location, create a symlink:

```bash
sudo ln -s "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" /usr/local/bin/google-chrome
```

Or start the dashboard manually with:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/.config/google-chrome-cdp" \
    --no-first-run --no-default-browser-check &
```

then use the automation scripts directly with `--cdp-url http://127.0.0.1:9222`.

## D2. "Port 9222 is in use"

Something else is already on port 9222. Kill it:

```bash
lsof -ti :9222 | xargs kill -9
```

Then retry "Launch Visible Browser" in the dashboard.

## D3. Chrome launches but CDP isn't reachable

```bash
curl -s http://127.0.0.1:9222/json/version
# If this fails, Chrome isn't listening with CDP
```

**Fix:** Close all Chrome windows, then in Terminal:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/.config/google-chrome-cdp"
```

Then hit "Launch Visible Browser" again.

## D4. "Session not found" (opencode errors)

```bash
pkill -f opencode
rm -rf ~/.local/share/opencode
opencode providers login
bash scripts/start_dashboard_stack.sh
```

## D5. Dashboard unreachable at http://127.0.0.1:8787

The stack might have failed to start. Check the terminal where you ran
`start_dashboard_stack.sh` for error messages. Common issues:

- Port 8787 already in use: `lsof -ti :8787 | xargs kill -9` then retry
- Port 4090 already in use: `lsof -ti :4090 | xargs kill -9` then retry
- `.env.dashboard` missing: re-run A8

## D6. File upload in automation fails

The ChatGPT automation (`chatgpt_web_sutomation.py`) uses `copy_to_windows_temp()`
which now falls back to `~/.ad-factory-upload-temp` on Mac. If upload still fails:

1. Check the temp dir exists: `ls ~/.ad-factory-upload-temp`
2. Ensure Chrome has permission to read `~/.ad-factory-upload-temp` (grant
   Full Disk Access if macOS prompts for it)
3. Ensure the image file exists in `~/ad-factory/input/images/`

## D7. `brew install` fails with permission errors

Run these to fix Homebrew ownership:

```bash
sudo chown -R "$(whoami)" /opt/homebrew
brew update
```

## D8. Dashboard is slow / high CPU

macOS on Apple Silicon is very fast, but Python in Rosetta mode isn't. Make
sure you're using native ARM64 Python:

```bash
python3 -c "import platform; print(platform.machine())"
# arm64   (Apple Silicon, native)
# x86_64  (running under Rosetta — slower)
```

If you see `x86_64`, install a native ARM64 Python: `brew install python@3.12`
and create a new venv.

---

# Quick reference card

| Task | Command | Terminal |
| --- | --- | --- |
| Start dashboard | `export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard \| cut -d'=' -f2)" && bash scripts/start_dashboard_stack.sh` | Terminal |
| Stop dashboard | `bash scripts/stop_dashboard_stack.sh` | Terminal |
| Pull latest | `git pull origin windows-setup` | Terminal |
| Re-login to AI | `opencode providers login` | Terminal |
| Open dashboard | <http://127.0.0.1:8787> | Browser |
| Kill Chrome CDP | `lsof -ti :9222 \| xargs kill -9` | Terminal |
| Test CDP | `curl -s http://127.0.0.1:9222/json/version` | Terminal |
| Launch Chrome manually | `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/.config/google-chrome-cdp" &` | Terminal |
