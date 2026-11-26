#!/usr/bin/env python3
"""Keep every household voice run for good, and publish the last 90 days for
/work (work item M30, step B). Python standard library only, no network
beyond Tempo on loopback.

Runs on the host, fired every 60 s by kronk-voice-runs-export.timer. Each run:

1. reads Tempo (loopback) for every voice.run with a speech-to-text stage,
   from the last good watermark minus one hour to now, a day per search
   (the first run, with no watermark, reads Tempo's whole 90 days; it is
   the same code). A watermark later than now (a clock that was ahead) is
   not trusted: the read starts an hour before now and the status says so;
2. reads, once, the whole voice trace of each run that ended more than 15
   minutes ago and has none stored yet (child spans can lag one export
   batch; nothing older than 15 minutes changes in Tempo). A run whose
   read failed goes to the back of the queue;
3. joins each run to its recording, once, when the run is final (15
   minutes old) and so is every run that could fit the same recordings,
   with voice_label_sheet.join (imported, not copied), so the answer does
   not depend on the order the runs reached Tempo;
4. writes the record in one BEGIN IMMEDIATE transaction;
5. rebuilds the published copies when they differ from the record (a
   published row changed, or a copy's rows do not match its meta);
6. writes the status files, every run.

Once an hour it also reads all of Tempo's 90 days and compares them with the
record: a run the record should hold and does not is taken (an insert);
a stored value Tempo gives differently is written to run_changes (why
'hourly check'), never applied, and counted in the status file.

Every read of Tempo has a time budget inside the unit's 50 s: no request of
the main read starts later than READ_BUDGET_S into the run, of the trace
reads TRACE_BUDGET_S, of the hourly check CHECK_BUDGET_S, and each request
has TEMPO_TIMEOUT_S. A whole-window read that runs out carries on where it
stopped on the next run (the main read through the watermark, the check
through export_state.check_progress).

The record: ~/.local/state/kronk-voice/voice_runs.db (--db, else
KRONK_VOICE_DB), directory 700, file 600, outside every container's mount,
beside the work-state database. This script is its only writer. Rollback
journal, not WAL (a read-only reader of a WAL file leaves -wal and -shm
behind). It is the file that is backed up (scripts/backups.py: the monthly
config tar, and a weekly copy with the recordings archive). Labels are a
second table this script never writes.

The rules a row lives by (docs/sprint/M30-review.md, point 9): no row is
ever deleted; a stored value is never replaced by an empty one; a stored
value that differs from a new non-empty one is replaced and the old row,
whole, goes to run_changes; a row a person marks `removed` is never written
again (but for last_seen_at) and never published; is_dev, local_time and
local_day are decided when the row is first written and never recomputed;
the watermark never moves past a window Tempo's search limit cut short or
past a failed read.

The published copies: data/voice_runs.db and data/dev/voice_runs.db (--publish,
repeatable), the same content, the record's runs of the last 90 days without
raw_trace, the audio columns, last_seen_at and removed rows, plus a one-row
meta. Built in memory, written to a temporary file in the same directory and
renamed into place, only when a published row changed. The orchestrator
opens them with mode=ro&immutable=1. A container that damages one damages a
copy the next change replaces.

Why this writes household speech under the repo's data/ when
voice_transcripts.py refuses --out inside the repo: Drew's order (M30, "the
/work endpoint should also include all the stt transcriptions"), and the
copies hold the words Grafana already shows the LAN for the same 90 days.
data/ is gitignored; the copies never reach git. Nowhere else in the repo is
accepted: a --publish or --status path inside the repo but outside data/ is
refused, and so is a --db inside the repo.

The status files: data/voice_runs.json and data/dev/voice_runs.json (--status,
repeatable), written every run, atomically: ts, schema, the last good read,
the last error, what this run did, and the summary the page shows folded
(as_of, runs in the last 24 hours, the newest run's time, how many of those
are marked wrong or carry an error). Never any text from a run; errors name
what failed, never what Tempo answered.

Tempo down, slow (a request is given TEMPO_TIMEOUT_S) or answering nonsense:
the record is not touched, the status files carry the reason with a fresh
ts, and the exit is 0 (the page says it). The copies follow the record, not
Tempo: they are published every run they differ. A record that cannot be
opened, or a SQLite error while writing it, exits 1 with the reason in the
status files.

The record is made only by --init (the first export, by hand, and a new one
on purpose). A run that finds no record exits 1: when a status file or a
published copy says one existed, the status says the record is gone and
names the restore step (docs/runbooks/restore.md, step 14); otherwise it
says to run --init.

--remove RUN_ID marks a run removed (a guest, the TV): its text and its
raw_trace emptied, its run_changes rows deleted, with secure_delete on
(every connection of this script has it), and the copies published at once.

--db, --publish and --status are all given or none: a run by hand that names
a scratch record but forgets the status file would otherwise write the real
data/. Tests and by-hand trials always name all three.

    voice_runs_export.py
    voice_runs_export.py --db S/voice_runs.db --publish S/pub.db --status S/pub.json --recordings S/rec
    voice_runs_export.py --check-now          (the hourly comparison, now)
    voice_runs_export.py --init               (the first export: makes the record)
    voice_runs_export.py --remove RUN_ID --why "2026-11-25 drew: a guest"

Feature doc: docs/features/voice-dataset.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
import time
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import voice_label_sheet as ls  # noqa: E402  (the audio join, imported, not copied)
import voice_transcripts as vt  # noqa: E402  (fetch_runs, to_row, is_dev)

SCHEMA = 2                      # the record's PRAGMA user_version (2: audio_tried_at, trace_failed_at, check_progress)
PUBLISH_SCHEMA = 1              # the published copy's user_version and meta.schema
STATUS_SCHEMA = 1
STAGE_FILTER = ".*stt.*"        # TraceQL: runs with a speech-to-text stage (matches anchored or not)
OVERLAP_S = 3600                # each read starts this long before the watermark
FINAL_S = 900                   # older than this, a run is final in Tempo (EXPORT_AGE_MAX_S 840 + slack)
FIRST_READ_S = 92 * 86400       # the first read: all of Tempo (block_retention 2160h) and a margin
TRACE_KEEP_S = 91 * 86400       # a missing raw_trace is retried while Tempo can still hold it
TRACE_READS_MAX = 300           # whole-trace reads per run; the rest wait for the next run
# The time budget, in seconds from the start of the export: no request of a
# phase starts after its mark. With TEMPO_TIMEOUT_S a request, the run is
# done with Tempo by CHECK_BUDGET_S + TEMPO_TIMEOUT_S = 43 s, inside the
# unit's TimeoutStartSec of 50.
READ_BUDGET_S = 20              # the main read (searches and the Kronk-trace enrichment)
TRACE_BUDGET_S = 30             # the whole-trace reads
CHECK_BUDGET_S = 38             # the hourly check
CHECK_EVERY_S = 3600
CHECK_READ_S = 90 * 86400
PUBLISH_WINDOW_S = 90 * 86400
TEMPO_TIMEOUT_S = 5.0           # per request; a slow Tempo fails the read, not the timer
JOIN_NAS_AGE_S = 7 * 86400      # backups.py archives recordings older than this (KRONK_ARCHIVE_AGE_DAYS)
BUSY_TIMEOUT_S = 10             # a person's open write transaction on the record, waited for this long
TEMP_MAX_AGE_S = 600            # a temporary file of ours older than this was left by a kill
TEMP_RE = re.compile(r"\.(voice_runs[\w.-]*\.(?:db|json))\.(\d+)\.[0-9a-f]{8}\.tmp")

DEFAULT_DB = Path.home() / ".local" / "state" / "kronk-voice" / "voice_runs.db"
DEFAULT_PUBLISH = (ROOT / "data" / "voice_runs.db", ROOT / "data" / "dev" / "voice_runs.db")
DEFAULT_STATUS = (ROOT / "data" / "voice_runs.json", ROOT / "data" / "dev" / "voice_runs.json")
DATA = ROOT / "data"
RECORDINGS = Path(os.environ.get("KRONK_RECORDINGS_DIR") or Path.home() / "kronk-voice-recordings")
RESTORE_STEP = "docs/runbooks/restore.md, step 14"
clock = time.monotonic          # the budget's clock (the tests replace it)


class Refused(Exception):
    """A path or setting this script will not use. Exit 2."""


class RecordError(Exception):
    """The record cannot be opened or is not ours. Exit 1."""


class TempoError(Exception):
    """Tempo could not be read, or answered nonsense. The run writes nothing but its status."""


class BudgetSpent(TempoError):
    """A request was not started: this phase's time budget is spent."""


