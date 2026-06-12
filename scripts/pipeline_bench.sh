#!/usr/bin/env bash
# Kronk pipeline benchmark battery.
#
# Runs a fixed 8-prompt battery covering every route (direct, home+tool,
# research) and transport (/message web UI path, /v1/chat/completions and
# /api/chat shims), REPS times each, recording client wall time plus the
# server's own `timing` payload where available. Raw results land in
# docs/bench/bench_<date>_<label>.json so before/after runs are diffable.
#
# Born for the 2026-06 response-time program (see
# docs/REPORT_2026-06_response_time_program.md). Run it BEFORE a change
# (label e.g. baseline-pre) and AFTER (final-post); the report compares the
# two files. Battery and method must stay identical between runs — if you
# change the battery, prior files stop being comparable.
#
# Usage:
#   ./scripts/pipeline_bench.sh <label> [reps]
# Notes:
#   - Clears chat history first (DELETE /history) and between reps, so every
#     rep pays the same prompt sizes. Don't run while you care about current
#     chat history.
#   - One warmup request (discarded) before the battery.
#   - Research prompts hit the live web; their absolute numbers are noisy —
#     the within-run spread matters more than the median for those.
set -euo pipefail

LABEL="${1:?usage: pipeline_bench.sh <label> [reps]}"
REPS="${2:-3}"
BASE="${KRONK_BASE:-http://localhost}"
OUTDIR="${BENCH_OUTDIR:-docs/bench}"
STAMP=$(date +%Y-%m-%d_%H%M%S)
OUTFILE="$OUTDIR/bench_${STAMP}_${LABEL}.json"

mkdir -p "$OUTDIR"

log() { echo "$(date '+%H:%M:%S') bench: $*" >&2; }

# ── battery: id | transport | prompt ────────────────────────────────────────
# transport: message = POST /message (web UI path, SSE)
#            openai  = POST /v1/chat/completions (shim, non-stream)
#            ollama  = POST /api/chat (shim, non-stream)
BATTERY=(
  "direct_short|message|Why is the sky blue? One sentence."
  "direct_long|message|Explain how a heat pump works in one paragraph."
  "weather_now|message|What is the weather right now?"
  "weather_umbrella|message|Do I need an umbrella tomorrow?"
  "shopping_list|message|What is on my shopping list?"
  "research_bios|message|Search for the latest Framework Desktop BIOS version"
  "shim_timezone|openai|What time zone is Denver in?"
  "shim_weather|ollama|What is the weather this weekend?"
)

run_message() {  # $1 prompt → emits "wall_s|timing_json|tokens"
    local prompt="$1" t0 t1 wall out timing tokens
    t0=$(date +%s.%N)
    out=$(curl -sN --max-time 300 -X POST "$BASE/message" \
        -H "Content-Type: application/json" \
        -d "$(jq -nc --arg t "$prompt" '{text:$t}')")
    t1=$(date +%s.%N)
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')
    timing=$(grep -oE '\{"timing": .*\}' <<<"$out" | tail -1 | sed 's/^{"timing": //; s/}$//' || true)
    [[ -z "$timing" ]] && timing=null
    tokens=$(grep -c '"token"' <<<"$out" || true)
    echo "$wall|$timing|$tokens"
}

run_openai() {
    local prompt="$1" t0 t1 wall out tokens
    t0=$(date +%s.%N)
    out=$(curl -s --max-time 300 -X POST "$BASE/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(jq -nc --arg t "$prompt" '{messages:[{role:"user",content:$t}],stream:false}')")
    t1=$(date +%s.%N)
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')
    tokens=$(jq -r '.usage.completion_tokens // 0' <<<"$out" 2>/dev/null || echo 0)
    echo "$wall|null|$tokens"
}

