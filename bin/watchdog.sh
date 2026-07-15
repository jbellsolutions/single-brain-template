#!/bin/bash
# Single-Brain watchdog — runs every 60s via crontab.
# new-agent.sh fills in __AGENT_NAME__ / __BASE_DIR__.
#
# DELIBERATE SUPERSET of single-brain's basic watchdog (kept hardened here because
# a template stamps agents that must "just run" unattended). Heals two classes;
# healing is the priority, logging is best-effort:
#   1. run-state exited/dead/created → restart. (Docker's `restart:` policy also
#      catches these; belt-and-suspenders, and reads .State.Status DIRECTLY so a
#      stale .State.Health value on an exited container can't mask it.)
#   2. running BUT unhealthy for HEALS_THRESHOLD consecutive checks (~3 min) →
#      restart. Docker's restart policy does NOT restart a container that stays
#      *running* while its healthcheck fails, so without this a wedged-but-alive
#      gateway sits broken forever. The consecutive-strike threshold avoids
#      flapping during start_period (which reports `starting`, never `unhealthy`).
#
# NOT `set -e`: a logging failure (full disk / read-only vault mount) must never
# abort a restart. Vault writes are guarded and fall back to stderr (cron→syslog).
# (If porting an upstream single-brain watchdog change, preserve this hardening.)

set -uo pipefail
AGENT_NAME="__AGENT_NAME__"
BASE_DIR="__BASE_DIR__"
LOGDIR="$BASE_DIR/vault/daily-logs"
STATEDIR="$BASE_DIR/.watchdog"
DATE=$(date -u +%F)
LOGFILE="$LOGDIR/$DATE.md"
HEALS_THRESHOLD=3

mkdir -p "$STATEDIR" 2>/dev/null || true

# Best-effort vault log; always echoes to stderr, never fatal.
note() {
  echo "$1" >&2
  if mkdir -p "$LOGDIR" 2>/dev/null; then
    printf '%s\n' "$1" >> "$LOGFILE" 2>/dev/null || true
  fi
}

check_and_heal() {
  local name=$1
  # Skip silently if the container isn't defined on this host (e.g. sync not enabled).
  docker inspect "$name" >/dev/null 2>&1 || return 0
  local state health counter count ts
  state=$(docker inspect "$name" --format '{{.State.Status}}' 2>/dev/null || echo "missing")
  counter="$STATEDIR/unhealthy-$name.count"

  case "$state" in
    exited|dead|created)
      ts=$(date -u +%H:%M:%SZ)
      note ""
      note "## $ts watchdog: $name state=$state — restarting"
      docker restart "$name" >>"$LOGFILE" 2>&1 || docker restart "$name" >&2 || true
      rm -f "$counter" 2>/dev/null || true
      ;;
    running|paused|restarting)
      health=$(docker inspect "$name" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo "none")
      if [ "$health" = "unhealthy" ]; then
        count=$(cat "$counter" 2>/dev/null || echo 0)
        count=$((count + 1))
        echo "$count" > "$counter" 2>/dev/null || true
        if [ "$count" -ge "$HEALS_THRESHOLD" ]; then
          ts=$(date -u +%H:%M:%SZ)
          note ""
          note "## $ts watchdog: $name running but unhealthy x$count — restarting"
          docker restart "$name" >>"$LOGFILE" 2>&1 || docker restart "$name" >&2 || true
          rm -f "$counter" 2>/dev/null || true
        fi
        # else: below threshold — accrue silently, wait for the streak
      else
        rm -f "$counter" 2>/dev/null || true   # healthy/starting/none — reset the streak
      fi
      ;;
    missing)
      ts=$(date -u +%H:%M:%SZ)
      note ""
      note "## $ts watchdog: $name CONTAINER MISSING — manual fix required"
      ;;
  esac
}

check_and_heal "$AGENT_NAME"
check_and_heal "$AGENT_NAME-sync"
