#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PID_DIR="$ROOT_DIR/dashboard_storage/pids"
LOG_DIR="$ROOT_DIR/dashboard_storage/logs"
RUNS_DIR="$ROOT_DIR/dashboard_storage/runs"

OPENCODE_HOST="127.0.0.1"
OPENCODE_PORT="4090"
DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT="8787"
OPENCODE_PASSWORD="${OPENCODE_SERVER_PASSWORD:-opencode-local-pass}"
DEFAULT_XDG_DATA_HOME="${HOME}/.local/share"
if [[ -f "$DEFAULT_XDG_DATA_HOME/opencode/auth.json" ]]; then
  CURRENT_XDG_DATA_HOME="${XDG_DATA_HOME:-$DEFAULT_XDG_DATA_HOME}"
  if [[ ! -f "$CURRENT_XDG_DATA_HOME/opencode/auth.json" ]]; then
    export XDG_DATA_HOME="$DEFAULT_XDG_DATA_HOME"
    echo "Using OpenCode credentials from $XDG_DATA_HOME/opencode/auth.json"
  fi
fi

OPENCODE_PID_FILE="$PID_DIR/opencode.pid"
DASHBOARD_PID_FILE="$PID_DIR/dashboard.pid"
OPENCODE_LOG="$LOG_DIR/opencode.log"
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

start_opencode() {
  if is_pid_running "$OPENCODE_PID_FILE"; then
    echo "OpenCode server already running (pid $(cat "$OPENCODE_PID_FILE"))"
    return
  fi
  echo "Starting OpenCode server on $OPENCODE_HOST:$OPENCODE_PORT"
  OPENCODE_SERVER_PASSWORD="$OPENCODE_PASSWORD" \
    nohup opencode serve --hostname "$OPENCODE_HOST" --port "$OPENCODE_PORT" --cors "http://$DASHBOARD_HOST:$DASHBOARD_PORT" >"$OPENCODE_LOG" 2>&1 &
  echo $! >"$OPENCODE_PID_FILE"
}

start_dashboard() {
  if is_pid_running "$DASHBOARD_PID_FILE"; then
    echo "Dashboard server already running (pid $(cat "$DASHBOARD_PID_FILE"))"
    return
  fi
  echo "Starting dashboard API/UI on $DASHBOARD_HOST:$DASHBOARD_PORT"
  OPENCODE_API_URL="http://$OPENCODE_HOST:$OPENCODE_PORT" OPENCODE_SERVER_PASSWORD="$OPENCODE_PASSWORD" \
    nohup "$VENV_DIR/bin/uvicorn" dashboard.backend.app:app --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" --app-dir "$ROOT_DIR" >"$DASHBOARD_LOG" 2>&1 &
  echo $! >"$DASHBOARD_PID_FILE"
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local auth_flag=""
  if [[ "$1" == "-u" ]]; then
    auth_flag="-u $2"
    url="$3"
    name="$4"
  fi
  local retries=60
  local delay=0.2
  for _ in $(seq 1 "$retries"); do
    if curl -fsS $auth_flag "$url" >/dev/null 2>&1; then
      echo "$name is ready"
      return 0
    fi
    sleep "$delay"
  done
  echo "Warning: $name did not become ready at $url"
  return 1
}

open_browser() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v sensible-browser >/dev/null 2>&1; then
    sensible-browser "$url" >/dev/null 2>&1 || true
  else
    echo "Open this URL manually: $url"
  fi
}

start_opencode
start_dashboard

wait_for_url "http://$DASHBOARD_HOST:$DASHBOARD_PORT/api/defaults" "Dashboard"

echo
echo "Dashboard URL: http://$DASHBOARD_HOST:$DASHBOARD_PORT"
echo "OpenCode URL:  http://$OPENCODE_HOST:$OPENCODE_PORT"
echo "OpenCode password: $OPENCODE_PASSWORD"
echo "Dashboard log: $DASHBOARD_LOG"
echo "OpenCode log:  $OPENCODE_LOG"

open_browser "http://$DASHBOARD_HOST:$DASHBOARD_PORT"
