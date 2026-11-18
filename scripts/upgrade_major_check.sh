#!/usr/bin/env bash
# upgrade_major_check.sh — the quarterly major-release check (sprint M18).
#
# Finds releases scripts/upgrade_rules.toml calls major, writes the impact
# report to docs/upgrades/<date>.md (committed when the tree is clean and on
# the expected branch; otherwise under data/upgrades/reports/), then tells
# Drew one or more major upgrades need scheduling. It upgrades nothing.
#
# Usage:
#   scripts/upgrade_major_check.sh --dry-run          print the report; write and send nothing
#   scripts/upgrade_major_check.sh                    the real check (the timer's job)
#   scripts/upgrade_major_check.sh --out FILE         write the report to FILE only (no commit, no notice)
#
# Fired by systemd/kronk-upgrade-major-check.timer.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/upgrades.py" major "$@"
