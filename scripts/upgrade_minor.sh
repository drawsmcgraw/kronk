#!/usr/bin/env bash
# upgrade_minor.sh — the monthly patch-upgrade run (sprint M18).
#
# Applies the releases scripts/upgrade_rules.toml calls minor, one component
# at a time: pin, commit, bring up, verify, keep or roll back; then one
# notice. Declines when the house is busy or the tree is not clean or not
# on the expected branch. The logic and its long description live in
# scripts/upgrades.py; docs/features/upgrade-cadence.md is the guide.
#
# Usage:
#   scripts/upgrade_minor.sh --dry-run        what it would do now; changes nothing (safe any time)
#   scripts/upgrade_minor.sh                  the real run (the timer's job)
#   scripts/upgrade_minor.sh --ignore-month   a real run even if this month's is done
#
# Settings: KRONK_UPGRADE_BRANCH (the branch commits may land on; default
# from the rules file), KRONK_UPGRADE_STATE (default data/upgrades).
# Fired by systemd/kronk-upgrade-minor.timer.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/upgrades.py" minor "$@"
