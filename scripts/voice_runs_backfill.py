#!/usr/bin/env python3
"""Fill in the words of the early voice runs from the Whisper journal (work
item M30, step D). Python standard library only; run by hand, never by a
timer, with no test suite running (tests/conftest.py balances the record's
changes against the export unit's journal lines, and a backfill is not one).

The household runs of 2026-11-08 to 11-17 were traced before
TELEMETRY_RECORD_TEXT was on, so the record (scripts/voice_runs_export.py)
holds them with `heard` empty. Both Whisper units log every transcript to
the user journal, one line each:

    INFO:wyoming_faster_whisper.dispatch_handler:<the text, as Whisper returned it>

This joins those lines to the runs by time and writes the text with
heard_source = 'whisper_journal'. A row is filled only when the match is
certain:

- its `heard` is empty and it is not removed;
- exactly one transcript from the unit that served it (wyoming-whisper for
  a household run, wyoming-whisper-dev for a dev run, by `is_dev`) falls in
  its speech-to-text window: the voice.stt span of its raw_trace, else the
  whole run, widened by the audio join's slack (voice_label_sheet's
  JOIN_BEFORE_S before, JOIN_AFTER_S after), and the journal of that unit
  covers the window;
- that transcript's length equals the row's heard_chars (HA's
  voice.transcript_chars, counted on Whisper's text before voice_tracer
  strips it, so Whisper's leading space counts), or, for a row with no
  stored count, it is the only transcript from the unit within a minute of
  the window.

Anything else is left as it is and listed with its reason. A filled row
gets `heard` (the text stripped, as voice_tracer stores Tempo's),
heard_source, a raised seq and last_changed_at; heard_chars stays Tempo's.
Nothing is replaced, so nothing goes to run_changes. The exporter's rule 1
then holds the words: Tempo's empty text never replaces them, and its
hourly check compares only the values Tempo has.

The same pass joins the audio of rows the exporter stored "not tried"
(audio_join NULL after a try: older than seven days with no local
recording, the recording perhaps archived) against the recordings archive
on the NAS, read-only (--nas-recordings, default
/mnt/nas-backup/recordings: <device>/<pipeline>/<YYYY-MM>/<run>/<file>,
which voice_label_sheet.scan reads). The answer is the exporter's own:
voice_runs_export.audio_joins, imported, run over an in-memory copy of the
record's runs in which only those rows are untried, against the NAS root.
A row with a recording still under the local recordings directory in its
window is left (a set that straddles the archive's cut would be joined
against half its recordings). `certain` and `ambiguous` are stored with
audio_tried_at; audio_rel is the recording's name in HA's layout (the month
dropped), as for every other row. The audio columns raise no seq and are
not published.

Without --apply: a report, and the record is opened read-only (its bytes do
not change). With --apply: under the record's lock (waited for as --remove
waits for it) and in one BEGIN IMMEDIATE transaction. The copies are not
published here: the exporter's next run (a minute) publishes the filled
rows, as it does any raised seq.

Repeatable: a second --apply finds nothing to fill (the rows have text) and
nothing to join (the rows are tried), and writes nothing.

The output is run ids, counts, lengths and reasons, never a transcript.

    voice_runs_backfill.py                         (the report: what --apply would do)
    voice_runs_backfill.py --apply
    voice_runs_backfill.py --db S/voice_runs.db --journal S/whisper.json --nas-recordings S/nas --recordings S/rec

--journal FILE is a saved `journalctl --user -u wyoming-whisper.service -u
wyoming-whisper-dev.service -o json` export; without it this runs that
command itself, read-only.

Feature doc: docs/features/voice-dataset.md ("The backfill").
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import voice_runs_export as ve  # noqa: E402  (the lock, the record, the audio join: imported, not copied)

ls = ve.ls
HOUSEHOLD_UNIT, DEV_UNIT = "wyoming-whisper.service", "wyoming-whisper-dev.service"
UNIT_OF = {0: HOUSEHOLD_UNIT, 1: DEV_UNIT}
JOURNAL_CMD = ["journalctl", "--user", "-u", HOUSEHOLD_UNIT, "-u", DEV_UNIT, "-o", "json", "--no-pager"]
JOURNAL_TIMEOUT_S = 300
# dispatch_handler since wyoming-faster-whisper 3.x; handler before it.
TRANSCRIPT_RE = re.compile(r"INFO:wyoming_faster_whisper\.(?:dispatch_)?handler:(.*)", re.S)
ALONE_S = 60.0                  # a row with no stored count: the only transcript within this of its window
NAS_RECORDINGS = Path("/mnt/nas-backup/recordings")
SOURCE = "whisper_journal"


class JournalError(Exception):
    """The journal could not be read. Exit 1, nothing written."""


@dataclass
class Transcript:
    t: float                    # epoch seconds, when the line was logged (Whisper's transcription done)
    text: str


@dataclass
class Journal:
    source: str
    lines: dict = field(default_factory=dict)       # unit -> [Transcript], by time
    first: dict = field(default_factory=dict)       # unit -> the earliest entry of any kind
    last: dict = field(default_factory=dict)        # unit -> the latest
    unreadable: int = 0

    def between(self, unit: str, lo: float, hi: float) -> list[Transcript]:
        return [x for x in self.lines.get(unit, ()) if lo <= x.t <= hi]


def _message(e: dict) -> str | None:
    m = e.get("MESSAGE")
    if isinstance(m, list):                         # journald's form for a message that is not clean UTF-8
        try:
            return bytes(m).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return None
    return m if isinstance(m, str) else None


def parse_journal(lines, source: str) -> Journal:
    """The transcripts of the two Whisper units from journalctl's JSON lines,
    and how far back and forward each unit's journal reaches."""
    j = Journal(source)
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            unit = e.get("_SYSTEMD_USER_UNIT") or e.get("USER_UNIT")
            t = int(e.get("_SOURCE_REALTIME_TIMESTAMP") or e["__REALTIME_TIMESTAMP"]) / 1e6
        except (ValueError, TypeError, KeyError, AttributeError):
            j.unreadable += 1
            continue
        if unit not in (HOUSEHOLD_UNIT, DEV_UNIT):
            continue
        j.first[unit] = min(j.first.get(unit, t), t)
        j.last[unit] = max(j.last.get(unit, t), t)
        msg = _message(e)
        # The unit's own process (not systemd's lines about the unit).
        m = TRANSCRIPT_RE.fullmatch(msg) if msg is not None and e.get("_SYSTEMD_USER_UNIT") == unit else None
        if m:
            j.lines.setdefault(unit, []).append(Transcript(t, m.group(1)))
    for v in j.lines.values():
        v.sort(key=lambda x: x.t)
    return j


