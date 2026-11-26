"""GET /api/voice_runs (work item M30 step C): the voice runs of the last 90
days, for the "Heard" section of /work. Read-only; mounted by main.py with
include_router.

scripts/voice_runs_export.py runs on the host every 60 s
(systemd/kronk-voice-runs-export.timer). It keeps every household voice run
in a record outside every container's mount and publishes the last 90 days
to data/voice_runs.db (and data/dev/), a SQLite file built whole and renamed
into place, with a status file beside it, data/voice_runs.json: the last
good read, the last error, what the run did, and the summary the folded
section shows (runs in the last 24 hours, the newest, how many of those are
marked wrong or carry an error). docs/features/voice-dataset.md.

This route reads both and nothing else. The copy is opened once per
request, mode=ro&immutable=1: no -wal, -shm or -journal is made beside it,
and a copy renamed into place while the request runs is not seen by it (the
open file is the old one). It calls no Tempo, no Home Assistant, no model,
and imports no HTTP client.

The query (docs/sprint/M30-review.md, point 7): newest first, keyset paging
on (start_s, run_id), never OFFSET; `limit` (default 200, clamped to 500; 0
answers the status and the summary without opening the copy); `before`, the
cursor `next_before` of the page before; `q`, matched in what was heard
and in the reply, bound and with its LIKE wildcards escaped; `flagged`
(marked wrong or carrying an error); `include_dev`. With `changed_after`
(a `max_seq` the page has seen) it answers only the rows inserted or
changed since, each with `match` (whether it passes the page's filters),
and `resync` when the page must load its list again: a row went away (a
removal raises no seq, so only the copy's row count shows it; also a row
leaving the 90 days), or more changed than one answer holds. To tell an
insert from a change it needs the page's last `known_rows` and
`known_built_s` (the copy's meta.rows and meta.built_s). See
docs/features/work-page.md, "Heard".
"""
import datetime as dt
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import servers

router = APIRouter()

VOICE_RUNS_DB = Path(os.getenv("VOICE_RUNS_DB", "/data/voice_runs.db"))
VOICE_RUNS_STATUS = Path(os.getenv("VOICE_RUNS_STATUS", "/data/voice_runs.json"))
# The exporter runs every 60 s; a status older than this is stale (criterion C3).
STATUS_MAX_AGE_S = 180
STATUS_EVERY_S = 60
TIMER = "systemd/kronk-voice-runs-export.timer"
STATUS_SCHEMA_KNOWN = 1
COPY_SCHEMA_KNOWN = 1
LIMIT_DEFAULT = 200
LIMIT_MAX = 500
Q_MAX = 200

# What a row carries to the page. Columns of the published copy only; the
# copy holds no raw_trace, no audio columns, no removed rows.
COLUMNS = (
    "run_id", "trace_id", "start_s", "end_s", "local_time", "local_day", "service", "pipeline", "is_dev",
    "satellite_id", "stages", "wake_detection", "heard", "heard_chars", "heard_source", "reply", "reply_chars",
    "response_type", "tier", "rule", "direct_action", "tools", "kronk_trace_id", "error_code", "corrected_by",
    "corrected_after_s", "corrected_by_run_id", "correction_kind", "correction_of_run_id", "mark_basis",
    "window_cut_short", "stt_engine", "listen_ms", "transcribe_ms", "intent_ms", "first_seen_at", "seq",
)


class BadQuery(ValueError):
    """A parameter this route does not take as given. HTTP 400."""


def _flag(params, name: str) -> bool:
    v = params.get(name)
    if v is None or v == "":
        return False
    if v.lower() in ("1", "true", "yes", "on"):
        return True
    if v.lower() in ("0", "false", "no", "off"):
        return False
    raise BadQuery(f"{name} must be 1 or 0, not {v[:40]!r}")


def _int(params, name: str, default=None, minimum: int = 0):
    v = params.get(name)
    if v is None or v == "":
        return default
    try:
        n = int(v)
    except ValueError:
        raise BadQuery(f"{name} must be a whole number, not {v[:40]!r}") from None
    if n < minimum:
        raise BadQuery(f"{name} must be {minimum} or more, not {n}")
    return n