# ═══ The schema (docs/sprint/M30-review.md, Appendix A) ═════════════════════

RUN_COLUMNS = (
    "run_id", "trace_id", "start_s", "end_s", "local_time", "local_day", "service", "pipeline",
    "pipeline_id", "is_dev", "satellite_id", "device_id", "conversation_id", "language", "stages",
    "wake_detection", "heard", "heard_chars", "heard_source", "reply", "reply_chars", "response_type",
    "tier", "rule", "direct_action", "tools", "kronk_trace_id", "kronk_join_matches", "blueprint_run_id",
    "error_code", "corrected_by", "corrected_after_s", "corrected_by_run_id", "correction_kind",
    "correction_of_run_id", "mark_basis", "window_cut_short", "stt_engine", "listen_ms", "transcribe_ms",
    "intent_ms", "stt_config_id", "audio_join", "audio_rel", "audio_bytes", "audio_mtime_ns", "audio_tried_at",
    "raw_trace", "trace_failed_at", "removed", "first_seen_at", "last_seen_at", "last_changed_at", "seq",
)

DDL = """
CREATE TABLE stt_config (
  id             INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL UNIQUE,
  unit           TEXT NOT NULL,
  model          TEXT NOT NULL,
  model_repo     TEXT,
  model_revision TEXT,
  compute_type   TEXT,
  language       TEXT,
  initial_prompt TEXT,
  packages       TEXT,
  first_seen_at  TEXT NOT NULL
);
CREATE TABLE runs (
  run_id              TEXT PRIMARY KEY,
  trace_id            TEXT NOT NULL,
  start_s             REAL NOT NULL,
  end_s               REAL NOT NULL,
  local_time          TEXT NOT NULL,
  local_day           TEXT NOT NULL,
  service             TEXT NOT NULL,
  pipeline            TEXT,
  pipeline_id         TEXT,
  is_dev              INTEGER NOT NULL,
  satellite_id        TEXT,
  device_id           TEXT,
  conversation_id     TEXT,
  language            TEXT,
  stages              TEXT NOT NULL,
  wake_detection      TEXT,
  heard               TEXT,
  heard_chars         INTEGER,
  heard_source        TEXT,
  reply               TEXT,
  reply_chars         INTEGER,
  response_type       TEXT,
  tier                TEXT,
  rule                TEXT,
  direct_action       TEXT,
  tools               TEXT,
  kronk_trace_id      TEXT,
  kronk_join_matches  INTEGER,
  blueprint_run_id    TEXT,
  error_code          TEXT,
  corrected_by        TEXT,
  corrected_after_s   REAL,
  corrected_by_run_id TEXT,
  correction_kind     TEXT,
  correction_of_run_id TEXT,
  mark_basis          TEXT,
  window_cut_short    TEXT,
  stt_engine          TEXT,
  listen_ms           INTEGER,
  transcribe_ms       INTEGER,
  intent_ms           INTEGER,
  stt_config_id       INTEGER REFERENCES stt_config(id),
  audio_join          TEXT,
  audio_rel           TEXT,
  audio_bytes         INTEGER,
  audio_mtime_ns      INTEGER,
  audio_tried_at      TEXT,
  raw_trace           TEXT,
  trace_failed_at     TEXT,
  removed             TEXT,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  last_changed_at     TEXT NOT NULL,
  seq                 INTEGER NOT NULL
);
CREATE INDEX runs_time ON runs (start_s DESC, run_id DESC);
CREATE UNIQUE INDEX runs_seq ON runs (seq);
CREATE TABLE labels (
  run_id       TEXT PRIMARY KEY REFERENCES runs(run_id),
  correct_text TEXT,
  speaker      TEXT,
  corpus_id    TEXT,
  notes        TEXT,
  labelled_at  TEXT NOT NULL,
  labelled_by  TEXT NOT NULL
);
CREATE TABLE run_changes (
  id         INTEGER PRIMARY KEY,
  run_id     TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  why        TEXT NOT NULL,
  old_row    TEXT NOT NULL,
  detail     TEXT
);
CREATE INDEX run_changes_run ON run_changes (run_id, id);
CREATE TABLE export_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  watermark_s   REAL,
  last_ok_at    TEXT,
  last_error    TEXT,
  last_error_at TEXT,
  left_out_dev_text_runs INTEGER,
  last_check_at TEXT,
  last_check    TEXT,
  check_progress TEXT
);
INSERT INTO export_state (id) VALUES (1);
"""
TABLES = ("runs", "labels", "stt_config", "run_changes", "export_state")

# Columns a read of Tempo fills, from the search (fetch_runs' attributes) ...
SEARCH_COLS = {
    "trace_id": "_trace_id", "start_s": "_start_s", "end_s": "_end_s", "service": "_service",
    "pipeline": "ha.pipeline_name", "satellite_id": "ha.satellite_id", "device_id": "kronk.origin.device_id",
    "stages": "voice.stages", "heard": "voice.transcript", "heard_chars": "voice.transcript_chars",
    "reply": "voice.speech", "tier": "voice.handled_by", "rule": "kronk.rule",
    "direct_action": "kronk.direct_action", "tools": "kronk.tools", "kronk_trace_id": "kronk.trace_id",
    "blueprint_run_id": "ha.blueprint_run_id", "error_code": "voice.error_code",
    "corrected_by": "voice.corrected_by", "corrected_after_s": "voice.corrected_after_s",
    "corrected_by_run_id": "voice.corrected_by_run_id", "correction_kind": "voice.correction_kind",
    "correction_of_run_id": "voice.correction_of_run_id", "mark_basis": "voice.mark_basis",
}
# ... and from the whole voice trace (root span attributes the listing does not
# select, and the child spans), read once the run is FINAL_S old.
ROOT_TRACE_COLS = {
    "pipeline_id": "ha.pipeline_id", "conversation_id": "ha.conversation_id", "language": "voice.language",
    "wake_detection": "voice.wake_detection", "reply_chars": "voice.speech_chars",
    "response_type": "voice.response_type", "kronk_join_matches": "kronk.join_matches",
    "window_cut_short": "voice.window_cut_short",
}
CHILD_TRACE_COLS = ("stt_engine", "listen_ms", "transcribe_ms", "intent_ms")
TRACE_COLS = tuple(ROOT_TRACE_COLS) + CHILD_TRACE_COLS + ("raw_trace",)
MERGED = tuple(SEARCH_COLS) + TRACE_COLS
# What the page's copy leaves out: the trace, the audio, and a column that
# changes on every read without the row changing.
UNPUBLISHED = {"raw_trace", "audio_join", "audio_rel", "audio_bytes", "audio_mtime_ns", "audio_tried_at",
               "trace_failed_at", "removed", "last_seen_at"}
PUBLISHED_COLUMNS = tuple(c for c in RUN_COLUMNS if c not in UNPUBLISHED)


def now_iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s) -> float | None:
    try:
        t = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


def empty(v) -> bool:
    return v is None or v == "" or v == [] or v == "[]"