def read_journal(path: str | None) -> Journal:
    if path is not None:
        try:
            with open(path, "rb") as fh:
                return parse_journal(fh, path)
        except OSError as e:
            raise JournalError(f"{path}: {e.strerror or e}") from None
    try:
        p = subprocess.run(JOURNAL_CMD, capture_output=True, timeout=JOURNAL_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise JournalError(f"journalctl: {type(e).__name__}: {e}") from None
    if p.returncode != 0:
        raise JournalError(f"journalctl exited {p.returncode}")
    return parse_journal(p.stdout.splitlines(), "journalctl --user (the two Whisper units)")


# ═══ The words ══════════════════════════════════════════════════════════════

def stt_window(row: dict) -> tuple[float, float, str]:
    """The run's speech-to-text window, widened by the audio join's slack:
    the voice.stt span of its raw_trace, else the whole run."""
    lo, hi, basis = row["start_s"], row["end_s"], "whole run"
    if row.get("raw_trace"):
        try:
            stored = json.loads(row["raw_trace"])
            spans = ve.trace_spans({"trace": stored} if "resourceSpans" in stored else stored)
            sp = next(s for s in spans if s.get("name") == "voice.stt")
            lo, hi = int(sp["startTimeUnixNano"]) / 1e9, int(sp["endTimeUnixNano"]) / 1e9
            basis = "stt span"
        except (ValueError, TypeError, KeyError, StopIteration, ve.TempoError):
            pass
    return lo - ls.JOIN_BEFORE_S, hi + ls.JOIN_AFTER_S, basis


def decide(row: dict, j: Journal) -> tuple[str, str, str, str | None]:
    """(what, reason, detail, text): what is 'fill' for a certain match
    (text is the words to store), 'left' otherwise, 'has' for a row that
    already has text (detail: whether the journal agrees, the calibration
    of this rule on rows Tempo gave words). The reason is a short category
    for the counts; the detail carries lengths and times, never words."""
    if row["removed"] is not None:
        return "left", "removed", "", None
    unit = UNIT_OF[1 if row["is_dev"] else 0]
    other = UNIT_OF[0 if row["is_dev"] else 1]
    lo, hi, basis = stt_window(row)
    inside = j.between(unit, lo, hi)
    chars = row["heard_chars"]
    if not ve.empty(row["heard"]):
        if (len(inside) == 1 and (chars is None or len(inside[0].text) == chars)
                and inside[0].text.strip() == row["heard"]):
            return "has", "the journal agrees", "one transcript in the window, the same length and words", None
        detail = f"{len(inside)} transcript(s) from {unit} in the window"
        if len(inside) == 1:
            detail += (f"; journal {len(inside[0].text)} characters, row {chars}; words "
                       + ("the same" if inside[0].text.strip() == row["heard"] else "differ"))
        return "has", "the journal does not agree", detail, None
    if chars == 0:
        return "left", "Whisper heard nothing", "heard_chars 0", None
    widen = ALONE_S if chars is None else 0.0
    if unit not in j.first:
        return "left", "the journal does not cover the run", f"nothing from {unit}", None
    if j.first[unit] > lo - widen:
        return "left", "the journal does not cover the run", (
            f"{unit} from {iso(j.first[unit])}, the window opens {iso(lo - widen)}"), None
    if j.last[unit] < hi + widen:
        return "left", "the journal does not cover the run", (
            f"{unit} to {iso(j.last[unit])}, the window closes {iso(hi + widen)}"), None
    if not inside:
        n_other = len(j.between(other, lo, hi))
        return "left", "no transcript in the window", (
            f"none from {unit} ({basis})" + (f"; {n_other} from {other}" if n_other else "")), None
    if len(inside) > 1:
        return "left", "several transcripts in the window", f"{len(inside)} from {unit} ({basis})", None
    tx = inside[0]
    if chars is None:
        near = j.between(unit, lo - ALONE_S, hi + ALONE_S)
        if len(near) > 1:
            return "left", "no stored count, not alone", (
                f"{len(near) - 1} other transcript(s) from {unit} within {ALONE_S:g} s of the window"), None
        detail = f"no stored count; the only transcript from {unit} within {ALONE_S:g} s, {len(tx.text)} characters"
    elif len(tx.text) != chars:
        return "left", "length differs", f"journal {len(tx.text)} characters, row {chars}", None
    else:
        detail = f"journal {len(tx.text)} characters = row {chars}"
    if not tx.text.strip():
        return "left", "blank transcript", f"{len(tx.text)} characters, all space", None
    return "fill", "certain", f"{detail} ({basis}, {unit})", tx.text.strip()


# ═══ The audio, against the NAS layout ══════════════════════════════════════

def nas_audio(conn, local: Path, nas: Path, now: float) -> tuple[dict, dict, dict]:
    """The audio join of every row stored "not tried" (audio_join NULL,
    audio_tried_at set, not removed) against the NAS layout, by the
    exporter's audio_joins over an in-memory copy of the runs in which only
    those rows are untried. Returns ({run_id: (join, rel, bytes, mtime_ns)}
    for certain and ambiguous answers, {run_id: why} for the rest, the
    report). Reads the record; writes nothing."""
    targets = [dict(r) for r in conn.execute(
        f"SELECT {ve.JOIN_COLS} FROM runs WHERE audio_join IS NULL AND audio_tried_at IS NOT NULL "
        "AND removed IS NULL ORDER BY start_s, run_id")]
    rep: dict = {"not_tried": len(targets)}
    if not targets:
        return {}, {}, rep
    for root, what in ((local, "the local recordings directory"), (nas, "the NAS recordings archive")):
        try:
            if not stat.S_ISDIR(os.lstat(root).st_mode):
                rep["skipped"] = f"{what} {root} is not a directory"
                return {}, {}, rep
            os.listdir(root)
        except OSError as e:
            rep["skipped"] = f"{what} {root} cannot be read ({e.strerror or e})"
            return {}, {}, rep
    try:
        local_recs = ls.scan(local)
    except OSError as e:
        rep["skipped"] = f"the local recordings directory {local} cannot be read ({e.strerror or e})"
        return {}, {}, rep
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    try:
        mem.executescript(ve.DDL)
        cols = ", ".join(ve.RUN_COLUMNS)
        mem.executemany(f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(ve.RUN_COLUMNS))})",
                        [tuple(r) for r in conn.execute(f"SELECT {cols} FROM runs")])
        # Every row but the targets counts as tried, so audio_joins answers for these alone.
        mem.execute("UPDATE runs SET audio_tried_at = COALESCE(audio_tried_at, 'held by the backfill')")
        left: dict = {}
        ids = []
        for t in targets:
            _, near_local = ve._closure(mem, t, local_recs)
            if near_local:
                left[t["run_id"]] = f"{len(near_local)} recording(s) still under {local} in its window; left as it is"
            else:
                ids.append(t["run_id"])
        if ids:
            mem.execute("UPDATE runs SET audio_tried_at = NULL WHERE run_id IN (SELECT value FROM json_each(?))",
                        (json.dumps(ids),))
        answers, jrep = ve.audio_joins(mem, nas, now)
    finally:
        mem.close()
    if jrep.get("audio_not_tried"):
        rep["skipped"] = jrep["audio_not_tried"]
        return {}, {}, rep
    out = {}
    for rid in ids:
        j, rel, size, mtime = answers.get(rid, ("waiting", None, None, None))
        if j == "certain":
            parts = rel.split("/")
            if len(parts) == 5 and ls.ARCHIVE_MONTH_RE.fullmatch(parts[2]):
                rel = "/".join(parts[:2] + parts[3:])           # HA's layout, as every other row's audio_rel
            out[rid] = (j, rel, size, mtime)
        elif j == "ambiguous":
            out[rid] = (j, None, None, None)
        elif j == "waiting":
            left[rid] = "a run that could fit the same recording is younger than 15 minutes; try again later"
        else:
            left[rid] = "no recording on the NAS either"
    rep.update(certain=sum(1 for v in out.values() if v[0] == "certain"),
               ambiguous=sum(1 for v in out.values() if v[0] == "ambiguous"), left=len(left))
    return out, left, rep


