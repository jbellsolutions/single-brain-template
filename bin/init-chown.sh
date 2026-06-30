#!/bin/bash
# Pre-entrypoint wrapper: runs as root, fixes ownership of paths the
# unprivileged `hermes` user needs to read/write at runtime, then chains
# to the upstream entrypoint which does the gosu privilege drop.
#
# Workarounds for upstream image bugs in nousresearch/hermes-agent:
#
# 1. /opt/hermes/ui-tui/ ships as root:root, so the TUI rebuild fails
#    with EACCES on dist/entry-exports.js → "Chat unavailable: 1".
#
# 2. /opt/data may contain root-owned files (notably auth.json mode 0600)
#    because the upstream entrypoint only chowns when the top-level dir
#    is wrong, not its contents. When auth.json is unreadable, the
#    dashboard's Models/Providers picker comes up empty.
#
# Optimization: skip the recursive chown if top-level ownership is
# already correct. With /opt/data bind-mounted and holding gigabytes
# of session data, a blind `chown -R` walks every file and adds
# tens of seconds to every container start.

set -e

HERMES_UID=$(id -u hermes)
HERMES_GID=$(id -g hermes)

heal() {
  local path=$1
  [ -d "$path" ] || return 0
  local current_uid
  current_uid=$(stat -c %u "$path" 2>/dev/null || echo 0)
  if [ "$current_uid" != "$HERMES_UID" ]; then
    echo "init-chown: fixing $path (was uid=$current_uid)"
    chown -R "$HERMES_UID:$HERMES_GID" "$path" 2>/dev/null || true
  fi
}

heal /opt/hermes/ui-tui
heal /opt/data

exec /opt/hermes/docker/entrypoint.sh "$@"
