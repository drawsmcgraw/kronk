"""Shared fixtures for Kronk tests.

Import policy (2026-06-12): services are imported as PACKAGES
(`tool_service.main`, `voice_tracer.main`, …) so the flat container layout
keeps working. Only `orchestrator/` goes on sys.path directly: its modules
import each other by bare name (`import agents`) and have no cross-service
collisions. (A per-service sys.path-juggling fixture once poisoned
sys.modules across test files; don't bring one back.)
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# Household taste store (tool_service/taste.py) — never the container's
# /data paths from a test run. Read at import, so set before any import.
# Removed when the run ends (M21 fix round 2: every run left one behind).
_TASTE_TMP = tempfile.mkdtemp(prefix="kronk-taste-")
atexit.register(shutil.rmtree, _TASTE_TMP, ignore_errors=True)
os.environ.setdefault("MUSIC_TASTE_DB", os.path.join(_TASTE_TMP, "music_taste.db"))
os.environ.setdefault("MUSIC_TASTE_FILE", os.path.join(_TASTE_TMP, "music_taste.yaml"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Work state (work item M33): scripts/work.py and scripts/agent.py write
# ~/.local/state/kronk-work/work.db unless KRONK_WORK_DB says otherwise. A
# test run always points it at a path in a temporary directory that does
# not exist (nothing makes it there unless a test does), even when the
# shell exports KRONK_WORK_DB; and the run fails if the live file, its
# -wal, -shm or dump changed while it ran (criterion 7).
_WORK_TMP = tempfile.mkdtemp(prefix="kronk-work-")
atexit.register(shutil.rmtree, _WORK_TMP, ignore_errors=True)
os.environ["KRONK_WORK_DB"] = os.path.join(_WORK_TMP, "work.db")
LIVE_WORK_DB = os.path.join(os.path.expanduser("~"), ".local", "state", "kronk-work", "work.db")


def _live_work_stat():
    out = {}
    for suffix in ("", "-wal", "-shm", ".dump.sql"):
        try:
            st = os.stat(LIVE_WORK_DB + suffix)
            out[suffix] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            out[suffix] = None
    return out


_LIVE_WORK_BEFORE = _live_work_stat()

# The voice-run record (work item M30 step B): scripts/voice_runs_export.py and
# scripts/backups.py read ~/.local/state/kronk-voice/voice_runs.db unless
# KRONK_VOICE_DB says otherwise. A test run points it at a scratch path that
# does not exist, and fails if the live record, its journal, or a published
# copy or status file under data/ changed while it ran (M30 criterion B6).
_VOICE_TMP = tempfile.mkdtemp(prefix="kronk-voice-")
atexit.register(shutil.rmtree, _VOICE_TMP, ignore_errors=True)
os.environ["KRONK_VOICE_DB"] = os.path.join(_VOICE_TMP, "voice_runs.db")
LIVE_VOICE_FILES = [os.path.join(os.path.expanduser("~"), ".local", "state", "kronk-voice", "voice_runs.db" + s)
                    for s in ("", "-journal", "-wal", "-shm")] + [
    os.path.join(REPO_ROOT, "data", d, f) for d in ("", "dev") for f in ("voice_runs.db", "voice_runs.json")]


def _live_voice_stat():
    out = {}
    for p in LIVE_VOICE_FILES:
        try:
            st = os.stat(p)
            out[p] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            out[p] = None
    return out


_LIVE_VOICE_BEFORE = _live_voice_stat()
_SESSION_START_S = time.time()
VOICE_UNIT = "kronk-voice-runs-export.service"


def _exporter_runs(since_s: float) -> list[tuple[float, float]]:
    """The live timer's runs since since_s, as (start, end) epoch seconds,
    from its unit's user journal (the lines "Starting ..." and "Finished
    ..." or "Failed ..."; the unit logs no transcript text). Since step B
    went live the timer rewrites the status files and the record every
    60 s, so a long test run always sees them change; a change made inside
    one of these runs is the timer's. Empty when the journal cannot be
    read: then every change is reported."""
    try:
        out = subprocess.run(["journalctl", "--user", "-u", VOICE_UNIT, "--since", f"@{int(since_s) - 120}",
                              "-o", "json", "--output-fields=MESSAGE", "--no-pager"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    runs, start = [], None
    for line in out.splitlines():
        try:
            e = json.loads(line)
            t, msg = int(e["__REALTIME_TIMESTAMP"]) / 1e6, str(e.get("MESSAGE") or "")
        except (ValueError, KeyError, TypeError):
            continue
        if msg.startswith("Starting "):
            start = t
        elif start is not None and (msg.startswith("Finished ") or msg.startswith("Failed ")
                                    or "Failed with result" in msg or "Deactivated" in msg):
            runs.append((start - 0.2, t + 0.2))
            start = None
    return runs


def _voice_changes_not_the_timers(before: dict, after: dict) -> list[str]:
    """The live voice files that changed while the tests ran and whose
    mtime (the new one, or the old one for a file now gone) falls in no run
    of the live timer."""
    changed = [p for p in after if after[p] != before[p]]
    if not changed:
        return []
    runs = _exporter_runs(_SESSION_START_S)
    out = []
    for p in changed:
        st = after[p] or before[p]
        t = st[0] / 1e9
        if not any(a <= t <= b for a, b in runs):
            out.append(p)
    return out


def pytest_sessionfinish(session, exitstatus):
    after = _live_work_stat()
    if after != _LIVE_WORK_BEFORE:
        changed = [LIVE_WORK_DB + k for k in after if after[k] != _LIVE_WORK_BEFORE[k]]
        sys.stderr.write(f"\nFAILED: the test run changed the live work database: {', '.join(changed)}\n")
        session.exitstatus = 1
    changed = _voice_changes_not_the_timers(_LIVE_VOICE_BEFORE, _live_voice_stat())
    if changed:
        sys.stderr.write(f"\nFAILED: the test run changed the live voice-run record or its copies: "
                         f"{', '.join(changed)}\n")
        session.exitstatus = 1

# config/ (sprint M21): the services read /config in the container, which
# since step 2 is a live config directory outside the repo (edited on
# /settings). Tests always read the repo's own config/, the shipped
# defaults, and never a live directory, even when the shell exports
# KRONK_CONFIG_DIR: so an edit through the page fails no test, and the pins
# prove the shipped files (criterion 20). Tests that need other files set
# KRONK_CONFIG_DIR to a temporary directory with monkeypatch.
os.environ["KRONK_CONFIG_DIR"] = os.path.join(REPO_ROOT, "config")
# The /settings corpus check (sprint M21 step 2) reads the corpus and the MA
# snapshot from /app/corpus/ in the images; tests read the repo's copies.
os.environ["VOICE_CORPUS_FILE"] = os.path.join(REPO_ROOT, "tests", "voice_corpus.jsonl")
os.environ["MUSIC_SNAPSHOT_FILE"] = os.path.join(REPO_ROOT, "tests", "fixtures", "music_snapshot.json")

_ORCH = os.path.join(REPO_ROOT, "orchestrator")
if _ORCH not in sys.path:
    sys.path.append(_ORCH)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ── no_network: fail any real lookup or connection (sprint M5 change 1) ──────
# The /fetch and /http_check tests feed the code private and placeholder
# hosts. A fake HTTP client serves them, but a bug or a mutant that builds
# its own client would reach the real network: in the M5 end-to-end test a
# mutant sent ARP and mDNS traffic for placeholder hosts. Modules that test
# outbound fetches opt in with `pytestmark = pytest.mark.usefixtures(
# "no_network")`. Every name lookup and every IPv4/IPv6 connect or send
# raises NetworkBlocked before a packet leaves; AF_UNIX sockets (and the
# event loop's socketpair) are unaffected.
import socket as _socket

import pytest as _pytest


class NetworkBlocked(AssertionError):
    """A test tried to use the real network."""


_INET = (_socket.AF_INET, _socket.AF_INET6)


def _blocked(what):
    def refuse(*a, **kw):
        raise NetworkBlocked(f"test tried to use the network: {what}{a!r}")
    return refuse


def _sock_guard(name):
    real = getattr(_socket.socket, name)

    def guarded(self, *a, **kw):
        if self.family in _INET:
            raise NetworkBlocked(f"test tried to use the network: socket.{name}{a!r}")
        return real(self, *a, **kw)
    guarded.__name__ = name
    return guarded


NO_NETWORK_PATCHES = {
    "getaddrinfo": "lookup", "gethostbyname": "lookup", "gethostbyname_ex": "lookup",
    "gethostbyaddr": "lookup", "getnameinfo": "lookup", "create_connection": "connect",
}
NO_NETWORK_SOCKET_METHODS = ("connect", "connect_ex", "sendto", "sendmsg")


@_pytest.fixture
def no_network(monkeypatch):
    for fn in NO_NETWORK_PATCHES:
        monkeypatch.setattr(_socket, fn, _blocked(f"socket.{fn}"))
    for m in NO_NETWORK_SOCKET_METHODS:
        monkeypatch.setattr(_socket.socket, m, _sock_guard(m))
    yield
