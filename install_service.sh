#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user
cp systemd/oss-scout-run.service systemd/oss-scout-run.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oss-scout-run.timer
systemctl --user list-timers oss-scout-run.timer
