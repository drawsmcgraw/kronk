"""GET /api/work (work item M27): the state of the work items, for /work.
Read-only; mounted by main.py with include_router.

scripts/work_export.py runs on the host every 60 s
(systemd/kronk-work-export.timer) and writes data/work.json and
data/dev/work.json: the work items, the queue, their steps and stages and
the promotions from the dump of the work-state database (work item M34),
the backlog from ROADMAP.md, and which agents herdr shows working.
Production runs from a release snapshot and can see neither the working
tree nor the database (outside every container's mount), so the file is
its only source. This route reads that file and nothing else: no database,
no dump.

This route reads the file and says how fresh it is: fresh, stale (older
than 180 s; the outline is still returned, as the last known state),
missing, unreadable, or of a schema this orchestrator does not know. It
never writes, runs nothing and calls no model. A schema higher than the one
known here is read anyway: once item-M27 is deployed the exporter only adds
keys, and raises the schema when it does. See docs/features/work-page.md.

Polled every 3 s by each open /work (work item M32 step 1), so:

- the parsed file is kept in memory keyed by its device, inode, mtime and
  size (servers.read_source's cache): an unchanged file costs an open and
  an fstat, and is read once per change however many pages poll;
- ?have=<ts>, the file's ts the page already shows, answers the same
  envelope with "work": null and "unchanged": true when the file's ts is
  that one. The envelope (state, age_s) is fresh on every poll;
- every envelope carries page_version, the hash of work.html and kronk.css
  taken once at start and stamped into the page /work serves (main.py), so
  an open page learns that another page is deployed; and server_s, this
  host's clock, which the page uses for its reload limit instead of the
  browser's;
- Cache-Control: no-store; the route, /work and /api/voice_runs are left
  out of the telemetry (telemetry._DEFAULT_EXCLUDED).
"""
import hashlib
import os
import time
from pathlib import Path

from fastapi import APIRouter, Response

import servers

router = APIRouter()

WORK_PATH = Path(os.getenv("WORK_PATH", "/data/work.json"))
# The exporter runs every 60 s; older than this, the file is stale.
WORK_MAX_AGE_S = 180
WORK_EVERY_S = 60
WORK_TIMER = "systemd/kronk-work-export.timer"
SCHEMA_KNOWN = 3                # 2 (M34): drawn from the work-state database; 3 (M32 step 1): house_zone

# The page's files, baked into the image (/app/static; the tree's
# orchestrator/static under the tests). Dev mounts only /data and /config.
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent / "static"))
PAGE_FILES = ("work.html", "kronk.css")
# The placeholder in work.html that main.py's _serve() fills with the version.
PAGE_STAMP = '<meta name="kronk-page-version" content="">'
NO_STORE = {"Cache-Control": "no-store"}


def page_version(static_dir: Path | None = None) -> str:
    """The first 12 hex of SHA-256 over work.html and kronk.css as they are
    on disk (the placeholder unfilled). A file that cannot be read is
    hashed as its name and the word "unreadable", so the version is still
    one string for the whole process."""
    d = STATIC_DIR if static_dir is None else static_dir
    h = hashlib.sha256()
    for name in PAGE_FILES:
        try:
            data = (d / name).read_bytes()
        except OSError:
            data = f"{name} unreadable".encode()
        h.update(name.encode() + b"\0" + data + b"\0")
    return h.hexdigest()[:12]


# Taken once, at start: the files do not change under a running image.
PAGE_VERSION = page_version()


def stamp(html: str, version: str = PAGE_VERSION) -> str:
    """The page with its version in its own bytes."""
    return html.replace(PAGE_STAMP, f'<meta name="kronk-page-version" content="{version}">', 1)


# servers.read_source's in-memory copy of work.json (see the module doc).
_cache: dict = {}


def _same_ts(have: str | None, ts) -> bool:
    """Whether ?have= names the file's ts. The page sends back the number it
    was given; a value that is not a number never matches."""
    if have is None or isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    try:
        return float(have) == float(ts)
    except (TypeError, ValueError, OverflowError):
        return False


def work_source(now: float | None = None, have: str | None = None) -> dict:
    """{"source": {file, state, age_s, max_age_s, every_s, reason, schema,
    schema_note}, "schema_known": n, "work": the exporter's outline or
    null, "unchanged": bool, "page_version": str, "server_s": float}.
    state is fresh, stale, missing, unreadable or unknown_schema; work is
    null when missing, unreadable or of an unknown schema, and when have is
    the file's ts (then unchanged is true)."""
    now = time.time() if now is None else now
    src = servers.read_source(WORK_PATH, WORK_MAX_AGE_S, WORK_EVERY_S, WORK_TIMER, now, cache=_cache)
    data = src.pop("data")
    src["schema"] = None
    src["schema_note"] = None
    if data is not None:
        schema = data.get("schema")
        src["schema"] = schema
        if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
            src["state"] = "unknown_schema"
            src["reason"] = (f"{WORK_PATH.name} has schema {schema!r}, which this orchestrator does not "
                             f"know (it reads schema {SCHEMA_KNOWN})")
            data = None
        elif schema > SCHEMA_KNOWN:
            src["schema_note"] = (f"{WORK_PATH.name} has schema {schema}, newer than this page's "
                                  f"{SCHEMA_KNOWN}: keys added since are not shown")
    unchanged = data is not None and _same_ts(have, data.get("ts"))
    return {"source": src, "schema_known": SCHEMA_KNOWN, "work": None if unchanged else data,
            "unchanged": unchanged, "page_version": PAGE_VERSION, "server_s": round(now, 3)}


@router.get("/api/work")
async def work_status(response: Response, have: str | None = None):
    response.headers.update(NO_STORE)
    return work_source(have=have)