def _float(params, name: str):
    v = params.get(name)
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        raise BadQuery(f"{name} must be a number, not {v[:40]!r}") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise BadQuery(f"{name} must be a finite number")
    return f


def parse_query(params) -> dict:
    """The request's parameters, checked. Raises BadQuery."""
    limit = _int(params, "limit", LIMIT_DEFAULT)
    out = {"limit": min(limit, LIMIT_MAX), "limit_asked": limit, "before": None, "q": None,
           "flagged": _flag(params, "flagged"), "include_dev": _flag(params, "include_dev"),
           "changed_after": _int(params, "changed_after"), "known_rows": _int(params, "known_rows"),
           "known_built_s": _float(params, "known_built_s")}
    b = params.get("before")
    if b:
        s, sep, rid = b.partition(",")
        try:
            start = float(s)
        except ValueError:
            start = None
        if not sep or not rid or start is None or start != start:
            raise BadQuery("before must be the answer's next_before, \"<start_s>,<run_id>\"")
        out["before"] = (start, rid)
    q = params.get("q")
    if q is not None and q.strip():
        if len(q) > Q_MAX:
            raise BadQuery(f"q is longer than {Q_MAX} characters")
        out["q"] = q.strip()
    if out["changed_after"] is not None and out["before"] is not None:
        raise BadQuery("changed_after and before do not go together")
    return out


def like_pattern(q: str) -> str:
    """q as a LIKE pattern matching it anywhere, its own % and _ (and the
    escape character) taken literally (ESCAPE '\\')."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def filters(query: dict) -> tuple[str, list]:
    """The WHERE terms of the page's filters, and their bound values."""
    terms, args = [], []
    if not query["include_dev"]:
        terms.append("is_dev = 0")
    if query["flagged"]:
        terms.append("(corrected_by IS NOT NULL OR error_code IS NOT NULL)")
    if query["q"]:
        terms.append("(heard LIKE ? ESCAPE '\\' OR reply LIKE ? ESCAPE '\\')")
        pat = like_pattern(query["q"])
        args += [pat, pat]
    return " AND ".join(terms) or "1", args


# ── the status file ─────────────────────────────────────────────────────────

def status_source(now: float | None = None) -> tuple[dict, dict | None]:
    """The envelope ({file, state, age_s, max_age_s, every_s, reason,
    schema, ok, last_ok_at, last_error, last_error_at, warnings}) and the
    exporter's summary, or None. state is fresh, stale, missing,
    unreadable or unknown_schema; the summary is given when stale too (the
    last known counts) and null otherwise."""
    src = servers.read_source(VOICE_RUNS_STATUS, STATUS_MAX_AGE_S, STATUS_EVERY_S, TIMER, now)
    data = src.pop("data")
    for k in ("schema", "ok", "last_ok_at", "last_error", "last_error_at"):
        src[k] = None
    src["warnings"] = []
    if data is None:
        return src, None
    schema = data.get("schema")
    src["schema"] = schema
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        src["state"] = "unknown_schema"
        src["reason"] = (f"{VOICE_RUNS_STATUS.name} has schema {schema!r}, which this orchestrator does not "
                         f"know (it reads schema {STATUS_SCHEMA_KNOWN})")
        return src, None
    ok = data.get("ok")
    src["ok"] = ok if isinstance(ok, bool) else None
    for k in ("last_ok_at", "last_error", "last_error_at"):
        v = data.get(k)
        src[k] = v if isinstance(v, str) else None
    src["warnings"] = status_warnings(data)
    summary = data.get("summary")
    return src, summary if isinstance(summary, dict) else None


