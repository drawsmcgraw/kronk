#!/usr/bin/env bash
# SearXNG canary — fired weekly by kronk-searxng-canary.timer (one-shot).
#
# Two checks, one alert:
#   1. SEARCH: two fixed multi-word queries through tool_service /search
#      (the exact path the research agent uses). FAIL when no engine
#      answered, or fewer than MIN_HITS of the top 5 titles/snippets contain
#      one of the query's key terms (first-word junk scores 0). Fewer than
#      MIN_ENGINES answering is logged as a warning and appended to an alert
#      only if something else failed: duckduckgo alone is the normal state
#      from this address (brave/qwant/google are benched most days), and a
#      weekly nag about it would be ignored. This is the check that would
#      have caught 2026-10-05 (every search "succeeded" with first-word junk
#      from the one engine left standing —
#      docs/incidents/INVESTIGATION_2026-10-05_last_week_tonight.md).
#   2. PIN AGE: the compose pin's date (searxng/searxng:YYYY.M.D-sha) versus
#      the newest YYYY.M.D tag on Docker Hub; flag when the pin is older
#      than MAX_PIN_AGE_DAYS. The "bump monthly" comment in the compose file
#      lapsed for four months; this fires instead.
#
# On failure: one HA mobile-app push (lib/notify.sh), at most once per
# COOLDOWN_SEC, naming the engines / the newest tag. The bump itself stays
# a deliberate step: docs/runbooks/searxng-bump.md.
#
# Operations:
#   run now:  scripts/searxng_canary.sh            (exit 0 pass, 1 fail, 2 error)
#   force:    CANARY_FORCE_ALERT=1 scripts/searxng_canary.sh   (tests the push)
#   timer:    systemctl --user list-timers kronk-searxng-canary.timer
#   log:      journalctl --user -u kronk-searxng-canary.service -n 50
set -uo pipefail
REPO_DIR="${KRONK_REPO_DIR:-/home/drew/git-repos/drawsmcgraw/kronk}"
STATE_FILE="${STATE_FILE:-$REPO_DIR/data/searxng_canary.json}"
COOLDOWN_SEC="${COOLDOWN_SEC:-86400}"
MIN_ENGINES="${MIN_ENGINES:-2}"
MIN_HITS="${MIN_HITS:-2}"
MAX_PIN_AGE_DAYS="${MAX_PIN_AGE_DAYS:-35}"
TOOL_CONTAINER="${TOOL_CONTAINER:-kronk-tool_service-1}"
NOTIFY_LOG_PREFIX="searxng-canary"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') searxng-canary: $*"; }
source "$REPO_DIR/scripts/lib/notify.sh"

failures=()

# ── 1. search quality, through tool_service's own /search ────────────────────
# query|term1,term2,... — multi-word on purpose (the first-word-junk failure
# needs it); a result counts as a hit if it contains ANY listed term.
QUERIES=(
  "Last Week Tonight John Oliver latest episode topic|last week tonight,john oliver"
  "GS-9 step 5 annual salary federal pay scale|gs-9,gs 9,federalpay,pay scale,general schedule"
)
warnings=()
for spec in "${QUERIES[@]}"; do
  q="${spec%%|*}"; term="${spec##*|}"
  out=$(docker exec -i "$TOOL_CONTAINER" python - "$q" "$term" <<'PY' 2>&1
import sys, json, httpx
q, terms = sys.argv[1], [t.strip().lower() for t in sys.argv[2].split(",") if t.strip()]
try:
    r = httpx.get("http://localhost:8003/search", params={"q": q, "count": 5}, timeout=45)
    d = r.json()
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"})); sys.exit(0)
res = d.get("results", [])
hits = sum(1 for x in res if any(t in (x.get("title", "") + " " + x.get("snippet", "")).lower() for t in terms))
print(json.dumps({"status": r.status_code, "engines": d.get("engines", []), "unresponsive": d.get("unresponsive_engines", []),
                  "hits": hits, "n": len(res), "detail": d.get("detail")}))
PY
)
  if ! echo "$out" | jq -e . >/dev/null 2>&1; then failures+=("search '$q': canary could not run probe: ${out:0:160}"); continue; fi
  err=$(jq -r '.error // empty' <<<"$out"); [ -n "$err" ] && { failures+=("search '$q': $err"); continue; }
  engines=$(jq -r '.engines | length' <<<"$out"); hits=$(jq -r '.hits' <<<"$out"); names=$(jq -r '.engines | join(",")' <<<"$out"); un=$(jq -r '.unresponsive | join("; ")' <<<"$out"); status=$(jq -r '.status' <<<"$out")
  log "search '$q': status=$status engines=[$names] hits=$hits/5 unresponsive=[$un]"
  if [ "$status" != "200" ]; then failures+=("search '$q': HTTP $status $(jq -r '.detail // ""' <<<"$out")"); continue; fi
  (( engines == 0 )) && failures+=("search '$q': no engine answered; benched: $un")
  (( engines > 0 && engines < MIN_ENGINES )) && warnings+=("search '$q': only [$names] answered; benched: $un")
  (( hits < MIN_HITS )) && failures+=("search '$q': $hits/5 results mention any of '$term' (junk?) from [$names]")
