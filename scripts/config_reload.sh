#!/usr/bin/env bash
# config_reload.sh — reload config/ in the orchestrator and tool_service
# (sprint M21 step 1; docs/features/config-directory.md).
#
# Usage:
#   scripts/config_reload.sh dev     orchestrator-dev + tool_service-dev
#   scripts/config_reload.sh prod    orchestrator + tool_service
#
# POST /config/reload is host and compose network only: nginx refuses
# /config/ and the orchestrator refuses any request nginx proxied. The
# orchestrator publishes no host port, so its reload goes through
# `docker exec` to its own localhost; tool_service is reached on its
# loopback port. Each answers with what changed (200) or with the errors,
# file and line, and the last good values still in effect (422).
# Exit status: 0 when both reloads succeeded, 1 otherwise.
set -uo pipefail

case "${1:-}" in
    dev)  orch=kronk-orchestrator-dev-1; orch_port=8100; ts_port=8103 ;;
    prod) orch=kronk-orchestrator-1;     orch_port=8000; ts_port=8003 ;;
    *)    echo "usage: $0 dev|prod" >&2; exit 2 ;;
esac

ok=0
echo "== orchestrator ($orch)"
docker exec "$orch" python -c "
import sys, urllib.request, urllib.error
req = urllib.request.Request('http://localhost:$orch_port/config/reload', method='POST')
try:
    print(urllib.request.urlopen(req, timeout=10).read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()); sys.exit(1)
" || ok=1
echo
echo "== tool_service (127.0.0.1:$ts_port)"
curl -sS --max-time 10 -X POST -w '\n%{http_code}\n' "http://127.0.0.1:$ts_port/config/reload" \
    | { body=$(cat); echo "$body" | head -n -1; code=$(echo "$body" | tail -n 1); [ "$code" = 200 ]; } || ok=1
exit $ok
