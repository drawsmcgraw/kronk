#!/usr/bin/env bash
# Kronk memory watchdog.
#
# Polls the orchestrator's /api/system endpoint every $POLL_INTERVAL seconds.
# Pushes alerts via Home Assistant's notify.mobile_app_<device> service when:
#   - free RAM drops below MEM_WARN_GB for WARN_CONSEC consecutive polls (warning)
#   - free RAM drops below MEM_CRIT_GB for CRIT_CONSEC consecutive polls (critical)
#   - GTT (GPU memory) exceeds GTT_ALERT_GB for GTT_CONSEC consecutive polls
#   - GPU busy% pinned ≥ GPU_BUSY_ALERT while the model servers accumulate
#     ~zero GPU engine time, for GPU_CONSEC consecutive polls — the ROCm
#     idle-spin wedge that precedes the silent hangs. Real Vulkan inference
#     accumulates per-process drm-engine-* time (~95% of wall, mostly on
#     drm-engine-compute); the wedge reads 100% busy with none.
#     See docs/incidents/INCIDENT_2026-08-12.md.
#
# Born from the 2026-05-31 hard hang — see docs/incidents/INCIDENT_2026-05-31.md.
# GPU idle-spin canary added after the 2026-08-12 hang (spin ran 25 days
# unobserved; this check exists so that never happens again).
# `mem_free` (not mem_available) is the load-bearing signal on this hardware:
# the immediately-available pool that goes tight before the system locks up.
#
# Testing: MEMWATCH_DRY_RUN=1 logs alerts instead of pushing to HA. Force the
# GPU canary to fire (harmless, no push) with:
#   MEMWATCH_DRY_RUN=1 POLL_INTERVAL=2 GPU_BUSY_ALERT=0 GPU_ENGINE_MIN_PCT=100000 \
#     GPU_CONSEC=2 timeout 10 scripts/memwatch.sh
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

# GPU idle-spin canary (values in %; see header)
GPU_BUSY_ALERT="${GPU_BUSY_ALERT:-95}"  # wedge reads 100; genuine idle is 0–2
# Below this share of the poll interval attributed to the model fleet's
# drm-engine-* counters, the fleet counts as idle (1% of 60 s = 0.6 s of
# engine time; real inference measures ~95% of wall, the wedge is ~0).
GPU_ENGINE_MIN_PCT="${GPU_ENGINE_MIN_PCT:-1}"

# Consecutive-poll requirements (avoid alerting on transient spikes)
WARN_CONSEC="${WARN_CONSEC:-3}"
CRIT_CONSEC="${CRIT_CONSEC:-2}"
GTT_CONSEC="${GTT_CONSEC:-5}"
GPU_CONSEC="${GPU_CONSEC:-5}"      # 5 polls @ 60 s = pinned for 5 min before alerting

COOLDOWN_SEC="${COOLDOWN_SEC:-1800}"  # 30 min per alert type

# ── shared notify lib (token loading + ha_notify) ───────────────────────────
NOTIFY_LOG_PREFIX=memwatch
source "$(dirname "$0")/lib/notify.sh"
load_ha_token || exit 1

# ── helpers ──────────────────────────────────────────────────────────────────
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') memwatch: $*"; }

awk_lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }
awk_gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }'; }
to_gb()  { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1e9 }'; }

# Highest gpu_busy_percent across cards (one iGPU on this box; glob for safety).
gpu_busy() {
    cat /sys/class/drm/card*/device/gpu_busy_percent 2>/dev/null | sort -rn | head -1
}

