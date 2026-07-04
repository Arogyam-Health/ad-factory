#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PID_DIR="$ROOT_DIR/dashboard_storage/pids"
LOG_DIR="$ROOT_DIR/dashboard_storage/logs"
RUNS_DIR="$ROOT_DIR/dashboard_storage/runs"

DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT="8787"
DASHBOARD_PID_FILE="$PID_DIR/dashboard.pid"
DASHBOARD_LOG="$LOG_DIR/dashboard.log"

mkdir -p "$PID_DIR" "$LOG_DIR" "$RUNS_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements-dashboard.txt" >/dev/null

is_pid_running() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$pid_file")"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

start_dashboard() {
  if is_pid_running "$DASHBOARD_PID_FILE"; then
    echo "Dashboard server already running (pid $(cat "$DASHBOARD_PID_FILE"))"
    return
  fi
  echo "Starting dashboard API/UI on $DASHBOARD_HOST:$DASHBOARD_PORT"
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
  fi
  if [[ -z "${OPENCODE_API_KEY:-}" ]]; then
    echo "WARNING: OPENCODE_API_KEY not set. Set it in .env or export it."
  fi
  if [[ -z "${OPENCODE_API_URL:-}" ]]; then
    echo "WARNING: OPENCODE_API_URL not set. Set it in .env or export it."
  fi
  nohup "$VENV_DIR/bin/uvicorn" dashboard.backend.app:app --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" --app-dir "$ROOT_DIR" >"$DASHBOARD_LOG" 2>&1 &
  echo $! >"$DASHBOARD_PID_FILE"
}

open_browser() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v sensible-browser >/dev/null 2>&1; then
    sensible-browser "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  else
    echo "Open this URL manually: $url"
  fi
}

start_dashboard

echo "Waiting for dashboard..."
for i in $(seq 1 30); do
  if curl -fsS "http://$DASHBOARD_HOST:$DASHBOARD_PORT/api/defaults" >/dev/null 2>&1; then
    echo "Dashboard is ready"
    break
  fi
  sleep 0.2
done

echo
echo "Dashboard URL: http://$DASHBOARD_HOST:$DASHBOARD_PORT"
echo "Dashboard log: $DASHBOARD_LOG"

open_browser "http://$DASHBOARD_HOST:$DASHBOARD_PORT"
