#!/usr/bin/env python3
"""Work-item state, the queue, leftovers and decisions owed to Drew, in one
SQLite database (work item M33 step 1, docs/sprint/M33-criteria.md,
docs/features/work-state.md). Run on the host by the coordinator; called by
scripts/agent.py for agent steps. This file is the only writer.

    work.py [--db PATH] [--repo DIR] <command> ...

    init                                   an empty database
    add --title T [--tier N] [--criteria PATH] [--after ITEM] [--id ID]
    show ITEM | list [--state S] | queue
    start ITEM                             queued -> active
    move ITEM POSITION | hold ITEM --why T | unhold ITEM | drop ITEM --why T
    after ITEM (OTHER | --clear)           ITEM may not start or move before OTHER is promoted
    note ITEM [--next T] [--waits drew|outside|item|none] [--waits-note T] [--tier N]
              [--criteria PATH] [--title T]
    step add ITEM KIND [--round N] [--name X] [--report PATH]     a coordinator step, file committed
    step start ITEM KIND [--round N] --agent NAME [--model M] [--report PATH] [--at TIME]
    step finish|stop|refresh|note-gone ITEM KIND [--round N] [--name X] [--why T]
    grade ITEM pass|fail [--kind K] [--round N]
    promote ITEM... (--tag T | --no-tag) --deployed yes|no [--verified PATH] [--untested T] [--followup] [--at TIME]
    promote --label T (--tag T | --no-tag) --deployed yes|no --verified PATH [--at TIME]   a deploy with no item
    matter add KIND --owner drew|coordinator --text T [--item ITEM] [--blocks] [--source S]
    matter close ID --how T | matter void ID --why T | matter list [--open] [--kind K] [--item ITEM]
    log [--item ITEM] [--day YYYY-MM-DD] [--limit N]
    check                                  read-only: rows against git, herdr, the files
    resume                                 plain text: where the work stands
    dump [--out FILE] | restore-dump [--from FILE]
    import-legacy --at SHA [--seed FILE] [--compare WORK_JSON]

Every answer is JSON lines on stdout except `resume`, which is plain text.
A refusal is one line on stderr and exit 1; a database that is missing,
empty, corrupt, locked or from a newer schema is one line on stderr naming
which, and exit 2; `check` exits 3 when it found a disagreement.

Where: --db, else KRONK_WORK_DB, else ~/.local/state/kronk-work/work.db,
outside every container's mount (mode 600, directory 700). It is never made
as a side effect: `init`, `import-legacy` and `restore-dump` make it; every
other command, and agent.py, says it is missing. The database records the
repo it describes (meta `repo`); agent.py writes only when the launch repo
is that repo, so a launch in a temporary repo never touches it.

Every writing command is one BEGIN IMMEDIATE transaction (busy timeout 10 s
here, 2 s from agent.py) and one log row (time, who, the command line, the
changed rows as JSON); the log is append-only by trigger. After every write
the whole database is written as SQL to <db>.dump.sql (temp file, rename,
mode 600) while the write lock is still held; `restore-dump` rebuilds from
it.

Evidence is copied, never typed: a step's verdict is read from its
committed report by scripts/work_export.py's own verdicts(), its commit is
the last commit that touched the report, a tag must be one git lists. The
stage of an item is never stored: `resume` computes it with work_export's
stage() from the step rows, the promotion row, the tags and herdr, and
item_stage() around it (a grading read before a verdict, a step off HEAD's
history not counted). Stored text is printed on one line, control bytes
escaped; the writing commands refuse text with a newline or a control byte.

Git is read lock-free (work_export's git(), --no-optional-locks). The only
herdr call is `herdr agent list`, through work_export. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCHEMA = 1
BUSY_S = 10.0                  # work.py's busy timeout
AGENT_BUSY_S = 2.0             # agent.py's
STATES = ("queued", "held", "active", "promoted", "dropped")
KINDS = ("criteria", "review", "build", "test", "fix", "other")
AGENT_KINDS = ("review", "build", "test", "fix")
MATTER_KINDS = ("leftover", "decision", "escape")
OWNERS = ("drew", "coordinator")
WAITS = ("drew", "outside", "item")
ID_RE = re.compile(r"^M(\d+)([a-z]?)(?:-step(\d+))?$")
VERDICT_WORDS = ("PASSES", "FAILS", "NOT DETERMINED")
SOURCE_MAX = 4000              # Drew's words whole (longest Source: 722, M33); a bound only so a runaway paragraph can't swell work.json
ESCAPE_WINDOW = 10             # escapes are counted within the last ten promoted items (WORKFLOW.md)

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE item (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  tier        INTEGER CHECK (tier IN (1,2,3)),
  state       TEXT NOT NULL CHECK (state IN ('queued','held','active','promoted','dropped')),
  position    INTEGER,
  after_item  TEXT REFERENCES item(id),
  criteria    TEXT,
  next_step   TEXT,
  waits_on    TEXT CHECK (waits_on IN ('drew','outside','item')),
  waits_note  TEXT,
  held_why    TEXT,
  ended_why   TEXT,
  created_at  TEXT NOT NULL,
  ended_at    TEXT,
  CHECK ((position IS NOT NULL) = (state IN ('queued','held'))),
  CHECK ((waits_on IS NULL) = (waits_note IS NULL)),
  CHECK ((held_why IS NOT NULL) = (state = 'held'))
);
CREATE TABLE step (
  id          INTEGER PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES item(id),
  kind        TEXT NOT NULL CHECK (kind IN ('criteria','review','build','test','fix','other')),
  round       INTEGER NOT NULL DEFAULT 1,
  name        TEXT NOT NULL DEFAULT '',
  by          TEXT NOT NULL CHECK (by IN ('agent','coordinator')),
  agent       TEXT,
  model       TEXT,
  state       TEXT NOT NULL CHECK (state IN ('started','finished','stopped')),
  report      TEXT,
  commit_sha  TEXT,
  verdict     TEXT CHECK (verdict IN ('PASSES','FAILS','NOT DETERMINED')),
  graded      TEXT CHECK (graded IN ('pass','fail')),
  note        TEXT,
  started_at  TEXT,
  ended_at    TEXT,
  UNIQUE (item_id, kind, round, name),
  CHECK (state = 'started' OR ended_at IS NOT NULL)
);
CREATE TABLE promotion (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL DEFAULT 'promotion' CHECK (kind IN ('promotion','followup')),
  tag         TEXT,
  commit_sha  TEXT,
  at          TEXT NOT NULL,
  deployed    INTEGER CHECK (deployed IN (0,1)),
  verified    TEXT NOT NULL,
  untested    TEXT,
  note        TEXT
);
CREATE TABLE promotion_item (
  promotion_id INTEGER NOT NULL REFERENCES promotion(id),
  item_id      TEXT NOT NULL REFERENCES item(id),
  PRIMARY KEY (promotion_id, item_id)
) WITHOUT ROWID;
CREATE TABLE open_thing (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('leftover','decision','escape')),
  item_id     TEXT REFERENCES item(id),
  owner       TEXT CHECK (owner IN ('drew','coordinator')),
  text        TEXT NOT NULL,
  blocks      INTEGER NOT NULL DEFAULT 0 CHECK (blocks IN (0,1)),
  source      TEXT,
  opened_at   TEXT NOT NULL,
  closed_at   TEXT,
  closed_how  TEXT,
  CHECK ((closed_at IS NULL) = (closed_how IS NULL))
);
CREATE TABLE log (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, cmd TEXT NOT NULL, changes TEXT NOT NULL
);
CREATE TRIGGER log_no_update BEFORE UPDATE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TRIGGER log_no_delete BEFORE DELETE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
"""
TABLES = ("meta", "item", "step", "promotion", "promotion_item", "open_thing", "log")


class WorkError(Exception):
    """kind: missing, empty, corrupt, locked, newer (the database: exit 2),
    refused (exit 1)."""

    DB_KINDS = ("missing", "empty", "corrupt", "locked", "newer")

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(f"{kind}: {message}" if kind in self.DB_KINDS else message)


def refuse(msg: str) -> WorkError:
    return WorkError("refused", msg)


# ═════════════════════════════ work_export ═════════════════════════════════

_WE = None


