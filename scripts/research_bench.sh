#!/usr/bin/env bash
# Deep-research benchmark: multi-hop questions through the full pipeline.
#
# Measures what pipeline_bench.sh can't: research QUALITY on questions that
# need chained lookups (rank → fetch → enumerate → per-item lookup), plus
# the pathology counters for the 2026-06-12 budget-cliff incident (forced
# synthesis, leaked tool-call syntax).
#
# Usage: ./scripts/research_bench.sh <label> [reps]
# Output: docs/bench/research_<date>_<label>.json + a console table.
# Compare runs with: jq side-by-side, or just read the answers — quality
# scoring is a human judgment call by design.
set -euo pipefail

LABEL="${1:?usage: research_bench.sh <label> [reps]}"
REPS="${2:-2}"
BASE="${KRONK_BASE:-http://localhost}"
OUTDIR="${BENCH_OUTDIR:-docs/bench}"
STAMP=$(date +%Y-%m-%d_%H%M%S)
OUTFILE="$OUTDIR/research_${STAMP}_${LABEL}.json"
mkdir -p "$OUTDIR"

log() { echo "$(date '+%H:%M:%S') research_bench: $*" >&2; }

# Multi-hop battery. Answers verifiable; Q4 is a single-hop control.
QUESTIONS=(
  "gdp_heads|Give me the names of the heads of state of the top five countries ranked by GDP"
  "sa_capitals|What are the capitals of the three most populous countries in South America?"
  "ceo_marketcap|Who are the CEOs of the three largest US companies by market cap?"
  "py_version|What is the latest stable Python version?"
)

results="[]"
for rep in $(seq 1 "$REPS"); do
  for entry in "${QUESTIONS[@]}"; do
    IFS='|' read -r id q <<<"$entry"
    log "rep $rep/$REPS  $id"
    curl -s -X DELETE "$BASE/history" >/dev/null
    t0=$(date +%s.%N)
    raw=$(curl -sN --max-time 600 -X POST "$BASE/message" \
      -H "Content-Type: application/json" \
      -d "$(jq -nc --arg t "$q" '{text:$t}')")
    t1=$(date +%s.%N)
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
    answer=$(grep -oE '"token": "[^"]*"' <<<"$raw" | sed 's/"token": "//;s/"$//' | tr -d '\n' \
      | python3 -c "import sys,codecs; print(codecs.decode(sys.stdin.read(),'unicode_escape'))" 2>/dev/null || echo "(decode error)")
    leak=$(grep -c "tool_call" <<<"$answer" || true)
    results=$(jq -c \
      --arg id "$id" --argjson rep "$rep" --argjson wall "$wall" \
      --arg answer "$answer" --argjson leak "$leak" \
      '. += [{id:$id, rep:$rep, wall_s:$wall, leaked_tool_syntax:($leak>0), answer:$answer}]' \
      <<<"$results")
  done
done
curl -s -X DELETE "$BASE/history" >/dev/null

jq -n --arg label "$LABEL" --arg ts "$(date -Is)" --argjson reps "$REPS" --argjson results "$results" \
  '{label:$label, at:$ts, reps:$reps, results:$results}' > "$OUTFILE"
log "wrote $OUTFILE"

echo
echo "=== research bench — $LABEL ==="
jq -r '.results[] | [.id, .rep, .wall_s, (if .leaked_tool_syntax then "LEAK" else "ok" end), (.answer[0:90] | gsub("\n";" "))] | @tsv' "$OUTFILE" \
  | awk -F'\t' '{printf "%-14s r%-2s %6.1fs %-5s %s\n",$1,$2,$3,$4,$5}'
