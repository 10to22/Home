#!/usr/bin/env bash
# Install and start the systemd service so phrasehome runs on boot.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(pwd)
RUN_USER=${SUDO_USER:-$(whoami)}
RUN_UID=$(id -u "$RUN_USER")
sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$RUN_USER|g" -e "s|@UID@|$RUN_UID|g" deploy/phrasehome.service \
  > /etc/systemd/system/phrasehome.service
systemctl daemon-reload
systemctl enable --now phrasehome
echo "Running. Logs: journalctl -u phrasehome -f"
