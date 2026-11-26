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
# does not exist. Two fences keep a test off the live files (M30 criterion
# B6; step C fix round 1, the coordinator's ruling on M30-e2e6 B1):
#
# 1. In this process, a write is refused: sqlite3.connect of a live path,
#    open()/io.open() in a write mode, os.open with a write flag, and
#    os.replace, os.rename, os.remove, os.unlink, os.truncate on one raise
#    LiveVoiceFileBlocked, and the test that tried fails even if the code
#    under test caught the error (_live_voice_hooks below). A read of a live
#    path with open(..., "r") is not refused; nothing in the suite does one.
#    A subprocess is not fenced (the exporter run as a subprocess against
#    scratch is the case that matters, and it never names a live path).
# 2. At the end of the session the live files are compared with the start.
#    The live timer (kronk-voice-runs-export.timer) writes the record and
#    both status files every 60 s and a copy when its content changes, so
#    a change is excused only as far as the unit's journal accounts for it:
#    - the record (read mode=ro: its run ids, their seq and four counts, no
#      text) must exist if it did at the start; every run id must still be
#      there; a seq may only rise; the rows added and the seq raised must
#      equal what the journal's "new N, changed N" lines in between add up
#      to; the count of removed rows never moves (a removal is never the
#      timer's); labels and run_changes never shrink. If the journal cannot
#      be read, any difference fails.
#    - a published copy may change only inside a run whose line says a
#      copy was written; one gone at the end fails.
#    - a status file may change only inside a run of the unit (it heals
#      itself on the next); one gone at the end fails.
#    - the record's -journal, -wal and -shm: as the status files, but one
#      gone is the timer's when its last mtime was inside a run (the
#      -journal comes and goes inside every run).
#    Failure messages carry paths and counts, never text of a run.
_VOICE_TMP = tempfile.mkdtemp(prefix="kronk-voice-")
atexit.register(shutil.rmtree, _VOICE_TMP, ignore_errors=True)
os.environ["KRONK_VOICE_DB"] = os.path.join(_VOICE_TMP, "voice_runs.db")
LIVE_VOICE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "kronk-voice")
LIVE_VOICE_RECORD = os.path.join(LIVE_VOICE_DIR, "voice_runs.db")
LIVE_VOICE_SIDE = [LIVE_VOICE_RECORD + s for s in ("-journal", "-wal", "-shm")]
LIVE_VOICE_COPIES = [os.path.join(REPO_ROOT, "data", d, "voice_runs.db") for d in ("", "dev")]
LIVE_VOICE_STATUS = [os.path.join(REPO_ROOT, "data", d, "voice_runs.json") for d in ("", "dev")]
LIVE_VOICE_FILES = [LIVE_VOICE_RECORD] + LIVE_VOICE_SIDE + LIVE_VOICE_COPIES + LIVE_VOICE_STATUS
VOICE_UNIT = "kronk-voice-runs-export.service"
JOURNAL_MARGIN_S = 0.2

import re as _re
import sqlite3 as _sqlite3
import urllib.parse as _urlparse

_real_sqlite_connect = _sqlite3.connect


def _live_voice_stat():
    out = {}
    for p in LIVE_VOICE_FILES:
        if p == LIVE_VOICE_RECORD:
            continue
        try:
            st = os.stat(p)
            out[p] = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            out[p] = None
    return out


def _record_snapshot(path: str = LIVE_VOICE_RECORD) -> dict | None:
    """The record as the guard compares it: None when it does not exist;
    else {"runs": {run_id: seq}, "removed", "labels", "run_changes": counts,
    "t": (before, after) the read, epoch seconds}, or {"error": why, "t": ...}
    when it cannot be read. Opened mode=ro (it never writes, and makes no
    -journal); reads run ids, seq and counts, no text."""
    t0 = time.time()
    if not os.path.lexists(path):
        return None
    try:
        conn = _real_sqlite_connect(f"file:{_urlparse.quote(path)}?mode=ro", uri=True, timeout=5,
                                    isolation_level=None)
        try:
            conn.execute("BEGIN")
            runs = dict(conn.execute("SELECT run_id, seq FROM runs").fetchall())
            removed = conn.execute("SELECT COUNT(*) FROM runs WHERE removed IS NOT NULL").fetchone()[0]
            labels = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
            changes = conn.execute("SELECT COUNT(*) FROM run_changes").fetchone()[0]
            conn.execute("COMMIT")
        finally:
            conn.close()
    except _sqlite3.Error as e:
        return {"error": f"{type(e).__name__}: {e}", "t": (t0, time.time())}
    return {"runs": runs, "removed": removed, "labels": labels, "run_changes": changes, "t": (t0, time.time())}


