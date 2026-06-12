#!/usr/bin/env bash
# Kronk perf-interrupt watcher.
#
# Tails the kernel journal for "perf: interrupt took too long" warnings and
# pushes a Home Assistant mobile-app notification each time one fires.
#
# Why we care: on Strix Halo (gfx1151) under kernel 6.17, both silent hangs
# (2026-06-08 and 2026-06-09) were preceded by a multi-hour ramp in NMI handler
# latency, visible only as these "interrupt took too long" warnings. The
# warnings are our only leading indicator before the journal goes dark — see
# docs/incidents/INCIDENT_2026-05-31.md and conversation log.
#
# Each warning looks like:
#   kernel: perf: interrupt took too long (3918 > 3913), lowering
#       kernel.perf_event_max_sample_rate to 51000
#
# Same notification path as memwatch / bootnotify: POST to
# notify/mobile_app_pixel_7 with HA_TOKEN from .env. Different `tag` so
# Android doesn't collapse these with the others.
#
# Coalescing: kernel only emits a new warning when latency crosses a higher
# water mark, so spam is naturally bounded. We add a 5-min minimum gap as
# belt-and-suspenders against any future kernel behavior change.
#
# Env overrides (set in the systemd unit):
#   HA_NOTIFY_SERVICE  — defaults to notify/mobile_app_pixel_7
#   MIN_GAP_SEC        — minimum seconds between notifications (default 300)
#   TEST_MODE=1        — send one synthetic alert and exit (for verification)
set -euo pipefail

REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
HA_URL="${HA_URL:-http://localhost:8123}"
HA_NOTIFY_SERVICE="${HA_NOTIFY_SERVICE:-notify/mobile_app_pixel_7}"
MIN_GAP_SEC="${MIN_GAP_SEC:-300}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') perfwatch: $*"; }

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

send_alert() {
    local title="$1" message="$2"
    local payload
    payload=$(jq -n --arg t "$title" --arg m "$message" \
        '{"title":$t,"message":$m,"data":{"tag":"kronk-perfwatch","group":"kronk-alerts"}}')
    if curl -sf -X POST "$HA_URL/api/services/$HA_NOTIFY_SERVICE" \
        -H "Authorization: Bearer $HA_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null; then
        log "notified: $title — $message"
    else
        log "HA notify FAILED for: $title"
    fi
}

# ── test mode: fire one alert and exit ──────────────────────────────────────
if [[ "${TEST_MODE:-0}" == "1" ]]; then
    uptime_str=$(uptime -p 2>/dev/null || echo unknown)
    send_alert "Kronk: perfwatch TEST" \
        "This is a test alert from perfwatch.sh — wired up and reachable.\n\nUptime: ${uptime_str}"
    exit 0
fi

# ── main loop ───────────────────────────────────────────────────────────────
log "watching kernel journal for 'perf: interrupt took too long' (min gap ${MIN_GAP_SEC}s)"

last_ts=0
# -k = kernel only, -f = follow, -o cat strips metadata, --since=now skips backlog
journalctl -kf -o cat --since=now 2>/dev/null \
| grep --line-buffered "perf: interrupt took too long" \
| while IFS= read -r line; do
    now=$(date +%s)
    since=$(( now - last_ts ))
    if (( since < MIN_GAP_SEC )); then
        log "coalesced (gap ${since}s < ${MIN_GAP_SEC}s): $line"
        continue
    fi
    last_ts=$now

    # Pull "took too long (NEW > OLD)" and the new sample-rate cap out of the line.
    # Example: perf: interrupt took too long (3918 > 3913), lowering kernel.perf_event_max_sample_rate to 51000
    ns_value=$(grep -oE 'took too long \([0-9]+' <<< "$line" | grep -oE '[0-9]+' || echo "?")
    new_rate=$(grep -oE 'sample_rate to [0-9]+' <<< "$line" | grep -oE '[0-9]+' || echo "?")
    uptime_str=$(uptime -p 2>/dev/null || echo unknown)

    send_alert "Kronk: perf interrupt latency rising" \
        "NMI handler took ${ns_value} ns; kernel throttled perf_event_max_sample_rate to ${new_rate}.\n\nThis preceded both silent hangs (6/8, 6/9). Uptime: ${uptime_str}."
done
