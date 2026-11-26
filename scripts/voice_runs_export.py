#!/usr/bin/env python3
"""Keep every household voice run for good, and publish the last 90 days for
/work (work item M30, step B). Python standard library only, no network
beyond Tempo on loopback.

Runs on the host, fired every 60 s by kronk-voice-runs-export.timer. Each run:

1. reads Tempo (loopback) for every voice.run with a speech-to-text stage,
   from the last good watermark minus one hour to now (the first run, with
   no watermark, reads Tempo's whole 90 days; it is the same code);
2. reads, once, the whole voice trace of each run that ended more than 15
   minutes ago and has none stored yet (child spans can lag one export
   batch; nothing older than 15 minutes changes in Tempo);
3. tries, once, the join of each new run to its recording, with
   voice_label_sheet.join (imported, not copied);
4. writes the record in one BEGIN IMMEDIATE transaction;
5. rebuilds the published copies when a published row changed;
6. writes the status files, every run.

Once an hour it also reads all of Tempo's 90 days and compares them with the
record: a difference is written to run_changes (why 'hourly check'), never
applied, and counted in the status file.

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
the record and the copies are not touched, the status files carry the
reason with a fresh ts, and the exit is 0 (the page says it). A record that
cannot be opened exits 1.

--db, --publish and --status are all given or none: a run by hand that names
a scratch record but forgets the status file would otherwise write the real
data/. Tests and by-hand trials always name all three.

    voice_runs_export.py
    voice_runs_export.py --db S/voice_runs.db --publish S/pub.db --status S/pub.json --recordings S/rec
    voice_runs_export.py --check-now          (the hourly comparison, now)

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

SCHEMA = 1                      # the record's PRAGMA user_version
PUBLISH_SCHEMA = 1              # the published copy's user_version and meta.schema
STATUS_SCHEMA = 1
STAGE_FILTER = ".*stt.*"        # TraceQL: runs with a speech-to-text stage (matches anchored or not)
OVERLAP_S = 3600                # each read starts this long before the watermark
FINAL_S = 900                   # older than this, a run is final in Tempo (EXPORT_AGE_MAX_S 840 + slack)
FIRST_READ_S = 92 * 86400       # the first read: all of Tempo (block_retention 2160h) and a margin
TRACE_KEEP_S = 91 * 86400       # a missing raw_trace is retried while Tempo can still hold it
TRACE_READS_MAX = 300           # whole-trace reads per run; the rest wait for the next run
TRACE_FAILS_MAX = 3             # trace reads that fail before the rest wait for the next run
TRACE_BUDGET_S = 15             # ... or this long spent on them (the unit's TimeoutStartSec is 50)
CHECK_EVERY_S = 3600
CHECK_READ_S = 90 * 86400
PUBLISH_WINDOW_S = 90 * 86400
TEMPO_TIMEOUT_S = 5.0           # per request; a slow Tempo fails the read, not the timer
JOIN_LOCAL_MAX_AGE_S = 6 * 86400   # recordings older than 7 days may be on the NAS: the join waits
TEMP_MAX_AGE_S = 600            # a temporary file of ours older than this was left by a kill
TEMP_RE = re.compile(r"\.(voice_runs[\w.-]*\.(?:db|json))\.(\d+)\.[0-9a-f]{8}\.tmp")

DEFAULT_DB = Path.home() / ".local" / "state" / "kronk-voice" / "voice_runs.db"
DEFAULT_PUBLISH = (ROOT / "data" / "voice_runs.db", ROOT / "data" / "dev" / "voice_runs.db")
DEFAULT_STATUS = (ROOT / "data" / "voice_runs.json", ROOT / "data" / "dev" / "voice_runs.json")
DATA = ROOT / "data"
RECORDINGS = Path(os.environ.get("KRONK_RECORDINGS_DIR") or Path.home() / "kronk-voice-recordings")


class Refused(Exception):
    """A path or setting this script will not use. Exit 2."""


class RecordError(Exception):
    """The record cannot be opened or is not ours. Exit 1."""


class TempoError(Exception):
    """Tempo could not be read, or answered nonsense. The run writes nothing but its status."""


# ═══ The schema (docs/sprint/M30-review.md, Appendix A) ═════════════════════

RUN_COLUMNS = (
    "run_id", "trace_id", "start_s", "end_s", "local_time", "local_day", "service", "pipeline",
    "pipeline_id", "is_dev", "satellite_id", "device_id", "conversation_id", "language", "stages",
    "wake_detection", "heard", "heard_chars", "heard_source", "reply", "reply_chars", "response_type",
    "tier", "rule", "direct_action", "tools", "kronk_trace_id", "kronk_join_matches", "blueprint_run_id",
    "error_code", "corrected_by", "corrected_after_s", "corrected_by_run_id", "correction_kind",
    "correction_of_run_id", "mark_basis", "window_cut_short", "stt_engine", "listen_ms", "transcribe_ms",
    "intent_ms", "stt_config_id", "audio_join", "audio_rel", "audio_bytes", "audio_mtime_ns", "raw_trace",
    "removed", "first_seen_at", "last_seen_at", "last_changed_at", "seq",
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
  raw_trace           TEXT,
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
  last_check    TEXT
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
UNPUBLISHED = {"raw_trace", "audio_join", "audio_rel", "audio_bytes", "audio_mtime_ns", "removed", "last_seen_at"}
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


def open_record(db: Path) -> sqlite3.Connection:
    """The record, made when absent (file 600, rollback journal). Refused
    when others can read it or the schema is not this script's. Called with
    the directory lock held (lock_dir)."""
    if not db.exists():
        fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    _private(db, "the record", 0o600)
    try:
        conn = sqlite3.connect(str(db), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            conn.execute("PRAGMA journal_mode=DELETE")
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if v == 0 and not names:
            conn.executescript(f"BEGIN IMMEDIATE;\n{DDL}\nPRAGMA user_version = {SCHEMA};\nCOMMIT;")
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

    def get(self, url: str):
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
        return trace_fields(self.get(f"{self.url}/api/v2/traces/{trace_id}"))


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


def audio_joins(recordings: Path, targets: list[dict], others: list[dict]) -> tuple[dict, str | None]:
    """For each target row: (audio_join, audio_rel, audio_bytes,
    audio_mtime_ns) by voice_label_sheet.join over the recordings near it,
    with every record row in that span competing for them. 'ambiguous' is
    stored, never guessed. ({}, why) when the directory cannot be read: the
    rows stay untried (NULL)."""
    if not targets:
        return {}, None
    try:
        if not stat.S_ISDIR(os.lstat(recordings).st_mode):
            return {}, f"the recordings directory {recordings} is not a directory"
        os.listdir(recordings)
    except OSError as e:
        return {}, f"the recordings directory {recordings} cannot be read ({e.strerror or e})"
    lo = min(t["start_s"] for t in targets) - 60
    hi = max(t["end_s"] for t in targets) + 60
    recs = [r for r in ls.scan(recordings) if lo <= r.mtime <= hi]
    seen = {t["run_id"] for t in targets}
    pool = targets + [o for o in others if o["run_id"] not in seen and o["end_s"] >= lo and o["start_s"] <= hi]
    runs = [run_for_join(r) for r in pool]
    ls.join(recs, runs)
    out = {}
    for i, t in enumerate(targets):
        got = [r for r in recs if r.run is runs[i]]
        if len(got) == 1:
            p = got[0].path
            try:
                st = os.stat(p)
                out[t["run_id"]] = ("certain", p.relative_to(recordings).as_posix(), st.st_size, st.st_mtime_ns)
            except OSError:
                out[t["run_id"]] = ("none", None, None, None)
        elif any(r.note.startswith("ambiguous") and i in ls._candidates(r, runs) for r in recs):
            out[t["run_id"]] = ("ambiguous", None, None, None)
        else:
            out[t["run_id"]] = ("none", None, None, None)
    return out, None


# ═══ The export ═════════════════════════════════════════════════════════════

def export(conn, tempo: Tempo, now: float, recordings: Path, dev_pipelines=vt.DEV_PIPELINES,
           check_now: bool = False, log=print) -> dict:
    """One run against an open record. Reads first (no lock held), then one
    BEGIN IMMEDIATE transaction. Raises TempoError when the incremental read
    fails: nothing is written then."""
    st = state(conn)
    wm = st["watermark_s"]
    since = max(now - FIRST_READ_S, (wm - OVERLAP_S) if wm is not None else now - FIRST_READ_S)
    report = {"read_from_s": round(since, 3), "read_to_s": round(now, 3), "first_read": wm is None}
    listed, truncated = tempo.runs(since, now)                 # TempoError: nothing written
    report["listed"] = len(listed)
    report["truncated_windows"] = len(truncated)
    no_id = [a for a in listed if not a.get("ha.pipeline_run_id")]
    listed = [a for a in listed if a.get("ha.pipeline_run_id")]
    report["skipped_no_run_id"] = len(no_id)

    stored = {r["run_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE run_id IN (SELECT value FROM json_each(?))",
        (json.dumps([a["ha.pipeline_run_id"] for a in listed]),))}

    # The whole trace, once, for runs that ended FINAL_S ago (rule 2).
    want = [dict(r) for r in conn.execute(
        "SELECT run_id, trace_id, end_s FROM runs WHERE raw_trace IS NULL AND removed IS NULL "
        "AND end_s < ? AND start_s > ? ORDER BY start_s DESC LIMIT ?",
        (now - FINAL_S, now - TRACE_KEEP_S, TRACE_READS_MAX))]
    have = {w["run_id"] for w in want}
    for a in listed:
        rid = a["ha.pipeline_run_id"]
        if rid not in stored and rid not in have and a["_end_s"] < now - FINAL_S and len(want) < TRACE_READS_MAX:
            want.append({"run_id": rid, "trace_id": a["_trace_id"], "end_s": a["_end_s"]})
    traces, trace_fail = {}, []
    t_start = time.monotonic()
    for w in want:
        if len(trace_fail) >= TRACE_FAILS_MAX or time.monotonic() - t_start > TRACE_BUDGET_S:
            report["trace_reads_deferred"] = len(want) - len(traces) - len(trace_fail)
            break
        try:
            traces[w["run_id"]] = tempo.trace(w["trace_id"])
        except TempoError as e:
            trace_fail.append(str(e)[:120])
    report["trace_reads"] = len(traces)
    report["trace_read_failures"] = len(trace_fail)

    check = None
    if check_now or st["last_check_at"] is None or now - (parse_iso(st["last_check_at"]) or 0) >= CHECK_EVERY_S:
        try:
            check_runs, check_trunc = tempo.runs(now - CHECK_READ_S, now - FINAL_S, enrich=False)
            check = {"runs": [a for a in check_runs if a.get("ha.pipeline_run_id")], "truncated": len(check_trunc)}
        except TempoError as e:
            check = {"error": str(e)[:200]}

    # Audio, for the runs this read sees for the first time and that are recent enough to be local.
    news = [new_row(a, now, dev_pipelines) for a in listed if a["ha.pipeline_run_id"] not in stored]
    targets = [r for r in news if r["end_s"] > now - JOIN_LOCAL_MAX_AGE_S]
    nearby = []
    if targets:
        lo = min(t["start_s"] for t in targets) - 120
        hi = max(t["end_s"] for t in targets) + 120
        nearby = [dict(r) for r in conn.execute(
            "SELECT run_id, trace_id, start_s, end_s, pipeline, stages, device_id FROM runs "
            "WHERE end_s >= ? AND start_s <= ?", (lo, hi))]
    joins, join_why = audio_joins(recordings, targets, nearby)
    report["audio_joined"] = sum(1 for j in joins.values() if j[0] == "certain")
    report["audio_ambiguous"] = sum(1 for j in joins.values() if j[0] == "ambiguous")
    if join_why:
        report["audio_not_tried"] = join_why

    stamp = now_iso(now)
    inserted = changed = replaced_n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()[0]
        # Re-read inside the lock: a person's edit since the read above wins.
        stored = {r["run_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE run_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted({a["ha.pipeline_run_id"] for a in listed} | set(traces))),))}
        for row in news:
            rid = row["run_id"]
            if rid in stored:
                continue
            if rid in traces:
                row.update({k: norm(k, v) for k, v in traces.pop(rid).items()})
            if rid in joins:
                row["audio_join"], row["audio_rel"], row["audio_bytes"], row["audio_mtime_ns"] = joins[rid]
            seq += 1
            row["seq"] = seq
            conn.execute(f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
                         [row[c] for c in RUN_COLUMNS])
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
        seen = sorted({a["ha.pipeline_run_id"] for a in listed}
                      | ({a["ha.pipeline_run_id"] for a in check["runs"]} if check and "runs" in check else set()))
        conn.execute("UPDATE runs SET last_seen_at = ? WHERE run_id IN (SELECT value FROM json_each(?))",
                     (stamp, json.dumps(seen)))

        # The watermark: past this read, but never past a cut-short window (rule 9).
        new_wm = now - FINAL_S
        if truncated:
            new_wm = min(new_wm, min(t for t, _ in truncated))
        if wm is not None:
            new_wm = max(wm, new_wm)
        errors = []
        if truncated:
            errors.append(f"{len(truncated)} window(s) hit Tempo's search limit; the watermark stops at the first")
        if trace_fail:
            errors.append(f"{len(trace_fail)} trace read(s) failed ({trace_fail[0]}); tried again next run")
        if no_id:
            errors.append(f"{len(no_id)} run(s) without ha.pipeline_run_id left out")
        if check is not None:
            summary = do_check(conn, check, new_wm, stamp)
            report["check"] = summary
            if summary.get("error"):
                errors.append(f"the hourly check could not read Tempo: {summary['error']}")
            elif summary["missing"] or summary["differing"]:
                errors.append(f"the hourly check: {summary['missing']} run(s) in Tempo missing from the record, "
                              f"{summary['differing']} differing (run_changes, why 'hourly check')")
            conn.execute("UPDATE export_state SET last_check_at = ?, last_check = ? WHERE id = 1",
                         (stamp, json.dumps(summary, sort_keys=True)))
        conn.execute("UPDATE export_state SET watermark_s = ?, last_ok_at = ? WHERE id = 1", (round(new_wm, 3), stamp))
        if errors:
            conn.execute("UPDATE export_state SET last_error = ?, last_error_at = ? WHERE id = 1",
                         ("; ".join(errors)[:600], stamp))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    report.update(inserted=inserted, changed=changed, replaced=replaced_n, watermark_s=round(new_wm, 3),
                  problems=errors)
    return report


def do_check(conn, check: dict, watermark: float, stamp: str) -> dict:
    """The hourly comparison: every run Tempo lists that the record should
    hold (it started before the watermark) is there and agrees. A
    difference is recorded in run_changes (why 'hourly check', the stored
    row whole and Tempo's values in detail), once per distinct difference;
    the row is not changed."""
    if "error" in check:
        return {"error": check["error"]}
    missing, differing, ids = 0, 0, []
    for a in check["runs"]:
        rid = a["ha.pipeline_run_id"]
        if a["_start_s"] >= watermark:
            continue
        r = conn.execute("SELECT * FROM runs WHERE run_id = ?", (rid,)).fetchone()
        if r is None:
            missing += 1
            if len(ids) < 20:
                ids.append(rid)
            continue
        old = dict(r)
        if old["removed"] is not None:
            continue
        diff = {c: v for c, v in search_values(a).items() if v is not None and old.get(c) != v}
        if not diff:
            continue
        differing += 1
        detail = json.dumps({"tempo": diff}, ensure_ascii=False, sort_keys=True)
        last = conn.execute("SELECT detail FROM run_changes WHERE run_id = ? AND why = 'hourly check' "
                            "ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        if last is None or last[0] != detail:
            conn.execute("INSERT INTO run_changes (run_id, changed_at, why, old_row, detail) "
                         "VALUES (?, ?, 'hourly check', ?, ?)",
                         (rid, stamp, json.dumps(old, ensure_ascii=False, sort_keys=True), detail))
    return {"at": stamp, "listed": len(check["runs"]), "missing": missing, "missing_ids": ids,
            "differing": differing, "truncated_windows": check["truncated"]}


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
    unreadable, another schema: rebuilt)."""
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        try:
            if c.execute("PRAGMA user_version").fetchone()[0] != PUBLISH_SCHEMA:
                return None
            return c.execute("SELECT content_sha256 FROM meta WHERE id = 1").fetchone()[0]
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


def record_failed(status_to: list[Path], now: float, e: Exception, log) -> int:
    log(f"FAILED: the record cannot be opened: {e}")
    write_status(status_to, {"ts": round(now, 3), "schema": STATUS_SCHEMA, "ok": False,
                             "last_error": f"the record cannot be opened: {str(e)[:300]}",
                             "last_error_at": now_iso(now), "last_ok_at": None, "summary": None})
    return 1


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

    try:
        ensure_dir(db)
        lock = lock_dir(db)
        if lock is None:
            log("another export holds the record's lock; this run does nothing")
            return 0
    except (RecordError, OSError) as e:
        return record_failed(status_to, now, e, log)
    try:
        conn = open_record(db)
    except (RecordError, OSError) as e:
        os.close(lock)
        return record_failed(status_to, now, e, log)
    try:
        error = None
        report: dict = {}
        try:
            report = export(conn, tempo, now, Path(args.recordings).expanduser(), dev_pipelines,
                            args.check_now, log)
        except TempoError as e:
            error = f"Tempo: {e}"
        published, pub_err = [], None
        if error is None:
            try:
                published = publish(conn, publish_to, now)
            except (OSError, sqlite3.Error) as e:
                pub_err = f"publishing failed: {type(e).__name__}: {getattr(e, 'strerror', None) or e}"
        st = state(conn)
        runs, max_seq = conn.execute("SELECT COUNT(*), MAX(seq) FROM runs").fetchone()
        body = {
            "ts": round(now, 3), "schema": STATUS_SCHEMA, "record_schema": SCHEMA, "publish_schema": PUBLISH_SCHEMA,
            "ok": error is None and pub_err is None,
            "last_ok_at": st["last_ok_at"], "last_ok_s": parse_iso(st["last_ok_at"]),
            "last_error": error or pub_err or st["last_error"],
            "last_error_at": now_iso(now) if (error or pub_err) else st["last_error_at"],
            "watermark_s": st["watermark_s"],
            "record": {"runs": runs, "max_seq": max_seq},
            "this_run": {**report, "published": len(published), "took_s": round(time.monotonic() - t0, 3)},
            "last_check": json.loads(st["last_check"]) if st["last_check"] else None,
            "summary": summary(conn, now),
        }
        serr = write_status(status_to, body)
    finally:
        os.close(lock)
        conn.close()
    if error:
        log(f"not read: {error}; the record and the copies are unchanged")
    else:
        log(f"listed {report['listed']}, new {report['inserted']}, changed {report['changed']} "
            f"(replaced {report['replaced']}), traces read {report['trace_reads']} "
            f"(failed {report['trace_read_failures']}), copies written {len(published)} of {len(publish_to)}"
            + (f"; problems: {'; '.join(report['problems'])}" if report.get("problems") else "")
            + (f"; {pub_err}" if pub_err else ""))
    for e in serr:
        log(f"status file not written: {e}")
    return 1 if (pub_err or serr) else 0


if __name__ == "__main__":
    sys.exit(main())
