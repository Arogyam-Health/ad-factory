# Local agent setup

Share **only** `ad-factory-local-agent.zip`. That zip already contains the
Python code, `requirements-local-agent.txt`, and these setup guides. Do not
send `requirements-local-agent.txt` or any README as a separate file.

The website stays on Render. Each machine only runs the local agent, Chrome,
and stores ads under that user's `ad-factory-agent` folder.

## What to follow

1. Give the other machine `ad-factory-local-agent.zip` (this repo file, or
   https://github.com/Vinay-003/ad-factory/raw/render-setup/ad-factory-local-agent.zip).
2. Unzip it. Leave `scripts/`, `local_agent_runtime/`, `dashboard/backend/`,
   and `docs/` inside the unzipped folder.
3. Open **one** OS guide from the unzipped `docs/` folder and follow it
   top to bottom:

| OS | File inside the zip |
| --- | --- |
| Windows | `docs/LOCAL_AGENT_WINDOWS.md` |
| Ubuntu | `docs/LOCAL_AGENT_UBUNTU.md` |
| macOS | `docs/LOCAL_AGENT_MAC.md` |

Do not clone the whole repo. Do not copy only `local_agent_runtime`.

## venv without activate

Packages stay in a local `.venv` folder. Do **not** `pip install` globally and
do **not** run `Activate.ps1` / `source .venv/bin/activate`. Call the venv
Python directly, as the OS guides do:

```text
Windows:  .venv\Scripts\python.exe -m pip install -r requirements-local-agent.txt
          .venv\Scripts\python.exe scripts\start_local_agent.py

Ubuntu/Mac:  .venv/bin/python -m pip install -r requirements-local-agent.txt
             .venv/bin/python scripts/start_local_agent.py
```

`requirements-local-agent.txt` is already in the zip root.

Python scripts do **not** need a PowerShell execution-policy bypass. If Chrome
is not found, see the **Chrome path** section in the OS guide.
