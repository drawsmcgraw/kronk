#!/usr/bin/env bash
# Music provider canary — fired daily by kronk-music-canary.timer (one-shot).
#
# MA checks the YouTube Music cookie only when the provider loads (the
# Premium/format-141 probe in providers/ytmusic handle_async_init), and a dead
# cookie still streams signed-out — so a cookie can be dead for days with
# nothing noticing until a restart (INVESTIGATION_2026-10-06_youtube_cookie.md).
# This reloads the YouTube Music provider once a day — which re-runs MA's own
# check — and reports whether ytmusic / pandora / filesystem_local are
# available. Skipped (logged) if any speaker is playing YouTube at the time.
# One HA mobile-app push per failure, 24 h cooldown; state, including the
# first day a provider was seen dead, in data/music_canary.json.
#
# Operations:
#   run now:  scripts/music_provider_canary.sh      (exit 0 pass, 1 fail, 3 skipped)
#   timer:    systemctl --user list-timers kronk-music-canary.timer
#   log:      journalctl --user -u kronk-music-canary.service -n 50
set -uo pipefail
REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
STATE_FILE="${STATE_FILE:-$REPO_DIR/data/music_canary.json}"
COOLDOWN_SEC="${COOLDOWN_SEC:-86400}"
YT_INSTANCE="${YT_INSTANCE:-ytmusic--kGmHoQDn}"
NOTIFY_LOG_PREFIX="music-canary"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') music-canary: $*"; }
source "$REPO_DIR/scripts/lib/notify.sh"
load_ha_token || exit 2

playing_yt=$(curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states" \
  | jq -r '[.[] | select(.entity_id|startswith("media_player.")) | select(.state=="playing") | select((.attributes.media_content_id // "")|startswith("ytmusic"))] | length')
if [ "${playing_yt:-0}" != "0" ]; then log "a speaker is playing YouTube Music — reload skipped"; exit 3; fi

out=$(docker exec -i kronk-tool_service-1 python - "$YT_INSTANCE" <<'PY' 2>&1
import asyncio, json, sys, main
inst = sys.argv[1]
async def go():
    try:
        await main.ma_command("config/providers/reload", instance_id=inst)
    except main.MAError as e:
        print(json.dumps({"reload_error": str(e)}), file=sys.stderr)
    await asyncio.sleep(20)
    r = await main.ma_command("providers")
    print(json.dumps({p["domain"]: bool(p.get("available")) for p in r if p.get("domain") in ("ytmusic", "pandora", "filesystem_local")}))
asyncio.run(go())
PY
)
avail=$(echo "$out" | grep -E '^\{' | tail -1)
[ -z "$avail" ] && { log "probe failed: ${out:0:200}"; avail='{}'; }
log "available: $avail"
failures=()
for d in ytmusic pandora filesystem_local; do
  [ "$(jq -r --arg d "$d" '.[$d] // false' <<<"$avail")" = "true" ] || failures+=("$d")
done
mkdir -p "$(dirname "$STATE_FILE")"; now=$(date +%s); today=$(date +%F)
prev=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')
if [ "${#failures[@]}" -eq 0 ]; then
  log PASS; jq --arg t "$(date -Iseconds)" '{last_run:$t, result:"pass", last_alert:(.last_alert // 0)}' <<<"$prev" > "$STATE_FILE"; exit 0
fi
first_dead=$(jq -r '.first_dead // empty' <<<"$prev"); [ -z "$first_dead" ] && first_dead=$today
msg="Not available: ${failures[*]} (first seen $first_dead). YouTube: paste a fresh cookie (docs/features/voice-music-control.md)."
log "FAIL: $msg"
last_alert=$(jq -r '.last_alert // 0' <<<"$prev")
if (( now - last_alert >= COOLDOWN_SEC )) && ha_notify music-canary "Kronk music provider down" "$msg"; then last_alert=$now; fi
jq -n --arg t "$(date -Iseconds)" --arg f "${failures[*]}" --arg fd "$first_dead" --argjson la "$last_alert" '{last_run:$t, result:"fail", failing:$f, first_dead:$fd, last_alert:$la}' > "$STATE_FILE"
exit 1