def _n(d, key) -> int:
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def status_warnings(data: dict) -> list[str]:
    """What the status file says is wrong beside `ok` (M30 rulings: `ok:
    true` is not the only signal). Counts and names only: the exporter puts
    no run's text in its status file, and none is added here."""
    out = []
    if data.get("record_lost"):
        out.append("the record is gone: the exporter is not making it again (docs/runbooks/restore.md, step 14)")
    run = data.get("this_run") if isinstance(data.get("this_run"), dict) else {}
    if _n(run, "truncated_windows"):
        out.append(f"the last read was cut short by Tempo's search limit in {_n(run, 'truncated_windows')} "
                   "window(s): runs in them may be missing until the next read")
    if _n(run, "trace_read_failures"):
        out.append(f"{_n(run, 'trace_read_failures')} whole-trace read(s) failed in the last run; "
                   "those rows wait for a later run")
    if _n(run, "enrich_failures"):
        out.append(f"{_n(run, 'enrich_failures')} Kronk-trace enrichment(s) failed in the last run")
    check = data.get("last_check") if isinstance(data.get("last_check"), dict) else {}
    if _n(check, "differing"):
        out.append(f"the last hourly check found {_n(check, 'differing')} row(s) Tempo now gives differently "
                   "(run_changes, why 'hourly check')")
    if check.get("error"):
        out.append("the last hourly check could not read Tempo")
    if data.get("check_progress"):
        out.append("an hourly check is under way across runs")
    return out


# ── the published copy ──────────────────────────────────────────────────────

def open_copy(path: Path | None = None) -> sqlite3.Connection:
    """The published copy, read-only and immutable: nothing is written
    beside it, and a copy renamed into place later is not seen by this
    connection. Raises FileNotFoundError, or sqlite3.Error."""
    path = path or VOICE_RUNS_DB
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def copy_meta(conn) -> dict:
    """{state, reason, schema, rows, max_seq, content_sha256, built_at,
    built_s, window_start_s}. state ok, damaged (its row count is not
    meta's: the exporter mends it on its next run; its rows are still
    served) or unknown_schema."""
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    out = {"state": "ok", "reason": None, "schema": user_version, "rows": None, "max_seq": None,
           "content_sha256": None, "built_at": None, "built_s": None, "window_start_s": None}
    if user_version < COPY_SCHEMA_KNOWN:
        out["state"] = "unknown_schema"
        out["reason"] = (f"{VOICE_RUNS_DB.name} has schema {user_version}, which this orchestrator does not "
                         f"know (it reads schema {COPY_SCHEMA_KNOWN})")
        return out
    try:
        m = conn.execute("SELECT schema, rows, max_seq, content_sha256, built_at, built_s, window_start_s "
                         "FROM meta WHERE id = 1").fetchone()
        conn.execute(f"SELECT {', '.join(COLUMNS)} FROM runs LIMIT 0").fetchall()
    except sqlite3.Error as e:
        out["state"] = "unknown_schema"
        out["reason"] = f"{VOICE_RUNS_DB.name} (schema {user_version}) is not the shape this orchestrator reads: {e}"
        return out
    if m is None:
        out["state"] = "unknown_schema"
        out["reason"] = f"{VOICE_RUNS_DB.name} has no meta row"
        return out
    out.update(zip(("schema", "rows", "max_seq", "content_sha256", "built_at", "built_s", "window_start_s"), m))
    if user_version > COPY_SCHEMA_KNOWN:
        out["reason"] = (f"{VOICE_RUNS_DB.name} has schema {user_version}, newer than this orchestrator's "
                         f"{COPY_SCHEMA_KNOWN}: columns added since are not shown")
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    if count != out["rows"]:
        out["state"] = "damaged"
        out["reason"] = (f"{VOICE_RUNS_DB.name} holds {count} rows and its meta says {out['rows']}: the exporter "
                         "rebuilds it on its next run; the rows shown are what it holds")
    return out


def row_out(cols: tuple, r: tuple) -> dict:
    d = dict(zip(cols, r))
    heard = d.get("heard")
    if heard:
        d["heard_state"] = "text"
    elif d.get("heard_chars") == 0:
        d["heard_state"] = "nothing_heard"       # Whisper heard no words (stt-no-text-recognized)
    else:
        d["heard_state"] = "not_recorded"        # traced without text (TELEMETRY_RECORD_TEXT off)
    reply = d.get("reply") or ""
    head, _, rest = reply.strip().partition("\n")
    d["reply_head"] = head.strip() or None
    d["reply_rest"] = rest.strip() or None
    d["flagged"] = d.get("corrected_by") is not None or d.get("error_code") is not None
    return d


