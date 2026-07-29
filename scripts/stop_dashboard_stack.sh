#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/dashboard_storage/pids"
DASHBOARD_PORTS=(4090 5555)

stop_dashboard_fallbacks() {
  local stopped=0

  for session in dashboard dashboard4090; do
    if tmux has-session -t "$session" >/dev/null 2>&1; then
      tmux kill-session -t "$session" >/dev/null 2>&1 || true
      echo "Stopped dashboard tmux session ($session)"
      stopped=1
    fi
  done

  for port in "${DASHBOARD_PORTS[@]}"; do
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      kill "$pid" >/dev/null 2>&1 || true
      sleep 0.2
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
      echo "Stopped dashboard listener on port $port (pid $pid)"
      stopped=1
    done < <(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  done

  if [[ "$stopped" -eq 0 ]]; then
    echo "dashboard not running"
  fi
}

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
stop_dashboard_fallbacks
