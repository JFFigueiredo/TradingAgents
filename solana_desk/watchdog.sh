#!/usr/bin/env bash
# Watchdog for solana_desk — flock-guarded; relaunches without --reset.
# Usage: CONFIG_ID=control bash watchdog.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONFIG_ID="${CONFIG_ID:-control}"
DATA_DIR="$ROOT/data/$CONFIG_ID"
LOG="$DATA_DIR/desk.log"
PIDFILE="$DATA_DIR/desk.pid"
LOCKFILE="$DATA_DIR/watchdog.lock"
PYTHON="${PYTHON:-python3}"

mkdir -p "$DATA_DIR"

exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "watchdog already running for $CONFIG_ID"
  exit 0
fi

launch_desk() {
  cd "$ROOT"
  # 9>&- so child does not inherit flock
  nohup setsid "$PYTHON" run.py --config "$CONFIG_ID" --headless 9>&- \
    >>"$DATA_DIR/watchdog.out" 2>&1 &
  echo $! >"$PIDFILE"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launched desk pid=$(cat "$PIDFILE")" >>"$DATA_DIR/watchdog.out"
}

is_running() {
  if [[ ! -f "$PIDFILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null
}

log_fresh() {
  [[ -f "$LOG" ]] || return 1
  local now mtime age
  now=$(date +%s)
  mtime=$(stat -c %Y "$LOG" 2>/dev/null || stat -f %m "$LOG")
  age=$((now - mtime))
  (( age < 180 ))
}

while true; do
  if ! is_running || ! log_fresh; then
    if is_running; then
      pid="$(cat "$PIDFILE")"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) freezing — SIGTERM $pid" >>"$DATA_DIR/watchdog.out"
      kill -TERM "$pid" 2>/dev/null || true
      sleep 5
      kill -KILL "$pid" 2>/dev/null || true
    fi
    launch_desk
  fi
  sleep 60
done
