#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM for Prove It. Safe to run again.
#   ssh root@<vm> 'bash -s' < deploy/setup_vm.sh
# Then copy .env to /opt/prove-it/.env and run `docker compose up -d --build` there.
set -euo pipefail

REPO=https://github.com/EnioAguiar/prove-it.git
APP_DIR=/opt/prove-it
JOB_ROOT=/srv/proveit/jobs

export DEBIAN_FRONTEND=noninteractive

echo "== Docker"
if ! command -v docker >/dev/null; then
  apt-get update -q
  apt-get install -yq ca-certificates curl git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker --version

# No gVisor: tested on this VM (2026-11-05), a sandbox under runsc that hits --pids-limit dies
# with exit 2 and loses all output, so the verdict and the containment evidence are lost.
# Sandboxes use hardened runc (see SANDBOX flags in app/sandbox.py).

echo "== Firewall: SSH, HTTP, HTTPS only"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "== App"
install -d -m 0755 "$JOB_ROOT"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi
[ -f "$APP_DIR/.env" ] || echo "Missing $APP_DIR/.env: copy it from your machine (scp .env root@<vm>:$APP_DIR/.env)"