# Cumulative drm-engine-* nanoseconds (all engines — Vulkan inference lands on
# drm-engine-compute, not gfx) across the model fleet (llama-server, wyoming-*),
# deduped per DRM client+engine (a process holds several fds to the same
# client). This is the wedge discriminator: real Vulkan inference accumulates
# engine time here; the ROCm idle-spin reads 100% busy with ~none.
# Caveat: ROCm/KFD work is invisible to fdinfo, so a reintroduced ROCm build
# trips this alert during real use too — deliberate, since "a ROCm build is
# back" is exactly what needs flagging either way.
fleet_engine_ns() {
    local pid total=0 part
    for pid in $(pgrep -f 'llama-server|wyoming' || true); do
        part=$(awk '
            /^drm-client-id:/ { cid = $2 }
            /^drm-engine-/ { if (!((cid, $1) in seen)) { seen[cid, $1] = 1; sum += $2 } }
            END { printf "%.0f", sum + 0 }
        ' "/proc/$pid/fdinfo/"* 2>/dev/null) || part=0
        total=$(( total + ${part:-0} ))
    done
    echo "$total"
}

# Per-alert-class state (in-memory, resets on restart — fine, conservative)
declare -A consec=([warn]=0 [crit]=0 [gtt]=0 [gpu]=0)
declare -A last_ts=([warn]=0 [crit]=0 [gtt]=0 [gpu]=0)
prev_eng=""   # previous poll's fleet_engine_ns; first poll only seeds it

notify() {
    local key="$1" title="$2" message="$3"
    local now=$(date +%s)
    local since=$(( now - ${last_ts[$key]:-0} ))
    if (( since < COOLDOWN_SEC )); then
        log "[$key] cooldown $(( COOLDOWN_SEC - since ))s — suppressing"
        return
    fi
    last_ts[$key]=$now
    if [[ "${MEMWATCH_DRY_RUN:-0}" == "1" ]]; then
        log "DRY-RUN [$key] would notify: $title — $message"
        return
    fi
    ha_notify kronk-memwatch "$title" "$message"
}

# ── poll loop ────────────────────────────────────────────────────────────────
log "started — polling $KRONK_API every ${POLL_INTERVAL}s"
log "thresholds: mem_warn=${MEM_WARN_GB}GB mem_crit=${MEM_CRIT_GB}GB gtt_alert=${GTT_ALERT_GB}GB"
log "gpu canary: busy≥${GPU_BUSY_ALERT}% with fleet-engine<${GPU_ENGINE_MIN_PCT}% of interval"
log "consec: warn=${WARN_CONSEC} crit=${CRIT_CONSEC} gtt=${GTT_CONSEC} gpu=${GPU_CONSEC}  cooldown=${COOLDOWN_SEC}s"
[[ "${MEMWATCH_DRY_RUN:-0}" == "1" ]] && log "DRY-RUN mode — alerts will be logged, not pushed"

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

    # ── GPU idle-spin check (ROCm wedge canary — INCIDENT_2026-08-12.md) ──
    busy=$(gpu_busy || true); busy=${busy:-0}
    cur_eng=$(fleet_engine_ns || true); cur_eng=${cur_eng:-0}
    eng_min_ns=$(( POLL_INTERVAL * GPU_ENGINE_MIN_PCT * 10000000 ))  # PCT% of interval, in ns
    if [[ -n "$prev_eng" ]]; then
        delta_eng=$(( cur_eng - prev_eng ))
        if (( delta_eng < 0 )); then
            # counters went backwards = a model server restarted; not a steady wedge
            consec[gpu]=0
        elif (( busy >= GPU_BUSY_ALERT )) && (( delta_eng < eng_min_ns )); then
            consec[gpu]=$(( consec[gpu] + 1 ))
            if (( consec[gpu] >= GPU_CONSEC )); then
                notify gpu "Kronk: GPU idle-spin (ROCm wedge?)" \
                    "GPU pinned at ${busy}% for ${consec[gpu]} polls with ~zero model-server engine time — the idle-spin that precedes silent hangs (docs/incidents/INCIDENT_2026-08-12.md). Check for a llama unit on a ROCm build: systemctl --user list-units 'llama-*'"
            fi
        else
            consec[gpu]=0
        fi
    fi
    prev_eng=$cur_eng

    sleep "$POLL_INTERVAL"
done
