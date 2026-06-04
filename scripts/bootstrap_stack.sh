#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

if [[ "${OS:-}" == "Windows_NT" ]]; then
  echo "Use scripts/bootstrap_stack.ps1 on Windows PowerShell."
  exit 1
fi

echo "[1/6] Checking Python3"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.10+ first."
  exit 1
fi

echo "[2/6] Creating virtualenv if needed"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

echo "[3/6] Installing dashboard dependencies"
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements-dashboard.txt"

echo "[4/6] Checking OpenCode CLI"
if ! command -v opencode >/dev/null 2>&1; then
  echo "OpenCode not found. Installing via npm..."
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found. Install Node.js LTS first: https://nodejs.org/"
    exit 1
  fi
  npm install -g opencode-ai
fi

echo "[5/6] Verifying OpenCode"
opencode --version || true

echo "[6/6] Starting full stack"
bash "$ROOT_DIR/scripts/start_dashboard_stack.sh"

echo
echo "Next steps:"
echo "  1) Open dashboard: http://127.0.0.1:8787"
echo "  2) Add provider: opencode providers login"
echo "  3) Verify models: opencode models"
