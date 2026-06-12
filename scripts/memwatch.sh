#!/usr/bin/env bash
# Kronk memory watchdog.
#
# Polls the orchestrator's /api/system endpoint every $POLL_INTERVAL seconds.
# Pushes alerts via Home Assistant's notify.mobile_app_<device> service when:
#   - free RAM drops below MEM_WARN_GB for WARN_CONSEC consecutive polls (warning)
#   - free RAM drops below MEM_CRIT_GB for CRIT_CONSEC consecutive polls (critical)
#   - GTT (GPU memory) exceeds GTT_ALERT_GB for GTT_CONSEC consecutive polls
#
# Born from the 2026-05-31 hard hang — see docs/incidents/INCIDENT_2026-05-31.md.
# `mem_free` (not mem_available) is the load-bearing signal on this hardware:
# the immediately-available pool that goes tight before the system locks up.
#
# Per-alert-class cooldown prevents notification spam: each class only fires
# once per COOLDOWN_SEC, even if the threshold stays crossed.
#
# Config is all env-overridable so this can be tuned via systemd `Environment=`
# without editing the script.
set -euo pipefail

REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
KRONK_API="${KRONK_API:-http://localhost/api/system}"
HA_URL="${HA_URL:-http://localhost:8123}"
HA_NOTIFY_SERVICE="${HA_NOTIFY_SERVICE:-notify/mobile_app_pixel_7}"  # path under /api/services/
POLL_INTERVAL="${POLL_INTERVAL:-60}"

# Thresholds (GB)
MEM_WARN_GB="${MEM_WARN_GB:-12}"   # was 4 originally; operator picked 12 for early-warning headroom
MEM_CRIT_GB="${MEM_CRIT_GB:-4}"    # crash territory historically was ~2 GB; 4 GB gives margin
GTT_ALERT_GB="${GTT_ALERT_GB:-90}" # GTT cap is ~101 GB; >90 = saturation imminent

# Consecutive-poll requirements (avoid alerting on transient spikes)
WARN_CONSEC="${WARN_CONSEC:-3}"
CRIT_CONSEC="${CRIT_CONSEC:-2}"
GTT_CONSEC="${GTT_CONSEC:-5}"

COOLDOWN_SEC="${COOLDOWN_SEC:-1800}"  # 30 min per alert type

# ── load HA_TOKEN ────────────────────────────────────────────────────────────
if [[ -z "${HA_TOKEN:-}" ]] && [[ -f "$REPO_DIR/.env" ]]; then
    while IFS='=' read -r k v; do
        [[ "$k" == "HA_TOKEN" ]] && export HA_TOKEN="$v"
    done < <(grep '^HA_TOKEN=' "$REPO_DIR/.env")
fi
if [[ -z "${HA_TOKEN:-}" ]]; then
    echo "ERROR: HA_TOKEN not set (no env var, not in $REPO_DIR/.env)" >&2
    exit 1
fi

# ── helpers ──────────────────────────────────────────────────────────────────
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') memwatch: $*"; }

awk_lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }
awk_gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }'; }
to_gb()  { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1e9 }'; }

# Per-alert-class state (in-memory, resets on restart — fine, conservative)
declare -A consec=([warn]=0 [crit]=0 [gtt]=0)
declare -A last_ts=([warn]=0 [crit]=0 [gtt]=0)

notify() {
    local key="$1" title="$2" message="$3"
    local now=$(date +%s)
    local since=$(( now - ${last_ts[$key]:-0} ))
    if (( since < COOLDOWN_SEC )); then
        log "[$key] cooldown $(( COOLDOWN_SEC - since ))s — suppressing"
        return
    fi
    last_ts[$key]=$now
    local payload
    payload=$(jq -n --arg t "$title" --arg m "$message" \
        '{"title":$t,"message":$m,"data":{"tag":"kronk-memwatch","group":"kronk-alerts"}}')
    if curl -sf -X POST "$HA_URL/api/services/$HA_NOTIFY_SERVICE" \
        -H "Authorization: Bearer $HA_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null; then
        log "[$key] notified: $title — $message"
    else
        log "[$key] HA notify FAILED for: $title"
    fi
}

# ── poll loop ────────────────────────────────────────────────────────────────
log "started — polling $KRONK_API every ${POLL_INTERVAL}s"
log "thresholds: mem_warn=${MEM_WARN_GB}GB mem_crit=${MEM_CRIT_GB}GB gtt_alert=${GTT_ALERT_GB}GB"
log "consec: warn=${WARN_CONSEC} crit=${CRIT_CONSEC} gtt=${GTT_CONSEC}  cooldown=${COOLDOWN_SEC}s"

while true; do
    if ! resp=$(curl -sf --max-time 10 "$KRONK_API" 2>/dev/null); then
        log "WARN: $KRONK_API unreachable; sleeping and retrying"
        sleep "$POLL_INTERVAL"
        continue
    fi

    mem_free=$(jq -r '.mem_free // 0' <<< "$resp")
    gtt_used=$(jq -r '.gtt_used // 0' <<< "$resp")
    mem_free_gb=$(to_gb "$mem_free")
    gtt_used_gb=$(to_gb "$gtt_used")

    # ── RAM checks (CRIT dominates WARN — count up to whichever band we're in) ──
    if awk_lt "$mem_free_gb" "$MEM_CRIT_GB"; then
        consec[crit]=$(( consec[crit] + 1 ))
        consec[warn]=$(( consec[warn] + 1 ))
        if (( consec[crit] >= CRIT_CONSEC )); then
            notify crit "Kronk: CRITICAL free RAM" \
                "free=${mem_free_gb} GB (≤ ${MEM_CRIT_GB} GB) for ${consec[crit]} polls. Risk of host hang. Investigate."
        fi
    elif awk_lt "$mem_free_gb" "$MEM_WARN_GB"; then
        consec[warn]=$(( consec[warn] + 1 ))
        consec[crit]=0
        if (( consec[warn] >= WARN_CONSEC )); then
            notify warn "Kronk: low free RAM" \
                "free=${mem_free_gb} GB (≤ ${MEM_WARN_GB} GB) for ${consec[warn]} polls. Memory is tightening."
        fi
    else
        consec[warn]=0
        consec[crit]=0
    fi

    # ── GTT check ──
    if awk_gt "$gtt_used_gb" "$GTT_ALERT_GB"; then
        consec[gtt]=$(( consec[gtt] + 1 ))
        if (( consec[gtt] >= GTT_CONSEC )); then
            notify gtt "Kronk: high GTT usage" \
                "GTT=${gtt_used_gb} GB (≥ ${GTT_ALERT_GB} GB) for ${consec[gtt]} polls. GPU memory pool saturating."
        fi
    else
        consec[gtt]=0
    fi

    sleep "$POLL_INTERVAL"
done
