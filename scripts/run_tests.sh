#!/usr/bin/env bash
# Run the Kronk test suite with the right interpreter.
#
# The suite needs tests/.venv (fastapi, chromadb, fastembed, langfuse, …);
# running bare `pytest` with the system python collects import errors and
# looks like the code is broken. This wrapper kills that footgun.
#
# Usage: ./scripts/run_tests.sh [pytest args]
set -euo pipefail
cd "$(dirname "$0")/.."
exec tests/.venv/bin/python -m pytest tests/ "$@"