# ═══ The pass ═══════════════════════════════════════════════════════════════

def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def plan(conn, j: Journal, local: Path, nas: Path | None, now: float) -> dict:
    rows = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY start_s, run_id")]
    words = [(r["run_id"], *decide(r, j)) for r in rows]
    audio, audio_left, arep = nas_audio(conn, local, nas, now) if nas is not None else ({}, {}, {"off": True})
    return {"words": words, "audio": audio, "audio_left": audio_left, "audio_report": arep}


def apply(conn, p: dict, now: float) -> tuple[int, int]:
    """The plan's writes, in the caller's transaction. Each guarded by the
    state it was planned on, so a row a person changed since is not touched.
    Returns (rows filled, rows joined)."""
    stamp = ve.now_iso(now)
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()[0]
    filled = joined = 0
    for rid, what, _, _, text in p["words"]:
        if what != "fill":
            continue
        n = conn.execute("UPDATE runs SET heard = ?, heard_source = ?, seq = ?, last_changed_at = ? "
                         "WHERE run_id = ? AND (heard IS NULL OR heard = '') AND removed IS NULL",
                         (text, SOURCE, seq + 1, stamp, rid)).rowcount
        if n:
            seq += 1
            filled += 1
    for rid, (j, rel, size, mtime) in p["audio"].items():
        joined += conn.execute("UPDATE runs SET audio_join = ?, audio_rel = ?, audio_bytes = ?, audio_mtime_ns = ?, "
                               "audio_tried_at = ? WHERE run_id = ? AND audio_join IS NULL "
                               "AND audio_tried_at IS NOT NULL AND removed IS NULL",
                               (j, rel, size, mtime, stamp, rid)).rowcount
    return filled, joined