def work_export():
    """scripts/work_export.py, loaded from beside this file with no bytecode
    written: its stage(), verdicts(), handoff_of(), git and herdr readers."""
    global _WE
    if _WE is None:
        path = HERE / "work_export.py"
        before = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("kronk_work_export_for_work_tool", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            raise refuse(f"cannot load {path} ({type(e).__name__})") from None
        finally:
            sys.dont_write_bytecode = before
        _WE = mod
    return _WE


# ═════════════════════════════ small helpers ═══════════════════════════════

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc(text: str | None) -> str | None:
    """An ISO time (any offset, or none: local) as UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not text:
        return None
    try:
        t = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise refuse(f"time {text!r} is not an ISO time (2026-11-24T10:00:00-0500)") from None
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(iso: str | None) -> str:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return str(iso)


def safe_utc(text) -> str | None:
    try:
        return to_utc(text) if isinstance(text, str) else None
    except WorkError:
        return None


def minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(max(0.0, (dt.datetime.now(dt.timezone.utc) - t).total_seconds()) / 60, 1)


_STARTED = now_iso()


def who() -> str:
    """The writer for the log: KRONK_WORK_WHO, else the Claude Code session
    the environment names, else this process."""
    w = os.environ.get("KRONK_WORK_WHO")
    if w:
        return w[:200]
    s = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if s:
        return f"session {s[:80]}"
    return f"pid {os.getpid()} started {_STARTED}"


def norm(item: str) -> str:
    i = work_export().norm_id(item)
    if not ID_RE.match(i):
        raise refuse(f"{item!r} is not an item id (M33, M4b, M21-step2)")
    return i


def id_num(i: str) -> int | None:
    m = ID_RE.match(i)
    return int(m.group(1)) if m else None


def default_db() -> Path:
    d = os.environ.get("KRONK_WORK_DB")
    return Path(d) if d else Path.home() / ".local" / "state" / "kronk-work" / "work.db"


def dump_path(db: Path) -> Path:
    return db.with_name(db.name + ".dump.sql")


# json.dumps escapes U+0000..U+001F itself; these it passes through raw with
# ensure_ascii=False, and a terminal acts on some of them (C1 controls).
_JSON_RAW_CONTROLS = re.compile("[\x7f-\x9f\u2028\u2029]")


def emit(out, obj) -> None:
    """One JSON line: no stored text can break it into two or reach the
    terminal as a control byte (M33 fix round 1, S3)."""
    text = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    out.write(_JSON_RAW_CONTROLS.sub(lambda m: f"\\u{ord(m.group()):04x}", text) + "\n")


def is_control(ch: str) -> bool:
    """A control byte or a line break: C0, DEL, C1, U+2028, U+2029."""
    return unicodedata.category(ch) in ("Cc", "Zl", "Zp")


_SHORT_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def one_line(text) -> str:
    """Stored text as it is printed in `resume`: on one line, every control
    byte escaped (\\n, \\x1b, \\u2028), so no row can pass for the tool's
    own headings or fix lines (S3)."""
    s = "" if text is None else str(text)
    if not any(is_control(ch) for ch in s):
        return s
    out = []
    for ch in s:
        if not is_control(ch):
            out.append(ch)
        elif ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x100:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(f"\\u{ord(ch):04x}")
    return "".join(out)


def plain(option: str, value: str | None) -> str | None:
    """Refuse text that holds a newline or a control byte (S3): what is
    stored is printed on one line, and a multi-line paste is refused here
    rather than escaped later."""
    if value is None:
        return None
    bad = next((ch for ch in value if is_control(ch)), None)
    if bad is not None:
        raise refuse(f"{option} holds a newline or a control byte ({one_line(bad)}); give it on one line")
    return value


def rowd(r) -> dict | None:
    return None if r is None else {k: r[k] for k in r.keys()}


# ════════════════════════════════ git ══════════════════════════════════════

def git(repo: Path, *args: str) -> tuple[int | None, str]:
    rc, out, _ = work_export().git(repo, *args)
    return rc, out


def git_bytes(repo: Path, args: list[str], data: bytes | None = None, timeout: float = 30) -> bytes:
    p = subprocess.run(["git", "--no-optional-locks", *args], cwd=repo, input=data, capture_output=True,
                       timeout=timeout, env={**os.environ, **work_export().GIT_ENV})
    if p.returncode != 0:
        raise refuse(f"git {args[0]} failed: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return p.stdout


def cat_files(repo: Path, specs: list[str]) -> dict[str, str | None]:
    """{spec: text or None (missing)} for '<rev>:<path>' specs, one git call."""
    if not specs:
        return {}
    raw = git_bytes(repo, ["cat-file", "--batch"], ("\n".join(specs) + "\n").encode())
    out: dict = {}
    pos = 0
    for spec in specs:
        nl = raw.index(b"\n", pos)
        header = raw[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        if header.endswith(" missing") or header.endswith(" ambiguous"):
            out[spec] = None
            continue
        size = int(header.split()[2])
        out[spec] = raw[pos:pos + size].decode("utf-8", "replace")
        pos += size + 1
    return out


def head_sha(repo: Path) -> str | None:
    rc, out = git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    return out.strip() if rc == 0 and out.strip() else None


def resolve(repo: Path, rev: str) -> str:
    rc, out = git(repo, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
    if rc != 0 or not out.strip():
        raise refuse(f"git does not know the commit {rev!r}")
    return out.strip()


def commit_time(repo: Path, rev: str) -> str:
    rc, out = git(repo, "log", "-1", "--format=%cI", rev)
    if rc != 0 or not out.strip():
        raise refuse(f"git cannot date {rev!r}")
    return to_utc(out.strip())


def tag_commits(repo: Path, merged: str | None = None) -> dict[str, str]:
    """{tag: commit} for every tag (or those reachable from `merged`)."""
    args = ["for-each-ref", "refs/tags", "--format=%(refname:short)%09%(*objectname)%09%(objectname)"]
    if merged:
        args.insert(2, f"--merged={merged}")
    rc, out = git(repo, *args)
    if rc != 0:
        raise refuse("git for-each-ref failed")
    tags = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0]:
            tags[parts[0]] = parts[1] or parts[2]
    return tags


def head_commits(repo: Path) -> set | None:
    """Every commit in HEAD's history (None: git could not say)."""
    rc, out = git(repo, "rev-list", "HEAD")
    return set(out.split()) if rc == 0 else None


def in_head(repo: Path, path: str) -> bool:
    rc, _ = git(repo, "cat-file", "-e", f"HEAD:{path}")
    return rc == 0


def report_evidence(repo: Path, path: str, item: str, kind: str) -> tuple[str, str, str | None, str | None]:
    """(commit, its time, verdict, second subject note) for a report
    committed in HEAD: the last commit that touched it, and the verdict
    work_export reads (test steps only)."""
    if not in_head(repo, path):
        raise refuse(f"{path} is not committed in HEAD; commit it first")
    rc, out = git(repo, "log", "-1", "--format=%H%x09%cI", "HEAD", "--", path)
    if rc != 0 or "\t" not in out:
        raise refuse(f"git log cannot find the commit of {path}")
    sha, _, when = out.strip().partition("\t")
    verdict = note = None
    if kind == "test":
        text = cat_files(repo, [f"HEAD:{path}"])[f"HEAD:{path}"] or ""
        verdict, note = verdict_of(text, item)
    return sha, to_utc(when), verdict, note


def verdict_of(text: str, item: str) -> tuple[str | None, str | None]:
    found = work_export().verdicts(text[:65536].splitlines(), item)
    own = [v for v in found if v["subject"] == "item"] or found[:1]
    others = [f"{v['subject']} {v['verdict']}" for v in found if own and v is not own[0]]
    return (own[0]["verdict"] if own else None), ("; ".join(others) or None)


def default_report(item: str, kind: str, rnd: int, name: str = "") -> str | None:
    base = f"docs/sprint/{item}-"
    if kind == "criteria":
        return base + "criteria.md"
    if kind == "review":
        return base + "review.md"
    if kind == "build":
        return base + "build.md"
    if kind == "test":
        return base + ("e2e.md" if rnd == 1 else f"e2e{rnd}.md")
    if kind == "fix":
        return base + f"fix{rnd}.md"
    return base + f"{name}.md" if name else None


# ════════════════════════════ the database ═════════════════════════════════

def classify(e: sqlite3.Error, timeout: float) -> WorkError:
    msg = str(e)
    if isinstance(e, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg):
        return WorkError("locked", f"still locked after {timeout:g} s ({msg})")
    if isinstance(e, sqlite3.DatabaseError):
        return WorkError("corrupt", msg)
    return WorkError("corrupt", f"{type(e).__name__}: {msg}")


def connect(path: Path, timeout: float) -> sqlite3.Connection:
    """Open an existing database; never makes one."""
    if not path.exists():
        raise WorkError("missing", f"no database at {path} (`work.py init` or `import-legacy` makes it)")
    if not path.is_file():
        raise WorkError("corrupt", f"{path} is not a regular file")
    if path.stat().st_size == 0:
        raise WorkError("empty", f"{path} has no schema (an empty file)")
    try:
        conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error as e:
        raise classify(e, timeout) from None
    return conn


def validate(conn: sqlite3.Connection, path: Path) -> dict:
    """Inside a transaction: the meta rows, or why the database is unusable."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta" not in names:
        raise WorkError("empty", f"{path} has no schema (no meta table)")
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    try:
        v = int(meta.get("schema_version", ""))
    except ValueError:
        raise WorkError("corrupt", f"{path}: meta has no schema_version") from None
    if v > SCHEMA:
        raise WorkError("newer", f"{path} is schema {v}; this tool knows {SCHEMA}: use the newer work.py")
    missing = [t for t in TABLES if t not in names]
    if missing:
        raise WorkError("corrupt", f"{path}: tables missing: {', '.join(missing)}")
    return meta


class Read:
    """A read transaction: one snapshot, the meta checked."""

    def __init__(self, path: Path, timeout: float | None = None, deep: bool = False):
        self.path, self.timeout, self.deep = path, BUSY_S if timeout is None else timeout, deep

    def __enter__(self):
        self.conn = connect(self.path, self.timeout)
        try:
            self.conn.execute("BEGIN")
            self.meta = validate(self.conn, self.path)
            if self.deep:
                bad = [r[0] for r in self.conn.execute("PRAGMA quick_check")]
                if bad != ["ok"]:
                    raise WorkError("corrupt", f"{self.path}: quick_check: {'; '.join(bad[:3])}")
        except sqlite3.Error as e:
            self.conn.close()
            raise classify(e, self.timeout) from None
        except WorkError:
            self.conn.close()
            raise
        return self

    def __exit__(self, et, ev, tb):
        try:
            self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        self.conn.close()
        if isinstance(ev, sqlite3.Error):
            raise classify(ev, self.timeout) from None
        return False

    def one(self, sql, *args):
        return rowd(self.conn.execute(sql, args).fetchone())

    def all(self, sql, *args):
        return [rowd(r) for r in self.conn.execute(sql, args)]


class Tx(Read):
    """One writing command: BEGIN IMMEDIATE, the changes, one log row, the
    dump written while the lock is held, COMMIT. A command that changed
    nothing leaves no log row and no dump."""

    def __init__(self, path: Path, cmd: str, timeout: float | None = None, writer: str | None = None):
        super().__init__(path, timeout)
        self.cmd, self.writer, self.changes, self.items = cmd, writer or who(), [], set()
        self.dump_error = None

    def __enter__(self):
        self.conn = connect(self.path, self.timeout)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.meta = validate(self.conn, self.path)
        except sqlite3.Error as e:
            self.conn.close()
            raise classify(e, self.timeout) from None
        except WorkError:
            self.conn.close()
            raise
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None and self.changes:
                self.conn.execute("INSERT INTO log (at, who, cmd, changes) VALUES (?,?,?,?)",
                                  (now_iso(), self.writer, self.cmd,
                                   json.dumps({"items": sorted(self.items), "rows": self.changes},
                                              ensure_ascii=False, separators=(",", ":"))))
                self.dump_error = write_dump(self.conn, dump_path(self.path))
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        except sqlite3.Error as e:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self.conn.close()
            write_dump_after(self.path)
            raise classify(e, self.timeout) from None
        self.conn.close()
        if self.dump_error:
            # The change is committed; only its copy beside the database is
            # behind. Said at the write, exit unchanged (S7).
            print(f"{self.cmd.split(' ')[0]}: the change is committed, but the dump {dump_path(self.path)} "
                  f"was not written ({one_line(self.dump_error)[:200]}); `python3 scripts/work.py dump` "
                  f"writes it again", file=sys.stderr, flush=True)
        if isinstance(ev, sqlite3.IntegrityError):
            raise refuse(f"the database refused the change ({ev})") from None
        if isinstance(ev, sqlite3.Error):
            raise classify(ev, self.timeout) from None
        return False

    def _note(self, table: str, row: dict | None):
        for k in ("item_id",) + (("id",) if table == "item" else ()):
            if row and row.get(k):
                self.items.add(row[k])

    def insert(self, table: str, **vals) -> int:
        cols = list(vals)
        cur = self.conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [vals[c] for c in cols])
        new = dict(vals)
        if table not in ("meta", "promotion_item", "item"):
            new = {"id": cur.lastrowid, **vals}
        self.changes.append({"t": table, "op": "insert", "new": new})
        self._note(table, new)
        return cur.lastrowid

    def update(self, table: str, key: dict, **vals) -> bool:
        where = " AND ".join(f"{k} = ?" for k in key)
        old = self.one(f"SELECT * FROM {table} WHERE {where}", *key.values())
        if old is None:
            raise refuse(f"no {table} row {key}")
        diff = {k: v for k, v in vals.items() if old.get(k) != v}
        if not diff:
            return False
        self.conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in diff)} WHERE {where}",
                          [*diff.values(), *key.values()])
        self.changes.append({"t": table, "op": "update", "key": key,
                             "old": {k: old.get(k) for k in diff}, "new": diff})
        self._note(table, old)
        return True


def write_dump(conn: sqlite3.Connection, path: Path) -> str | None:
    """The database as SQL, beside it: temp file, rename, mode 600. Returns
    why it failed, or None. The write is not undone by a failed dump."""
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for line in conn.iterdump():
                    f.write(line + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except (OSError, sqlite3.Error) as e:
        return f"{type(e).__name__}: {e}"
    return None


def write_dump_after(db: Path) -> None:
    """After a failed commit: the dump again from what is in the file."""
    try:
        conn = sqlite3.connect(str(db), timeout=1)
        try:
            write_dump(conn, dump_path(db))
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def missing_dirs(d: Path) -> list[Path]:
    """The directories that mkdir(parents=True) would make, deepest first."""
    out = []
    while not d.exists() and d != d.parent:
        out.append(d)
        d = d.parent
    return out


def remove_dirs(dirs: list[Path]) -> None:
    """Remove directories this command made, if they are still empty."""
    for d in dirs:
        try:
            d.rmdir()
        except OSError:
            return


def create(path: Path, repo: Path, how: str) -> None:
    """A new database with the schema and its meta rows; refused if the
    file exists. Directory 700, file 600, WAL."""
    if path.exists() or Path(str(path) + "-wal").exists():
        raise refuse(f"{path} exists; not replaced")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise refuse(f"{path} exists; not replaced") from None
    os.close(fd)
    os.chmod(path, 0o600)
    try:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(DDL)
        conn.execute("BEGIN")
        for k, v in (("schema_version", str(SCHEMA)), ("repo", os.path.realpath(repo)),
                     ("created_at", now_iso()), ("created_by", how)):
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (k, v))
        conn.execute("COMMIT")
        conn.close()
    except sqlite3.Error:
        path.unlink(missing_ok=True)
        raise


# ═══════════════════════════════ items ═════════════════════════════════════

def get_item(tx: Read, item: str) -> dict:
    row = tx.one("SELECT * FROM item WHERE id = ?", item)
    if row is None:
        raise refuse(f"no item {item} (`work.py list` shows them)")
    return row


def renumber(tx: Tx) -> None:
    """Queued and held items numbered 1..n in their order."""
    rows = tx.all("SELECT id, position FROM item WHERE state IN ('queued','held') ORDER BY position, id")
    for n, r in enumerate(rows, start=1):
        if r["position"] != n:
            tx.update("item", {"id": r["id"]}, position=n)


def next_number(tx: Read) -> int:
    nums = [id_num(r["id"]) or 0 for r in tx.all("SELECT id FROM item")]
    return max(nums, default=0) + 1


def check_after(tx: Read, item: str, other: str | None) -> None:
    seen = {item}
    cur = other
    while cur:
        if cur in seen:
            raise refuse(f"{item} after {other} makes a loop")
        seen.add(cur)
        row = tx.one("SELECT after_item FROM item WHERE id = ?", cur)
        if row is None:
            raise refuse(f"no item {cur}")
        cur = row["after_item"]


def after_blocks(tx: Read, it: dict) -> str | None:
    """Why the item may not start or move: the one it follows is not promoted."""
    if not it["after_item"]:
        return None
    other = tx.one("SELECT state FROM item WHERE id = ?", it["after_item"])
    if other and other["state"] == "promoted":
        return None
    return f"{it['id']} follows {it['after_item']}, which is {other['state'] if other else 'unknown'}, not promoted"


# ═══════════════════════════ stage, from rows ══════════════════════════════

def counted_steps(steps: list[dict], commits: set | None = None) -> list[dict]:
    """The finished steps the stage is computed from, in order. A step
    whose commit is not in HEAD's history is not counted (Appendix C row 5,
    S8); `commits` None (git not read) counts every finished step."""
    done = [s for s in steps if s["state"] == "finished" and in_branch(s.get("commit_sha"), commits) is not False]
    rank = work_export().step_rank
    done.sort(key=lambda s: (s["ended_at"] or "", rank(s["kind"], s["round"]), s["id"]))
    return done


def stage_steps(steps: list[dict], commits: set | None = None) -> list[dict]:
    """Counted steps in the shape work_export.stage() reads."""
    out = []
    for s in counted_steps(steps, commits):
        out.append({"kind": s["kind"], "n": s["round"] if s["kind"] in ("test", "fix") else None,
                    "file": Path(s["report"]).name if s["report"] else (s["name"] or s["kind"]),
                    "verdicts": [{"subject": "item", "verdict": s["verdict"]}] if s["verdict"] else []})
    return out


def item_stage(steps: list[dict], promoted: bool, tag: bool, agents: list[dict] | None,
               item: str | None = None, commits: set | None = None) -> dict:
    """work_export.stage() (not edited here: the page runs from it) fed
    from rows, and three things around it:

    - a test the coordinator graded reads its grading before its verdict
      (S1, review Appendix B): graded pass is "to promote", graded fail
      "waiting for the coordinator", the verdict and the grading both in
      the detail;
    - an item with no finished step says so (S2), not "files written, not
      committed";
    - the `item-<id>` of the tag detail is filled in (N7)."""
    we = work_export()
    counted = counted_steps(steps, commits)
    st = we.stage(stage_steps(steps, commits), tag, promoted, agents)
    if st["by"] == "files":
        last = counted[-1] if counted else None
        if last is None:
            st["detail"] = "no finished step recorded"
        elif last["kind"] == "test" and last.get("graded"):
            key = "to_promote" if last["graded"] == "pass" else "waiting"
            st.update(stage=key, words=we.STAGE_WORDS[key],
                      detail=f"test {last['round']}: {last['verdict'] or 'verdict not read'}, "
                             f"graded {last['graded']}")
    if item and st.get("detail") and "item-<id>" in st["detail"]:
        st["detail"] = st["detail"].replace("item-<id>", f"item-{item}")
    return st


# ══════════════════════════════ commands ═══════════════════════════════════

class Ctx:
    def __init__(self, db: Path, repo: Path, out, argv: list[str]):
        self.db, self.repo, self.out = db, repo, out
        self.cmd = "work.py " + " ".join(shlex.quote(a) for a in argv)

    def tx(self) -> Tx:
        return Tx(self.db, self.cmd)

    def run(self, *args: str) -> str:
        """A fix line that runs as printed from the repo root: `python3
        scripts/work.py`, with --db and --repo when this run was given ones
        that the default would not find (S5)."""
        parts = ["python3", "scripts/work.py"]
        if os.path.abspath(self.db) != os.path.abspath(default_db()):
            parts += ["--db", os.path.abspath(self.db)]
        if os.path.realpath(self.repo) != os.path.realpath(REPO):
            parts += ["--repo", os.path.realpath(self.repo)]
        return " ".join(shlex.quote(str(x)) for x in [*parts, *args])

    def git_run(self, *args: str) -> str:
        """A git fix line, run from the repo root, for this run's repo."""
        parts = ["git"]
        if os.path.realpath(self.repo) != os.path.realpath(REPO):
            parts += ["-C", os.path.realpath(self.repo)]
        return " ".join(shlex.quote(str(x)) for x in [*parts, *args])

    def emit(self, obj):
        emit(self.out, obj)


