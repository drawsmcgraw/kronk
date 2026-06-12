#!/usr/bin/env bash
# Kronk boot notifier.
#
# Runs once at boot via kronk-bootnotify.service. Inspects the previous boot's
# journal to decide whether the prior shutdown was clean or a hard hang, then
# pushes a Home Assistant mobile-app notification with the verdict.
#
# Born from the 2026-06-08 hard hang (the second in a month) — see
# docs/incidents/INCIDENT_2026-05-31.md. Pairs with the AMD platform watchdog
# (sp5100_tco) + systemd RuntimeWatchdog: watchdog auto-reboots the box when
# wedged, this script tells the operator's phone it happened.
#
# Same notification path as memwatch (notify/mobile_app_pixel_7, same HA_TOKEN
# in .env, same shape of POST). Different `tag` so notifications don't collide
# in Android's notification shade.
#
# Detection heuristic: a clean shutdown leaves "systemd-shutdown" markers in
# the prior boot's journal. A hard hang stops the journal mid-stream with no
# such marker. If we can't read -b -1 at all (first boot after install, or
# journals truncated), we exit silently rather than notify.
#
# Env overrides (set in the systemd unit):
#   FORCE_UNCLEAN=1    — bypass detection, always notify (for testing)
#   HA_NOTIFY_SERVICE  — defaults to notify/mobile_app_pixel_7
#   WAIT_MAX_SEC       — how long to wait for HA to be reachable (default 300)
set -euo pipefail

REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
HA_URL="${HA_URL:-http://localhost:8123}"
HA_NOTIFY_SERVICE="${HA_NOTIFY_SERVICE:-notify/mobile_app_pixel_7}"
WAIT_MAX_SEC="${WAIT_MAX_SEC:-300}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') boot_notify: $*"; }

# ── load HA_TOKEN (same pattern as memwatch.sh) ─────────────────────────────
if [[ -z "${HA_TOKEN:-}" ]] && [[ -f "$REPO_DIR/.env" ]]; then
    while IFS='=' read -r k v; do
        [[ "$k" == "HA_TOKEN" ]] && export HA_TOKEN="$v"
    done < <(grep '^HA_TOKEN=' "$REPO_DIR/.env")
fi
if [[ -z "${HA_TOKEN:-}" ]]; then
    log "ERROR: HA_TOKEN not set (no env var, not in $REPO_DIR/.env)"
    exit 1
fi

# ── detection ───────────────────────────────────────────────────────────────
# Look at the previous boot's journal for shutdown markers.
# If absent → previous boot ended unexpectedly.
detect_unclean() {
    if [[ "${FORCE_UNCLEAN:-0}" == "1" ]]; then
        log "FORCE_UNCLEAN=1 — treating as unclean for test"
        return 0
    fi

    # journalctl -b -1 errors out if there is no previous boot (e.g. first boot
    # after install). In that case, don't notify.
    if ! journalctl -b -1 -n 1 --no-pager >/dev/null 2>&1; then
        log "no previous boot in journal — nothing to report"
        return 1
    fi

    # Clean shutdown leaves one of these in the prior boot's tail:
    #   "systemd-shutdown[1]: Syncing filesystems and block devices"
    #   "Reached target shutdown.target"
    #   "Reached target reboot.target"
    #   "Reached target poweroff.target"
    if journalctl -b -1 -q --grep "systemd-shutdown\[1\]: Syncing filesystems|Reached target (shutdown|reboot|poweroff|halt)\.target" >/dev/null 2>&1; then
        return 1  # clean
    fi
    return 0  # unclean
}

# ── grab evidence for the alert body ────────────────────────────────────────
gather_context() {
    local boot_end boot_start uptime_str last_line
    # `journalctl --list-boots` columns: idx id FIRST LAST
    boot_start=$(journalctl --list-boots --no-pager 2>/dev/null | awk '$1=="-1"{print $4, $5, $6; exit}')
    boot_end=$(journalctl --list-boots --no-pager 2>/dev/null | awk '$1=="-1"{print $8, $9, $10; exit}')
    last_line=$(journalctl -b -1 -n 1 --no-pager -o short 2>/dev/null | tail -1 | cut -c1-180)
    uptime_str=$(uptime -p 2>/dev/null || echo "unknown")
    cat <<EOF
Last boot started:  ${boot_start:-unknown}
Last log entry:     ${boot_end:-unknown}
Current uptime:     ${uptime_str}
Tail:               ${last_line:-(none)}
EOF
}

# ── wait for HA to be reachable ─────────────────────────────────────────────
wait_for_ha() {
    local deadline=$(( $(date +%s) + WAIT_MAX_SEC ))
    while (( $(date +%s) < deadline )); do
        if curl -sf --max-time 5 -H "Authorization: Bearer $HA_TOKEN" \
            "$HA_URL/api/" >/dev/null 2>&1; then
            return 0
        fi
        sleep 5
    done
    return 1
}

# ── send notification ───────────────────────────────────────────────────────
send_alert() {
    local title="$1" message="$2"
    local payload
    payload=$(jq -n --arg t "$title" --arg m "$message" \
        '{"title":$t,"message":$m,"data":{"tag":"kronk-bootnotify","group":"kronk-alerts"}}')
    if curl -sf -X POST "$HA_URL/api/services/$HA_NOTIFY_SERVICE" \
        -H "Authorization: Bearer $HA_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null; then
        log "notified: $title"
    else
        log "HA notify FAILED: $title"
        return 1
    fi
}

# ── main ────────────────────────────────────────────────────────────────────
if ! detect_unclean; then
    log "previous shutdown was clean — no alert"
    exit 0
fi

log "detected unclean prior shutdown; waiting for HA"
if ! wait_for_ha; then
    log "HA did not become reachable within ${WAIT_MAX_SEC}s — giving up"
    exit 1
fi

ctx=$(gather_context)
log "context: $ctx"

if [[ "${FORCE_UNCLEAN:-0}" == "1" ]]; then
    send_alert "Kronk: bootnotify TEST" "This is a test alert from boot_notify.sh.\n\n${ctx}"
else
    send_alert "Kronk auto-rebooted" "Previous shutdown was not clean — likely watchdog reboot after hang.\n\n${ctx}"
fi