def open_read_only(db: Path) -> sqlite3.Connection:
    """The record for the report: the exporter's checks of mode and owner,
    opened mode=ro (rollback journal: a reader leaves no file beside it)."""
    ve._private(db.parent, "the record's directory", 0o700)
    ve._private(db, "the record", 0o600)
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        conn.close()
        raise
    if v != ve.SCHEMA or any(t not in names for t in ve.TABLES):
        conn.close()
        raise ve.RecordError(f"{db} is not a voice-run record of schema {ve.SCHEMA} (schema {v})")
    return conn


def report(p: dict, j: Journal, log, applied: tuple[int, int] | None) -> None:
    for unit in (HOUSEHOLD_UNIT, DEV_UNIT):
        n = len(j.lines.get(unit, ()))
        log(f"journal {unit}: {n} transcript line(s)"
            + (f", entries {iso(j.first[unit])} to {iso(j.last[unit])}" if unit in j.first else ", no entries"))
    if j.unreadable:
        log(f"journal: {j.unreadable} line(s) not read (not JSON, or no time)")
    left: dict = {}
    fills = agree = disagree = 0
    for rid, what, why, detail, _ in p["words"]:
        tag = {"fill": "fill", "left": "left", "has": "text"}[what]
        log(f"{tag:5} {rid}  {why}" + (f": {detail}" if detail else ""))
        if what == "fill":
            fills += 1
        elif what == "has":
            agree, disagree = (agree + 1, disagree) if why == "the journal agrees" else (agree, disagree + 1)
        else:
            left[why] = left.get(why, 0) + 1
    log(f"words: {len(p['words'])} row(s); {'filled' if applied else 'would fill'} "
        f"{applied[0] if applied else fills}; already with text {agree + disagree} (the journal agrees on "
        f"{agree}); left {sum(left.values())}"
        + (": " + ", ".join(f"{why} {n}" for why, n in sorted(left.items())) if left else ""))
    a = p["audio_report"]
    if a.get("off"):
        log("audio: not tried (--no-audio)")
        return
    for rid, (jn, _, size, _) in sorted(p["audio"].items()):
        log(f"audio {rid}  {jn}" + (f", {size} bytes" if size is not None else ""))
    for rid, why in sorted(p["audio_left"].items()):
        log(f"audio {rid}  left: {why}")
    if a.get("skipped"):
        log(f"audio: {a['not_tried']} row(s) stored not tried; none joined: {a['skipped']}")
    else:
        log(f"audio: {a['not_tried']} row(s) stored not tried; {'joined' if applied else 'would join'} "
            f"{applied[1] if applied else a.get('certain', 0) + a.get('ambiguous', 0)} "
            f"({a.get('certain', 0)} certain, {a.get('ambiguous', 0)} ambiguous); left {a.get('left', 0)}")


