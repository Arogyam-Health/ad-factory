# Local agent on Ubuntu

The website stays on Render. This Ubuntu machine only runs the **local agent**, **Chrome**, and stores ads under `~/ad-factory-agent`.

Pairing and image serving use `127.0.0.1`, so the dashboard must be opened in a browser **on this same machine**.

Default production URL:

```text
https://ad-factory-3rn5.onrender.com
```

## What you need

- Ubuntu 22.04 or newer
- Python 3.10 or newer
- Git
- Google Chrome (not only Chromium if you can install Chrome)
- This repository

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Install Google Chrome. Either:

```bash
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
```

or install Chromium (`sudo apt install -y chromium-browser`) if Chrome is not available. If the agent cannot find the browser, set:

```bash
export CHROME_PATH="$(command -v google-chrome || command -v chromium-browser)"
```

## 2. Get the code and Python deps

```bash
git clone <this-repo-url>
cd info
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt
python -m playwright install chromium
```

Do not copy a `.venv` from another OS or machine. Create it on this Ubuntu box.

## 3. Copy the dashboard session cookie

1. On **this** Ubuntu machine, open Chrome and sign in to
   `https://ad-factory-3rn5.onrender.com`.
2. Press `F12` (or `Ctrl+Shift+I`) → **Application** → **Cookies** → the site.
3. Copy the value of the `session` cookie. That value is the session id the
   launcher asks for.
4. Do not paste it into a chat, a file, or a command line if you can avoid it.
   The launcher hides the input and does not put it on `argv`.

## 4. Start the local agent

From the repo root, with the venv active:

```bash
python scripts/start_local_agent.py
```

When it asks `Session cookie:`, paste the cookie and press Enter. The cursor
will not show the paste. That is expected.

The script then runs the local agent with:

- `--api-base https://ad-factory-3rn5.onrender.com`
- `--data-dir "$HOME/ad-factory-agent"` (this user's home, not a hardcoded path)
- `--launch-browser --browser chrome`

A Chrome window should open with remote debugging on `127.0.0.1:9222`. Sign in
to ChatGPT and/or Gemini **in that window**. Keep it open while jobs run.

## 5. Pair the dashboard

Keep the agent terminal running. In a normal browser tab on this machine, open
the Render dashboard. Pairing talks to `http://127.0.0.1:8765` on this computer.
A dashboard open on a different machine cannot use this agent.

Keep `~/ad-factory-agent/config/agent.json`. Later starts can leave the cookie
blank and reuse that registration.

## Later starts

```bash
cd /path/to/info
source .venv/bin/activate
python scripts/start_local_agent.py
```

Press Enter at the cookie prompt if this machine is already registered.

## Optional overrides

```bash
python scripts/start_local_agent.py --api-base https://ad-factory-3rn5.onrender.com
python scripts/start_local_agent.py --data-dir "$HOME/ad-factory-agent"
export AGENT_DATA_DIR="$HOME/ad-factory-agent"
export CHROME_PATH=/usr/bin/google-chrome
```

## Stop

In the agent terminal press `Ctrl+C`. Then `unset AD_FACTORY_SESSION` is not
required; the launcher never exports the cookie into your interactive shell.

## Troubleshooting

- **Lock error / another agent is running** — only one agent may use
  `~/ad-factory-agent`. Stop the other process.
- **Pairing fails** — confirm the agent is running on this machine, the
  dashboard is opened here, and nothing else is bound to `127.0.0.1:8765`.
- **Chrome not found** — install Google Chrome or set `CHROME_PATH`.
- **Jobs fail login** — use the Chrome window the agent launched, not your
  everyday Chrome profile, and log into ChatGPT/Gemini there.