_RUN_LINE = _re.compile(r"\bnew (\d+), changed (\d+)\b")
_COPIES_LINE = _re.compile(r"\bcopies written (\d+) of \d+")


def _exporter_runs(since_s: float) -> list[dict] | None:
    """The live timer's runs since since_s, from its unit's user journal:
    each "Starting ..." through the next "Finished ...", "Failed ..." or
    "Deactivated", as {"start", "end" (None: still running when read), "new",
    "changed", "copies"} (epoch seconds; the counts summed over the run's
    lines). The unit logs counts, never text. None when the journal cannot
    be read: then every change is reported."""
    try:
        p = subprocess.run(["journalctl", "--user", "-u", VOICE_UNIT, "--since", f"@{int(since_s) - 120}",
                            "-o", "json", "--output-fields=MESSAGE", "--no-pager"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    runs, cur = [], None
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
            t, msg = int(e["__REALTIME_TIMESTAMP"]) / 1e6, str(e.get("MESSAGE") or "")
        except (ValueError, KeyError, TypeError):
            continue
        if msg.startswith("Starting "):
            cur = {"start": t, "end": None, "new": 0, "changed": 0, "copies": 0}
            runs.append(cur)
        elif cur is None:
            continue
        elif msg.startswith("Finished ") or msg.startswith("Failed ") or "Failed with result" in msg \
                or "Deactivated" in msg:
            cur["end"] = max(cur["end"] or t, t)
        else:
            # The exporter's own line comes through its stdout stream, which
            # journald may read after systemd's "Finished": it belongs to the
            # last run started (runs are a minute apart), and ends it no
            # earlier than itself.
            m, c = _RUN_LINE.search(msg), _COPIES_LINE.search(msg)
            if m:
                cur["new"] += int(m.group(1))
                cur["changed"] += int(m.group(2))
            if c:
                cur["copies"] += int(c.group(1))
            if (m or c) and cur["end"] is not None:
                cur["end"] = max(cur["end"], t)
    return runs


def _in_a_run(t: float, runs: list[dict], copies: bool = False) -> bool:
    m = JOURNAL_MARGIN_S
    return any(r["start"] - m <= t <= (r["end"] if r["end"] is not None else float("inf")) + m
               and (not copies or r["copies"] > 0) for r in runs)


def _record_problems(before: dict | None, after: dict | None, runs: list[dict] | None) -> list[str]:
    """What is wrong with the record's change from before to after, given
    the timer's runs; [] when nothing changed or the journal accounts for it."""
    if before is None and after is None:
        return []
    if before is None:
        return ["the record exists now and did not at the start (the timer never makes it)"]
    if after is None:
        return ["the record was deleted"]
    if "error" in before or "error" in after:
        return [f"the record could not be read at the {'start' if 'error' in before else 'end'} "
                f"({(before.get('error') or after.get('error'))[:200]}); it cannot be compared"]
    out = []
    b, a = before["runs"], after["runs"]
    gone = [r for r in b if r not in a]
    if gone:
        out.append(f"{len(gone)} run(s) of the {len(b)} at the start are gone")
    lowered = sum(1 for r in b if r in a and a[r] < b[r])
    if lowered:
        out.append(f"{lowered} run(s) have a lower seq than at the start")
    if after["removed"] != before["removed"]:
        out.append(f"the removed rows went from {before['removed']} to {after['removed']} (a removal is never "
                   "the timer's)")
    for k in ("labels", "run_changes"):
        if after[k] < before[k]:
            out.append(f"{k} went from {before[k]} to {after[k]}")
    if out:
        return out
    b_max = max(b.values(), default=0)
    a_max = max(a.values(), default=0)
    added = [r for r in a if r not in b]
    raised = [r for r in b if a[r] > b[r]]
    if not added and not raised and a_max == b_max:
        return []
    what = f"{len(added)} row(s) added, {len(raised)} seq raised, max seq {b_max} to {a_max}"
    if runs is None:
        return [f"the record changed ({what}) and the timer's journal cannot be read"]
    if any(a[r] <= b_max for r in added + raised):
        return [f"the record changed ({what}): a row added or raised has a seq at or below the start's max"]
    # Runs wholly between the two reads count; one that overlaps a read
    # (its commit may be on either side) may or may not.
    m = JOURNAL_MARGIN_S
    (b0, b1), (a0, a1) = before["t"], after["t"]
    sure, maybe = [], []
    for r in runs:
        end = r["end"] if r["end"] is not None else float("inf")
        if end + m < b0 or r["start"] - m > a1:
            continue
        (sure if r["start"] - m > b1 and end + m < a0 else maybe).append(r)
    if len(maybe) > 6:
        return [f"the record changed ({what}) and {len(maybe)} timer runs overlap the reads; it cannot be told"]
    for mask in range(1 << len(maybe)):
        used = sure + [r for i, r in enumerate(maybe) if mask >> i & 1]
        new, changed = sum(r["new"] for r in used), sum(r["changed"] for r in used)
        if len(added) == new and a_max - b_max == new + changed and len(raised) <= changed:
            return []
    new, changed = sum(r["new"] for r in sure), sum(r["changed"] for r in sure)
    return [f"the record changed ({what}) and the timer's journal in between accounts for new {new}, "
            f"changed {changed}" + (f" (and {len(maybe)} run(s) overlapping the reads)" if maybe else "")]


def _file_problems(before: dict, after: dict, runs: list[dict] | None) -> list[str]:
    """The copies, the status files and the record's side files that changed
    in a way the timer's runs do not account for."""
    out = []
    for p in after:
        if after[p] == before.get(p):
            continue
        if runs is None:
            out.append(f"{p} (the timer's journal cannot be read)")
            continue
        if after[p] is None:
            if p in LIVE_VOICE_SIDE and _in_a_run(before[p][0] / 1e9, runs):
                continue
            out.append(f"{p} (deleted)")
            continue
        if not _in_a_run(after[p][0] / 1e9, runs, copies=p in LIVE_VOICE_COPIES):
            out.append(p)
    return out


def _voice_guard_problems(before_files, after_files, before_record, after_record, runs) -> list[str]:
    return ([f"{LIVE_VOICE_RECORD}: {x}" for x in _record_problems(before_record, after_record, runs)]
            + _file_problems(before_files, after_files, runs))


# ── fence 1: no in-process write of a live voice path ───────────────────────

class LiveVoiceFileBlocked(AssertionError):
    """A test tried to write a live voice-run file."""


_LIVE_VOICE_EXACT = {os.path.realpath(p) for p in LIVE_VOICE_FILES}
_LIVE_VOICE_UNDER = [os.path.realpath(LIVE_VOICE_DIR)]
_LIVE_VOICE_TRIED: list[str] = []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _is_live_voice(path) -> bool:
    if isinstance(path, int):
        return False
    try:
        p = os.fsdecode(os.fspath(path))
    except TypeError:
        return False
    if p.startswith("file:"):
        p = _urlparse.unquote(_urlparse.urlsplit(p).path)
    if p in ("", ":memory:"):
        return False
    r = os.path.realpath(p)
    return r in _LIVE_VOICE_EXACT or any(r == d or r.startswith(d + os.sep) for d in _LIVE_VOICE_UNDER)


def _refuse_live(what: str, path):
    msg = f"a test tried to {what} a live voice-run file: {os.fsdecode(os.fspath(path))}"
    _LIVE_VOICE_TRIED.append(msg)
    raise LiveVoiceFileBlocked(msg)


def _guard_connect(database, *a, **kw):
    if _is_live_voice(database):
        _refuse_live("open with sqlite3.connect", database)
    return _real_sqlite_connect(database, *a, **kw)


def _wrap_open(real):
    def guarded_open(file, mode="r", *a, **kw):
        if isinstance(mode, str) and any(c in mode for c in "wax+") and _is_live_voice(file):
            _refuse_live(f"open (mode {mode!r})", file)
        return real(file, mode, *a, **kw)
    guarded_open.__wrapped__ = real
    return guarded_open


def _wrap_os_open(real):
    def guarded(path, flags, *a, **kw):
        if flags & _WRITE_FLAGS and _is_live_voice(path):
            _refuse_live("os.open for writing", path)
        return real(path, flags, *a, **kw)
    guarded.__wrapped__ = real
    return guarded


def _wrap_path_op(real, what, nargs):
    def guarded(*a, **kw):
        for x in a[:nargs]:
            if _is_live_voice(x):
                _refuse_live(what, x)
        return real(*a, **kw)
    guarded.__wrapped__ = real
    return guarded


import builtins as _builtins
import io as _io

_sqlite3.connect = _guard_connect
_sqlite3.dbapi2.connect = _guard_connect
_builtins.open = _wrap_open(_builtins.open)
_io.open = _builtins.open
os.open = _wrap_os_open(os.open)
for _name, _n in (("replace", 2), ("rename", 2), ("remove", 1), ("unlink", 1), ("truncate", 1)):
    setattr(os, _name, _wrap_path_op(getattr(os, _name), f"os.{_name}", _n))

_LIVE_VOICE_BEFORE = _live_voice_stat()
_LIVE_RECORD_BEFORE = _record_snapshot()
_SESSION_START_S = time.time()


def pytest_sessionfinish(session, exitstatus):
    after = _live_work_stat()
    if after != _LIVE_WORK_BEFORE:
        changed = [LIVE_WORK_DB + k for k in after if after[k] != _LIVE_WORK_BEFORE[k]]
        sys.stderr.write(f"\nFAILED: the test run changed the live work database: {', '.join(changed)}\n")
        session.exitstatus = 1
    if _LIVE_VOICE_TRIED:
        sys.stderr.write(f"\nFAILED: {len(_LIVE_VOICE_TRIED)} attempt(s) to write a live voice-run file were "
                         f"refused: {_LIVE_VOICE_TRIED[0]}\n")
        session.exitstatus = 1
    files, record = _live_voice_stat(), _record_snapshot()
    runs = _exporter_runs(_SESSION_START_S)
    problems = _voice_guard_problems(_LIVE_VOICE_BEFORE, files, _LIVE_RECORD_BEFORE, record, runs)
    if problems and runs is not None:
        # A run that ended just before the last read may not be in the
        # journal yet: read the journal once more, never the files again.
        time.sleep(2)
        runs = _exporter_runs(_SESSION_START_S)
        problems = _voice_guard_problems(_LIVE_VOICE_BEFORE, files, _LIVE_RECORD_BEFORE, record, runs)
    if problems:
        sys.stderr.write("\nFAILED: the test run changed the live voice-run record or its copies: "
                         f"{'; '.join(problems)}\n")
        session.exitstatus = 1


import pytest as _pytest_hooks


def _fail_if_tried(n: int) -> None:
    if len(_LIVE_VOICE_TRIED) > n:
        raise LiveVoiceFileBlocked(_LIVE_VOICE_TRIED[n])


# A refused write fails the test that made it, in its setup, call or
# teardown, even when the code under test caught LiveVoiceFileBlocked.
@_pytest_hooks.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    n = len(_LIVE_VOICE_TRIED)
    res = yield
    _fail_if_tried(n)
    return res


@_pytest_hooks.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    n = len(_LIVE_VOICE_TRIED)
    res = yield
    _fail_if_tried(n)
    return res


@_pytest_hooks.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    n = len(_LIVE_VOICE_TRIED)
    res = yield
    _fail_if_tried(n)
    return res

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
