"""GET /api/backups (sprint M16): the backup jobs' status file, for the
/services page. Read-only; mounted by main.py with include_router.

scripts/backups.py (the monthly config backup, the weekly recordings
archive, the daily stale check) writes data/backups/status.json on the host,
which production sees as /data/backups/status.json. The page shows a warning
when the last run of a job failed or was declined, when its last success is
older than the limits the script wrote into the file (35 days for the config
backup, the archive's interval plus a margin), when the stale check found a
problem (the destination not mounted, no backup on it), or when the last
config backup could not read something. The NAS is not mounted into this
container, so the age comes from the status file; the stale check, which
runs on the host, looks at the destination itself.

This route never writes. Every problem is a warning, never a 500.
"""
import datetime as dt
import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

STATUS_FILE = os.getenv("BACKUP_STATUS_FILE", "/data/backups/status.json")
JOBS = (("config", "config backup", "config_days", 35), ("archive", "recordings archive", "archive_days", 9))


def _when(s):
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def summarize(raw: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    warnings: list[str] = []
    jobs = {}
    for key, word, limit_key, default in JOBS:
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        last = entry.get("last") if isinstance(entry.get("last"), dict) else None
        jobs[key] = {"last": last, "last_ok_at": entry.get("last_ok_at")}
        if last and last.get("result") in ("failed", "declined"):
            warnings.append(f"the last {word} {last['result']} ({last.get('at')}): {str(last.get('detail'))[:200]}")
        limit = limits.get(limit_key, default)
        ok = _when(entry.get("last_ok_at"))
        if ok is not None and isinstance(limit, (int, float)) and now - ok > dt.timedelta(days=limit):
            warnings.append(f"the last good {word} was {(now - ok).days} days ago (limit {limit})")
        if key == "config" and last and last.get("result") == "ok" and last.get("unreadable"):
            warnings.append(f"the last config backup could not read {len(last['unreadable'])} item(s): "
                            + "; ".join(str(x) for x in last["unreadable"][:3]))
    check = raw.get("check") if isinstance(raw.get("check"), dict) else {}
    for w in check.get("warnings") or []:
        if str(w) not in warnings:
            warnings.append(str(w))
    return {"present": True, "warnings": warnings, "jobs": jobs, "check_at": check.get("at"),
            "updated": raw.get("updated")}


@router.get("/api/backups")
async def backups_status():
    path = Path(STATUS_FILE)
    base = {"file": str(path)}
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {**base, "present": False, "warnings": [], "jobs": {},
                "note": "no backup job has written a status file yet"}
    except (OSError, ValueError, RecursionError) as e:
        return {**base, "present": False, "jobs": {},
                "warnings": [f"backup status file unreadable: {e.__class__.__name__}: {str(e)[:200]}"]}
    if not isinstance(raw, dict):
        return {**base, "present": False, "jobs": {},
                "warnings": ["backup status file unreadable: not a JSON object"]}
    try:
        return {**base, **summarize(raw)}
    except Exception as e:  # noqa: BLE001 - this route never raises
        return {**base, "present": True, "jobs": {},
                "warnings": [f"backup status file unreadable: {e.__class__.__name__}: {str(e)[:200]}"]}
