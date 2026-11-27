#!/usr/bin/env bash
# Claude Code StopFailure hook: a phone push when a session's turn ends on an
# API error (rate limit, overload, server error, auth), so a coordinator
# session that stalls overnight is seen in minutes, not at breakfast
# (2026-11-24: an API error stalled the coordinator for eight hours).
# Decision #69, Drew 2026-11-26.
#
# Claude Code runs it with the event's JSON on stdin: session_id, cwd,
# hook_event_name, error (rate_limit, server_error, authentication_failed,
# billing_error, unknown, ...), error_details and last_assistant_message
# (both optional). StopFailure has no decision control: the exit code and
# output are ignored, so this script always exits 0 and never blocks.
#
# Sends through scripts/lib/notify.sh (HA mobile app push, the watchdogs'
# target). One push per error type per COOLDOWN_SEC (default 600), so a
# retry storm is one alert. KRONK_NOTIFY_DRY_RUN=1 prints the push to stdout
# instead of sending it (the suite uses this).
#
# Installed by a StopFailure entry in ~/.claude/settings.json; the sanitized
# copy and its reason are in docs/claude-code/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY_LOG_PREFIX="stopfailure"
# shellcheck source=../lib/notify.sh
source "$HERE/../lib/notify.sh"

COOLDOWN_SEC="${COOLDOWN_SEC:-600}"
STATE_DIR="${KRONK_ALERT_STATE_DIR:-$HOME/.local/state/kronk-alerts}"

input="$(cat)"
field() { jq -r --arg k "$1" '.[$k] // empty | tostring' <<<"$input" 2>/dev/null; }

error="$(field error)"; error="${error:-unknown}"
details="$(field error_details)"
[[ -z "$details" ]] && details="$(field last_assistant_message)"
session="$(field session_id)"
cwd="$(field cwd)"

# One alert per error type per cooldown. The error type is a word; anything
# else is folded to keep the state file name safe.
key="$(printf '%s' "$error" | tr -c 'A-Za-z0-9_-' '_')"
stamp="$STATE_DIR/stopfailure.$key"
now="$(date +%s)"
if [[ -f "$stamp" ]] && (( now - $(cat "$stamp" 2>/dev/null || echo 0) < COOLDOWN_SEC )); then
    _notify_log "suppressed [$error] (within ${COOLDOWN_SEC}s of the last push)"
    exit 0
fi

title="Claude session stopped: $error"
message="${details:0:200}"
[[ -z "$message" ]] && message="The turn ended on an API error."
message="$message (session ${session:0:8}, $(basename "${cwd:-?}"))"

if [[ "${KRONK_NOTIFY_DRY_RUN:-}" == "1" ]]; then
    printf 'DRY RUN tag=claude-stopfailure\ntitle=%s\nmessage=%s\n' "$title" "$message"
else
    load_ha_token && ha_notify "claude-stopfailure" "$title" "$message"
fi
mkdir -p "$STATE_DIR" && echo "$now" >"$stamp"
exit 0
