#!/usr/bin/env bash
# backup_check.sh — the daily stale check for the backups (sprint M16).
#
# Warns (one notice when the warnings change, repeated weekly; and on
# /services) when the newest config backup on the destination is older than
# 35 days, the recordings archive has not finished a run in 9 days, the
# destination is not mounted, or the last run of either job failed.
# Writes only data/backups/status.json. Fired by systemd/kronk-backup-check.timer.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/backups.py" check "$@"
