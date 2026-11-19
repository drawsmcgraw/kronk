#!/usr/bin/env bash
# backup.sh — the monthly config backup (sprint M16).
#
# One dated tar of what is needed to rebuild Kronk (secrets included, in the
# clear: the file is mode 0600), written to <dest>/config/, read back and
# verified, then renamed; the newest 12 are kept. Start and end notices go
# through scripts/lib/notify.sh. The logic lives in scripts/backups.py;
# docs/features/backups.md is the guide, docs/runbooks/restore.md the way back.
#
# Usage:
#   scripts/backup.sh --dry-run              what would go in, with sizes; writes and sends nothing
#   scripts/backup.sh                        the real run (the timer's job; also before a risky change)
#   scripts/backup.sh --dest <dir>           another destination (tests, a USB disk by hand)
#   ... --no-notify                          a test or by-hand run: no notice, whatever the environment says
#
# Settings: KRONK_BACKUP_DIR (default /mnt/nas-backup, which must be a mount
# point), KRONK_BACKUP_KEEP (12), KRONK_BACKUP_MAX_MB (50), KRONK_BACKUP_STATE
# (data/backups). Fired by systemd/kronk-backup.timer.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/backups.py" config "$@"
