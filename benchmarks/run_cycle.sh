#!/usr/bin/env bash
# Pre-warm llama.cpp servers, rebuild orchestrator with the requested
# coordinator flavor, then run the benchmark suite.
#
# Usage:
#   benchmarks/run_cycle.sh <label> <KRONK_COORDINATOR> [COORDINATOR_MODEL]
#
# Example:
#   benchmarks/run_cycle.sh v2-coord-gemma v2 gemma-4-e4b
#   benchmarks/run_cycle.sh v2-coord-nemo  v2 mistral-nemo
#   benchmarks/run_cycle.sh v1-baseline    v1
set -euo pipefail

LABEL="${1:?label required, e.g. v1-baseline}"
COORDINATOR_FLAVOR="${2:?second arg: v1 or v2}"
COORDINATOR_MODEL="${3:-gemma-4-e4b}"
# Pass these via env if you want to override; otherwise defaults are kept.
KRONK_FAST_PATH_ENABLED="${KRONK_FAST_PATH_ENABLED:-true}"
KRONK_FAST_PATH_THRESHOLD="${KRONK_FAST_PATH_THRESHOLD:-0.65}"
export KRONK_FAST_PATH_ENABLED KRONK_FAST_PATH_THRESHOLD COORDINATOR_MODEL

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "═══ bench cycle: label=$LABEL flavor=$COORDINATOR_FLAVOR model=$COORDINATOR_MODEL fast_path=$KRONK_FAST_PATH_ENABLED ═══"

echo "→ restart llama.cpp servers (clean KV/sampler state)"
for svc in llama-gemma3-4b llama-gemma4-e4b llama-mistral-nemo; do
    systemctl --user restart "$svc"
done

echo "→ wait for ports"
for port in 11438 11439 11435; do
    until ss -ltn 2>/dev/null | grep -q ":$port "; do sleep 0.5; done
done
sleep 5  # allow models to actually load weights

echo "→ smoke-test gemma-4-e4b"
out=$(curl -s -X POST http://localhost:11438/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gemma-4-e4b","messages":[{"role":"user","content":"reply with: ok"}],"max_tokens":5,"stream":false}' \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])")
echo "  gemma → $out"

if [ "$COORDINATOR_FLAVOR" = "v2" ]; then
    echo "→ orchestrator: KRONK_COORDINATOR=v2 COORDINATOR_MODEL=$COORDINATOR_MODEL"
    # Recreate with overrides — relies on docker-compose.yml passing these env vars through.
    KRONK_COORDINATOR="$COORDINATOR_FLAVOR" COORDINATOR_MODEL="$COORDINATOR_MODEL" \
        docker compose up -d --build orchestrator 2>&1 | tail -5
    docker compose restart nginx 2>&1 | tail -2
else
    echo "→ orchestrator: KRONK_COORDINATOR=v1 (default)"
    KRONK_COORDINATOR=v1 COORDINATOR_MODEL="$COORDINATOR_MODEL" \
        docker compose up -d --build orchestrator 2>&1 | tail -5
    docker compose restart nginx 2>&1 | tail -2
fi

echo "→ wait for orchestrator healthy"
until curl -sf http://localhost/api/agents >/dev/null 2>&1; do sleep 1; done
echo "  ready"

echo "→ run bench"
python3 benchmarks/agent_bench.py --label "$LABEL" --trials 3

echo "═══ cycle done ═══"
