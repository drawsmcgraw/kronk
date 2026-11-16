"""Shared fixtures for Kronk tests.

Import policy (2026-06-12): services are imported as PACKAGES
(`tool_service.main`, `voice_tracer.main`, …) so the flat container layout
keeps working. Only `orchestrator/` goes on sys.path directly: its modules
import each other by bare name (`import agents`) and have no cross-service
collisions. (A per-service sys.path-juggling fixture once poisoned
sys.modules across test files; don't bring one back.)
"""
import os
import sys
import tempfile

# Household taste store (tool_service/taste.py) — never the container's
# /data paths from a test run. Read at import, so set before any import.
_TASTE_TMP = tempfile.mkdtemp(prefix="kronk-taste-")
os.environ.setdefault("MUSIC_TASTE_DB", os.path.join(_TASTE_TMP, "music_taste.db"))
os.environ.setdefault("MUSIC_TASTE_FILE", os.path.join(_TASTE_TMP, "music_taste.yaml"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ORCH = os.path.join(REPO_ROOT, "orchestrator")
if _ORCH not in sys.path:
    sys.path.append(_ORCH)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