def c_init(c: Ctx, a) -> int:
    create(c.db, c.repo, "init")
    with c.tx() as tx:
        tx.insert("meta", key="initialised_by", value=tx.writer)
    c.emit({"db": str(c.db), "schema": SCHEMA, "repo": os.path.realpath(c.repo)})
    return 0


def c_add(c: Ctx, a) -> int:
    plain("--title", a.title)
    plain("--criteria", a.criteria)
    with c.tx() as tx:
        if a.id:
            item = norm(a.id)
            if tx.one("SELECT id FROM item WHERE id = ?", item):
                raise refuse(f"{item} is used (a dropped number is never reused)")
            m = ID_RE.match(item)
            if not m.group(2) and not m.group(3) and int(m.group(1)) < next_number(tx):
                raise refuse(f"{item}: numbers up to M{next_number(tx) - 1} are allocated; plain ids are "
                             f"given out by `add` (M{next_number(tx)} is next)")
        else:
            item = f"M{next_number(tx)}"
        if a.after:
            after = norm(a.after)
            get_item(tx, after)
        else:
            after = None
        pos = (tx.one("SELECT MAX(position) AS p FROM item")["p"] or 0) + 1
        tx.insert("item", id=item, title=a.title, tier=a.tier, state="queued", position=pos, after_item=after,
                  criteria=a.criteria or f"docs/sprint/{item}-criteria.md", created_at=now_iso())
    c.emit({"id": item, "state": "queued", "position": pos})
    return 0


def criteria_head(repo: Path, path: str | None) -> dict:
    """The handoff line and the Source: paragraph, read from the criteria
    file's head in the working tree (they are never copied into rows)."""
    out = {"handoff": None, "source": None, "criteria_read": None}
    if not path:
        return out
    we = work_export()
    lines, why = we.read_head(repo / path)
    if lines is None:
        out["criteria_read"] = why
        return out
    out["handoff"] = we.handoff_of(lines)
    for i, line in enumerate(lines):
        if line.startswith("Source:"):
            para = [line[len("Source:"):]]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.startswith("#"):
                    break
                para.append(nxt)
            out["source"] = we.clip(we.joined(para), SOURCE_MAX)
            break
    return out


def c_show(c: Ctx, a) -> int:
    item = norm(a.item)
    with Read(c.db) as r:
        it = get_item(r, item)
        steps = r.all("SELECT * FROM step WHERE item_id = ? ORDER BY id", item)
        proms = r.all("SELECT p.* FROM promotion p JOIN promotion_item pi ON pi.promotion_id = p.id "
                      "WHERE pi.item_id = ? ORDER BY p.id", item)
        matters = r.all("SELECT * FROM open_thing WHERE item_id = ? ORDER BY id", item)
    tags = tag_commits(c.repo)
    promoted = any(p["kind"] == "promotion" for p in proms)
    st = item_stage(steps, promoted, f"item-{item}" in tags, None, item, head_commits(c.repo))
    c.emit({**it, **criteria_head(c.repo, it["criteria"]), "stage": st, "steps": steps, "promotions": proms,
            "matters": matters})
    return 0


def c_list(c: Ctx, a) -> int:
    with Read(c.db) as r:
        sql = "SELECT id, title, state, tier, position, after_item, next_step FROM item"
        rows = r.all(sql + " WHERE state = ?", a.state) if a.state else r.all(sql)
    we = work_export()
    for row in sorted(rows, key=lambda x: we.id_key(x["id"])):
        c.emit(row)
    return 0


def queue_rows(r: Read) -> list[dict]:
    rows = r.all("SELECT * FROM item WHERE state IN ('queued','held') ORDER BY position, id")
    nxt = None
    for row in rows:
        row["blocked_by"] = after_blocks(r, row)
        if nxt is None and row["state"] == "queued" and not row["blocked_by"]:
            nxt = row["id"]
        row["next"] = row["id"] == nxt
    return rows


def c_queue(c: Ctx, a) -> int:
    with Read(c.db) as r:
        rows = queue_rows(r)
    for row in rows:
        c.emit({k: row[k] for k in ("position", "id", "title", "state", "tier", "after_item", "blocked_by",
                                     "next", "next_step", "waits_on", "waits_note", "held_why")})
    return 0


def c_start(c: Ctx, a) -> int:
    item = norm(a.item)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] == "held":
            raise refuse(f"{item} is held ({it['held_why']}); `unhold` it first")
        if it["state"] != "queued":
            raise refuse(f"{item} is {it['state']}, not queued")
        why = after_blocks(tx, it)
        if why:
            raise refuse(why)
        tx.update("item", {"id": item}, state="active", position=None)
        renumber(tx)
    c.emit({"id": item, "state": "active"})
    return 0


def c_move(c: Ctx, a) -> int:
    item = norm(a.item)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] not in ("queued", "held"):
            raise refuse(f"{item} is {it['state']}; only a queued or held item has a place")
        why = after_blocks(tx, it)
        if why:
            raise refuse(f"{why}; not moved")
        rows = [r["id"] for r in tx.all("SELECT id FROM item WHERE state IN ('queued','held') "
                                        "ORDER BY position, id")]
        if not 1 <= a.position <= len(rows):
            raise refuse(f"position {a.position}: the queue has 1 to {len(rows)}")
        rows.remove(item)
        rows.insert(a.position - 1, item)
        for n, i in enumerate(rows, start=1):
            tx.update("item", {"id": i}, position=n)
    c.emit({"id": item, "position": a.position, "queue": rows})
    return 0


def c_hold(c: Ctx, a) -> int:
    item = norm(a.item)
    plain("--why", a.why)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] != "queued":
            raise refuse(f"{item} is {it['state']}; only a queued item is held")
        tx.update("item", {"id": item}, state="held", held_why=a.why)
    c.emit({"id": item, "state": "held", "position": it["position"]})
    return 0


def c_unhold(c: Ctx, a) -> int:
    item = norm(a.item)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] != "held":
            raise refuse(f"{item} is {it['state']}, not held")
        tx.update("item", {"id": item}, state="queued", held_why=None)
    c.emit({"id": item, "state": "queued", "position": it["position"]})
    return 0


def c_drop(c: Ctx, a) -> int:
    item = norm(a.item)
    plain("--why", a.why)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] not in ("queued", "held", "active"):
            raise refuse(f"{item} is {it['state']}; not dropped")
        tx.update("item", {"id": item}, state="dropped", position=None, held_why=None, ended_why=a.why,
                  ended_at=now_iso())
        renumber(tx)
        followers = [r["id"] for r in tx.all("SELECT id FROM item WHERE after_item = ? AND state IN "
                                             "('queued','held','active')", item)]
    c.emit({"id": item, "state": "dropped", "followers_to_review": followers})
    return 0


def c_after(c: Ctx, a) -> int:
    item = norm(a.item)
    with c.tx() as tx:
        get_item(tx, item)
        if a.clear:
            other = None
        else:
            if not a.other:
                raise refuse("give the item it follows, or --clear")
            other = norm(a.other)
            if other == item:
                raise refuse(f"{item} cannot follow itself")
            check_after(tx, item, other)
        tx.update("item", {"id": item}, after_item=other)
    c.emit({"id": item, "after_item": other})
    return 0


def c_note(c: Ctx, a) -> int:
    item = norm(a.item)
    for opt, val in (("--next", a.next), ("--waits-note", a.waits_note), ("--criteria", a.criteria),
                     ("--title", a.title)):
        plain(opt, val)
    with c.tx() as tx:
        it = get_item(tx, item)
        vals = {}
        if a.next is not None:
            vals["next_step"] = a.next or None
        if a.waits is not None:
            if a.waits == "none":
                if a.waits_note:
                    raise refuse("--waits none takes no --waits-note")
                vals.update(waits_on=None, waits_note=None)
            else:
                note = a.waits_note or it["waits_note"]
                if not note:
                    raise refuse(f"--waits {a.waits} needs --waits-note (what it waits on)")
                vals.update(waits_on=a.waits, waits_note=note)
        elif a.waits_note is not None:
            if not it["waits_on"]:
                raise refuse("--waits-note needs --waits drew|outside|item")
            vals["waits_note"] = a.waits_note
        if a.tier is not None:
            vals["tier"] = a.tier
        if a.criteria is not None:
            vals["criteria"] = a.criteria
        if a.title is not None:
            vals["title"] = a.title
        if not vals:
            raise refuse("nothing to note: --next, --waits, --waits-note, --tier, --criteria or --title")
        tx.update("item", {"id": item}, **vals)
        new = get_item(tx, item)
    c.emit({k: new[k] for k in ("id", "next_step", "waits_on", "waits_note", "tier", "criteria", "title")})
    return 0


# ════════════════════════════════ steps ════════════════════════════════════

def find_step(tx: Read, item: str, kind: str, rnd: int, name: str = "") -> dict | None:
    return tx.one("SELECT * FROM step WHERE item_id = ? AND kind = ? AND round = ? AND name = ?",
                  item, kind, rnd, name)


def need_step(tx: Read, item: str, kind: str, rnd: int, name: str = "") -> dict:
    s = find_step(tx, item, kind, rnd, name)
    if s is None:
        raise refuse(f"no {kind} step round {rnd}{' ' + name if name else ''} for {item}")
    return s


def c_step_add(c: Ctx, a) -> int:
    item = norm(a.item)
    report = a.report or default_report(item, a.kind, a.round, a.name)
    if not report:
        raise refuse("kind other needs --name or --report")
    sha, when, verdict, note = report_evidence(c.repo, report, item, a.kind)
    with c.tx() as tx:
        it = get_item(tx, item)
        if it["state"] == "dropped":
            raise refuse(f"{item} is dropped")
        if find_step(tx, item, a.kind, a.round, a.name):
            raise refuse(f"{item} already has a {a.kind} step round {a.round}; `step refresh` re-reads it")
        sid = tx.insert("step", item_id=item, kind=a.kind, round=a.round, name=a.name, by="coordinator",
                        state="finished", report=report, commit_sha=sha, verdict=verdict, note=note,
                        ended_at=when)
    c.emit({"step": sid, "item": item, "kind": a.kind, "round": a.round, "report": report, "commit": sha[:7],
            "verdict": verdict})
    return 0


def step_start(db: Path, repo: Path, item: str, kind: str, rnd: int, agent: str, model: str | None,
               report: str | None, at: str | None, cmd: str, timeout: float | None = None,
               writer: str | None = None) -> dict:
    """An agent step started (or started again after a stop). A step
    already started is left as it is; a finished one is refused."""
    if kind not in AGENT_KINDS:
        raise refuse(f"kind {kind}: agent steps are {', '.join(AGENT_KINDS)}")
    report = report or default_report(item, kind, rnd)
    started = to_utc(at) if at else now_iso()
    with Tx(db, cmd, timeout, writer) as tx:
        guard_repo(tx, repo)
        it = get_item(tx, item)
        if it["state"] != "active":
            raise refuse(f"{item} is {it['state']}, not active (`work.py start {item}` first)")
        s = find_step(tx, item, kind, rnd)
        if s is None:
            sid = tx.insert("step", item_id=item, kind=kind, round=rnd, name="", by="agent", agent=agent,
                            model=model, state="started", report=report, started_at=started)
            return {"step": sid, "state": "started", "changed": True}
        if s["state"] == "finished":
            raise refuse(f"{item} {kind} round {rnd} is finished; a new agent is a new round (--round)")
        if s["state"] == "stopped":
            tx.update("step", {"id": s["id"]}, state="started", agent=agent, model=model, report=report,
                      started_at=started, ended_at=None, note=None)
            return {"step": s["id"], "state": "started", "changed": True}
        changed = tx.update("step", {"id": s["id"]}, agent=agent, model=model or s["model"],
                            report=report or s["report"], started_at=to_utc(at) if at else s["started_at"])
        return {"step": s["id"], "state": "started", "changed": changed}


