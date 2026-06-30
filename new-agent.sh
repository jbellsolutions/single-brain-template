#!/usr/bin/env bash
# new-agent.sh — stamp out a new Single-Brain agent from one config file.
#
#   ./new-agent.sh agent.env
#
# Reads agent.env, creates <BASE_DIR>, copies the parameterized stack, renders
# the Hermes config seed + watchdog, and prints the launch steps. Idempotent:
# re-running refreshes code but never overwrites an existing config.yaml/.env
# (so it won't clobber a live agent's state).
set -euo pipefail

TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${1:-}"
[ -n "$CFG" ] && [ -f "$CFG" ] || { echo "usage: $0 <agent.env>   (copy agent.example.env first)"; exit 1; }

# shellcheck disable=SC1090
set -a; source "$CFG"; set +a

# ── validate ──────────────────────────────────────────────────────────────────
fail() { echo "ERROR: $*" >&2; exit 1; }
: "${AGENT_NAME:?set AGENT_NAME in $CFG}"
: "${BASE_DIR:?set BASE_DIR in $CFG}"
[[ "$AGENT_NAME" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "AGENT_NAME must be [a-z0-9-] (got '$AGENT_NAME')"
[ -n "${FIREWORKS_API_KEY:-}" ] && [[ "$FIREWORKS_API_KEY" != fw_REPLACE_ME ]] || fail "FIREWORKS_API_KEY is required (primary model)"
case "${FIREWORKS_API_KEY}" in fw_*) :;; *) echo "WARN: FIREWORKS_API_KEY doesn't start with fw_";; esac

echo "▸ Provisioning agent '$AGENT_NAME' at $BASE_DIR"

# Enable only channels that have a token. The gateway exits/crash-loops if NO
# enabled channel connects, so never enable a channel without its token.
SLACK_ENABLED=$([ -n "${SLACK_BOT_TOKEN:-}" ] && echo true || echo false)
TELEGRAM_ENABLED=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo true || echo false)
if [ "$SLACK_ENABLED" = false ] && [ "$TELEGRAM_ENABLED" = false ]; then
  echo "  ⚠  No SLACK_BOT_TOKEN or TELEGRAM_BOT_TOKEN set — the gateway needs ≥1 connected"
  echo "     channel to stay up. Add at least one channel token to $CFG before launching."
fi

# ── lay down the stack ────────────────────────────────────────────────────────
mkdir -p "$BASE_DIR"/{bin,sync,hermes/data,vault/daily-logs,logs}
cp "$TEMPLATE_DIR/compose.yml"              "$BASE_DIR/compose.yml"
cp "$TEMPLATE_DIR/bin/init-chown.sh"        "$BASE_DIR/bin/init-chown.sh"
cp "$TEMPLATE_DIR/bin/start-hermes.sh"      "$BASE_DIR/bin/start-hermes.sh"
find "$TEMPLATE_DIR/sync" -maxdepth 1 -type f -exec cp {} "$BASE_DIR/sync/" \;  # files only (skip __pycache__)
chmod +x "$BASE_DIR/bin/"*.sh

# .env — the live one wins if it already exists (don't clobber a running agent)
if [ -f "$BASE_DIR/.env" ]; then
  echo "  · keeping existing $BASE_DIR/.env (not overwritten)"
else
  cp "$CFG" "$BASE_DIR/.env"; chmod 600 "$BASE_DIR/.env"; echo "  · wrote $BASE_DIR/.env"
fi

# watchdog — render AGENT_NAME / BASE_DIR
sed -e "s#__AGENT_NAME__#${AGENT_NAME}#g" -e "s#__BASE_DIR__#${BASE_DIR}#g" \
    "$TEMPLATE_DIR/bin/watchdog.sh" > "$BASE_DIR/bin/watchdog.sh"
chmod +x "$BASE_DIR/bin/watchdog.sh"

# config seed — render persona + home channels; only seed if not already present
CONFIG_DST="$BASE_DIR/hermes/data/config.yaml"
if [ -f "$CONFIG_DST" ]; then
  echo "  · keeping existing $CONFIG_DST (Hermes-managed; not overwritten)"
else
  sed -e "s#__AGENT_PERSONA__#${AGENT_PERSONA:-technical}#g" \
      -e "s#__SLACK_ENABLED__#${SLACK_ENABLED}#g" \
      -e "s#__TELEGRAM_ENABLED__#${TELEGRAM_ENABLED}#g" \
      -e "s#__TELEGRAM_HOME_CHANNEL__#${TELEGRAM_HOME_CHANNEL:-}#g" \
      -e "s#__SLACK_HOME_CHANNEL__#${SLACK_HOME_CHANNEL:-}#g" \
      "$TEMPLATE_DIR/hermes/config.template.yaml" > "$CONFIG_DST"
  # drop empty home-channel lines so we don't seed blank keys
  [ -z "${TELEGRAM_HOME_CHANNEL:-}" ] && sed -i.bak "/^TELEGRAM_HOME_CHANNEL: ''$/d" "$CONFIG_DST"
  [ -z "${SLACK_HOME_CHANNEL:-}" ]    && sed -i.bak "/^SLACK_HOME_CHANNEL: ''$/d"    "$CONFIG_DST"
  rm -f "$CONFIG_DST.bak"
  echo "  · seeded $CONFIG_DST (Fireworks primary + Together fallback, persona=${AGENT_PERSONA:-technical})"
fi

cat <<EOF

✅ Agent '$AGENT_NAME' staged at $BASE_DIR

Launch:
  cd $BASE_DIR
  docker compose up -d                      # core agent (Hermes + channels)
  docker compose --profile sync up -d       # + Obsidian/Notion memory mirror (needs NOTION_* in .env)

Watchdog (auto-restart on crash):
  (crontab -l 2>/dev/null; echo "* * * * * $BASE_DIR/bin/watchdog.sh") | crontab -

Verify:
  docker compose ps
  docker exec $AGENT_NAME /opt/hermes/.venv/bin/hermes fallback list   # confirm primary=Fireworks
  docker compose logs -f $AGENT_NAME

UI (localhost only): SSH-tunnel  ssh -L ${HERMES_PORT:-18789}:127.0.0.1:${HERMES_PORT:-18789} <host>
  or expose on your tailnet:     tailscale serve ${HERMES_PORT:-18789}
EOF
