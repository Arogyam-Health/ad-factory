# Local agent on Ubuntu

The website stays on Render. This Ubuntu machine only runs the **local agent**,
**Chrome**, and stores ads under `~/ad-factory-agent`.

Pairing uses `127.0.0.1`, so open the dashboard in a browser **on this same
machine**.

Default production URL: `https://ad-factory-pzgh.onrender.com`

## 1. Install Python and Chrome

Install **Python 3.12 exactly**. 3.13+ cannot run the agent (`cgi` was
removed). If `apt` has no `python3.12` package, use the
[deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) or
install 3.12 from https://www.python.org/downloads/.

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv unzip
python3.12 --version
```

Install Google Chrome:

```bash
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
```

Chromium (`sudo apt install -y chromium-browser`) also works if Chrome is not
available.

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
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-local-agent.txt
```

That installs the Playwright **Python library** only. Do **not** run
`playwright install chromium`. The agent drives the Google Chrome already
installed on this machine, through CDP on port 9222.

Create the `.venv` on this Ubuntu box. Do not copy one from Windows or Mac.

## 3. Chrome path (only if auto-detect fails)

The agent looks for Chrome automatically (`CHROME_PATH`, then `PATH`, then
`/usr/bin/google-chrome`). To force a binary:

```bash
export CHROME_PATH=/usr/bin/google-chrome
```

Or pass it when starting:

```bash
.venv/bin/python scripts/start_local_agent.py --chrome-path /usr/bin/google-chrome
```

Typical Ubuntu paths:

```text
/usr/bin/google-chrome
/usr/bin/google-chrome-stable
/usr/bin/chromium-browser
/usr/bin/chromium
/snap/bin/chromium
```

## 4. Copy the dashboard session cookie

1. On this Ubuntu machine, open Chrome and sign in to
   `https://ad-factory-pzgh.onrender.com`.
2. Press `F12` → **Application** → **Cookies** → the site.
3. Copy the `session` cookie value.

## 5. Start the local agent

```bash
cd ~/ad-factory/ad-factory-local-agent
.venv/bin/python scripts/start_local_agent.py
```

Or: `./start_local_agent.sh` (uses `.venv/bin/python` when it exists).

Paste the cookie at `Session cookie:` (hidden). Later starts can press Enter
with a blank cookie if `~/ad-factory-agent/config/agent.json` already exists.

Sign in to ChatGPT and/or Gemini in the Chrome window the agent opens. Keep it
open. Pair the Render dashboard in a normal tab on this same machine.

Stop with `Ctrl+C`.