def norm(col: str, v):
    """A value as the record stores it: empty is None; tools a JSON array;
    times to the millisecond."""
    if empty(v):
        return None
    if col == "tools":
        if isinstance(v, str):
            v = [v]
        return json.dumps([str(x) for x in v], ensure_ascii=False) if v else None
    if col in ("start_s", "end_s"):
        return round(float(v), 3)
    if col in ("heard_chars", "reply_chars", "kronk_join_matches", "listen_ms", "transcribe_ms", "intent_ms"):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if col == "corrected_after_s":
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return v if isinstance(v, (int, float)) else str(v)


# ═══ Paths ══════════════════════════════════════════════════════════════════

def inside(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def check_paths(db: Path, publish: list[Path], status: list[Path]) -> None:
    """The record never in the repo; the copies and status files in the
    repo only under data/ (gitignored)."""
    root, data = ROOT.resolve(), DATA.resolve()
    if inside(db.resolve(), root):
        raise Refused(f"{db} is inside the repo; the record lives outside it (default {DEFAULT_DB})")
    for p in publish + status:
        r = p.resolve()
        if inside(r, root) and not inside(r, data):
            raise Refused(f"{p} is inside the repo but not under data/; household speech is published "
                          "under the gitignored data/ only")
        if r == db.resolve():
            raise Refused(f"{p} is the record itself")


def tmp_name(p: Path) -> Path:
    return p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")


def clean_temps(d: Path, now: float, log) -> None:
    """Our own temporary files a killed run left (they can hold household
    speech), older than TEMP_MAX_AGE_S, by exact name pattern only."""
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    for e in entries:
        if not TEMP_RE.fullmatch(e.name):
            continue
        try:
            st = os.lstat(e.path)
            if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and now - st.st_mtime > TEMP_MAX_AGE_S:
                os.unlink(e.path)
                log(f"removed a temporary file a killed run left: {e.path}")
        except OSError:
            pass


def write_atomic(p: Path, data: bytes, mode: int) -> None:
    """data at p, by a temporary file in the same directory and a rename. The
    temporary file is removed whatever goes wrong."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_name(p)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        dfd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


# ═══ The record ═════════════════════════════════════════════════════════════

def _private(p: Path, what: str, want: int) -> None:
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        raise RecordError(f"{what} {p} is a symlink; refused")
    if st.st_uid != os.getuid():
        raise RecordError(f"{what} {p} is not owned by this user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise RecordError(f"{what} {p} is mode {stat.S_IMODE(st.st_mode):o}; it must be {want:o} "
                          "(household speech): chmod it, then run again")


def ensure_dir(db: Path) -> None:
    """The record's directory: made 700 when absent; refused when others can read it."""
    d = db.parent
    if not d.exists():
        d.mkdir(mode=0o700, parents=True)
    _private(d, "the record's directory", 0o700)


def open_record(db: Path, create: bool = False) -> sqlite3.Connection:
    """The record (file 600, rollback journal, secure_delete on), made when
    absent only with create (--init; main has already refused a run without
    it). Refused when others can read it or the schema is not this
    script's. Called with the directory lock held (lock_dir)."""
    if not os.path.lexists(db):
        if not create:
            raise RecordError(f"{db} does not exist")
        fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    _private(db, "the record", 0o600)
    try:
        conn = sqlite3.connect(str(db), timeout=BUSY_TIMEOUT_S, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # Freed space is zeroed, so a removed run's words (and an old value
        # a change replaced) do not stay in the file's free pages.
        conn.execute("PRAGMA secure_delete = ON")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            conn.execute("PRAGMA journal_mode=DELETE")
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if v == 0 and not names:
            conn.executescript(f"BEGIN IMMEDIATE;\n{DDL}\nPRAGMA user_version = {SCHEMA};\nCOMMIT;")
        elif v == 1 and "runs" in names:
            raise RecordError(f"{db} is schema {v}, made by the unfixed step-B build before any promotion; "
                              f"this script knows {SCHEMA} and has no migration: set it aside and run --init")
        elif v > SCHEMA:
            raise RecordError(f"{db} is schema {v}; this script knows {SCHEMA}")
        elif v != SCHEMA or any(t not in names for t in TABLES):
            raise RecordError(f"{db} is not a voice-run record (schema {v}, tables {sorted(names)})")
    except sqlite3.Error as e:
        raise RecordError(f"{db}: {type(e).__name__}: {e}") from None
    return conn


def lock_dir(db: Path) -> int | None:
    """An exclusive flock on the record's directory: one export at a time,
    the timer's and a run by hand alike. None when another holds it."""
    fd = os.open(db.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def state(conn) -> dict:
    return dict(conn.execute("SELECT * FROM export_state WHERE id = 1").fetchone())


def row_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# ═══ Tempo ══════════════════════════════════════════════════════════════════

def http_get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 (loopback only, vt.require_loopback)
        return json.loads(r.read())


def _is_digits(x) -> bool:
    return isinstance(x, str) and x.isdigit() or isinstance(x, int) and not isinstance(x, bool)


def check_search(p) -> None:
    """A search answer has the shape Tempo gives, or the read failed. Says
    what is wrong, never what came back."""
    if not isinstance(p, dict) or not isinstance(p.get("traces", []), list) or "traces" not in p:
        raise TempoError(f"Tempo's search answer is not an object with a list of traces ({type(p).__name__})")
    for t in p["traces"]:
        if not isinstance(t, dict) or not isinstance(t.get("traceID"), str):
            raise TempoError("a trace in Tempo's search answer has no traceID")
        sets = t.get("spanSets")
        sets = sets if sets is not None else ([t["spanSet"]] if t.get("spanSet") is not None else [])
        if not isinstance(sets, list):
            raise TempoError("a trace's spanSets in Tempo's search answer is not a list")
        for ss in sets:
            if not isinstance(ss, dict) or not isinstance(ss.get("spans") or [], list):
                raise TempoError("a span set in Tempo's search answer is not an object with spans")
            for sp in ss.get("spans") or []:
                if not isinstance(sp, dict) or not _is_digits(sp.get("startTimeUnixNano") or t.get("startTimeUnixNano")):
                    raise TempoError("a span in Tempo's search answer has no start time")
                if sp.get("durationNanos") is not None and not _is_digits(sp.get("durationNanos")):
                    raise TempoError("a span in Tempo's search answer has a duration that is not a number")
                attrs = sp.get("attributes") or []
                if not isinstance(attrs, list) or any(not isinstance(a, dict) or not isinstance(a.get("key"), str)
                                                      for a in attrs):
                    raise TempoError("a span's attributes in Tempo's search answer are not key/value objects")


def trace_spans(payload) -> list[dict]:
    """The spans of a trace-by-id answer (/api/v2/traces: {"trace":
    {"resourceSpans"}}; /api/traces: {"batches"}), or TempoError."""
    if not isinstance(payload, dict):
        raise TempoError(f"Tempo's trace answer is not an object ({type(payload).__name__})")
    rs = (payload.get("trace") or {}).get("resourceSpans") if isinstance(payload.get("trace"), dict) else None
    rs = rs if rs is not None else payload.get("batches")
    if not isinstance(rs, list):
        raise TempoError("Tempo's trace answer has no resourceSpans")
    spans = []
    for r in rs:
        for ss in (r.get("scopeSpans") or r.get("instrumentationLibrarySpans") or []) if isinstance(r, dict) else []:
            for sp in ss.get("spans") or [] if isinstance(ss, dict) else []:
                if isinstance(sp, dict):
                    spans.append(sp)
    return spans


def _attrs(sp: dict) -> dict:
    return {a.get("key"): vt._value(a.get("value")) for a in sp.get("attributes") or [] if isinstance(a, dict)}


def _ms(sp: dict) -> int | None:
    try:
        return round((int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6)
    except (KeyError, TypeError, ValueError):
        return None


def trace_fields(payload: dict) -> dict:
    """The columns the whole voice trace gives: root attributes the listing
    does not select, the STT engine, the stage times, and the trace itself."""
    spans = trace_spans(payload)
    root = next((sp for sp in spans if sp.get("name") == "voice.run"), None)
    if root is None:
        raise TempoError("the voice trace has no voice.run span")
    ra = _attrs(root)
    out = {col: ra.get(key) for col, key in ROOT_TRACE_COLS.items()}
    by = {}
    for sp in spans:
        by.setdefault(sp.get("name"), sp)
    out["stt_engine"] = _attrs(by["voice.stt"]).get("voice.engine") if "voice.stt" in by else None
    out["listen_ms"] = _ms(by["voice.stt.listen"]) if "voice.stt.listen" in by else None
    out["transcribe_ms"] = _ms(by["voice.stt.transcribe"]) if "voice.stt.transcribe" in by else None
    out["intent_ms"] = _ms(by["voice.intent"]) if "voice.intent" in by else None
    out["raw_trace"] = json.dumps(payload.get("trace") if "trace" in payload else payload,
                                  ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return out


class Tempo:
    """Tempo on loopback through one function, get_json(url, timeout): the
    boundary the tests replace."""

    def __init__(self, url: str, get_json=http_get_json, timeout: float = TEMPO_TIMEOUT_S):
        self.url = vt.require_loopback(url)
        self._get, self.timeout = get_json, timeout
        self.calls = 0
        self.deadline: float | None = None     # clock() after which no request starts (the phase's budget)
        self.budget_hit = False                # a request was refused for the budget since the phase began

    def phase(self, deadline: float | None) -> None:
        self.deadline, self.budget_hit = deadline, False

    def get(self, url: str):
        if self.deadline is not None and clock() > self.deadline:
            self.budget_hit = True
            raise BudgetSpent("this read's time budget is spent")
        self.calls += 1
        try:
            p = self._get(url, self.timeout)
        except TempoError:
            raise
        except Exception as e:  # noqa: BLE001 - down, refused, timed out, not JSON: all one failure
            raise TempoError(f"{type(e).__name__}: {str(e)[:200]}") from None
        if "/api/search" in url:
            check_search(p)
        return p

    def runs(self, since: float, until: float, enrich: bool = True) -> tuple[list[dict], list]:
        truncated: list = []
        try:
            runs = vt.fetch_runs(since, until, self.url, include_dev=True, get_json=self.get, enrich=enrich,
                                 truncated=truncated, stages=STAGE_FILTER)
        except TempoError:
            raise
        except Exception as e:  # noqa: BLE001 - a shape check_search let through
            raise TempoError(f"Tempo's search answer could not be read: {type(e).__name__}") from None
        # A run without an STT stage is never a row, even when Tempo ignores the filter.
        return [a for a in runs if "stt" in str(a.get("voice.stages") or "")], truncated

    def trace(self, trace_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id or ""):
            raise TempoError("not a trace id")
        p = self.get(f"{self.url}/api/v2/traces/{trace_id}")
        try:
            return trace_fields(p)
        except TempoError:
            raise
        except Exception as e:  # noqa: BLE001 - any shape of answer is a failed trace read, never a raise
            raise TempoError(f"Tempo's trace answer could not be read: {type(e).__name__}") from None


def read_window(tempo: Tempo, since: float, until: float, deadline: float, enrich: bool) -> tuple[list, list, float]:
    """The runs that started in [since, until], a day per search, oldest
    first, until the phase's budget is spent. Returns (runs, truncated
    windows, read_to): every run that started in [since, read_to) is in
    runs (read_to == until: the whole window). A day whose search or
    enrichment the budget cut is dropped whole and read again next time.
    TempoError (not the budget) propagates: the read failed."""
    tempo.phase(deadline)
    got, seen, truncated = [], set(), []
    t = since
    try:
        while t < until:
            t2 = min(t + vt.DAY_S, until)
            try:
                runs, trunc = tempo.runs(t, t2, enrich=enrich)
            except BudgetSpent:
                break
            if tempo.budget_hit:            # an enrichment was refused: this day again, whole
                break
            for a in runs:
                if a["_start_s"] >= t2 and t2 < until:
                    continue                # [t, t2): the next day has it
                k = a.get("ha.pipeline_run_id") or a["_trace_id"]
                if k not in seen:
                    seen.add(k)
                    got.append(a)
            truncated += trunc
            t = t2
    finally:
        tempo.phase(None)
    got.sort(key=lambda a: a["_start_s"])
    return got, truncated, min(t, until)


# ═══ Rows ═══════════════════════════════════════════════════════════════════

def search_values(a: dict) -> dict:
    return {col: norm(col, a.get(key)) for col, key in SEARCH_COLS.items()}


def new_row(a: dict, now: float, dev_pipelines) -> dict:
    """A run's first row: is_dev, local_time and local_day decided now, for good."""
    v = search_values(a)
    start = a["_start_s"]
    row = dict.fromkeys(RUN_COLUMNS)
    row.update(v)
    row.update(run_id=a["ha.pipeline_run_id"],
               local_time=vt.to_row(a)["time"],
               local_day=dt.datetime.fromtimestamp(start).astimezone().date().isoformat(),
               is_dev=1 if vt.is_dev(a, dev_pipelines) else 0,
               heard_source="tempo" if v["heard"] is not None else None,
               service=v["service"] or "",
               stages=v["stages"] or "",
               first_seen_at=now_iso(now), last_seen_at=now_iso(now), last_changed_at=now_iso(now))
    return row


def merge(old: dict, new: dict) -> tuple[dict, bool]:
    """Rule 1: an empty new value never replaces a stored one; a differing
    non-empty one does. Returns (the changed columns, whether a stored value
    was replaced, so the old row goes to run_changes)."""
    changes, replaced = {}, False
    for col, v in new.items():
        if v is None or col not in MERGED:
            continue
        if old.get(col) is None:
            changes[col] = v
        elif old[col] != v:
            changes[col] = v
            replaced = True
    if "heard" in changes:
        changes["heard_source"] = "tempo"
    return changes, replaced


# ═══ Audio ══════════════════════════════════════════════════════════════════

def run_for_join(r: dict) -> dict:
    """A record row as voice_label_sheet.join reads a run."""
    return {"ha.pipeline_name": r.get("pipeline"), "voice.stages": r.get("stages"),
            "kronk.origin.device_id": r.get("device_id"), "_start_s": r["start_s"], "_end_s": r["end_s"],
            "ha.pipeline_run_id": r["run_id"], "_trace_id": r.get("trace_id")}


JOIN_COLS = "run_id, trace_id, start_s, end_s, pipeline, stages, device_id"


def _closure(conn, t: dict, recs: list) -> tuple[list[dict], list]:
    """Every record row and recording that voice_label_sheet.join could link
    to run t, through any chain of shared candidates: a run's span is
    [start - JOIN_BEFORE_S, end + JOIN_AFTER_S], a recording its mtime; the
    span is widened until no row outside it overlaps it. A superset of t's
    group in join, so the join over it gives t the answer the join over
    every run and recording would."""
    lo, hi = t["start_s"] - ls.JOIN_BEFORE_S, t["end_s"] + ls.JOIN_AFTER_S
    while True:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {JOIN_COLS} FROM runs WHERE start_s <= ? AND end_s >= ? ORDER BY start_s, run_id",
            (hi + ls.JOIN_BEFORE_S, lo - ls.JOIN_AFTER_S))]
        nlo = min([lo] + [r["start_s"] - ls.JOIN_BEFORE_S for r in rows])
        nhi = max([hi] + [r["end_s"] + ls.JOIN_AFTER_S for r in rows])
        if (nlo, nhi) == (lo, hi):
            return rows, [r for r in recs if lo <= r.mtime <= hi]
        lo, hi = nlo, nhi


def audio_joins(conn, recordings: Path, now: float) -> tuple[dict, dict]:
    """The audio join of every row not yet tried (audio_tried_at NULL), not
    removed, and final, by voice_label_sheet.join over all the record's
    rows and recordings that can compete with it (_closure). A row waits
    while any row in that set is younger than FINAL_S: a run that could
    fit the same recording may not have reached Tempo yet. Returns
    ({run_id: (audio_join, audio_rel, audio_bytes, audio_mtime_ns)}, the
    report). 'ambiguous' is stored, never guessed. No recording for a row
    older than JOIN_NAS_AGE_S is NULL, not 'none': its recording may be on
    the NAS. A recordings directory that cannot be read leaves every row
    untried, to be tried again."""
    targets = [dict(r) for r in conn.execute(
        f"SELECT {JOIN_COLS} FROM runs WHERE audio_tried_at IS NULL AND removed IS NULL AND end_s < ? "
        "ORDER BY start_s", (now - FINAL_S,))]
    rep: dict = {}
    if not targets:
        return {}, rep
    try:
        if not stat.S_ISDIR(os.lstat(recordings).st_mode):
            rep["audio_not_tried"] = f"the recordings directory {recordings} is not a directory"
            return {}, rep
        os.listdir(recordings)
        recs = ls.scan(recordings)
    except OSError as e:
        rep["audio_not_tried"] = f"the recordings directory {recordings} cannot be read ({e.strerror or e})"
        return {}, rep
    out, waiting = {}, 0
    for t in targets:
        if not any(t["start_s"] - ls.JOIN_BEFORE_S <= r.mtime <= t["end_s"] + ls.JOIN_AFTER_S for r in recs):
            # No recording can be this run's: join would pair it with none, whatever else is near.
            out[t["run_id"]] = (None if now - t["end_s"] > JOIN_NAS_AGE_S else "none", None, None, None)
            continue
        rows, near = _closure(conn, t, recs)
        if any(r["end_s"] >= now - FINAL_S for r in rows):
            waiting += 1
            continue
        runs = [run_for_join(r) for r in rows]
        i = next(k for k, r in enumerate(rows) if r["run_id"] == t["run_id"])
        for r in near:
            r.run, r.note = None, ""
        ls.join(near, runs)
        got = [r for r in near if r.run is runs[i]]
        res = None
        if len(got) == 1:
            p = got[0].path
            try:
                st = os.stat(p)
                res = ("certain", p.relative_to(recordings).as_posix(), st.st_size, st.st_mtime_ns)
            except OSError:
                res = None
        if res is None and any(r.note.startswith("ambiguous") and i in ls._candidates(r, runs) for r in near):
            res = ("ambiguous", None, None, None)
        if res is None:
            res = (None if now - t["end_s"] > JOIN_NAS_AGE_S else "none", None, None, None)
        out[t["run_id"]] = res
    rep.update(audio_joined=sum(1 for j in out.values() if j[0] == "certain"),
               audio_ambiguous=sum(1 for j in out.values() if j[0] == "ambiguous"),
               audio_none=sum(1 for j in out.values() if j[0] == "none"),
               audio_not_local=sum(1 for j in out.values() if j[0] is None))
    if waiting:
        rep["audio_waiting"] = waiting
    return out, rep


# ═══ The export ═════════════════════════════════════════════════════════════

def _insert(conn, row: dict) -> None:
    conn.execute(f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
                 [row[c] for c in RUN_COLUMNS])


def check_due(st: dict, now: float, check_now: bool) -> bool:
    """The hourly check runs when asked, when one is under way, when none
    has run, an hour after the last, and when the last is stamped later
    than now (a clock that was ahead must not stop it for that long)."""
    last = parse_iso(st["last_check_at"])
    return (check_now or st["check_progress"] is not None or last is None or last > now
            or now - last >= CHECK_EVERY_S)


def export(conn, tempo: Tempo, now: float, recordings: Path, dev_pipelines=vt.DEV_PIPELINES,
           check_now: bool = False, log=print) -> dict:
    """One run against an open record. Reads first (no lock held), then one
    BEGIN IMMEDIATE transaction. Raises TempoError when the main read
    fails: nothing is written then. The report's "not_ok" makes the
    status say ok: false."""
    t0 = clock()
    st = state(conn)
    wm = st["watermark_s"]
    report: dict = {"first_read": wm is None}
    errors: list[str] = []
    ahead = wm is not None and wm > now
    if ahead:
        # A watermark later than now: a run with a clock that was ahead. Not
        # trusted: read from an hour before now; the hourly check takes what
        # this read cannot reach.
        since = now - OVERLAP_S
        report["watermark_ahead_s"] = round(wm - now, 3)
        report["not_ok"] = (f"the watermark was {round(wm - now)} s later than now (a clock that was ahead?); "
                            "read from an hour before now, and the hourly check takes what that missed")
        errors.append(report["not_ok"])
    else:
        since = max(now - FIRST_READ_S, (wm - OVERLAP_S) if wm is not None else now - FIRST_READ_S)
    report.update(read_from_s=round(since, 3), read_to_s=round(now, 3))
    listed, truncated, read_to = read_window(tempo, since, now, t0 + READ_BUDGET_S, enrich=True)  # TempoError
    if read_to < now:
        report["read_stopped_at_s"] = round(read_to, 3)
        errors.append(f"the read stopped at {now_iso(read_to)} on its {READ_BUDGET_S} s budget; "
                      "the next run carries on from there")
    report["listed"] = len(listed)
    report["truncated_windows"] = len(truncated)
    no_id = [a for a in listed if not a.get("ha.pipeline_run_id")]
    listed = [a for a in listed if a.get("ha.pipeline_run_id")]
    report["skipped_no_run_id"] = len(no_id)
    enrich_fail = [a["_enrich_error"] for a in listed if a.get("_enrich_error")]
    report["enrich_failures"] = len(enrich_fail)

    stored = {r["run_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE run_id IN (SELECT value FROM json_each(?))",
        (json.dumps([a["ha.pipeline_run_id"] for a in listed]),))}

    # The whole trace, once, for runs that ended FINAL_S ago (rule 2): those
    # never tried first, newest first; then those whose read failed, the
    # longest-waiting first, so a trace that never reads cannot hold up the rest.
    fresh = [dict(r) for r in conn.execute(
        "SELECT run_id, trace_id, start_s FROM runs WHERE raw_trace IS NULL AND removed IS NULL "
        "AND trace_failed_at IS NULL AND end_s < ? AND start_s > ? ORDER BY start_s DESC LIMIT ?",
        (now - FINAL_S, now - TRACE_KEEP_S, TRACE_READS_MAX))]
    fresh += [{"run_id": a["ha.pipeline_run_id"], "trace_id": a["_trace_id"], "start_s": a["_start_s"]}
              for a in listed if a["ha.pipeline_run_id"] not in stored and a["_end_s"] < now - FINAL_S]
    fresh.sort(key=lambda w: (-w["start_s"], w["run_id"]))
    retry = [dict(r) for r in conn.execute(
        "SELECT run_id, trace_id, start_s FROM runs WHERE raw_trace IS NULL AND removed IS NULL "
        "AND trace_failed_at IS NOT NULL AND end_s < ? AND start_s > ? "
        "ORDER BY trace_failed_at, start_s DESC LIMIT ?",
        (now - FINAL_S, now - TRACE_KEEP_S, TRACE_READS_MAX))]
    want = (fresh + retry)[:TRACE_READS_MAX]
    traces, trace_fail = {}, {}
    tempo.phase(t0 + TRACE_BUDGET_S)
    try:
        for k, w in enumerate(want):
            try:
                traces[w["run_id"]] = tempo.trace(w["trace_id"])
            except BudgetSpent:
                report["trace_reads_deferred"] = len(want) - k
                break
            except TempoError as e:
                trace_fail[w["run_id"]] = str(e)[:120]
    finally:
        tempo.phase(None)
    report["trace_reads"] = len(traces)
    report["trace_read_failures"] = len(trace_fail)

    # The hourly check: Tempo's 90 days in day searches, under its budget,
    # carried on next run where it stopped (export_state.check_progress).
    check = None
    if check_due(st, now, check_now):
        prog = json.loads(st["check_progress"]) if st["check_progress"] and not check_now else None
        prog = prog or {"started_at": now_iso(now), "next_s": now - CHECK_READ_S, "listed": 0, "missing": 0,
                        "taken": 0, "missing_ids": [], "differing": 0, "truncated_windows": 0}
        until = now - FINAL_S
        try:
            runs, trunc, upto = read_window(tempo, max(prog["next_s"], now - CHECK_READ_S), until,
                                            t0 + CHECK_BUDGET_S, enrich=False)
            check = {"runs": [a for a in runs if a.get("ha.pipeline_run_id")], "truncated": len(trunc),
                     "progress": prog, "upto": upto, "done": upto >= until}
        except TempoError as e:
            check = {"error": str(e)[:200]}

    joins: dict = {}
    stamp = now_iso(now)
    inserted = changed = replaced_n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()[0]
        # Re-read inside the lock: a person's edit since the read above wins.
        stored = {r["run_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE run_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted({a["ha.pipeline_run_id"] for a in listed} | set(traces))),))}
        for a in listed:
            rid = a["ha.pipeline_run_id"]
            if rid in stored:
                continue
            row = new_row(a, now, dev_pipelines)
            if rid in traces:
                row.update({k: norm(k, v) for k, v in traces.pop(rid).items()})
            seq += 1
            row["seq"] = seq
            _insert(conn, row)
            stored[rid] = row
            inserted += 1
        updates: dict[str, dict] = {}
        for a in listed:
            rid = a["ha.pipeline_run_id"]
            updates.setdefault(rid, {}).update(search_values(a))
        for rid, tf in traces.items():
            updates.setdefault(rid, {}).update({k: norm(k, v) for k, v in tf.items()})
        for rid, new in updates.items():
            old = stored.get(rid)
            if old is None:
                continue
            if old.get("removed") is not None:
                continue                                    # a person removed it: never written again
            ch, replaced = merge(old, new)
            if not ch:
                continue
            if replaced:
                conn.execute("INSERT INTO run_changes (run_id, changed_at, why, old_row) VALUES (?, ?, 'export', ?)",
                             (rid, stamp, json.dumps(old, ensure_ascii=False, sort_keys=True)))
                replaced_n += 1
            seq += 1
            ch.update(seq=seq, last_changed_at=stamp)
            conn.execute(f"UPDATE runs SET {', '.join(f'{c} = ?' for c in ch)} WHERE run_id = ?",
                         [*ch.values(), rid])
            changed += 1
        if trace_fail:                                      # bookkeeping: the queue's order, not a change
            conn.execute("UPDATE runs SET trace_failed_at = ? WHERE run_id IN (SELECT value FROM json_each(?)) "
                         "AND removed IS NULL", (stamp, json.dumps(sorted(trace_fail))))
        seen = sorted({a["ha.pipeline_run_id"] for a in listed}
                      | ({a["ha.pipeline_run_id"] for a in check["runs"]} if check and "runs" in check else set()))
        conn.execute("UPDATE runs SET last_seen_at = ? WHERE run_id IN (SELECT value FROM json_each(?))",
                     (stamp, json.dumps(seen)))

        # The watermark: past what this read covered, but never past a
        # cut-short window (rule 9) nor past where the budget stopped it.
        new_wm = min(now - FINAL_S, read_to)
        if truncated:
            new_wm = min(new_wm, min(t for t, _ in truncated))
        if wm is not None and not ahead:
            new_wm = max(wm, new_wm)
        elif wm is None and read_to <= since:
            new_wm = None                                   # a first read that read nothing yet
        if truncated:
            errors.append(f"{len(truncated)} window(s) hit Tempo's search limit; the watermark stops at the first")
        if trace_fail:
            errors.append(f"{len(trace_fail)} trace read(s) failed ({next(iter(trace_fail.values()))}); "
                          "tried again after the others waiting")
        if enrich_fail:
            errors.append(f"{len(enrich_fail)} Kronk trace read(s) for the rule failed ({enrich_fail[0]}); "
                          "tried again while the run is in the read window")
        if no_id:
            errors.append(f"{len(no_id)} run(s) without ha.pipeline_run_id left out")
        if check is not None:
            summary, take = do_check(conn, check, new_wm if new_wm is not None else -1, stamp)
            for a in take:
                row = new_row(a, now, dev_pipelines)
                seq += 1
                row["seq"] = seq
                _insert(conn, row)
                inserted += 1
            report["check"] = summary
            if summary.get("error"):
                errors.append(f"the hourly check could not read Tempo: {summary['error']}")
            elif take or summary["differing"]:
                errors.append(f"the hourly check: {len(take)} run(s) in Tempo missing from the record, taken; "
                              f"{summary['differing']} differing (run_changes, why 'hourly check')")
            if summary.get("in_progress"):
                conn.execute("UPDATE export_state SET check_progress = ? WHERE id = 1",
                             (json.dumps(summary["in_progress"], sort_keys=True),))
            else:
                conn.execute("UPDATE export_state SET last_check_at = ?, last_check = ?, check_progress = NULL "
                             "WHERE id = 1", (stamp, json.dumps(summary, sort_keys=True)))

        # The audio, for rows final now (after every insert above, so each
        # competes with every run that has arrived).
        joins, jrep = audio_joins(conn, recordings, now)
        report.update(jrep)
        for rid, (j, rel, size, mtime) in joins.items():
            conn.execute("UPDATE runs SET audio_join = ?, audio_rel = ?, audio_bytes = ?, audio_mtime_ns = ?, "
                         "audio_tried_at = ? WHERE run_id = ?", (j, rel, size, mtime, stamp, rid))
        if jrep.get("audio_not_tried"):
            errors.append(f"audio not joined: {jrep['audio_not_tried']}; tried again next run")

        conn.execute("UPDATE export_state SET watermark_s = ?, last_ok_at = ? WHERE id = 1",
                     (round(new_wm, 3) if new_wm is not None else None, stamp))
        if errors:
            conn.execute("UPDATE export_state SET last_error = ?, last_error_at = ? WHERE id = 1",
                         ("; ".join(errors)[:600], stamp))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    report.update(inserted=inserted, changed=changed, replaced=replaced_n,
                  watermark_s=round(new_wm, 3) if new_wm is not None else None, problems=errors)
    return report


def do_check(conn, check: dict, watermark: float, stamp: str) -> tuple[dict, list[dict]]:
    """One pass of the hourly comparison over the days check read: every
    run Tempo lists that the record should hold (it started before the
    watermark) is there and agrees. A missing run is returned to be taken
    (an insert, not a rewrite). A difference is recorded in run_changes
    (why 'hourly check', the stored row whole and Tempo's values in
    detail), once per distinct difference; the row is not changed. The
    counts add up across passes; the summary has "in_progress" while days
    are left."""
    if "error" in check:
        return {"at": stamp, "error": check["error"]}, []
    prog = dict(check["progress"])
    take = []
    for a in check["runs"]:
        rid = a["ha.pipeline_run_id"]
        if a["_start_s"] >= watermark:
            continue
        prog["listed"] += 1
        r = conn.execute("SELECT * FROM runs WHERE run_id = ?", (rid,)).fetchone()
        if r is None:
            if any(t["ha.pipeline_run_id"] == rid for t in take):
                continue
            prog["missing"] += 1
            if len(prog["missing_ids"]) < 20:
                prog["missing_ids"].append(rid)
            take.append(a)
            continue
        old = dict(r)
        if old["removed"] is not None:
            continue
        diff = {c: v for c, v in search_values(a).items() if v is not None and old.get(c) != v}
        if not diff:
            continue
        prog["differing"] += 1
        detail = json.dumps({"tempo": diff}, ensure_ascii=False, sort_keys=True)
        last = conn.execute("SELECT detail FROM run_changes WHERE run_id = ? AND why = 'hourly check' "
                            "ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        if last is None or last[0] != detail:
            conn.execute("INSERT INTO run_changes (run_id, changed_at, why, old_row, detail) "
                         "VALUES (?, ?, 'hourly check', ?, ?)",
                         (rid, stamp, json.dumps(old, ensure_ascii=False, sort_keys=True), detail))
    prog["taken"] += len(take)
    prog["truncated_windows"] += check["truncated"]
    prog["next_s"] = round(check["upto"], 3)
    if not check["done"]:
        return {"in_progress": prog, "missing": prog["missing"], "differing": prog["differing"]}, take
    out = {k: prog[k] for k in ("started_at", "listed", "missing", "taken", "missing_ids", "differing",
                                "truncated_windows")}
    out["at"] = stamp
    return out, take


# ═══ The published copy ═════════════════════════════════════════════════════

PUBLISH_DDL = (
    "CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK (id = 1), schema INTEGER NOT NULL, built_at TEXT NOT NULL, "
    "built_s REAL NOT NULL, window_start_s REAL NOT NULL, rows INTEGER NOT NULL, max_seq INTEGER, "
    "content_sha256 TEXT NOT NULL)",
)


def published_rows(conn, now: float) -> tuple[list[tuple], str, float]:
    since = now - PUBLISH_WINDOW_S
    rows = conn.execute(f"SELECT {', '.join(PUBLISHED_COLUMNS)} FROM runs WHERE removed IS NULL AND start_s >= ? "
                        "ORDER BY run_id", (since,)).fetchall()
    h = hashlib.sha256(f"schema {PUBLISH_SCHEMA}\n".encode())
    i_id, i_seq = PUBLISHED_COLUMNS.index("run_id"), PUBLISHED_COLUMNS.index("seq")
    for r in rows:
        h.update(f"{r[i_id]}\x00{r[i_seq]}\n".encode())
    return [tuple(r) for r in rows], h.hexdigest(), since


def copy_sha(p: Path) -> str | None:
    """The content fingerprint of a published copy, or None (missing,
    unreadable, another schema, or a row count that is not meta's: rebuilt).
    Read every run, so a damaged copy is mended by the next run, not the
    next change."""
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        try:
            if c.execute("PRAGMA user_version").fetchone()[0] != PUBLISH_SCHEMA:
                return None
            sha, rows = c.execute("SELECT content_sha256, rows FROM meta WHERE id = 1").fetchone()
            if c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] != rows:
                return None                 # rows gone or added behind meta's back: rebuilt
            return sha
        finally:
            c.close()
    except (sqlite3.Error, TypeError):
        return None


def build_copy(rows: list[tuple], sha: str, since: float, now: float, record_ddl: str) -> bytes:
    mem = sqlite3.connect(":memory:")
    try:
        mem.execute(record_ddl)
        mem.execute("CREATE INDEX runs_time ON runs (start_s DESC, run_id DESC)")
        mem.execute("CREATE UNIQUE INDEX runs_seq ON runs (seq)")
        for d in PUBLISH_DDL:
            mem.execute(d)
        mem.executemany(f"INSERT INTO runs VALUES ({', '.join('?' * len(PUBLISHED_COLUMNS))})", rows)
        max_seq = max((r[PUBLISHED_COLUMNS.index("seq")] for r in rows), default=None)
        mem.execute("INSERT INTO meta VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                    (PUBLISH_SCHEMA, now_iso(now), round(now, 3), round(since, 3), len(rows), max_seq, sha))
        mem.execute(f"PRAGMA user_version = {PUBLISH_SCHEMA}")
        mem.commit()
        return bytes(mem.serialize())
    finally:
        mem.close()


def publish_ddl() -> str:
    """The copy's runs table: the record's columns, less UNPUBLISHED."""
    lines = []
    in_runs = False
    for line in DDL.splitlines():
        if line.startswith("CREATE TABLE runs"):
            in_runs = True
            continue
        if in_runs:
            if line.startswith(")"):
                break
            col = line.strip().split()[0]
            if col not in UNPUBLISHED:
                lines.append(line.strip().rstrip(",").replace(" REFERENCES stt_config(id)", ""))
    return "CREATE TABLE runs (\n  " + ",\n  ".join(lines) + "\n)"


def publish(conn, paths: list[Path], now: float) -> list[str]:
    """Each copy whose content differs from the record's, rebuilt and
    renamed into place. Returns the paths written."""
    rows, sha, since = published_rows(conn, now)
    stale = [p for p in paths if copy_sha(p) != sha]
    if not stale:
        return []
    blob = build_copy(rows, sha, since, now, publish_ddl())
    for p in stale:
        write_atomic(p, blob, 0o600)
    return [str(p) for p in stale]


# ═══ The status file ════════════════════════════════════════════════════════

def summary(conn, now: float) -> dict:
    """What the folded section shows: per household and dev, runs in the
    last 24 hours, the newest, how many of those are marked wrong or carry
    an error. Counts and times only."""
    out = {"as_of": now_iso(now), "as_of_s": round(now, 3)}
    for key, dev in (("household", 0), ("dev", 1)):
        r = conn.execute(
            "SELECT COUNT(*), SUM(corrected_by IS NOT NULL), SUM(error_code IS NOT NULL), "
            "SUM(corrected_by IS NOT NULL OR error_code IS NOT NULL) FROM runs "
            "WHERE removed IS NULL AND is_dev = ? AND start_s >= ?", (dev, now - 86400)).fetchone()
        newest = conn.execute("SELECT MAX(start_s) FROM runs WHERE removed IS NULL AND is_dev = ?", (dev,)).fetchone()[0]
        out[key] = {"runs_24h": r[0], "wrong_24h": r[1] or 0, "error_24h": r[2] or 0, "flagged_24h": r[3] or 0,
                    "newest_s": newest,
                    "newest_time": (dt.datetime.fromtimestamp(newest).astimezone().isoformat(timespec="seconds")
                                    if newest is not None else None)}
    return out


def write_status(paths: list[Path], body: dict) -> list[str]:
    errs = []
    data = (json.dumps(body, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode()
    for p in paths:
        try:
            write_atomic(p, data, 0o644)
        except OSError as e:
            errs.append(f"{p}: {e.strerror or e}")
    return errs


def prior_status(status_to: list[Path]) -> dict:
    """What the status files say of an earlier record: a dict, empty when
    none says anything (missing, unreadable, or written before any record)."""
    for p in status_to:
        try:
            st = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and (st.get("record") is not None or st.get("record_lost") or st.get("last_ok_at")):
            return st
    return {}


def failed_status(status_to: list[Path], now: float, msg: str, **extra) -> list[str]:
    return write_status(status_to, {"ts": round(now, 3), "schema": STATUS_SCHEMA, "ok": False,
                                    "last_error": msg[:400], "last_error_at": now_iso(now),
                                    "last_ok_at": None, "summary": None, **extra})


def record_failed(status_to: list[Path], now: float, e: Exception, log) -> int:
    log(f"FAILED: the record cannot be opened: {e}")
    failed_status(status_to, now, f"the record cannot be opened: {str(e)[:300]}")
    return 1


def record_missing(db: Path, publish_to: list[Path], status_to: list[Path], now: float, log) -> int:
    """No record and no --init: never made again silently (M30-e2e2 finding
    5). When a published copy or a status file says one existed, the record
    is gone: the status keeps saying so (record_lost, the last good read)
    until it is restored or --init makes a new one on purpose."""
    copies = [str(p) for p in publish_to if os.path.lexists(p)]
    prior = prior_status(status_to)
    if copies or prior:
        why = f"the published copy {copies[0]}" if copies else "the status file"
        msg = (f"the record {db} is gone, but {why} says one existed; it is not made again. Restore it "
               f"({RESTORE_STEP}), or run once with --init to start a new, empty record on purpose")
        log(f"FAILED: {msg}")
        failed_status(status_to, now, msg, record_lost=True,
                      last_ok_at=prior.get("last_ok_at"), last_ok_s=prior.get("last_ok_s"))
    else:
        msg = f"there is no record at {db} yet: the first export is run by hand with --init"
        log(f"FAILED: {msg}")
        failed_status(status_to, now, msg, record_lost=False)
    return 1


def remove_run(conn, rid: str, why: str, now: float) -> dict:
    """Mark a run removed: its text (heard, reply) and raw_trace emptied, its
    run_changes rows (old rows whole, words included) deleted, in one
    transaction with secure_delete on (open_record), so the words leave
    the file and its free pages. The row stays (nothing is deleted); the
    exporter never writes it again and never publishes it."""
    stamp = now_iso(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT removed FROM runs WHERE run_id = ?", (rid,)).fetchone()
        if r is None:
            conn.execute("ROLLBACK")
            return {"found": False}
        conn.execute("UPDATE runs SET removed = COALESCE(removed, ?), heard = NULL, reply = NULL, raw_trace = NULL, "
                     "last_changed_at = ? WHERE run_id = ?", (f"{stamp} {why}", stamp, rid))
        n = conn.execute("DELETE FROM run_changes WHERE run_id = ?", (rid,)).rowcount
        label = conn.execute("SELECT 1 FROM labels WHERE run_id = ?", (rid,)).fetchone() is not None
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {"found": True, "was_removed": r[0] is not None, "changes_deleted": n, "has_label": label}


def status_body(conn, now: float, t0: float, report: dict, error: str | None) -> dict:
    st = state(conn)
    runs, max_seq = conn.execute("SELECT COUNT(*), MAX(seq) FROM runs").fetchone()
    return {
        "ts": round(now, 3), "schema": STATUS_SCHEMA, "record_schema": SCHEMA, "publish_schema": PUBLISH_SCHEMA,
        "ok": error is None and not report.get("not_ok"),
        "last_ok_at": st["last_ok_at"], "last_ok_s": parse_iso(st["last_ok_at"]),
        "last_error": error or st["last_error"],
        "last_error_at": now_iso(now) if error else st["last_error_at"],
        "watermark_s": st["watermark_s"],
        "record": {"runs": runs, "max_seq": max_seq},
        "this_run": {**report, "took_s": round(time.monotonic() - t0, 3)},
        "last_check": json.loads(st["last_check"]) if st["last_check"] else None,
        "check_progress": json.loads(st["check_progress"]) if st["check_progress"] else None,
        "summary": summary(conn, now),
    }


# ═══ Main ═══════════════════════════════════════════════════════════════════

def main(argv=None, now: float | None = None, get_json=http_get_json, dev_pipelines=vt.DEV_PIPELINES,
         tempo_timeout: float = TEMPO_TIMEOUT_S, out=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help=f"the record (default KRONK_VOICE_DB, else {DEFAULT_DB})")
    ap.add_argument("--publish", action="append", help="a published copy (repeatable; default data/ and data/dev/)")
    ap.add_argument("--status", action="append", help="a status file (repeatable; default data/ and data/dev/)")
    ap.add_argument("--recordings", default=str(RECORDINGS), help="HA's recordings (default %(default)s)")
    ap.add_argument("--tempo", default=vt.TEMPO, help="Tempo on loopback (default %(default)s)")
    ap.add_argument("--check-now", action="store_true", help="run the hourly comparison in this run")
    ap.add_argument("--init", action="store_true",
                    help="make the record when there is none (the first export; a new one on purpose)")
    ap.add_argument("--remove", metavar="RUN_ID", help="mark a run removed, empty its text, publish; no Tempo read")
    ap.add_argument("--why", default="removed by hand", help="with --remove: who and why (no household words)")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    t0 = time.monotonic()
    now = time.time() if now is None else now

    def log(msg: str) -> None:
        print(f"voice_runs_export: {msg}", file=out, flush=True)

    given = [args.db is not None, args.publish is not None, args.status is not None]
    try:
        if any(given) and not all(given):
            raise Refused("--db, --publish and --status are given together or not at all "
                          "(a scratch record must not write the real data/)")
        if args.remove is not None and args.init:
            raise Refused("--remove and --init do not go together")
        db = Path(args.db or os.environ.get("KRONK_VOICE_DB") or DEFAULT_DB).expanduser()
        publish_to = [Path(p).expanduser() for p in (args.publish or DEFAULT_PUBLISH)]
        status_to = [Path(p).expanduser() for p in (args.status or DEFAULT_STATUS)]
        check_paths(db, publish_to, status_to)
        tempo = Tempo(args.tempo, get_json, tempo_timeout)
    except (Refused, vt.Refused) as e:
        log(f"REFUSED: {e}")
        return 2
    for d in {p.parent for p in publish_to + status_to}:
        clean_temps(d, now, log)

    if not os.path.lexists(db) and not args.init:
        return record_missing(db, publish_to, status_to, now, log)
    if args.init and os.path.lexists(db):
        log(f"--init: the record {db} exists; nothing to make, this is an ordinary run")
    try:
        ensure_dir(db)
        lock = lock_dir(db)
        if lock is None:
            log("another export holds the record's lock; this run does nothing")
            return 0
    except (RecordError, OSError) as e:
        return record_failed(status_to, now, e, log)
    try:
        conn = open_record(db, create=args.init)
    except (RecordError, OSError) as e:
        os.close(lock)
        return record_failed(status_to, now, e, log)
    try:
        error = db_err = None
        report: dict = {}
        try:
            if args.remove is not None:
                rm = remove_run(conn, args.remove, args.why, now)
                if not rm["found"]:
                    db_err = f"--remove: no run {args.remove} in the record"
                report = {"removed": 1 if rm["found"] else 0, **{k: v for k, v in rm.items() if k != "found"}}
            else:
                report = export(conn, tempo, now, Path(args.recordings).expanduser(), dev_pipelines,
                                args.check_now, log)
        except TempoError as e:
            error = f"Tempo: {e}"
        except sqlite3.Error as e:
            db_err = f"the record could not be written: {type(e).__name__}: {e}"
        # The copies follow the record, whatever Tempo did (a removal made
        # while Tempo is down is published at once).
        published, pub_err = [], None
        if db_err is None:
            try:
                published = publish(conn, publish_to, now)
            except (OSError, sqlite3.Error) as e:
                pub_err = f"publishing failed: {type(e).__name__}: {getattr(e, 'strerror', None) or e}"
        report["published"] = len(published)
        try:
            body = status_body(conn, now, t0, report, error or db_err or pub_err)
            serr = write_status(status_to, body)
        except sqlite3.Error as e:
            db_err = db_err or f"the record could not be read: {type(e).__name__}: {e}"
            serr = failed_status(status_to, now, db_err)
    finally:
        os.close(lock)
        conn.close()
    if db_err:
        log(f"FAILED: {db_err}")
    elif args.remove is not None:
        log(f"removed {args.remove}: text and raw_trace emptied, {report['changes_deleted']} run_changes row(s) "
            f"deleted, copies written {len(published)} of {len(publish_to)}"
            + ("; it has a label: labels.correct_text is not touched (the exporter never writes labels)"
               if report.get("has_label") else ""))
    elif error:
        log(f"not read: {error}; " + (f"the record is unchanged, copies written {len(published)} of {len(publish_to)}"
                                      if published else "the record and the copies are unchanged"))
    else:
        log(f"listed {report['listed']}, new {report['inserted']}, changed {report['changed']} "
            f"(replaced {report['replaced']}), traces read {report['trace_reads']} "
            f"(failed {report['trace_read_failures']}), copies written {len(published)} of {len(publish_to)}"
            + (f"; problems: {'; '.join(report['problems'])}" if report.get("problems") else ""))
    if pub_err:
        log(pub_err)
    for e in serr:
        log(f"status file not written: {e}")
    return 1 if (db_err or pub_err or serr) else 0


if __name__ == "__main__":
    sys.exit(main())
