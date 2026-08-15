# Local agent on Windows

The website stays on Render. This Windows PC only runs the **local agent**,
**Chrome**, and stores ads under `%USERPROFILE%\ad-factory-agent`.

Pairing uses `127.0.0.1`, so open the dashboard in a browser **on this same
PC**. You do not need WSL.

Default production URL: `https://ad-factory-3rn5.onrender.com`

## PowerShell execution policy

You do **not** need `Set-ExecutionPolicy Bypass`.

The local agent is Python (`.py`). Windows runs it with `py` / `python.exe`.
Execution policy only blocks PowerShell `.ps1` files such as
`Activate.ps1`.

Use one of these (no policy change):

```bat
py -3 scripts\start_local_agent.py
```

```bat
.venv\Scripts\python.exe scripts\start_local_agent.py
```

```bat
start_local_agent.bat
```

Do not run `.\.venv\Scripts\Activate.ps1`. You do not need it: call
`.venv\Scripts\python.exe` instead. If you still want activate:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

That is enough. Do not use Bypass.

## 1. Install Python and Chrome

1. Python 3.10+ from https://www.python.org/downloads/ — enable
   **Add python.exe to PATH**. Confirm:

   ```bat
   py -3 --version
   ```

2. Google Chrome from https://www.google.com/chrome/

Git is not required.

## 2. Download the local-agent zip

Share or download **only** `ad-factory-local-agent.zip`. It already includes
`requirements-local-agent.txt` and this guide. Do not send extra files.

https://github.com/Vinay-003/ad-factory/raw/render-setup/ad-factory-local-agent.zip

Right-click → Extract All (or unzip) so you have a folder named
`ad-factory-local-agent` that still contains `scripts\`, `local_agent_runtime\`,
and `dashboard\backend\`. Do not flatten those folders.

Create a **local** `.venv` and install into it with the venv Python (no
`Activate.ps1`, no global `pip`):

```bat
cd %USERPROFILE%\Downloads\ad-factory-local-agent
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-local-agent.txt
```

That installs the Playwright **Python library** only. Do **not** run
`playwright install chromium`. The agent drives the Google Chrome already
installed on this PC (`chrome.exe`), through CDP on port 9222.

Create the `.venv` on this PC. Do not copy one from Ubuntu or Mac.

## 3. Chrome path (only if auto-detect fails)

The agent looks for Chrome automatically (`CHROME_PATH`, then `PATH`, then
`%PROGRAMFILES%` and `%LOCALAPPDATA%`). To force `chrome.exe`:

PowerShell, current session:

```powershell
$env:CHROME_PATH = "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe"
```

Permanent user environment variable: Settings → System → About → Advanced
system settings → Environment Variables → New user variable:

| Name | Value |
| --- | --- |
| `CHROME_PATH` | `C:\Program Files\Google\Chrome\Application\chrome.exe` |

Or pass it when starting:

```bat
.venv\Scripts\python.exe scripts\start_local_agent.py --chrome-path "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

Typical Windows paths:

```text
%PROGRAMFILES%\Google\Chrome\Application\chrome.exe
%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe
%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
```

If Chrome is installed somewhere else, put that full `chrome.exe` path in
`CHROME_PATH` or `--chrome-path`.

## 4. Copy the dashboard session cookie

1. On this PC, open Chrome and sign in to
   `https://ad-factory-3rn5.onrender.com`.
2. Press `F12` → **Application** → **Cookies** → the site.
3. Copy the `session` cookie value.

## 5. Start the local agent

```bat
cd %USERPROFILE%\Downloads\ad-factory-local-agent
.venv\Scripts\python.exe scripts\start_local_agent.py
```

Or double-click `start_local_agent.bat`.

Paste the cookie at `Session cookie:` (hidden). Later starts can press Enter
with a blank cookie if `%USERPROFILE%\ad-factory-agent\config\agent.json`
already exists.

Sign in to ChatGPT and/or Gemini in the Chrome window the agent opens. Keep it
open. Pair the Render dashboard in a normal tab on this same PC.

If Windows Firewall asks, allow Python on private networks. Ports `9222` and
`8765` must stay on localhost.

Stop with `Ctrl+C`.
