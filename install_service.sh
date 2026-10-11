#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -f web/package.json ]; then
  (cd web && npm ci --silent && npm run build --silent)
fi
UV="$(command -v uv)" || { echo "uv not found on PATH (https://docs.astral.sh/uv/)"; exit 1; }
SCOUT_DIR="$(pwd)"
mkdir -p ~/.config/systemd/user
# The unit files in systemd/ are templates: fill in this checkout's folder and the uv binary.
for unit in oss-scout-run.service oss-scout.service; do
  sed -e "s|@SCOUT_DIR@|$SCOUT_DIR|g" -e "s|@UV_DIR@|$(dirname "$UV")|g" -e "s|@UV@|$UV|g" \
    "systemd/$unit" > ~/.config/systemd/user/"$unit"
done
cp systemd/oss-scout-run.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oss-scout-run.timer
systemctl --user enable --now oss-scout.service
systemctl --user restart oss-scout.service
systemctl --user list-timers oss-scout-run.timer
systemctl --user is-active oss-scout.service && echo "dashboard: http://127.0.0.1:8765"
