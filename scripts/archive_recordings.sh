#!/usr/bin/env bash
# archive_recordings.sh — the weekly recordings archive (sprint M16).
#
# Moves the voice recordings HA writes to ~/kronk-voice-recordings, once
# older than 7 days, to <dest>/recordings/<device>/<pipeline>/<YYYY-MM>/<run>/:
# copied, read back (size and SHA-256), and only then deleted locally through
# a throwaway container (the files are root-owned). Nothing on the
# destination is ever deleted. The logic lives in scripts/backups.py;
# docs/features/backups.md is the guide.
#
# Usage:
#   scripts/archive_recordings.sh --dry-run  what would move; writes, deletes and sends nothing
#   scripts/archive_recordings.sh            the real run (the timer's job)
#   ... --no-notify                          a test or by-hand run: no notice, whatever the environment says
#
# Settings: KRONK_BACKUP_DIR (default /mnt/nas-backup), KRONK_RECORDINGS_DIR
# (~/kronk-voice-recordings), KRONK_ARCHIVE_AGE_DAYS (7),
# KRONK_BACKUP_HELPER_IMAGE (alpine:3.20, already on the host; never pulled).
# Fired by systemd/kronk-recordings-archive.timer.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/backups.py" archive "$@"
