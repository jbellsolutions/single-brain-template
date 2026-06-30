#!/usr/bin/env bash
# provision-vps.sh — prepare a fresh Ubuntu 22.04+ VPS to host Single-Brain agents.
# Installs Docker (+ compose plugin) and clones this template. Run as a sudo user.
#
#   curl -fsSL https://raw.githubusercontent.com/jbellsolutions/single-brain-template/main/provision-vps.sh | bash
#   # then:  cd ~/single-brain-template && cp agent.example.env agent.env && nano agent.env && ./new-agent.sh agent.env
set -euo pipefail

REPO="${SBT_REPO:-https://github.com/jbellsolutions/single-brain-template.git}"
DEST="${SBT_DEST:-$HOME/single-brain-template}"

echo "▸ Single-Brain VPS provisioner"
# Works as root (fresh DO/Hetzner droplet) or as a sudo user.
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

# ── Docker ────────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  echo "  · installing Docker..."
  curl -fsSL https://get.docker.com | $SUDO sh
  # If running as a normal user, add to the docker group so `docker` works without sudo.
  [ -n "$SUDO" ] && { $SUDO usermod -aG docker "$USER"; echo "  · added $USER to docker group (run 'newgrp docker' or re-login)"; }
fi
docker compose version >/dev/null 2>&1 || { echo "  · installing compose plugin..."; $SUDO apt-get update -qq && $SUDO apt-get install -y -qq docker-compose-plugin; }
$SUDO systemctl enable --now docker >/dev/null 2>&1 || true

# ── git + template ────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || { $SUDO apt-get update -qq && $SUDO apt-get install -y -qq git; }
if [ -d "$DEST/.git" ]; then
  echo "  · updating $DEST"; git -C "$DEST" pull --ff-only || true
else
  echo "  · cloning template to $DEST"; git clone "$REPO" "$DEST"
fi

# ── optional: tailscale for private UI access ─────────────────────────────────
if ! command -v tailscale >/dev/null 2>&1; then
  echo "  · (optional) install Tailscale for private UI access:"
  echo "      curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
fi

cat <<EOF

✅ VPS ready. Next:
  cd $DEST
  cp agent.example.env agent.env
  nano agent.env          # fill AGENT_NAME, BASE_DIR, FIREWORKS_API_KEY, channel tokens
  ./new-agent.sh agent.env

One VPS can host multiple agents — give each a distinct AGENT_NAME, BASE_DIR, and HERMES_PORT.
EOF
