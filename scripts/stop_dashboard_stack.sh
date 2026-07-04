#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/dashboard_storage/pids"

stop_pid_file() {
  local pid_file="$1"
  local name="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name not running (no pid file)"
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.2
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    echo "Stopped $name (pid $pid)"
  else
    echo "$name not running"
  fi
  rm -f "$pid_file"
}

stop_pid_file "$PID_DIR/dashboard.pid" "dashboard"