def step_finish(db: Path, repo: Path, item: str, kind: str, rnd: int, cmd: str, name: str = "",
                agent: str | None = None, model: str | None = None, report: str | None = None,
                started_at: str | None = None, timeout: float | None = None, writer: str | None = None,
                insert_only_active: bool = False) -> dict:
    """Finish a step from the evidence: the report committed in HEAD, the
    last commit that touched it, its verdict. Idempotent: the same evidence
    writes nothing. A step with no row (a failed start, an agent launched
    before the recording) is inserted finished."""
    s = None
    if not report:
        with Read(db, timeout) as r:
            guard_repo(r, repo)
            s = find_step(r, item, kind, rnd, name)
    path = report or (s and s["report"]) or default_report(item, kind, rnd, name)
    if not path:
        raise refuse(f"{item} {kind} round {rnd}: no report path")
    sha, when, verdict, note = report_evidence(repo, path, item, kind)
    with Tx(db, cmd, timeout, writer) as tx:
        guard_repo(tx, repo)
        it = get_item(tx, item)
        s = find_step(tx, item, kind, rnd, name)
        if s is None:
            if it["state"] in ("dropped",):
                raise refuse(f"{item} is dropped")
            if insert_only_active and it["state"] != "active":
                raise refuse(f"{item} is {it['state']}, not active; no step written")
            sid = tx.insert("step", item_id=item, kind=kind, round=rnd, name=name,
                            by="agent" if agent else "coordinator", agent=agent, model=model,
                            state="finished", report=path, commit_sha=sha, verdict=verdict, note=note,
                            started_at=to_utc(started_at) if started_at else None, ended_at=now_iso())
            return {"step": sid, "state": "finished", "changed": True, "commit": sha[:7], "verdict": verdict}
        same = (s["state"] == "finished" and s["commit_sha"] == sha and s["verdict"] == verdict
                and s["report"] == path)
        if same:
            return {"step": s["id"], "state": "finished", "changed": False, "commit": sha[:7],
                    "verdict": verdict}
        tx.update("step", {"id": s["id"]}, state="finished", report=path, commit_sha=sha, verdict=verdict,
                  note=note if note else (s["note"] if s["state"] == "finished" else None),
                  ended_at=s["ended_at"] if s["state"] == "finished" else now_iso())
        return {"step": s["id"], "state": "finished", "changed": True, "commit": sha[:7], "verdict": verdict}


def step_stop(db: Path, repo: Path, item: str, kind: str, rnd: int, why: str, cmd: str, name: str = "",
              timeout: float | None = None, writer: str | None = None, agent: str | None = None,
              model: str | None = None, report: str | None = None, started_at: str | None = None) -> dict:
    """A started step stopped with the work not done. A finished step is
    left finished; no row at all is inserted stopped (agent.py close of an
    agent whose start was not recorded)."""
    with Tx(db, cmd, timeout, writer) as tx:
        guard_repo(tx, repo)
        it = get_item(tx, item)
        s = find_step(tx, item, kind, rnd, name)
        if s is None:
            if agent is None:
                raise refuse(f"no {kind} step round {rnd} for {item}")
            if it["state"] != "active":              # as step start refuses it (N1)
                raise refuse(f"{item} is {it['state']}, not active; no step written")
            sid = tx.insert("step", item_id=item, kind=kind, round=rnd, name=name, by="agent", agent=agent,
                            model=model, state="stopped", report=report or default_report(item, kind, rnd),
                            note=why, started_at=to_utc(started_at) if started_at else None,
                            ended_at=now_iso())
            return {"step": sid, "state": "stopped", "changed": True}
        if s["state"] != "started":
            return {"step": s["id"], "state": s["state"], "changed": False}
        tx.update("step", {"id": s["id"]}, state="stopped", note=why, ended_at=now_iso())
        return {"step": s["id"], "state": "stopped", "changed": True}


def guard_repo(tx: Read, repo: Path) -> None:
    want = tx.meta.get("repo")
    got = os.path.realpath(repo)
    if want and want != got:
        raise refuse(f"this database describes {want}, not {got}; nothing written")


def c_step_start(c: Ctx, a) -> int:
    res = step_start(c.db, c.repo, norm(a.item), a.kind, a.round, a.agent, a.model, a.report, a.at, c.cmd)
    c.emit(res)
    return 0


def c_step_finish(c: Ctx, a) -> int:
    c.emit(step_finish(c.db, c.repo, norm(a.item), a.kind, a.round, c.cmd, name=a.name))
    return 0


def c_step_stop(c: Ctx, a) -> int:
    c.emit(step_stop(c.db, c.repo, norm(a.item), a.kind, a.round, a.why, c.cmd, name=a.name))
    return 0


def c_step_refresh(c: Ctx, a) -> int:
    """Re-copy a finished step's verdict and commit from the report (Appendix C row 4)."""
    item = norm(a.item)
    with Read(c.db) as r:
        s = need_step(r, item, a.kind, a.round, a.name)
    if s["state"] != "finished":
        raise refuse(f"{item} {a.kind} round {a.round} is {s['state']}; `step finish` it")
    c.emit(step_finish(c.db, c.repo, item, a.kind, a.round, c.cmd, name=a.name))
    return 0


GONE_RE = re.compile(r"report deleted in ([0-9a-f]{40})")


def c_step_note_gone(c: Ctx, a) -> int:
    """A step whose report was deleted from HEAD: record the commit that
    deleted it, so `check` stops naming `report-gone` (S5). The row keeps
    its commit and verdict: the report is read at that commit."""
    item = norm(a.item)
    with Read(c.db) as r:
        s = need_step(r, item, a.kind, a.round, a.name)
    if not s["report"]:
        raise refuse(f"{item} {a.kind} round {a.round} has no report path")
    if in_head(c.repo, s["report"]):
        raise refuse(f"{s['report']} is in HEAD; nothing is gone")
    rc, out = git(c.repo, "log", "-1", "--diff-filter=D", "--format=%H", "HEAD", "--", s["report"])
    gone = out.strip() if rc == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", gone):
        raise refuse(f"no commit in HEAD's history deletes {s['report']}; not noted")
    with c.tx() as tx:
        s = need_step(tx, item, a.kind, a.round, a.name)
        mark = f"report deleted in {gone}"
        if mark not in (s["note"] or ""):
            tx.update("step", {"id": s["id"]}, note=f"{s['note']}; {mark}" if s["note"] else mark)
    c.emit({"step": s["id"], "item": item, "kind": a.kind, "round": a.round, "report": s["report"],
            "deleted_in": gone[:7]})
    return 0


def c_grade(c: Ctx, a) -> int:
    item = norm(a.item)
    with c.tx() as tx:
        get_item(tx, item)
        if a.round:
            s = need_step(tx, item, a.kind, a.round)
        else:
            s = tx.one("SELECT * FROM step WHERE item_id = ? AND kind = ? AND state = 'finished' "
                       "ORDER BY round DESC LIMIT 1", item, a.kind)
            if s is None:
                raise refuse(f"{item} has no finished {a.kind} step")
        if s["state"] != "finished":
            raise refuse(f"{item} {a.kind} round {s['round']} is {s['state']}; grade a finished step")
        tx.update("step", {"id": s["id"]}, graded=a.grade)
    c.emit({"item": item, "kind": a.kind, "round": s["round"], "verdict": s["verdict"], "graded": a.grade})
    return 0


# ═════════════════════════════ promotion ═══════════════════════════════════

def c_promote(c: Ctx, a) -> int:
    """A promotion of one or more items; or, with no item, a deploy made
    outside any work item (M34 plumbing 7): --label says what it was,
    --verified where its checks are, --at when it was deployed. Its row has
    no promotion_item; /work lists it apart from the items' rows."""
    items = [norm(i) for i in a.items]
    plain("--label", a.label)
    if items and a.label is not None:
        raise refuse("--label is for a promotion with no item; an item's promotion is named by its item")
    if not items:
        if not a.label:
            raise refuse("give the items promoted, or --label T for a deploy with no item")
        if a.followup:
            raise refuse("a follow-up is on a promoted item; give the item")
        if not a.verified:
            raise refuse("a promotion with no item needs --verified PATH (where its checks are committed)")
    if bool(a.tag) == bool(a.no_tag):
        raise refuse("give --tag T or --no-tag")
    at = to_utc(a.at) if a.at else None
    sha = None
    if a.tag:
        tags = tag_commits(c.repo)
        if a.tag not in tags:
            raise refuse(f"git lists no tag {a.tag}; not promoted")
        sha = tags[a.tag]
    verified = a.verified or f"docs/sprint/{items[0]}-promote.md"
    if not in_head(c.repo, verified):
        raise refuse(f"{verified} is not committed in HEAD (the promotion checks go there); not promoted")
    with c.tx() as tx:
        for item in items:
            it = get_item(tx, item)
            want = "promoted" if a.followup else "active"
            if it["state"] != want:
                rule = "a follow-up is on a promoted item" if a.followup else "only an active item is promoted"
                raise refuse(f"{item} is {it['state']}; {rule}")
        at = at or now_iso()
        pid = tx.insert("promotion", kind="followup" if a.followup else "promotion", tag=a.tag,
                        commit_sha=sha, at=at, deployed=1 if a.deployed == "yes" else 0, verified=verified,
                        untested=a.untested, note=a.label)
        for item in items:
            tx.insert("promotion_item", promotion_id=pid, item_id=item)
            if not a.followup:
                tx.update("item", {"id": item}, state="promoted", ended_at=at)
    c.emit({"promotion": pid, "items": items, "tag": a.tag, "commit": sha[:7] if sha else None,
            "deployed": a.deployed == "yes", "verified": verified, "at": at,
            **({"label": a.label} if not items else {})})
    return 0


# ══════════════════════════════ matters ════════════════════════════════════

def c_matter_add(c: Ctx, a) -> int:
    plain("--text", a.text)
    plain("--source", a.source)
    if a.kind == "escape" and not a.item:
        raise refuse("an escape is about a promoted item: --item")
    with c.tx() as tx:
        item = None
        if a.item:
            item = norm(a.item)
            get_item(tx, item)
        mid = tx.insert("open_thing", kind=a.kind, item_id=item, owner=a.owner, text=a.text,
                        blocks=1 if a.blocks else 0, source=a.source, opened_at=now_iso())
    c.emit({"matter": mid, "kind": a.kind, "item": item, "owner": a.owner, "blocks": bool(a.blocks)})
    return 0


VOID = "void: "                 # closed_how of a matter struck as entered by mistake (S9)


def is_void(m: dict) -> bool:
    return bool(m.get("closed_how")) and m["closed_how"].startswith(VOID)


def c_matter_close(c: Ctx, a) -> int:
    if a.how.startswith(VOID.strip()):
        raise refuse(f"--how may not start with {VOID.strip()!r}: `matter void` strikes a matter")
    with c.tx() as tx:
        m = tx.one("SELECT * FROM open_thing WHERE id = ?", a.id)
        if m is None:
            raise refuse(f"no matter {a.id}")
        if m["closed_at"]:
            raise refuse(f"matter {a.id} was closed {m['closed_at']}: {one_line(m['closed_how'])}")
        tx.update("open_thing", {"id": a.id}, closed_at=now_iso(), closed_how=a.how)
    c.emit({"matter": a.id, "closed": True, "how": a.how})
    return 0


def c_matter_void(c: Ctx, a) -> int:
    """Strike a matter entered by mistake, open or closed. The row stays,
    closed, with closed_how 'void: <why>'; the log keeps what it was. A
    void matter is not counted anywhere: not open, and not an escape (a
    closed escape still counts; a void one does not)."""
    plain("--why", a.why)
    with c.tx() as tx:
        m = tx.one("SELECT * FROM open_thing WHERE id = ?", a.id)
        if m is None:
            raise refuse(f"no matter {a.id}")
        if is_void(m):
            raise refuse(f"matter {a.id} is void already: {one_line(m['closed_how'])}")
        tx.update("open_thing", {"id": a.id}, closed_at=m["closed_at"] or now_iso(), closed_how=VOID + a.why)
    c.emit({"matter": a.id, "void": True, "why": a.why, "was_closed": bool(m["closed_at"])})
    return 0


def c_matter_list(c: Ctx, a) -> int:
    sql, args = "SELECT * FROM open_thing WHERE 1=1", []
    if a.open:
        sql += " AND closed_at IS NULL"
    if a.kind:
        sql += " AND kind = ?"
        args.append(a.kind)
    if a.item:
        sql += " AND item_id = ?"
        args.append(norm(a.item))
    with Read(c.db) as r:
        for m in r.all(sql + " ORDER BY id", *args):
            c.emit(m)
    return 0


def c_log(c: Ctx, a) -> int:
    sql, args = "SELECT * FROM log WHERE 1=1", []
    if a.day:
        sql += " AND at LIKE ?"
        args.append(a.day + "%")
    with Read(c.db) as r:
        rows = r.all(sql + " ORDER BY id", *args)
    if a.item:
        item = norm(a.item)
        rows = [x for x in rows if item in (json.loads(x["changes"]).get("items") or [])]
    for x in rows[-a.limit:] if a.limit else rows:
        x["changes"] = json.loads(x["changes"])
        c.emit(x)
    return 0


# ═══════════════════════════════ dump ══════════════════════════════════════

def c_dump(c: Ctx, a) -> int:
    target = Path(a.out) if a.out else dump_path(c.db)
    with Read(c.db) as r:
        err = write_dump(r.conn, target)
    if err:
        raise refuse(f"dump to {target} failed: {err}")
    c.emit({"dump": str(target)})
    return 0


def c_restore_dump(c: Ctx, a) -> int:
    """Rebuild the database from its dump. The target must not exist: move
    a damaged file aside first."""
    src = Path(a.source) if a.source else dump_path(c.db)
    if c.db.exists() or Path(str(c.db) + "-wal").exists():
        raise refuse(f"{c.db} exists; move it (and any -wal, -shm) aside first; not restored")
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as e:
        raise refuse(f"dump {src} cannot be read ({type(e).__name__})") from None
    made = missing_dirs(c.db.parent)
    c.db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=c.db.parent, prefix=f".{c.db.name}.restore.")
    os.close(fd)
    moved = False
    try:
        conn = sqlite3.connect(tmp, isolation_level=None)
        try:
            conn.executescript(text)
            if conn.in_transaction:
                # A dump cut at a statement boundary: its BEGIN loaded, its
                # COMMIT did not, so closing would keep nothing (S6).
                raise refuse(f"dump {src} ends inside its transaction (no COMMIT: it is cut); not restored")
            bad = [r[0] for r in conn.execute("PRAGMA integrity_check")]
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad != ["ok"] or fk:
                raise refuse(f"restored copy fails its checks ({bad[:2]}, {len(fk)} foreign key faults)")
            conn.execute("PRAGMA journal_mode=WAL")
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if set(TABLES) - names:
                raise refuse(f"dump {src} lacks tables: {', '.join(sorted(set(TABLES) - names))}")
        finally:
            conn.close()
        try:                               # what was kept on disk, read again as a reader would
            with Read(Path(tmp), timeout=1):
                pass
        except WorkError as e:
            raise refuse(f"dump {src} loads no usable schema ({e}); not restored") from None
        os.chmod(tmp, 0o600)
        os.replace(tmp, c.db)
        moved = True
    except sqlite3.Error as e:
        raise refuse(f"dump {src} does not load ({e}); not restored") from None
    finally:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            Path(p).unlink(missing_ok=True)
        if not moved:
            remove_dirs(made)
    with c.tx() as tx:
        tx.insert("meta", key=f"restored_at_{now_iso()}", value=str(src))
        n = tx.one("SELECT COUNT(*) AS n FROM item")["n"]
    c.emit({"db": str(c.db), "from": str(src), "items": n})
    return 0


