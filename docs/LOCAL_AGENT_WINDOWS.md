# Local agent on Windows

The website stays on Render. This Windows PC only runs the **local agent**,
**Chrome**, and stores ads under `%USERPROFILE%\ad-factory-agent`
(`C:\Users\<you>\ad-factory-agent` on a typical machine).

Pairing and image serving use `127.0.0.1`, so the dashboard must be opened in a
browser **on this same PC**. You do not need WSL for the local agent.

Default production URL:

```text
https://ad-factory-3rn5.onrender.com
```

## What you need

- Windows 10 or 11
- Python 3.10 or newer from https://www.python.org/downloads/
  - During setup, enable **Add python.exe to PATH**
- Git for Windows from https://git-scm.com/download/win
- Google Chrome
- This repository

This guide is native Windows (PowerShell or Command Prompt). If you instead want
the older full dashboard stack inside WSL, see [`WSL_SETUP.md`](WSL_SETUP.md).

## 1. Install Python, Git, and Chrome

1. Install Python 3.10+ and confirm it in **Command Prompt** or **PowerShell**:

   ```bat
   py -3 --version
   ```

2. Install Git for Windows.
3. Install Google Chrome from https://www.google.com/chrome/.

If the agent cannot find Chrome, set a user environment variable `CHROME_PATH`
to the `chrome.exe` path, or in PowerShell for the current session:

```powershell
$env:CHROME_PATH = "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe"
```

Typical locations (the agent also searches these using `LOCALAPPDATA` and
`PROGRAMFILES`, not a hardcoded user folder):

```text
%PROGRAMFILES%\Google\Chrome\Application\chrome.exe
%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
```

## 2. Get the code and Python deps

In PowerShell:

```powershell
git clone <this-repo-url>
cd info
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dashboard.txt
python -m playwright install chromium
```

If `Activate.ps1` is blocked, skip activation and call the venv Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\start_local_agent.py
```

Do not copy a Linux `.venv` onto Windows. Create it on this PC.

In Command Prompt the activate script is:

```bat
.venv\Scripts\activate.bat
```

## 3. Copy the dashboard session cookie

1. On **this** PC, open Chrome and sign in to
   `https://ad-factory-3rn5.onrender.com`.
2. Press `F12` → **Application** → **Cookies** → the site.
3. Copy the value of the `session` cookie. That value is the session id the
   launcher asks for.
4. Do not paste it into a chat, a file, or a saved command if you can avoid it.
   The launcher hides the input and does not put it on the command line.

## 4. Start the local agent

From the repo folder, with the venv active:

```powershell
python scripts\start_local_agent.py
```

When it asks `Session cookie:`, paste the cookie and press Enter. The paste is
hidden. That is expected.

The script then runs the local agent with:

- `--api-base https://ad-factory-3rn5.onrender.com`
- `--data-dir %USERPROFILE%\ad-factory-agent`
- `--launch-browser --browser chrome`

A Chrome window should open with remote debugging on `127.0.0.1:9222`. Sign in
to ChatGPT and/or Gemini **in that window**. Keep it open while jobs run.

Windows Firewall may ask whether Python can accept network connections. Allow it
on private networks. Ports `9222` and `8765` must stay on localhost only.

## 5. Pair the dashboard

Keep the agent window running. In a normal Chrome tab on this PC, open the
Render dashboard. Pairing talks to `http://127.0.0.1:8765` on this computer. A
phone or another PC cannot use this agent.

Keep `%USERPROFILE%\ad-factory-agent\config\agent.json`. Later starts can leave
the cookie blank and reuse that registration.

## Later starts

```powershell
cd C:\path\to\info
.\.venv\Scripts\Activate.ps1
python scripts\start_local_agent.py
```

Press Enter at the cookie prompt if this PC is already registered.

## Optional overrides

```powershell
python scripts\start_local_agent.py --api-base https://ad-factory-3rn5.onrender.com
python scripts\start_local_agent.py --data-dir "$env:USERPROFILE\ad-factory-agent"
$env:AGENT_DATA_DIR = "$env:USERPROFILE\ad-factory-agent"
$env:CHROME_PATH = "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe"
```

## Stop

In the agent terminal press `Ctrl+C`. The launcher does not leave
`AD_FACTORY_SESSION` in your interactive shell.

## Troubleshooting

- **Lock error / another agent is running** — only one agent may use
  `%USERPROFILE%\ad-factory-agent`. Stop the other `python.exe` local-agent
  process.
- **Pairing fails** — confirm the agent is running on this PC, the dashboard is
  opened here, and nothing else is using `127.0.0.1:8765`.
- **Chrome not found** — install Google Chrome or set `CHROME_PATH`.
- **Jobs fail login** — use the Chrome window the agent launched, not your
  everyday Chrome profile, and log into ChatGPT/Gemini there.
- **`python` not found** — use `py -3` or
  `.\.venv\Scripts\python.exe scripts\start_local_agent.py`.
