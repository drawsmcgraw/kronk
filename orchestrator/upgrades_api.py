"""GET /api/upgrades (sprint M18): the upgrade cadence's status file, for
the /services page. Read-only; mounted by main.py with include_router.

scripts/upgrades.py (the monthly minor run, the quarterly check, the notice
retry) writes data/upgrades/status.json on the host, which production sees
as /data/upgrades/status.json. Anything that needs Drew is in `attention`
until `python3 scripts/upgrades.py ack` clears it: a rollback (and above all
a failed one), a notice HA did not take, a month that never found a quiet
moment. That is how a failure stays visible when HA is the thing that
failed: the page does not depend on HA.

This route never writes. Acknowledging is a host command, not a web action.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

STATUS_FILE = os.getenv("UPGRADE_STATUS_FILE", "/data/upgrades/status.json")
ACK = "python3 scripts/upgrades.py ack"


def summarize(raw: dict) -> dict:
    attention = [a.get("text", "") for a in raw.get("attention") or []]
    rf = raw.get("rollback_failed")
    if rf and not any("rollback FAILED" in a for a in attention):
        attention.insert(0, f"Upgrade rollback FAILED for {rf.get('component')}: {rf.get('detail', '')}")
    return {
        "present": True,
        "warnings": attention,
        "ack": ACK if attention else None,
        "last_run": raw.get("last_run"),
        "pending_notices": len(raw.get("pending_notices") or []),
        "rollback_failed": rf,
        "major": raw.get("major"),
        "updated": raw.get("updated"),
    }


@router.get("/api/upgrades")
async def upgrades_status():
    path = Path(STATUS_FILE)
    base = {"file": str(path)}
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {**base, "present": False, "warnings": [], "last_run": None,
                "note": "no upgrade run has written a status file yet"}
    except (OSError, ValueError) as e:
        # Unreadable is itself worth a look: say so as a warning.
        return {**base, "present": False, "last_run": None,
                "warnings": [f"upgrade status file unreadable: {e.__class__.__name__}: {e}"]}
    return {**base, **summarize(raw if isinstance(raw, dict) else {})}