def list_runs(conn, query: dict) -> tuple[list[dict], str | None]:
    """A page of rows, newest first, and the cursor of the next page (None
    at the end)."""
    where, args = filters(query)
    if query["before"] is not None:
        where += " AND (start_s, run_id) < (?, ?)"
        args += list(query["before"])
    limit = query["limit"]
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM runs WHERE {where} "
                        "ORDER BY start_s DESC, run_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = [row_out(COLUMNS, r) for r in rows]
    nxt = f"{out[-1]['start_s']!r},{out[-1]['run_id']}" if more and out else None
    return out, nxt


def changed_runs(conn, query: dict, meta: dict) -> tuple[list[dict], bool]:
    """The rows whose seq is above changed_after, newest first, each with
    `match` (whether it passes the filters), and whether the page must load
    its list again."""
    after = query["changed_after"]
    where, args = filters(query)
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)}, ({where}) FROM runs WHERE seq > ? "
                        "ORDER BY start_s DESC, run_id DESC LIMIT ?", (*args, after, LIMIT_MAX + 1)).fetchall()
    resync = len(rows) > LIMIT_MAX
    rows = rows[:LIMIT_MAX]
    out = []
    for r in rows:
        d = row_out(COLUMNS, r[:-1])
        d["match"] = bool(r[-1])
        out.append(d)
    if not resync:
        resync = rows_went_away(conn, query, meta)
    return out, resync


def rows_went_away(conn, query: dict, meta: dict) -> bool:
    """Whether a row the page could hold is gone from the copy: removed by
    hand (no seq is raised) or out of the 90 days. The copy's row count now
    must be the page's known count plus the rows inserted since the copy
    the page last saw was built; any other count means a row went. Without
    known_rows and known_built_s it cannot be told, and the answer is to
    load again."""
    known, built = query["known_rows"], query["known_built_s"]
    if known is None or built is None or meta.get("rows") is None:
        return True
    if meta.get("built_s") is not None and abs(float(meta["built_s"]) - built) < 1e-3:
        return meta["rows"] != known            # the same copy: only a mismatch of the page's own
    stamp = dt.datetime.fromtimestamp(built, dt.timezone.utc).isoformat(timespec="seconds")
    inserted = conn.execute("SELECT COUNT(*) FROM runs WHERE seq > ? AND first_seen_at > ?",
                            (query["changed_after"], stamp)).fetchone()[0]
    return meta["rows"] != known + inserted


def voice_runs(params, now: float | None = None) -> dict:
    query = parse_query(params)
    source, summary = status_source(now)
    out = {"source": source, "summary": summary, "copy": None, "runs": [], "next_before": None,
           "max_seq": None, "resync": False,
           "query": {k: v for k, v in query.items() if k not in ("before",)}
           | {"before": params.get("before") or None}}
    if query["limit"] == 0 and query["changed_after"] is None:
        return out
    try:
        conn = open_copy()
    except FileNotFoundError:
        out["copy"] = {"state": "missing", "reason": (f"{VOICE_RUNS_DB.name} does not exist: the host exporter "
                                                      f"({TIMER}) is not installed or has not run")}
        return out
    except sqlite3.Error as e:
        out["copy"] = {"state": "unreadable", "reason": f"{VOICE_RUNS_DB.name} cannot be opened: {e}"}
        return out
    try:
        meta = copy_meta(conn)
        out["copy"] = meta
        if meta["state"] == "unknown_schema":
            return out
        out["max_seq"] = meta["max_seq"]
        if query["changed_after"] is not None:
            out["runs"], out["resync"] = changed_runs(conn, query, meta)
        else:
            out["runs"], out["next_before"] = list_runs(conn, query)
    except sqlite3.Error as e:
        out["copy"] = {"state": "unreadable", "reason": f"{VOICE_RUNS_DB.name} cannot be read: {e}"}
        out["runs"], out["next_before"], out["resync"] = [], None, False
    finally:
        conn.close()
    return out


@router.get("/api/voice_runs")
def voice_runs_route(request: Request):
    # A plain def: FastAPI runs it in its thread pool, so the SQLite read
    # never holds the event loop.
    try:
        return voice_runs(request.query_params)
    except BadQuery as e:
        return JSONResponse({"error": str(e)}, status_code=400)
