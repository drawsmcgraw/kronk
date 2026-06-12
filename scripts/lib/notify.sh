#!/usr/bin/env bash
# Shared HA mobile-app notification helpers for the watchdog scripts.
# Source this; do not execute it.
#
#   source "$(dirname "$0")/lib/notify.sh"
#   load_ha_token                      # populates $HA_TOKEN from env or .env
#   ha_notify TAG "Title" "Message"    # POST to notify/mobile_app_*
#
# Extracted 2026-06-12 from three copy-pasted implementations in
# memwatch.sh / boot_notify.sh / perfwatch.sh. Cooldown/coalescing logic
# stays in the callers — it differs per watcher.

REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
HA_URL="${HA_URL:-http://localhost:8123}"
HA_NOTIFY_SERVICE="${HA_NOTIFY_SERVICE:-notify/mobile_app_pixel_7}"

_notify_log() { echo "$(date '+%Y-%m-%d %H:%M:%S') ${NOTIFY_LOG_PREFIX:-notify}: $*"; }

# Populate HA_TOKEN from the environment or the repo .env. Exits non-zero
# (without killing the caller's shell) if no token can be found.
load_ha_token() {
    if [[ -z "${HA_TOKEN:-}" ]] && [[ -f "$REPO_DIR/.env" ]]; then
        while IFS='=' read -r k v; do
            [[ "$k" == "HA_TOKEN" ]] && export HA_TOKEN="$v"
        done < <(grep '^HA_TOKEN=' "$REPO_DIR/.env")
    fi
    if [[ -z "${HA_TOKEN:-}" ]]; then
        _notify_log "ERROR: HA_TOKEN not set (no env var, not in $REPO_DIR/.env)"
        return 1
    fi
}

# ha_notify TAG TITLE MESSAGE — fire one HA mobile-app push.
# TAG keeps Android from collapsing different watchers' notifications.
ha_notify() {
    local tag="$1" title="$2" message="$3"
    local payload
    payload=$(jq -n --arg t "$title" --arg m "$message" --arg tag "$tag" \
        '{"title":$t,"message":$m,"data":{"tag":$tag,"group":"kronk-alerts"}}')
    if curl -sf -X POST "$HA_URL/api/services/$HA_NOTIFY_SERVICE" \
        -H "Authorization: Bearer $HA_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null; then
        _notify_log "notified [$tag]: $title"
        return 0
    fi
    _notify_log "HA notify FAILED [$tag]: $title"
    return 1
}