# ═════════════════════════════════ check ═══════════════════════════════════

def agent_states() -> list[dict]:
    """The state files scripts/agent.py keeps (read only)."""
    d = Path(os.environ.get("KRONK_AGENT_STATE") or Path.home() / ".local" / "state" / "kronk-agents")
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and isinstance(st.get("name"), str) and p.name == f"{st['name']}.json":
            out.append(st)
    return out


def herdr_view(repo: Path) -> dict:
    we = work_export()
    try:
        h = we.herdr_agents(repo)
    except Exception as e:
        h = {"known": False, "agents": [], "reason": f"herdr agent list: {type(e).__name__}"}
    h["by_name"] = {a["name"]: a["status"] for a in h["agents"] if a["name"]}
    return h


def gather(c: Ctx) -> dict:
    """Everything check and resume read, in one snapshot and a few lock-free
    git calls and one herdr call."""
    with Read(c.db, deep=True) as r:
        g = {"meta": dict(r.meta),
             "items": r.all("SELECT * FROM item"),
             "steps": r.all("SELECT * FROM step ORDER BY id"),
             "promotions": r.all("SELECT * FROM promotion ORDER BY id"),
             "pitems": r.all("SELECT * FROM promotion_item"),
             "matters": r.all("SELECT * FROM open_thing ORDER BY id"),
             "log": r.all("SELECT id, at, who, cmd FROM log ORDER BY id DESC LIMIT 5"),
             "last_log": r.one("SELECT MAX(at) AS at FROM log")["at"],
             "queue": queue_rows(r)}
    g["git_errors"] = []
    try:
        g["tags"] = tag_commits(c.repo)
    except WorkError as e:
        g["tags"] = {}
        g["git_errors"].append(f"tags not read: {e}")
    rc, out = git(c.repo, "rev-list", "HEAD")
    if rc != 0:
        g["git_errors"].append(f"git rev-list HEAD failed in {c.repo}; commits and files not compared")
    g["branch_commits"] = set(out.split()) if rc == 0 else None
    rc, out = git(c.repo, "ls-tree", "--name-only", "HEAD", "docs/sprint/")
    g["sprint_files"] = [Path(x).name for x in out.splitlines()] if rc == 0 else []
    # The title of each committed file whose item has no row: check's fix
    # line for file-no-item carries it (S5).
    we = work_export()
    known = {i["id"] for i in g["items"]}
    rowless = [n for n in g["sprint_files"] if (m := we.FILE_RE.match(n)) and m.group(1) not in known]
    g["file_titles"] = {}
    try:
        heads = cat_files(c.repo, [f"HEAD:docs/sprint/{n}" for n in rowless])
    except (WorkError, OSError, subprocess.SubprocessError, ValueError):
        heads = {}
    for n in rowless:
        text = heads.get(f"HEAD:docs/sprint/{n}")
        title = we.short_title(we.title_of(text.splitlines()[:1]), we.FILE_RE.match(n).group(1)) if text else None
        if title and not any(is_control(ch) for ch in title):
            g["file_titles"][n] = title
    g["herdr"] = herdr_view(c.repo)
    g["agent_states"] = agent_states()
    g["head"] = work_export().git_head(c.repo)
    rc, out = git(c.repo, "status", "--porcelain=v1", "--untracked-files=normal")
    g["clean"] = (out.strip() == "") if rc == 0 else None
    reports = sorted({s["report"] for s in g["steps"] if s["state"] == "finished" and s["kind"] == "test"
                      and s["report"]})
    try:
        g["reports"] = cat_files(c.repo, [f"HEAD:{p}" for p in reports])
    except (WorkError, OSError, subprocess.SubprocessError, ValueError) as e:
        g["reports"] = {}
        g["git_errors"].append(f"reports not read: {e}")
    return g


def in_branch(sha: str | None, commits: set | None) -> bool | None:
    if not sha or commits is None:
        return None
    if len(sha) >= 40:
        return sha in commits
    return any(x.startswith(sha) for x in commits)


NOT_A_COMMAND = "not a command: "     # a fix line that says what to look up (S5)


