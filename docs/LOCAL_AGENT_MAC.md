# Local agent on macOS

The website stays on Render. This Mac only runs the **local agent**, **Chrome**,
and stores ads under `~/ad-factory-agent`.

Pairing uses `127.0.0.1`, so open the dashboard in a browser **on this same
Mac**.

Default production URL: `https://ad-factory-3rn5.onrender.com`

## 1. Install Python and Chrome

Install Python 3.10+ from https://www.python.org/downloads/macos/ or Homebrew:

```bash
brew install python
```

Confirm:

```bash
python3 --version
```

Install Google Chrome from https://www.google.com/chrome/.

## 2. Download the local-agent zip

Do not clone the whole repo. Share or download **only** `ad-factory-local-agent.zip`.
It already includes `requirements-local-agent.txt` and this guide:

https://github.com/Vinay-003/ad-factory/raw/render-setup/ad-factory-local-agent.zip

```bash
mkdir -p ~/ad-factory
cd ~/ad-factory
# save the zip here, then:
unzip -o ad-factory-local-agent.zip
cd ad-factory-local-agent
```

Leave `scripts/`, `local_agent_runtime/`, and `dashboard/backend/` inside this
folder. Moving those files out will break imports.

`requirements-local-agent.txt` is already in this unzipped folder. Do not
download it separately. Create a **local** `.venv` and install into it with
the venv Python (no `activate`, no global `pip`):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-local-agent.txt
.venv/bin/python -m playwright install chromium
```

Create the `.venv` on this Mac. Do not copy one from Windows or Ubuntu.

If macOS blocks `python3` or Chrome the first time, System Settings → Privacy &
Security → Open Anyway.

## 3. Chrome path (only if auto-detect fails)

The agent looks for Chrome automatically (`CHROME_PATH`, then `PATH`, then
`/Applications/Google Chrome.app/...`). To force a binary:

```bash
export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

Or pass it when starting:

```bash
.venv/bin/python scripts/start_local_agent.py --chrome-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

Typical Mac paths:

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Chromium.app/Contents/MacOS/Chromium
```

## 4. Copy the dashboard session cookie

1. On this Mac, open Chrome and sign in to
   `https://ad-factory-3rn5.onrender.com`.
2. Press `Cmd+Option+I` → **Application** → **Cookies** → the site.
3. Copy the `session` cookie value.

## 5. Start the local agent

```bash
cd ~/ad-factory/ad-factory-local-agent
.venv/bin/python scripts/start_local_agent.py
```

Or: `chmod +x start_local_agent.sh && ./start_local_agent.sh` (uses
`.venv/bin/python` when it exists).

Paste the cookie at `Session cookie:` (hidden). Later starts can press Enter
with a blank cookie if `~/ad-factory-agent/config/agent.json` already exists.

Sign in to ChatGPT and/or Gemini in the Chrome window the agent opens. Keep it
open. Pair the Render dashboard in a normal tab on this same Mac.

Stop with `Ctrl+C`.
