#!/bin/bash
# Entrypoint: start Hermes dashboard in background, gateway in foreground.
# Dashboard binds to 0.0.0.0 inside the container; Docker port binding
# (127.0.0.1:18789:9119) ensures it is only reachable via SSH tunnel on the host.

HERMES=/opt/hermes/.venv/bin/hermes

$HERMES dashboard   --host 0.0.0.0   --port 9119   --no-open   --insecure --tui &

exec $HERMES gateway run --accept-hooks