done

# ── 2. pin age vs Docker Hub ─────────────────────────────────────────────────
pin=$(grep -oE 'searxng/searxng:[0-9]{4}\.[0-9]+\.[0-9]+-[0-9a-f]+' "$REPO_DIR/docker-compose.yml" | head -1 | cut -d: -f2)
pin_date=$(echo "$pin" | cut -d- -f1 | tr . -)   # 2026.10.4 -> 2026-10-4
newest=$(curl -sf --max-time 30 "https://hub.docker.com/v2/repositories/searxng/searxng/tags?page_size=25&ordering=last_updated" \
  | jq -r '[.results[].name | select(test("^[0-9]{4}\\.[0-9]+\\.[0-9]+-"))] | .[0] // empty')
if [ -z "$pin" ]; then failures+=("pin: could not read the searxng image pin from docker-compose.yml")
elif [ -z "$newest" ]; then log "pin: $pin (Docker Hub tag list unavailable — age check skipped)"
else
  newest_date=$(echo "$newest" | cut -d- -f1 | tr . -)
  age_days=$(( ( $(date -d "$newest_date" +%s) - $(date -d "$pin_date" +%s) ) / 86400 ))
  log "pin: $pin  newest: $newest  behind by ${age_days}d"
  (( age_days > MAX_PIN_AGE_DAYS )) && failures+=("pin $pin is ${age_days} days behind the newest tag $newest — run docs/runbooks/searxng-bump.md")
fi

# ── verdict + alert (with cooldown) ──────────────────────────────────────────
mkdir -p "$(dirname "$STATE_FILE")"
now=$(date +%s)
if [ "${#failures[@]}" -eq 0 ] && [ -z "${CANARY_FORCE_ALERT:-}" ]; then
  log "PASS${warnings[0]:+ (warnings: ${#warnings[@]})}"; printf '  warning: %s\n' "${warnings[@]}"; jq -n --arg t "$(date -Iseconds)" --arg pin "$pin" '{last_run:$t, result:"pass", pin:$pin}' > "$STATE_FILE"; exit 0
fi
[ -n "${CANARY_FORCE_ALERT:-}" ] && failures+=("forced test alert (CANARY_FORCE_ALERT)")
msg=$(printf '%s\n' "${failures[@]}" "${warnings[@]/#/warning: }")
log "FAIL:"; printf '  - %s\n' "${failures[@]}"
last_alert=$(jq -r '.last_alert // 0' "$STATE_FILE" 2>/dev/null || echo 0)
if (( now - last_alert < COOLDOWN_SEC )) && [ -z "${CANARY_FORCE_ALERT:-}" ]; then
  log "alert cooldown ($(( COOLDOWN_SEC - (now - last_alert) ))s left) — not pushing"
else
  if load_ha_token && ha_notify searxng-canary "Kronk search canary failed" "$msg"; then
    log "alert pushed"; last_alert=$now
  else
    log "alert push FAILED"
  fi
fi
jq -n --arg t "$(date -Iseconds)" --arg pin "$pin" --arg m "$msg" --argjson la "$last_alert" '{last_run:$t, result:"fail", pin:$pin, failures:$m, last_alert:$la}' > "$STATE_FILE"
exit 1
