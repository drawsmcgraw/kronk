"""The /settings audit record (sprint M21 step 2, criterion 24).

One JSON line per unlock, failed unlock, refused unlock, lockout, lock,
save, revert, apply to dev and refused or failed save, appended to
SETTINGS_AUDIT_LOG (default /data/settings_audit.jsonl: production's
./data, dev's ./data/dev) and flushed to disk. Each line says who (the
client address and the instance), when, which file, which keys and the
commit. Never the passphrase, and no prompt text: a prompt edit is recorded
by file and commit; the diff is in the live directory's git history.
"""
import fcntl
import json
import logging
import os
import time
from pathlib import Path

import config_files

logger = logging.getLogger(__name__)

# Fields a caller may pass; anything else is dropped, so no text field can
# ride along by accident.
FIELDS = frozenset({"client", "file", "keys", "commit", "outcome", "reason", "overrides", "scope", "minutes",
                    "failures_in_window", "seconds_left", "reverted", "target", "base", "warnings"})


def path() -> Path:
    return Path(os.getenv("SETTINGS_AUDIT_LOG", "/data/settings_audit.jsonl"))


def record(event: str, **fields) -> dict:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "instance": os.getenv("SETTINGS_INSTANCE", "production"),
             "event": event}
    for k, v in fields.items():
        if k not in FIELDS or v is None:
            continue
        if isinstance(v, str):
            v = config_files.safe(v, config_files.MAX_MESSAGE)
        elif isinstance(v, (list, tuple)):
            v = [config_files.safe(x, 120) for x in v][:100]
        entry[k] = v
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        p = path()
        with open(p, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        # The action is not undone for a lost audit line, but it is loud.
        logger.error("settings audit record could not be written (%s): %s", e, line.strip())
    return entry
