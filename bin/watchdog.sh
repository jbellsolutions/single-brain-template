#!/bin/bash
# Single-Brain watchdog — runs every 60s via crontab.
# Restarts a container only if it's exited/dead/missing (NOT merely unhealthy —
# Docker's own restart policy + the long start_period handle slow/cold starts).
# new-agent.sh fills in __AGENT_NAME__ / __BASE_DIR__.

set -euo pipefail
AGENT_NAME="__AGENT_NAME__"
BASE_DIR="__BASE_DIR__"
LOGDIR="$BASE_DIR/vault/daily-logs"
DATE=$(date -u +%F)
LOGFILE="$LOGDIR/$DATE.md"
mkdir -p "$LOGDIR"
[ -f "$LOGFILE" ] || echo "# $DATE - daily log" > "$LOGFILE"

check_and_heal() {
  local name=$1
  # Skip silently if the container isn't defined on this host (e.g. sync not enabled).
  docker inspect "$name" >/dev/null 2>&1 || return 0
  local status
  status=$(docker inspect "$name" --format '{{.State.Health.Status}}{{if not .State.Health}}{{.State.Status}}{{end}}' 2>/dev/null || echo "missing")
  case "$status" in
    healthy|running|unhealthy|starting) ;;  # leave to Docker
    exited|dead|created|restarting)
      ts=$(date -u +%H:%M:%SZ)
      { echo ""; echo "## $ts watchdog: $name was $status — restarting"; } >> "$LOGFILE"
      docker restart "$name" >> "$LOGFILE" 2>&1
      ;;
    missing)
      ts=$(date -u +%H:%M:%SZ)
      { echo ""; echo "## $ts watchdog: $name CONTAINER MISSING — manual fix required"; } >> "$LOGFILE"
      ;;
  esac
}

check_and_heal "$AGENT_NAME"
check_and_heal "$AGENT_NAME-sync"
