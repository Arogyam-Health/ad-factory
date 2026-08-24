#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

if [[ ! -d "$ROOT_DIR/dashboard/backend" ]]; then
  echo "Dashboard backend folder not found"
  exit 1
fi

echo "[1/3] Creating local stack dirs"
mkdir -p "$ROOT_DIR/.local-stack/pids" "$ROOT_DIR/.local-stack/logs"

echo "[2/3] Ensuring Python dependencies"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements-dashboard.txt"

echo "[3/3] Starting FastAPI server"
exec "$VENV_DIR/bin/uvicorn" dashboard.backend.app:app --host 0.0.0.0 --port 8787 --reload --app-dir "$ROOT_DIR"
