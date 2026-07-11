#!/usr/bin/env bash
# MagicMirror updater — runs ON THE PI as user `pi`. Kronk stages this file
# via scp and runs it with a verb argument (update|status|rollback):
#   ssh pi@mirror '~/kronk/mm-update.sh status'
# The file is the canonical repo copy (kronk:magicmirror/mm-update.sh),
# bind-mounted into tool_service and pushed fresh each run — never
# hand-dropped on the Pi. See docs/plans/MAGICMIRROR_PLAN.md.
#
# This Pi runs MagicMirror as a systemd USER unit (magicmirror.service, an
# Electron kiosk on :8080) — NOT pm2 (discovered 2026-07-11 by live probe).
# `systemctl --user` needs XDG_RUNTIME_DIR pointed at the user's runtime
# bus over a non-login SSH session, set below.
#
# Contract with tool_service: machine-readable last line —
#   KRONK-OK <verb> <key=value ...>        on success
#   KRONK-FAIL <verb> step=<step> <detail> on failure (nonzero exit)
set -euo pipefail

MM_DIR="${MM_DIR:-$HOME/MagicMirror}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/mm-backups}"
MM_UNIT="${MM_UNIT:-magicmirror}"           # systemd --user unit name
KEEP_BACKUPS=3

# Make `systemctl --user` reachable from a non-login SSH session.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

verb="${1:-update}"

fail() { echo "KRONK-FAIL ${verb} step=$1 $2"; exit 1; }

svc() { systemctl --user "$@" "$MM_UNIT"; }

# Count third-party git modules (excludes core `default` and *.bak backups).
count_modules() {
  local n=0 d name
  shopt -s nullglob
  for d in "$MM_DIR"/modules/*/; do
    name="$(basename "$d")"
    [ "$name" = "default" ] && continue
    case "$name" in *.bak) continue;; esac
    [ -d "$d/.git" ] && n=$((n + 1))
  done
  echo "$n"
}

# Update every third-party git module in place: git pull (ff-only) + npm
# install where a package.json exists. Best-effort per module — a single
# module's failure never aborts the core update; results are reported.
# Skips: core `default`, *.bak backups, non-git dirs, and any module with
# TRACKED local edits (never clobber operator work — the full-tree backup
# already lets rollback undo everything anyway).
MOD_OK=0; MOD_SKIP=0; MOD_FAIL=0; MOD_FAIL_NAMES=""
update_modules() {
  local d name
  shopt -s nullglob
  for d in "$MM_DIR"/modules/*/; do
    name="$(basename "$d")"
    [ "$name" = "default" ] && continue
    case "$name" in *.bak) MOD_SKIP=$((MOD_SKIP + 1)); continue;; esac
    if [ ! -d "$d/.git" ]; then MOD_SKIP=$((MOD_SKIP + 1)); continue; fi
    if ! git -C "$d" diff --quiet || ! git -C "$d" diff --cached --quiet; then
      MOD_SKIP=$((MOD_SKIP + 1)); continue   # dirty — leave it alone
    fi
    if ! git -C "$d" pull --ff-only >/dev/null 2>&1; then
      MOD_FAIL=$((MOD_FAIL + 1)); MOD_FAIL_NAMES="${MOD_FAIL_NAMES},${name}(pull)"; continue
    fi
    if [ -f "$d/package.json" ]; then
      if ! ( cd "$d" && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 ); then
        MOD_FAIL=$((MOD_FAIL + 1)); MOD_FAIL_NAMES="${MOD_FAIL_NAMES},${name}(npm)"; continue
      fi
    fi
    MOD_OK=$((MOD_OK + 1))
  done
}

cd "$MM_DIR" 2>/dev/null || fail preflight "MagicMirror directory not found at $MM_DIR"

case "$verb" in

  status)
    rev=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
    ver=$(node -p "require('./package.json').version" 2>/dev/null || echo unknown)
    state=$(svc is-active 2>/dev/null || echo unknown)
    last_backup=$(ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz 2>/dev/null | head -1 || true)
    echo "KRONK-OK status rev=$rev version=$ver service=$state modules=$(count_modules) last_backup=${last_backup:-none}"
    ;;

  update)
    old_rev=$(git rev-parse --short HEAD) || fail preflight "not a git checkout"
    # Refuse to clobber local source edits (upstream guide: git reset --hard
    # is the operator's deliberate call, never the robot's). Untracked files
    # (e.g. a module's own data json) are fine — only tracked modifications
    # block.
    if ! git diff --quiet || ! git diff --cached --quiet; then
      fail preflight "tracked local changes in $MM_DIR — resolve by hand first (git status)"
    fi

    # FULL backup before anything else (operator requirement) — whole tree
    # incl. config.js, custom.css, modules/ and node_modules, so a rollback
    # is a pure restore with no npm reinstall. Exclude prior backups if they
    # live under the tree (they don't by default; guard anyway).
    mkdir -p "$BACKUP_DIR"
    stamp=$(date +%Y%m%d-%H%M%S)
    backup="$BACKUP_DIR/mm-backup-$stamp-$old_rev.tar.gz"
    tar --exclude="$(basename "$BACKUP_DIR")" -czf "$backup" \
        -C "$(dirname "$MM_DIR")" "$(basename "$MM_DIR")" \
      || fail backup "tar failed (disk full?)"
    ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

    # Official upgrade procedure (docs.magicmirror.builders upgrade guide):
    # git pull && node --run install-mm. npm-install fallback for older MM.
    git pull --ff-only || fail git-pull "git pull failed — see output above; backup at $backup"
    if node --run install-mm 2>/dev/null; then :
    elif npm install --no-audit --no-fund; then :
    else fail npm-install "dependency install failed; backup at $backup"
    fi

    # Third-party modules (operator requirement 2026-07-11) — best-effort,
    # after core deps, before the single restart that covers everything.
    update_modules

    svc restart || fail restart "systemctl --user restart $MM_UNIT failed; backup at $backup"
    sleep 5
    state=$(svc is-active 2>/dev/null || echo unknown)
    [ "$state" = "active" ] || fail verify "service is '$state' after restart; backup at $backup — run rollback"

    new_rev=$(git rev-parse --short HEAD)
    new_ver=$(node -p "require('./package.json').version" 2>/dev/null || echo unknown)
    mods="mods_ok=$MOD_OK mods_skipped=$MOD_SKIP mods_failed=$MOD_FAIL"
    [ -n "$MOD_FAIL_NAMES" ] && mods="$mods mod_failures=${MOD_FAIL_NAMES#,}"
    # version= is the friendly semver for the spoken announce; new= is the
    # git rev for the audit trail. Speech prefers version.
    echo "KRONK-OK update old=$old_rev new=$new_rev version=$new_ver backup=$(basename "$backup") $mods"
    ;;

  rollback)
    latest=$(ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz 2>/dev/null | head -1)
    [ -n "$latest" ] || fail preflight "no backups in $BACKUP_DIR"
    tar -xzf "$latest" -C "$(dirname "$MM_DIR")" || fail restore "tar extract failed"
    svc restart || fail restart "systemctl --user restart $MM_UNIT failed after restore"
    sleep 5
    state=$(svc is-active 2>/dev/null || echo unknown)
    [ "$state" = "active" ] || fail verify "service is '$state' after restore"
    echo "KRONK-OK rollback restored=$(basename "$latest")"
    ;;

  *)
    echo "KRONK-FAIL $verb step=verb allowed verbs: update (default), status, rollback"
    exit 2
    ;;
esac
