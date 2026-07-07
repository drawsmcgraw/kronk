#!/usr/bin/env bash
# MagicMirror updater — runs ON THE PI, as user `drew`, invoked by Kronk
# over SSH as user `kronk` through a forced-command key + a one-line
# sudoers grant. Reference copy; the live copy is /home/drew/kronk/mm-update.sh
# on the Pi. See docs/plans/MAGICMIRROR_PLAN.md for the full design.
#
# ── Pi-side setup (one time, as drew) ────────────────────────────────────────
#   sudo useradd -m -s /bin/bash kronk
#   sudo mkdir -p ~kronk/.ssh && sudo tee -a ~kronk/.ssh/authorized_keys <<'EOT'
#   command="sudo -u drew /home/drew/kronk/mm-update.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... kronk-mm-update
#   EOT
#   sudo chown -R kronk:kronk ~kronk/.ssh && sudo chmod 700 ~kronk/.ssh && sudo chmod 600 ~kronk/.ssh/authorized_keys
#   echo 'kronk ALL=(drew) NOPASSWD: /home/drew/kronk/mm-update.sh' | sudo tee /etc/sudoers.d/kronk-mm
#   mkdir -p /home/drew/kronk && cp mm-update.sh /home/drew/kronk/ && chmod 755 /home/drew/kronk/mm-update.sh
#   # The script must NOT be writable by kronk (sudoers points at it).
#
# The forced command ignores whatever the client sends EXCEPT that we read
# $SSH_ORIGINAL_COMMAND to pick an allowlisted verb: update (default),
# status, rollback. Anything else prints usage and exits 2. The kronk user
# can do exactly these three things and nothing else.
#
# Contract with Kronk's tool_service: machine-readable last line —
#   KRONK-OK <verb> <key=value ...>     on success
#   KRONK-FAIL <verb> step=<step> <detail>  on failure (nonzero exit)
set -euo pipefail

MM_DIR="${MM_DIR:-$HOME/MagicMirror}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/mm-backups}"
PM2_NAME="${PM2_NAME:-mm}"
KEEP_BACKUPS=3

verb="${SSH_ORIGINAL_COMMAND:-update}"
verb="${verb%% *}"   # first word only; no shell-through

fail() { echo "KRONK-FAIL ${verb} step=$1 $2"; exit 1; }

cd "$MM_DIR" 2>/dev/null || fail preflight "MagicMirror directory not found at $MM_DIR"

case "$verb" in

  status)
    rev=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
    ver=$(node -p "require('./package.json').version" 2>/dev/null || echo unknown)
    pm2_state=$(pm2 jlist 2>/dev/null | python3 -c "
import json,sys
apps=json.load(sys.stdin)
print(next((a['pm2_env']['status'] for a in apps if a['name']=='$PM2_NAME'), 'not-found'))" 2>/dev/null || echo unknown)
    last_backup=$(ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz 2>/dev/null | head -1 || true)
    echo "KRONK-OK status rev=$rev version=$ver pm2=$pm2_state last_backup=${last_backup:-none}"
    ;;

  update)
    old_rev=$(git rev-parse --short HEAD) || fail preflight "not a git checkout"
    # Refuse to clobber local source edits (upstream guide: git reset --hard
    # is the operator's deliberate call, never the robot's).
    if ! git diff --quiet || ! git diff --cached --quiet; then
      fail preflight "local changes in $MM_DIR — resolve by hand first (git status)"
    fi

    # FULL backup before anything else (operator requirement) — whole tree
    # including config.js, custom.css, modules/ and node_modules, so a
    # rollback is a pure restore with no npm reinstall needed.
    mkdir -p "$BACKUP_DIR"
    stamp=$(date +%Y%m%d-%H%M%S)
    backup="$BACKUP_DIR/mm-backup-$stamp-$old_rev.tar.gz"
    tar -czf "$backup" -C "$(dirname "$MM_DIR")" "$(basename "$MM_DIR")" \
      || fail backup "tar failed (disk full?)"
    ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

    # Official upgrade procedure (docs.magicmirror.builders upgrade guide,
    # verified 2026-07-06): git pull && node --run install-mm. Older
    # releases lack the install-mm script — fall back to npm install.
    git pull --ff-only || fail git-pull "git pull failed — see output above; backup at $backup"
    if node --run install-mm 2>/dev/null; then :
    elif npm install --no-audit --no-fund; then :
    else fail npm-install "dependency install failed; backup at $backup"
    fi

    pm2 restart "$PM2_NAME" --update-env || fail pm2-restart "pm2 restart $PM2_NAME failed; backup at $backup"
    sleep 5
    state=$(pm2 jlist | python3 -c "
import json,sys
apps=json.load(sys.stdin)
print(next((a['pm2_env']['status'] for a in apps if a['name']=='$PM2_NAME'), 'not-found'))")
    [ "$state" = "online" ] || fail verify "pm2 reports '$state' after restart; backup at $backup — run rollback"

    new_rev=$(git rev-parse --short HEAD)
    echo "KRONK-OK update old=$old_rev new=$new_rev backup=$(basename "$backup")"
    ;;

  rollback)
    latest=$(ls -1t "$BACKUP_DIR"/mm-backup-*.tar.gz 2>/dev/null | head -1)
    [ -n "$latest" ] || fail preflight "no backups in $BACKUP_DIR"
    tar -xzf "$latest" -C "$(dirname "$MM_DIR")" || fail restore "tar extract failed"
    pm2 restart "$PM2_NAME" --update-env || fail pm2-restart "pm2 restart failed after restore"
    echo "KRONK-OK rollback restored=$(basename "$latest")"
    ;;

  *)
    echo "KRONK-FAIL $verb step=verb allowed verbs: update (default), status, rollback"
    exit 2
    ;;
esac