run_ollama() {
    local prompt="$1" t0 t1 wall out tokens
    t0=$(date +%s.%N)
    out=$(curl -s --max-time 300 -X POST "$BASE/api/chat" \
        -H "Content-Type: application/json" \
        -d "$(jq -nc --arg t "$prompt" '{messages:[{role:"user",content:$t}],stream:false}')")
    t1=$(date +%s.%N)
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')
    tokens=$(jq -r '.eval_count // 0' <<<"$out" 2>/dev/null || echo 0)
    echo "$wall|null|$tokens"
}

# ── run ─────────────────────────────────────────────────────────────────────
log "label=$LABEL reps=$REPS → $OUTFILE"
started_at=$(date -Is)

log "clearing history + warmup"
curl -s -X DELETE "$BASE/history" >/dev/null
run_message "Say OK." >/dev/null   # warmup, discarded
curl -s -X DELETE "$BASE/history" >/dev/null

results="[]"
for rep in $(seq 1 "$REPS"); do
  for entry in "${BATTERY[@]}"; do
    IFS='|' read -r id transport prompt <<<"$entry"
    log "rep $rep/$REPS  $id ($transport)"
    case "$transport" in
      message) r=$(run_message "$prompt") ;;
      openai)  r=$(run_openai  "$prompt") ;;
      ollama)  r=$(run_ollama  "$prompt") ;;
    esac
    IFS='|' read -r wall timing tokens <<<"$r"
    results=$(jq -c \
      --arg id "$id" --arg tr "$transport" --argjson rep "$rep" \
      --argjson wall "$wall" --argjson timing "${timing:-null}" \
      --argjson tokens "${tokens:-0}" \
      '. += [{id:$id, transport:$tr, rep:$rep, wall_s:$wall, timing:$timing, stream_tokens:$tokens}]' \
      <<<"$results")
    # History isolation between battery items so prompt sizes stay constant.
    curl -s -X DELETE "$BASE/history" >/dev/null
  done
done

finished_at=$(date -Is)

jq -n \
  --arg label "$LABEL" --arg started "$started_at" --arg finished "$finished_at" \
  --argjson reps "$REPS" --argjson results "$results" \
  '{label:$label, started_at:$started, finished_at:$finished, reps:$reps, results:$results}' \
  > "$OUTFILE"

log "wrote $OUTFILE"

# ── summary table: wall, TTFT, tokens/s ─────────────────────────────────────
# TTFT/gen come from the server timing payload (message transport only).
# tokens/s = stream_tokens ÷ generation_s when timing exists; for shim
# transports (no timing payload) it falls back to tokens ÷ wall, marked '~'.
echo
echo "=== summary — $LABEL ==="
jq -r '
  .results | group_by(.id)[] |
  (map(.wall_s) | sort) as $w |
  ([.[] | .timing.ttft_s // empty]) as $ttfts |
  ([.[] | select(.timing.generation_s != null and .timing.generation_s > 0 and .stream_tokens > 0)
        | (.stream_tokens / .timing.generation_s)]) as $tps_timed |
  ([.[] | select(.timing == null and .stream_tokens > 0 and .wall_s > 0)
        | (.stream_tokens / .wall_s)]) as $tps_wall |
  [ .[0].id,
    ($w[($w|length/2|floor)]), ($w|min), ($w|max),
    (if ($ttfts|length) > 0 then ($ttfts|add/length) else null end),
    (if ($tps_timed|length) > 0 then ($tps_timed|add/length)
     elif ($tps_wall|length) > 0 then ($tps_wall|add/length) else null end),
    (if ($tps_timed|length) > 0 then "" elif ($tps_wall|length) > 0 then "~" else "" end)
  ] | @tsv' "$OUTFILE" \
  | awk -F'\t' 'BEGIN{printf "%-20s %8s %8s %8s %10s %10s\n","prompt","median","min","max","avg_ttft","tok/s"}
         {tt=($5==""?"   -":sprintf("%8.2f",$5));
          tp=($6==""?"     -":sprintf("%s%.1f",$7,$6));
          printf "%-20s %8.2f %8.2f %8.2f %10s %10s\n",$1,$2,$3,$4,tt,tp}'
echo "(~ = wall-derived tokens/s: shim transports carry no server timing payload)"
