# WSL Setup Guide - OpenCode Ad Dashboard

## Prerequisites (Windows side)

1. **Install WSL** (PowerShell as Administrator):
   ```powershell
   wsl --install -d Ubuntu
   ```
   Reboot when prompted.

2. **Disable Windows PATH in WSL** (prevents npm/CLI conflicts):
   ```powershell
   # Run in PowerShell (Admin)
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

## Daily Usage

```bash
cd ~/ad-factory

# Start stack
export OPENCODE_SERVER_PASSWORD="$(grep OPENCODE_SERVER_PASSWORD .env.dashboard | cut -d'=' -f2)"
bash scripts/start_dashboard_stack.sh

# Open in Windows browser: http://127.0.0.1:8787

# Stop stack
bash scripts/stop_dashboard_stack.sh
```

## Troubleshooting

### "Session not found" errors
```bash
pkill -f opencode
rm -rf ~/.local/share/opencode
opencode providers login
bash scripts/start_dashboard_stack.sh
```

### Playwright image generation fails
```bash
source .venv/bin/activate
pip install playwright
sudo python -m playwright install --with-deps chromium
```

### OpenCode CLI shows Windows binary
```bash
# Check which npm you're using
which npm
# If it shows /mnt/c/..., fix PATH:
export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v '/mnt/c' | tr '\n' ':')
hash -r
sudo npm install -g opencode-ai
```

### Port already in use
```bash
bash scripts/stop_dashboard_stack.sh
# Or manually:
pkill -f opencode
pkill -f uvicorn
```

## Architecture Notes

- **OpenCode CLI**: Runs in WSL Linux, communicates with local server
- **Dashboard Backend**: Python FastAPI in WSL, serves UI on port 8787
- **OpenCode Server**: Node.js in WSL, listens on port 4090
- **Playwright**: Headless browser in WSL for image generation
- **Windows Browser**: Just for viewing dashboard UI (no interaction with WSL processes)

All components run inside WSL. Windows is only used for the browser UI and file editing.