def main(argv=None, now: float | None = None, out=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help=f"the record (default KRONK_VOICE_DB, else {ve.DEFAULT_DB})")
    ap.add_argument("--journal", metavar="FILE", help="a saved journalctl -o json export (default: read the journal)")
    ap.add_argument("--apply", action="store_true", help="write; without it, the report only")
    ap.add_argument("--recordings", default=str(ve.RECORDINGS), help="HA's recordings (default %(default)s)")
    ap.add_argument("--nas-recordings", default=str(NAS_RECORDINGS),
                    help="the recordings archive, read-only (default %(default)s)")
    ap.add_argument("--no-audio", action="store_true", help="leave the audio join out")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    now = time.time() if now is None else now

    def log(msg: str) -> None:
        print(f"voice_runs_backfill: {msg}", file=out, flush=True)

    db = Path(args.db or os.environ.get("KRONK_VOICE_DB") or ve.DEFAULT_DB).expanduser()
    try:
        ve.check_paths(db, [], [])
    except ve.Refused as e:
        log(f"REFUSED: {e}")
        return 2
    if not os.path.lexists(db):
        log(f"FAILED: there is no record at {db}")
        return 1
    try:
        j = read_journal(args.journal)
    except JournalError as e:
        log(f"FAILED: the journal could not be read: {e}; nothing written")
        return 1
    local = Path(args.recordings).expanduser()
    nas = None if args.no_audio else Path(args.nas_recordings).expanduser()
    if not args.apply:
        log("dry run (no --apply): the record is opened read-only and nothing is written")
        try:
            conn = open_read_only(db)
        except (ve.RecordError, OSError, sqlite3.Error) as e:
            log(f"FAILED: the record cannot be opened: {e}")
            return 1
        try:
            p = plan(conn, j, local, nas, now)
        except sqlite3.Error as e:
            log(f"FAILED: the record could not be read: {type(e).__name__}: {e}")
            return 1
        finally:
            conn.close()
        report(p, j, log, None)
        return 0

    try:
        ve._private(db.parent, "the record's directory", 0o700)
        lock = ve.lock_dir(db, ve.LOCK_WAIT_S)
    except (ve.RecordError, OSError) as e:
        log(f"FAILED: the record cannot be opened: {e}")
        return 1
    if lock is None:
        log(f"FAILED: another export held the record's lock for {ve.LOCK_WAIT_S:g} s; nothing written. Run it again")
        return 1
    try:
        try:
            conn = ve.open_record(db)
        except (ve.RecordError, OSError) as e:
            log(f"FAILED: the record cannot be opened: {e}")
            return 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                p = plan(conn, j, local, nas, now)
                done = apply(conn, p, now)
                conn.execute("COMMIT" if any(done) else "ROLLBACK")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as e:
            log(f"FAILED: the record could not be written: {type(e).__name__}: {e}; nothing written")
            return 1
        finally:
            conn.close()
    finally:
        os.close(lock)
    report(p, j, log, done)
    if done[0]:
        log("the exporter's next run publishes the filled rows (their seq was raised)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