def findings(c: Ctx, g: dict) -> list[dict]:
    """One finding per disagreement between the rows and the evidence
    (review Appendix C), each with a fix line. A fix line is one command
    that runs as printed from the repo root, or, where a value only the
    coordinator can know is missing, starts "not a command: " and says what
    to look up and then what to run (S5)."""
    we = work_export()
    out = []

    def f(code, item, says, fix):
        out.append({"check": code, "item": item, "says": says, "fix": fix})

    def look(text):
        return NOT_A_COMMAND + text

    def step_args(s):
        return [s["item_id"], s["kind"], "--round", str(s["round"])] + (["--name", s["name"]] if s["name"] else [])

    items = {i["id"]: i for i in g["items"]}
    for e in g["git_errors"]:
        f("git", None, e, look(f"run `git -C {shlex.quote(os.path.realpath(c.repo))} status` and fix what git "
                               f"says; then run {c.run('check')}"))
    if g["meta"].get("repo") != os.path.realpath(c.repo):
        f("repo", None, f"this database describes {g['meta'].get('repo')}; --repo is {os.path.realpath(c.repo)}",
          look(f"use the database of {os.path.realpath(c.repo)} (--db or KRONK_WORK_DB), or run "
               f"python3 scripts/work.py with --repo {shlex.quote(str(g['meta'].get('repo')))} for this one"))
    herdr = g["herdr"]
    live = herdr["by_name"] if herdr.get("known") else None
    if live is None:
        f("herdr", None, f"herdr not reachable ({herdr.get('reason')}); started steps not compared with it",
          look(f"see why `herdr agent list` fails (HERDR_ENV set? herdr running?); then run {c.run('check')}"))
    states = {s["name"]: s for s in g["agent_states"]}
    tracked = set()
    for s in g["steps"]:
        it = items.get(s["item_id"])
        label = f"{s['item_id']} {s['kind']} round {s['round']}" + (f" {s['name']}" if s["name"] else "")
        if s["agent"]:
            tracked.add(s["agent"])
        if s["state"] == "started":
            report_in = bool(s["report"]) and in_head(c.repo, s["report"])
            status = live.get(s["agent"]) if live is not None and s["agent"] else None
            if live is not None and status not in ("working", "blocked"):
                who_ = f"agent {s['agent']} is {status or 'not in herdr'}"
                if report_in:
                    f("started-report-in", s["item_id"],
                      f"{label}: step still started; {who_} and {s['report']} is committed",
                      c.run("step", "finish", *step_args(s)))
                else:
                    f("started-not-working", s["item_id"],
                      f"{label}: step still started; {who_}, report not committed",
                      c.run("step", "stop", *step_args(s), "--why", f"{who_}; report not committed"))
            if it and it["state"] in ("promoted", "dropped"):
                f("started-on-ended", s["item_id"], f"{label}: started on a {it['state']} item",
                  c.run("step", "stop", *step_args(s), "--why", f"started on a {it['state']} item"))
        if s["state"] == "finished" and s["commit_sha"] and in_branch(s["commit_sha"], g["branch_commits"]) is False:
            if s["report"] and in_head(c.repo, s["report"]):
                fix = c.run("step", "refresh", *step_args(s))
            else:
                fix = look(f"find where {s['report']} went (`git log --all -- {s['report']}`); once it is "
                           f"committed in HEAD, run {c.run('step', 'refresh', *step_args(s))}")
            f("commit-not-in-branch", s["item_id"],
              f"{label}: commit {s['commit_sha'][:7]} is not in HEAD's history; not counted for the stage", fix)
        if s["state"] == "finished" and s["kind"] == "test" and s["report"]:
            text = g["reports"].get(f"HEAD:{s['report']}")
            if text is not None:
                now_v, _ = verdict_of(text, s["item_id"])
                if now_v != s["verdict"]:
                    f("verdict-changed", s["item_id"],
                      f"{label}: row says {s['verdict']}, {s['report']} now reads {now_v}",
                      c.run("step", "refresh", *step_args(s)))
            elif s["item_id"] in items and items[s["item_id"]]["state"] not in ("dropped",):
                gone = GONE_RE.search(s["note"] or "")
                if gone and in_branch(gone.group(1), g["branch_commits"]) is not False:
                    continue                    # noted: deleted in a commit of this branch
                f("report-gone", s["item_id"], f"{label}: {s['report']} is not in HEAD",
                  c.run("step", "note-gone", *step_args(s)))
    # Agents herdr or agent.py knows with no step (a failed write, a hand start).
    for name, status in (live or {}).items():
        item, role = we.agent_for(name)
        if not item or role not in AGENT_KINDS or item not in items or name in tracked:
            continue
        if status in ("working", "blocked"):
            st = states.get(name, {})
            digits = re.search(r"(?:review|build|test|fix)(\d+)$", name)      # m33-test2: round 2
            rnd = st.get("round") or (int(digits.group(1)) if digits else 1)
            args = ["step", "start", item, role, "--round", str(rnd), "--agent", name]
            args += ["--model", st["model"]] if st.get("model") else []
            args += ["--report", st["report"]] if st.get("report") else []
            done = next((s for s in g["steps"] if (s["item_id"], s["kind"], s["round"], s["name"])
                         == (item, role, rnd, "") and s["state"] == "finished"), None)
            if done:
                # The step is already finished from its committed report (an
                # import, or a step add); `step start` refuses a finished one.
                fix = look(f"{item} {role} round {rnd} is already finished from {done['report']}; when {name} "
                           f"is done, run python3 scripts/agent.py close {name}")
            elif st.get("launched_iso"):
                fix = c.run(*args, "--at", st["launched_iso"])
            else:
                fix = look(f"find when {name} was launched (its herdr pane, or the commit of its brief); then "
                           f"run {c.run(*args)} with --at and that time")
            f("agent-no-step", item, f"agent {name} is {status} in herdr; no step row", fix)
    for name, st in states.items():
        if name in tracked or (live and name in live and live[name] in ("working", "blocked")):
            continue
        if st.get("probe") or st.get("role") not in AGENT_KINDS or not st.get("report"):
            continue
        item = st.get("item")
        if item in items:
            args = ["step", "start", item, st["role"], "--round", str(st.get("round") or 1), "--agent", name]
            args += ["--model", st["model"]] if st.get("model") else []
            args += ["--report", st["report"]]
            if st.get("launched_iso"):
                fix = c.run(*args, "--at", st["launched_iso"])
            else:
                fix = look(f"find when {name} was launched; then run {c.run(*args)} with --at and that time")
            f("state-file-no-step", item, f"agent.py knows {name} (launched {st.get('launched_iso')}); no step row",
              fix)
    # Tags and promotions.
    promoted = {pi["item_id"] for pi in g["pitems"]
                for p in g["promotions"] if p["id"] == pi["promotion_id"] and p["kind"] == "promotion"}
    for i in items:
        if f"item-{i}" in g["tags"] and i not in promoted:
            f("tag-not-promoted", i, f"tag item-{i} exists; no promotion row",
              look(f"see whether item-{i} was deployed (`scripts/deploy_tag.sh status`) and commit "
                   f"docs/sprint/{i}-promote.md; then run {c.run('promote', i, '--tag', f'item-{i}')} with "
                   f"--deployed yes or --deployed no"))
    for p in g["promotions"]:
        if p["tag"] and p["tag"] not in g["tags"]:
            fix = (c.git_run("tag", p["tag"], p["commit_sha"]) if p["commit_sha"] else
                   look(f"find the commit {p['tag']} was on (`git log --all --oneline`); then run "
                        f"{c.git_run('tag', p['tag'])} with that commit"))
            f("tag-gone", None, f"promotion {p['id']} names tag {p['tag']}, which git does not list", fix)
    for i, it in items.items():
        if it["state"] == "promoted" and i not in promoted:
            f("promoted-no-row", i, f"{i} is promoted with no promotion row",
              look(f"only a hand edit or an import fault makes this; read {i}'s rows with {c.run('show', i)} "
                   f"and its entry in docs/sprint/PROMOTIONS.md, and record it with "
                   f"{c.run('matter', 'add', 'leftover', '--owner', 'coordinator', '--item', i, '--text', 'promoted with no promotion row')}"))
        if it["state"] in ("active",) and it["criteria"] and not in_head(c.repo, it["criteria"]):
            f("criteria-not-committed", i, f"{i} is active; {it['criteria']} is not committed",
              look(f"write and commit {it['criteria']}; then run {c.run('step', 'add', i, 'criteria')}"))
    # Files in docs/sprint with no row.
    reports = {Path(s["report"]).name for s in g["steps"] if s["report"]}
    nxt = max((id_num(i) or 0 for i in items), default=0) + 1
    for name in g["sprint_files"]:
        m = we.FILE_RE.match(name)
        if not m:
            continue
        item = m.group(1)
        if item not in items:
            idm = ID_RE.match(item)
            title = g["file_titles"].get(name)
            if idm and not idm.group(2) and not idm.group(3) and int(idm.group(1)) < nxt:
                fix = look(f"M{nxt - 1} and below are allocated and `add --id` refuses {item}; see why "
                           f"docs/sprint/{name} exists (`git log -- docs/sprint/{name}`) and rename or remove it")
            elif title:
                fix = c.run("add", "--id", item, "--title", title)
            else:
                fix = look(f"docs/sprint/{name} has no title line; run {c.run('add', '--id', item)} with "
                           f"--title and the item's title")
            f("file-no-item", item, f"docs/sprint/{name} is committed; no item {item}", fix)
        elif name not in reports and name.endswith(".md"):
            kind, n = we.step_kind(m.group(2))
            if m.group(2) == "promote":
                continue
            f("file-no-step", item, f"docs/sprint/{name} is committed; no step has it as its report",
              c.run("step", "add", item, kind, *(["--round", str(n)] if n and n > 1 else []),
                    *(["--name", m.group(2)] if kind == "other" else [])))
    # Promoted since the import with no ROADMAP Shipped line naming them.
    text, _ = we.read_text(c.repo / "ROADMAP.md")
    if text is not None:
        shipped = set()
        for sec in we.parse_roadmap(text, []):
            if not sec["open"]:
                for e in sec["entries"]:
                    shipped.update(we.resolve_links(e["links"], list(items)))
        for p in g["promotions"]:
            if p["kind"] != "promotion" or p["verified"].startswith("PROMOTIONS.md:"):
                continue
            for pi in g["pitems"]:
                if pi["promotion_id"] == p["id"] and pi["item_id"] not in shipped:
                    f("no-shipped-line", pi["item_id"],
                      f"{pi['item_id']} is promoted; no ROADMAP Shipped line names it",
                      look(f"add a line under ROADMAP.md's Shipped naming \"work item {pi['item_id']}\" and "
                           f"commit it; then run {c.run('check')}"))
    # The queue.
    pos = [q["position"] for q in g["queue"]]
    if pos != list(range(1, len(pos) + 1)):
        free = next(((n, q) for n, q in enumerate(g["queue"], start=1) if not q["blocked_by"]), None)
        fix = (c.run("move", free[1]["id"], str(free[0])) if free else
               look("every queued item follows one not promoted, so `move` refuses each; restore the database "
                    "from its dump or say so as a leftover"))
        f("queue-numbering", None, f"the queue is numbered {pos}, not 1 to {len(pos)}", fix)
    for q in g["queue"]:
        if q["after_item"] and items.get(q["after_item"], {}).get("state") == "dropped":
            f("after-dropped", q["id"], f"{q['id']} follows {q['after_item']}, which is dropped",
              c.run("after", q["id"], "--clear"))
    # The dump.
    dp = dump_path(c.db)
    if not dp.exists():
        f("dump-missing", None, f"{dp} is missing", c.run("dump"))
    elif g["last_log"]:
        dumped = dt.datetime.fromtimestamp(dp.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if dumped < g["last_log"]:
            f("dump-stale", None, f"{dp} ({dumped}) is older than the last write ({g['last_log']})", c.run("dump"))
    return out


def c_check(c: Ctx, a) -> int:
    fs = findings(c, gather(c))
    for x in fs:
        c.emit(x)
    return 3 if fs else 0


# ════════════════════════════════ resume ═══════════════════════════════════

def overview(g: dict) -> dict:
    """Where the work stands, as plain data (work item M34, plumbing 5):
    read-only, from the rows and the outside facts handed in. `resume`
    prints from it and scripts/work_export.py serialises it, so the page
    and `resume` cannot disagree. No database, no print, no git, no herdr.

    g: "items", "steps", "promotions", "pitems", "matters" (rows as dicts),
    "queue" (queue_rows()), "tags" ({tag: commit}), "branch_commits" (HEAD's
    commits, or None), "herdr" ({"known", "by_name": {name: status}, ...}).

    Returns: "stages" {item: item_stage()} for every item; "agents" {item:
    [{name, status, role}]} by herdr name, for any item id a name parses to;
    "active" [{item, stage, agents, started, last}] by id; "queue" as
    given; "decisions" (open, owed by Drew, oldest first) and
    "other_decisions"; "leftovers" {owner: [...]} and "untriaged"; "counts"
    {item or None: {"leftovers"|"decisions": {"drew", "coordinator",
    "not_triaged"}}} of open ones; "escape_window" (the last ESCAPE_WINDOW
    promoted items, newest first) and "escapes" in it."""
    we = work_export()
    steps_of: dict = {}
    for s in g["steps"]:
        steps_of.setdefault(s["item_id"], []).append(s)
    pmap: dict = {}
    for pi in g["pitems"]:
        pmap.setdefault(pi["item_id"], []).append(pi["promotion_id"])
    promo = {p["id"]: p for p in g["promotions"]}
    herdr = g["herdr"]
    per_item: dict = {}
    for name, status in herdr["by_name"].items():
        item, role = we.agent_for(name)
        if item:
            per_item.setdefault(item, []).append({"name": name, "status": status, "role": role})

    def stage_of(i):
        promoted = any(promo[p]["kind"] == "promotion" for p in pmap.get(i, []))
        agents = per_item.get(i, []) if herdr.get("known") else None
        return item_stage(steps_of.get(i, []), promoted, f"item-{i}" in g["tags"], agents, i, g["branch_commits"])

    stages = {it["id"]: stage_of(it["id"]) for it in g["items"]}
    active = []
    for it in sorted((i for i in g["items"] if i["state"] == "active"), key=lambda x: we.id_key(x["id"])):
        mine = steps_of.get(it["id"], [])
        done = [s for s in mine if s["state"] == "finished"]
        last = sorted(done, key=lambda s: (s["ended_at"] or "", we.step_rank(s["kind"], s["round"]), s["id"]))[-1] \
            if done else None
        active.append({"item": it, "stage": stages[it["id"]], "agents": per_item.get(it["id"], []),
                       "started": [s for s in mine if s["state"] == "started"], "last": last})

    open_m = [m for m in g["matters"] if m["closed_at"] is None]
    decisions = sorted((m for m in open_m if m["kind"] == "decision" and m["owner"] == "drew"),
                       key=lambda m: (m["opened_at"], m["id"]))
    other_dec = [m for m in open_m if m["kind"] == "decision" and m["owner"] != "drew"]
    left = [m for m in open_m if m["kind"] == "leftover"]
    counts: dict = {}
    for m in open_m:
        if m["kind"] in ("leftover", "decision"):
            per = counts.setdefault(m["item_id"], {"leftovers": {"drew": 0, "coordinator": 0, "not_triaged": 0},
                                                   "decisions": {"drew": 0, "coordinator": 0, "not_triaged": 0}})
            per[m["kind"] + "s"][m["owner"] or "not_triaged"] += 1

    proms = [p for p in g["promotions"] if p["kind"] == "promotion"]
    last_items: list = []
    for p in reversed(proms):
        for pi in g["pitems"]:
            if pi["promotion_id"] == p["id"] and pi["item_id"] not in last_items:
                last_items.append(pi["item_id"])
        if len(last_items) >= ESCAPE_WINDOW:
            break
    last_items = last_items[:ESCAPE_WINDOW]
    # A closed escape still counts (it happened); a void one was never one (S9).
    esc = [m for m in g["matters"] if m["kind"] == "escape" and m["item_id"] in last_items and not is_void(m)]
    return {"stages": stages, "agents": per_item, "active": active, "queue": g["queue"],
            "decisions": decisions, "other_decisions": other_dec,
            "leftovers": {owner: [m for m in left if m["owner"] == owner] for owner in OWNERS},
            "untriaged": [m for m in left if m["owner"] is None], "counts": counts,
            "escape_window": last_items, "escapes": esc}


def c_resume(c: Ctx, a) -> int:
    g = gather(c)
    fs = findings(c, g)
    v = overview(g)
    lines: list[str] = []

    def w(text: str) -> None:
        """Lines the tool writes. Each value from a row, git, herdr or a
        state file goes through one_line() (`o`), so no stored text can
        start a line of its own (S3)."""
        lines.extend(text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"))

    o = one_line
    herdr = g["herdr"]
    states = {s["name"]: s for s in g["agent_states"]}

    def waits(it):
        return f"{o(it['waits_on'])}: {o(it['waits_note'])}" if it["waits_on"] else "-"

    w(f"Work state: {o(c.db)} (schema {o(g['meta'].get('schema_version'))}), at {now_iso()}\n")
    w(f"\n== Check: {len(fs)} finding(s) (rows against git, herdr, the files)\n")
    for x in fs:
        w(f"- [{o(x['check'])}] {o(x['says'])}\n")
        if x["fix"]:
            w(f"    fix: {o(x['fix'])}\n")

    w(f"\n== Under way: {len(v['active'])}\n")
    if not herdr.get("known"):
        w(f"(herdr not reachable: {o(herdr.get('reason'))}; stages computed without agents)\n")
    for x in v["active"]:
        it, st = x["item"], x["stage"]
        w(f"{o(it['id'])}  tier {o(it['tier'] or '-')}  {o(it['title'])}\n")
        w(f"    stage: {o(st['words'])}" + (f" ({o(st['detail'])})" if st["detail"] else "") + "\n")
        for ag in x["agents"]:
            mins = None
            if ag["name"] in states:
                mins = minutes_since(safe_utc(states[ag["name"]].get("launched_iso")))
            w(f"    agent: {o(ag['name'])} {o(ag['status'])}" + (f", {mins:g} min since launch" if mins is not None else "")
              + "\n")
        for s in x["started"]:
            w(f"    started: {s['kind']} round {s['round']} by {o(s['agent'] or s['by'])} at {o(s['started_at'])}\n")
        last = x["last"]
        if last:
            w(f"    last step: {last['kind']} round {last['round']}"
              + (f", {last['verdict']}" if last["verdict"] else "")
              + (f", graded {last['graded']}" if last["graded"] else "")
              + f" ({o(last['report'])}, {o(last['ended_at'])})\n")
        w(f"    next step: {o(it['next_step'] or '-')}\n")
        w(f"    waits on: {waits(it)}\n")
        if it["after_item"]:
            w(f"    after: {o(it['after_item'])}\n")

    w(f"\n== Queue: {len(v['queue'])}\n")
    for q in v["queue"]:
        flag = "  <- next" if q["next"] else ""
        w(f"{q['position']}. {o(q['id'])}  tier {o(q['tier'] or '-')}  {o(q['title'])}{flag}\n")
        if q["state"] == "held":
            w(f"    HELD: {o(q['held_why'])}\n")
        w(f"    next step: {o(q['next_step'] or '-')}\n")
        w(f"    waits on: {waits(q)}\n")
        if q["after_item"]:
            w(f"    after: {o(q['after_item'])}" + (f" ({o(q['blocked_by'])})" if q["blocked_by"] else " (promoted)")
              + "\n")

    decisions = v["decisions"]
    w(f"\n== Owed by Drew: {len(decisions)} open decision(s), oldest first\n")
    for m in decisions:
        blocks = " BLOCKS the item" if m["blocks"] else ""
        w(f"- #{m['id']} {o(m['item_id'] or '(no item)')}{blocks}, {o(local_day(m['opened_at']))}: {o(m['text'])}\n")
    for m in v["other_decisions"]:
        w(f"- #{m['id']} {o(m['item_id'] or '(no item)')} (owner {o(m['owner'] or 'not triaged')}): {o(m['text'])}\n")

    untriaged = v["untriaged"]
    triaged = sum(len(x) for x in v["leftovers"].values())
    w(f"\n== Open leftovers: {triaged} triaged, {len(untriaged)} not yet triaged\n")
    for owner in OWNERS:
        mine = v["leftovers"][owner]
        w(f"{owner}: {len(mine)}\n")
        for m in mine:
            w(f"- #{m['id']} {o(m['item_id'] or '(no item)')}: {o(m['text'])}\n")
    if untriaged:
        w(f"not triaged: {len(untriaged)} (older PROMOTIONS.md cells: `work.py matter list --open` "
          f"lists them; close each, or split it into triaged ones)\n")

    last_items, esc = v["escape_window"], v["escapes"]
    w(f"\n== Escapes within the last {ESCAPE_WINDOW} promoted items ({o(', '.join(last_items)) or 'none'}): "
      f"{len(esc)}\n")
    for m in esc:
        w(f"- #{m['id']} {o(m['item_id'])}: {o(m['text'])}\n")
    if len(esc) >= 2:
        w("    two or more: every item runs as tier 3 until Drew turns the tiers back on (WORKFLOW.md)\n")

    h = g["head"]
    w(f"\n== Tree: branch {o(h.get('branch') or '(detached)')}, HEAD {o(h.get('head'))}, "
      f"{'clean' if g['clean'] else 'NOT clean' if g['clean'] is False else 'status not read'}"
      + (", a rebase is under way" if h.get("rebase") else "") + "\n")
    w(f"\n== Agents scripts/agent.py knows: {len(g['agent_states'])}\n")
    for st in g["agent_states"]:
        s = herdr["by_name"].get(st["name"], "gone") if herdr.get("known") else "not known"
        mins = minutes_since(safe_utc(st.get("launched_iso")))
        w(f"- {o(st['name'])} ({o(st.get('item'))} {o(st.get('role'))}): {o(s)}"
          + (f", {mins:g} min since launch" if mins is not None else "") + f", report {o(st.get('report'))}\n")
    w("\n== Last five writes\n")
    for x in g["log"]:
        cmd = re.sub(r" --(?:db|repo) (?:'[^']*'|\S+)", "", x["cmd"])
        w(f"- {o(x['at'])} {o(x['who'])}: {o(cmd)}\n")
    w("\n== Not covered here; the session still does these by hand:\n"
      "- the voice runs marked as corrected in Grafana since the last session (docs/features/voice-capture.md);\n"
      "- every ROADMAP.md entry that waits on something outside the repo (an upstream release, a merge),\n"
      "  checked against its source.\n")
    c.out.write("\n".join(lines) + "\n")
    return 0


# ═══════════════════════════════ import ════════════════════════════════════

def first_commits_at(repo: Path, sha: str) -> dict:
    """work_export.git_first_commits() as of a named commit: {file: (short
    sha, author date, commit index)}, the first commit that added each file
    in docs/sprint. (That function reads HEAD and takes no revision.)"""
    rc, out = git(repo, "log", sha, "--no-renames", "--diff-filter=A", "--name-only",
                  "--format=@%H%x09%aI", "--reverse", "--", "docs/sprint")
    if rc != 0:
        raise refuse("git log over docs/sprint failed")
    first: dict = {}
    cur = date = None
    n = -1
    for line in out.splitlines():
        if line.startswith("@"):
            cur, _, date = line[1:].partition("\t")
            n += 1
        elif line.strip() and cur:
            name = line.strip()
            if name.startswith("docs/sprint/") and "/" not in name[len("docs/sprint/"):]:
                first.setdefault(name[len("docs/sprint/"):], (cur, date, n))
    return first


SEED_KEYS = {"note", "items", "matters"}
SEED_ITEM_KEYS = {"id", "tier", "tag", "deployed", "deploy_note", "graded", "state", "why", "criteria_commit",
                  "dropped_commit", "title", "position", "next_step", "note", "after"}
SEED_MATTER_KEYS = {"kind", "item", "owner", "state", "how", "text", "blocks"}


def load_seed(path: str | None) -> dict:
    """The coordinator's seed, refused for any key it does not know (top
    level, item or matter), an item given twice (in any case), or a field
    of the wrong kind (S4). A matter naming an item no file, promotion or
    seed item knows is refused by the import."""
    if not path:
        return {"items": [], "matters": []}
    try:
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise refuse(f"seed {path} cannot be read ({type(e).__name__})") from None
    if not isinstance(seed, dict) or not isinstance(seed.get("items", []), list) \
            or not isinstance(seed.get("matters", []), list):
        raise refuse(f"seed {path}: items and matters must be lists")
    extra = set(seed) - SEED_KEYS
    if extra:
        raise refuse(f"seed {path}: unknown top-level keys {sorted(extra)}")
    seen: dict = {}
    for s in seed.get("items", []):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            raise refuse(f"seed item {s!r:.60}: not an object with an id")
        extra = set(s) - SEED_ITEM_KEYS
        if extra:
            raise refuse(f"seed item {s.get('id')}: unknown keys {sorted(extra)}")
        i = norm(s["id"])
        if i in seen:
            raise refuse(f"seed item {i} is given twice (as {seen[i]!r} and {s['id']!r})")
        seen[i] = s["id"]
        if s.get("state") and s["state"] not in STATES:
            raise refuse(f"seed item {s.get('id')}: state {s['state']!r}")
    for m in seed.get("matters", []):
        if not isinstance(m, dict):
            raise refuse(f"seed matter {m!r:.60}: not an object")
        extra = set(m) - SEED_MATTER_KEYS
        if extra:
            raise refuse(f"seed matter {str(m.get('text', ''))[:60]!r}: unknown keys {sorted(extra)}")
        if m.get("kind") not in MATTER_KINDS or m.get("owner") not in OWNERS + (None,) \
                or m.get("state") not in ("open", "closed") or not m.get("text"):
            raise refuse(f"seed matter {m.get('text', '')[:60]!r}: kind, owner, state or text not valid")
        if (m["state"] == "closed") != bool(m.get("how")):
            raise refuse(f"seed matter {m['text'][:60]!r}: a closed matter needs 'how', an open one none")
    return seed


def legacy_steps(tx: Tx, item: str, steps: list[dict], at_files: dict) -> None:
    for s in steps:
        kind = s["kind"]
        rnd = s["n"] or 1
        name = s["name"] if kind == "other" else ""
        by = "coordinator" if kind == "criteria" or name.endswith("criteria") else "agent"
        verdict = note = None
        if kind == "test":
            vs = s.get("verdicts") or []
            own = [v for v in vs if v["subject"] == "item"] or vs[:1]
            verdict = own[0]["verdict"] if own else None
            others = [f"{v['subject']} {v['verdict']}" for v in vs if own and v is not own[0]]
            note = "; ".join(others) or None
        tx.insert("step", item_id=item, kind=kind, round=rnd, name=name, by=by, state="finished",
                  report=f"docs/sprint/{s['file']}", commit_sha=s["commit"], verdict=verdict, note=note,
                  ended_at=to_utc(s["committed_at"]))


def c_import(c: Ctx, a) -> int:
    we = work_export()
    sha = resolve(c.repo, a.at)
    seed = load_seed(a.seed)
    at_time = commit_time(c.repo, sha)
    rc, out = git(c.repo, "ls-tree", "--name-only", sha, "docs/sprint/")
    if rc != 0:
        raise refuse(f"git ls-tree {sha[:7]} docs/sprint failed")
    names = [Path(x).name for x in out.splitlines() if x.strip()]
    blobs = cat_files(c.repo, [f"{sha}:docs/sprint/{n}" for n in names])
    first = first_commits_at(c.repo, sha)
    not_read: list = []
    with tempfile.TemporaryDirectory(prefix="kronk-work-import-") as td:
        sprint = Path(td) / "docs" / "sprint"
        sprint.mkdir(parents=True)
        for n in names:
            text = blobs[f"{sha}:docs/sprint/{n}"]
            if text is not None:
                (sprint / n).write_text(text, encoding="utf-8")
        scanned = we.scan_items(Path(td), first, not_read)
    ptext = blobs.get(f"{sha}:docs/sprint/PROMOTIONS.md") or ""
    promotions = we.parse_promotions(ptext)
    tags = tag_commits(c.repo, merged=sha)

    made: list | None = None
    if c.db.exists():
        with Read(c.db) as r:
            n = r.one("SELECT (SELECT COUNT(*) FROM item) + (SELECT COUNT(*) FROM open_thing) AS n")["n"]
        if n:
            raise refuse(f"{c.db} holds rows; import-legacy fills an empty database only")
    else:
        made = missing_dirs(c.db.parent)
        create(c.db, c.repo, f"import-legacy --at {sha[:7]}")
    try:
        return import_rows(c, a, we, sha, seed, at_time, names, blobs, scanned, not_read, ptext, promotions,
                           tags)
    except BaseException:
        if made is not None:               # this run made the file: a refusal leaves none (N4)
            for p in (c.db, Path(f"{c.db}-wal"), Path(f"{c.db}-shm"), dump_path(c.db)):
                p.unlink(missing_ok=True)
            remove_dirs(made)
        raise


def import_rows(c: Ctx, a, we, sha: str, seed: dict, at_time: str, names: list, blobs: dict, scanned: dict,
                not_read: list, ptext: str, promotions: list, tags: dict) -> int:
    seed_items = {norm(s["id"]): s for s in seed.get("items", [])}

    ids = set(scanned) | {i for e in promotions for i in e["ids"]} | set(seed_items)
    promoted_at: dict = {}
    promoted_line: dict = {}
    first_step: dict = {}
    for i, it in scanned.items():
        dates = [to_utc(x["committed_at"]) for x in it["steps"] if x.get("committed_at")]
        if dates:
            first_step[i] = min(dates)
    added = lines_first_added(c.repo, sha, "docs/sprint/PROMOTIONS.md")
    plines = ptext.splitlines()
    for e in promotions:
        e["at"] = promotion_time(e, plines, added, first_step, at_time)
        for i in e["ids"]:
            promoted_at.setdefault(i, e["at"])
            promoted_line.setdefault(i, e["line"])
    notes: list = []
    with c.tx() as tx:
        tx.insert("meta", key="imported_at", value=sha)
        tx.insert("meta", key="imported_on", value=now_iso())
        # Items, then their after links (an item may follow one inserted later).
        for item in sorted(ids, key=we.id_key):
            it = scanned.get(item, {"steps": [], "title": None})
            s = seed_items.get(item, {})
            title = s.get("title") or we.short_title(it.get("title"), item) or item
            crit = next((f"docs/sprint/{x['file']}" for x in it["steps"] if x["kind"] == "criteria"), None)
            dates = [to_utc(x["committed_at"]) for x in it["steps"] if x.get("committed_at")]
            if item in promoted_at:
                state = "promoted"
                if s.get("state") and s["state"] != "promoted":
                    notes.append(f"{item}: seed state {s['state']}, but PROMOTIONS.md promotes it "
                                 f"(line {promoted_line[item]}); imported promoted")
            elif s.get("state"):
                state = s["state"]
            else:
                state = "active"
                notes.append(f"{item}: under way at {sha[:7]} and not in the seed; imported active")
            created = min(dates) if dates else at_time
            ended = promoted_at[item] if state == "promoted" else None
            next_step = s.get("next_step")
            if s.get("note"):
                next_step = f"{next_step}; {s['note']}" if next_step else s["note"]
            vals = dict(id=item, title=title, tier=s.get("tier"), state=state,
                        position=s.get("position") if state in ("queued", "held") else None,
                        criteria=crit, next_step=next_step, created_at=created, ended_at=ended)
            if state in ("queued", "held") and vals["position"] is None:
                raise refuse(f"seed item {item} is {state} with no position")
            if state == "held":
                vals["held_why"] = s.get("why") or "held (imported)"
            if state == "dropped":
                vals.update(ended_why=s.get("why"), ended_at=commit_time(c.repo, s["dropped_commit"])
                            if s.get("dropped_commit") else at_time)
                if s.get("criteria_commit"):
                    csha = resolve(c.repo, s["criteria_commit"])
                    vals["criteria"] = f"docs/sprint/{item}-criteria.md"
                    vals["created_at"] = commit_time(c.repo, csha)
            tx.insert("item", **vals)
            legacy_steps(tx, item, it["steps"], blobs)
            if state == "dropped" and s.get("criteria_commit"):
                csha = resolve(c.repo, s["criteria_commit"])
                tx.insert("step", item_id=item, kind="criteria", round=1, name="", by="coordinator",
                          state="finished", report=f"docs/sprint/{item}-criteria.md", commit_sha=csha,
                          note="the file was deleted when the item was dropped; read it at this commit",
                          ended_at=commit_time(c.repo, csha))
        for item, s in seed_items.items():
            if s.get("after"):
                after = norm(s["after"])
                check_after(tx, item, after)
                tx.update("item", {"id": item}, after_item=after)
        renumber(tx)
        # Promotions, in file order.
        for e in promotions:
            tag, note = e["tag"], None
            if tag and tag not in tags:
                note, tag = f"tag written as {tag}, not in git at {sha[:7]}", None
            if not tag and e["tag_written"]:
                note = f"tag written as '{e['tag_written']}'"
            deployed = 0 if e["tag_only"] else None
            kind = "followup" if e["note"] else "promotion"
            if kind == "promotion":
                for i in e["ids"]:
                    s = seed_items.get(i, {})
                    if s.get("tag") and not tag:
                        if s["tag"] not in tags:
                            raise refuse(f"seed tag {s['tag']} for {i} is not in git at {sha[:7]}")
                        tag, note = s["tag"], (note + "; " if note else "") + "tag from the seed"
                    elif s.get("tag") and s["tag"] != tag:
                        notes.append(f"{i}: seed tag {s['tag']}, PROMOTIONS.md tag {tag}; the file's kept")
                    if "deployed" in s:
                        deployed = 1 if s["deployed"] else 0
                    if s.get("deploy_note"):
                        note = (note + "; " if note else "") + s["deploy_note"]
            pid = tx.insert("promotion", kind=kind, tag=tag, commit_sha=tags.get(tag) if tag else None,
                            at=e["at"], deployed=deployed, verified=f"PROMOTIONS.md:{e['line']}", note=note)
            for i in e["ids"]:
                tx.insert("promotion_item", promotion_id=pid, item_id=i)
        # Gradings from the seed: the last test the item was promoted on.
        for item, s in seed_items.items():
            if not s.get("graded"):
                continue
            last = tx.one("SELECT * FROM step WHERE item_id = ? AND kind = 'test' AND state = 'finished' "
                          "ORDER BY round DESC LIMIT 1", item)
            if last is None:
                raise refuse(f"seed grading for {item}: it has no test step")
            grade = "pass" if item in promoted_at else None
            tx.update("step", {"id": last["id"]}, graded=grade,
                      note=(last["note"] + "; " if last["note"] else "") + s["graded"])
        # Matters: the coordinator's triage, then the older cells untriaged.
        src = Path(a.seed).name if a.seed else None
        triaged = 0
        for m in seed.get("matters", []):
            item = norm(m["item"]) if m.get("item") else None
            if item and item not in ids:
                raise refuse(f"seed matter names {item}, which is not an item")
            closed = m["state"] == "closed"
            tx.insert("open_thing", kind=m["kind"], item_id=item, owner=m["owner"], text=m["text"],
                      blocks=1 if m.get("blocks") else 0, source=src, opened_at=at_time,
                      closed_at=at_time if closed else None, closed_how=m.get("how") if closed else None)
            triaged += 1
        older = older_cells(ptext, promotions)
        for line, item in older:
            tx.insert("open_thing", kind="leftover", item_id=item, owner=None, text=f"see PROMOTIONS.md:{line}",
                      source=f"PROMOTIONS.md:{line}", opened_at=at_time)
        counts = {t: tx.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in
                  ("item", "step", "promotion", "promotion_item", "open_thing")}
        reports = {r["report"] for r in tx.all("SELECT report FROM step WHERE report IS NOT NULL")}
        on_rows = {r["tag"] for r in tx.all("SELECT tag FROM promotion WHERE tag IS NOT NULL")}
    all_tags = sorted(tag_commits(c.repo))
    md = sorted(n for n in names if n.endswith(".md"))
    summary = {
        "import": sha[:7], "db": str(c.db), "counts": counts,
        "matters": {"triaged": triaged, "untriaged_older_cells": len(older)},
        "tags": {"in_git": len(all_tags), "on_a_promotion": len(set(all_tags) & on_rows),
                 "not_on_a_promotion": [t for t in all_tags if t not in on_rows]},
        "files": {"md_in_docs_sprint": len(md),
                  "a_steps_report": len([n for n in md if f"docs/sprint/{n}" in reports]),
                  "not_a_report": [n for n in md if f"docs/sprint/{n}" not in reports]},
        "not_read_by_the_parser": not_read, "notes": notes,
    }
    c.emit(summary)
    if a.compare:
        for d in compare(c, a.compare):
            c.emit(d)
    return 0


def lines_first_added(repo: Path, sha: str, path: str) -> dict[str, str]:
    """{line text: UTC time of the first commit (in sha's history) that
    added a line with exactly that text to the file}. One git call."""
    rc, out = git(repo, "log", sha, "--reverse", "--no-renames", "-p", "-U0", "--format=@%H%x09%cI", "--", path)
    if rc != 0:
        return {}
    first: dict = {}
    when = None
    for line in out.splitlines():
        m = re.match(r"^@([0-9a-f]{40})\t(\S+)$", line)
        if m:
            when = safe_utc(m.group(2))
        elif when and line.startswith("+") and not line.startswith(("+++ b/", "+++ /dev/null")):
            first.setdefault(line[1:], when)
    return first


def promotion_time(e: dict, plines: list[str], added: dict, first_step: dict, fallback: str) -> str:
    """An imported promotion's time (S10): the time of the commit that
    first added its line to PROMOTIONS.md, when that commit falls on the
    date written in the entry; else the written date at noon local; else
    the import commit's time. Never before the first step of an item it
    promotes."""
    text = plines[e["line"] - 1] if 0 < e["line"] <= len(plines) else None
    d = work_export().DATE_RE.search(e.get("date") or "")
    written = d.group(0) if d else None
    when = added.get(text) if text is not None else None
    if when and (written is None or local_day(when) == written):
        at = when
    elif written:
        at = to_utc(f"{written}T12:00:00")
    else:
        at = when or fallback
    floor = [first_step[i] for i in e["ids"] if i in first_step]
    return max([at, *floor])


def older_cells(ptext: str, promotions: list[dict]) -> list[tuple[int, str]]:
    """(line, item) for each table row whose "Held or left over" cell has
    text, and each '- Not done:' bullet: one untriaged row each."""
    lines = ptext.splitlines()
    out = []
    for e in promotions:
        if e["kind"] != "row":
            continue
        cells = lines[e["line"] - 1].split("|")
        if len(cells) >= 7 and cells[5].strip():
            out.append((e["line"], e["ids"][0]))
    heads = sorted((e["line"], e["ids"][0]) for e in promotions if e["kind"] == "heading")
    for n, line in enumerate(lines, start=1):
        if re.match(r"^- Not done:", line):
            owner = [i for ln, i in heads if ln < n]
            out.append((n, owner[-1] if owner else None))
    return sorted(out)


def compare(c: Ctx, path: str) -> list[dict]:
    """The imported rows against a work.json, item for item: group, stage
    (computed without agents), test verdicts, tags."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    with Read(c.db) as r:
        items = {i["id"]: i for i in r.all("SELECT * FROM item")}
        steps = r.all("SELECT * FROM step")
        proms = r.all("SELECT p.*, pi.item_id FROM promotion p JOIN promotion_item pi ON pi.promotion_id = p.id")
    tags = tag_commits(c.repo)
    commits = head_commits(c.repo)
    out = []
    under = set(data.get("under_way") or [])
    done_ids = {d["id"] for d in data.get("done") or []}
    for it in data.get("items") or []:
        i = it["id"]
        row = items.get(i)
        if row is None:
            out.append({"compare": "item", "item": i, "json": "present", "db": "absent"})
            continue
        jg = "under_way" if i in under else "done" if i in done_ids else "other"
        dg = {"queued": "under_way", "held": "under_way", "active": "under_way", "promoted": "done",
              "dropped": "dropped"}[row["state"]]
        if jg != dg:
            out.append({"compare": "group", "item": i, "json": jg, "db": dg})
        mine = [s for s in steps if s["item_id"] == i]
        promoted = any(p["item_id"] == i and p["kind"] == "promotion" for p in proms)
        # item None: work.json carries work_export's "item-<id>" as it is.
        st = item_stage(mine, promoted, f"item-{i}" in tags, None, None, commits)
        js = (it.get("stage") or {})
        if js.get("stage") != st["stage"] or js.get("detail") != st["detail"]:
            out.append({"compare": "stage", "item": i, "json": [js.get("stage"), js.get("detail"), js.get("by")],
                        "db": [st["stage"], st["detail"], st["by"]]})
        by_report = {Path(s["report"]).name: s for s in mine if s["report"]}
        for js_step in it.get("steps") or []:
            s = by_report.get(js_step["file"])
            if s is None:
                out.append({"compare": "step", "item": i, "json": js_step["file"], "db": "absent"})
                continue
            if js_step["kind"] == "test":
                vs = js_step.get("verdicts") or []
                own = [v for v in vs if v["subject"] == "item"] or vs[:1]
                jv = own[0]["verdict"] if own else None
                if jv != s["verdict"]:
                    out.append({"compare": "verdict", "item": i, "file": js_step["file"], "json": jv,
                                "db": s["verdict"]})
    for d in data.get("done") or []:
        mine = [p for p in proms if p["item_id"] == d["id"] and p["verified"] == f"PROMOTIONS.md:{d['line']}"]
        if not mine:
            out.append({"compare": "promotion", "item": d["id"], "json": f"line {d['line']}", "db": "absent"})
        elif mine[0]["tag"] != d["tag"]:
            out.append({"compare": "tag", "item": d["id"], "json": d["tag"], "db": mine[0]["tag"]})
    return out


# ═════════════════════════ for scripts/agent.py ════════════════════════════
# agent.py calls these and nothing else; they take the launch repo and
# write only when the database describes that repo. Busy timeout 2 s.

def agent_db() -> Path:
    return default_db()


def agent_started(repo: Path, item: str, kind: str, rnd: int, agent: str, model: str | None,
                  report: str | None, at: str | None, cmd: str) -> dict:
    return step_start(agent_db(), repo, item, kind, rnd, agent, model, report, at, cmd,
                      timeout=AGENT_BUSY_S, writer=f"agent.py ({who()})")


def agent_finished(repo: Path, item: str, kind: str, rnd: int, agent: str, model: str | None,
                   report: str | None, started_at: str | None, cmd: str) -> dict:
    return step_finish(agent_db(), repo, item, kind, rnd, cmd, agent=agent, model=model, report=report,
                       started_at=started_at, timeout=AGENT_BUSY_S, writer=f"agent.py ({who()})")


def agent_closed(repo: Path, item: str, kind: str, rnd: int, agent: str, model: str | None,
                 report: str | None, started_at: str | None, committed: bool, why: str, cmd: str) -> dict:
    """close: a step whose report is committed is finished from it;
    otherwise a started step is marked stopped with why."""
    if committed and report:
        return step_finish(agent_db(), repo, item, kind, rnd, cmd, agent=agent, model=model, report=report,
                           started_at=started_at, timeout=AGENT_BUSY_S, writer=f"agent.py ({who()})",
                           insert_only_active=True)
    return step_stop(agent_db(), repo, item, kind, rnd, why, cmd, timeout=AGENT_BUSY_S,
                     writer=f"agent.py ({who()})", agent=agent, model=model, report=report, started_at=started_at)


# ═══════════════════════════════════ main ══════════════════════════════════

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorkError("usage", message)


def parser() -> argparse.ArgumentParser:
    ap = Parser(prog="work.py", description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="the database (default: KRONK_WORK_DB, else ~/.local/state/kronk-work/work.db)")
    ap.add_argument("--repo", default=str(REPO), help="the repo it describes (default: this one)")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--db", help=argparse.SUPPRESS)
    common.add_argument("--repo", help=argparse.SUPPRESS)

    def p(name, fn, **kw):
        x = sub.add_parser(name, parents=[] if fn is None else [common], **kw)
        if fn is not None:
            x.set_defaults(fn=fn)
        return x

    p("init", c_init)
    x = p("add", c_add)
    x.add_argument("--title", required=True)
    x.add_argument("--tier", type=int, choices=(1, 2, 3))
    x.add_argument("--criteria")
    x.add_argument("--after")
    x.add_argument("--id", help="an explicit id: a split step (M34-step2) or the next number")
    x = p("show", c_show)
    x.add_argument("item")
    x = p("list", c_list)
    x.add_argument("--state", choices=STATES)
    p("queue", c_queue)
    x = p("start", c_start)
    x.add_argument("item")
    x = p("move", c_move)
    x.add_argument("item")
    x.add_argument("position", type=int)
    x = p("hold", c_hold)
    x.add_argument("item")
    x.add_argument("--why", required=True)
    x = p("unhold", c_unhold)
    x.add_argument("item")
    x = p("drop", c_drop)
    x.add_argument("item")
    x.add_argument("--why", required=True)
    x = p("after", c_after)
    x.add_argument("item")
    x.add_argument("other", nargs="?")
    x.add_argument("--clear", action="store_true")
    x = p("note", c_note)
    x.add_argument("item")
    x.add_argument("--next")
    x.add_argument("--waits", choices=WAITS + ("none",))
    x.add_argument("--waits-note")
    x.add_argument("--tier", type=int, choices=(1, 2, 3))
    x.add_argument("--criteria")
    x.add_argument("--title")

    x = p("step", None)
    ss = x.add_subparsers(dest="step_cmd", required=True, parser_class=Parser)

    def sp(name, fn, kinds=KINDS):
        y = ss.add_parser(name, parents=[common])
        y.set_defaults(fn=fn)
        y.add_argument("item")
        y.add_argument("kind", choices=kinds)
        y.add_argument("--round", type=int, default=1)
        return y
    y = sp("add", c_step_add)
    y.add_argument("--name", default="")
    y.add_argument("--report")
    y = sp("start", c_step_start, AGENT_KINDS)
    y.add_argument("--agent", required=True)
    y.add_argument("--model")
    y.add_argument("--report")
    y.add_argument("--at", help="the launch time, to close a hole (agent.py's launched_iso)")
    y = sp("finish", c_step_finish)
    y.add_argument("--name", default="")
    y = sp("stop", c_step_stop)
    y.add_argument("--name", default="")
    y.add_argument("--why", required=True)
    y = sp("refresh", c_step_refresh)
    y.add_argument("--name", default="")
    y = sp("note-gone", c_step_note_gone)
    y.add_argument("--name", default="")

    x = p("grade", c_grade)
    x.add_argument("item")
    x.add_argument("grade", choices=("pass", "fail"))
    x.add_argument("--kind", default="test", choices=KINDS)
    x.add_argument("--round", type=int)
    x = p("promote", c_promote)
    x.add_argument("items", nargs="*", help="the items promoted; none, with --label, for a deploy with no item")
    x.add_argument("--tag")
    x.add_argument("--no-tag", action="store_true")
    x.add_argument("--deployed", required=True, choices=("yes", "no"))
    x.add_argument("--verified", help="the committed promotion checks (default docs/sprint/<item>-promote.md)")
    x.add_argument("--untested", help="what shipped that no test agent saw")
    x.add_argument("--followup", action="store_true", help="a note on an item already promoted")
    x.add_argument("--label", help="what was deployed, for a promotion with no item")
    x.add_argument("--at", help="when it was deployed (ISO time; default now)")

    x = p("matter", None)
    ms = x.add_subparsers(dest="matter_cmd", required=True, parser_class=Parser)
    y = ms.add_parser("add", parents=[common])
    y.set_defaults(fn=c_matter_add)
    y.add_argument("kind", choices=MATTER_KINDS)
    y.add_argument("--owner", required=True, choices=OWNERS)
    y.add_argument("--text", required=True)
    y.add_argument("--item")
    y.add_argument("--blocks", action="store_true", help="the item waits on this")
    y.add_argument("--source")
    y = ms.add_parser("close", parents=[common])
    y.set_defaults(fn=c_matter_close)
    y.add_argument("id", type=int)
    y.add_argument("--how", required=True)
    y = ms.add_parser("void", parents=[common])
    y.set_defaults(fn=c_matter_void)
    y.add_argument("id", type=int)
    y.add_argument("--why", required=True)
    y = ms.add_parser("list", parents=[common])
    y.set_defaults(fn=c_matter_list)
    y.add_argument("--open", action="store_true")
    y.add_argument("--kind", choices=MATTER_KINDS)
    y.add_argument("--item")

    x = p("log", c_log)
    x.add_argument("--item")
    x.add_argument("--day")
    x.add_argument("--limit", type=int)
    p("check", c_check)
    p("resume", c_resume)
    x = p("dump", c_dump)
    x.add_argument("--out")
    x = p("restore-dump", c_restore_dump)
    x.add_argument("--from", dest="source")
    x = p("import-legacy", c_import)
    x.add_argument("--at", required=True)
    x.add_argument("--seed")
    x.add_argument("--compare", help="a work.json to compare the import with, item for item")
    return ap


def main(argv=None, out=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    cmd = "work.py"
    try:
        a = parser().parse_args(argv)
        cmd = f"work.py {a.cmd}"
        db = Path(a.db) if getattr(a, "db", None) else default_db()
        c = Ctx(db, Path(a.repo), out, argv)
        rc = a.fn(c, a)
        out.flush()
        return rc
    except WorkError as e:
        print(f"{cmd}: {e}", file=sys.stderr)
        return 2 if e.kind in WorkError.DB_KINDS else 1
    except sqlite3.Error as e:
        err = classify(e, BUSY_S)
        print(f"{cmd}: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{cmd}: interrupted", file=sys.stderr)
        return 1
    except Exception as e:                     # never a traceback to the coordinator
        print(f"{cmd}: internal error ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
