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
"""
import os
from pathlib import Path

from fastapi import APIRouter

import servers

router = APIRouter()

WORK_PATH = Path(os.getenv("WORK_PATH", "/data/work.json"))
# The exporter runs every 60 s; older than this, the file is stale.
WORK_MAX_AGE_S = 180
WORK_EVERY_S = 60
WORK_TIMER = "systemd/kronk-work-export.timer"
SCHEMA_KNOWN = 2                # schema 2 (M34): the file drawn from the work-state database


def work_source(now: float | None = None) -> dict:
    """{"source": {file, state, age_s, max_age_s, every_s, reason, schema,
    schema_note}, "schema_known": n, "work": the exporter's outline or
    null}. state is fresh, stale, missing, unreadable or unknown_schema;
    work is null only when missing, unreadable or of an unknown schema."""
    src = servers.read_source(WORK_PATH, WORK_MAX_AGE_S, WORK_EVERY_S, WORK_TIMER, now)
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
    return {"source": src, "schema_known": SCHEMA_KNOWN, "work": data}


@router.get("/api/work")
async def work_status():
    return work_source()
