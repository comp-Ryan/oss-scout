#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -f web/package.json ]; then
  (cd web && npm ci --silent && npm run build --silent)
fi
mkdir -p ~/.config/systemd/user
cp systemd/oss-scout-run.service systemd/oss-scout-run.timer systemd/oss-scout.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oss-scout-run.timer
systemctl --user enable --now oss-scout.service
systemctl --user restart oss-scout.service
systemctl --user list-timers oss-scout-run.timer
systemctl --user is-active oss-scout.service && echo "dashboard: http://127.0.0.1:8765"
