#!/usr/bin/env python3
"""Daily check: is the two-week voice audio capture still on after its end date? (sprint M12)

Run once a day by kronk-voice-capture-check.timer (systemd/). From the end
date on, while Home Assistant is still recording pipeline audio, it pushes
one notification a day to the operator's phone (the existing HA notify path,
scripts/lib/notify.sh) saying capture should be turned off, and how. Before
the end date, and once capture is off, it sends nothing.

"Still recording" means either of:
  - the live /config/configuration.yaml in the homeassistant container has
    an uncommented `debug_recording_dir:` under `assist_pipeline:` (read with
    `docker exec homeassistant cat`), or
  - a recording in VOICE_RECORDINGS_DIR is newer than the running HA
    container's start: the file was changed but HA not restarted yet.
If neither can be read, it says so in the push rather than stay quiet.

It reads only. It never restarts or changes HA and never deletes a recording.

Env (set in the unit; the end date is set there once, at install):
  VOICE_CAPTURE_END      YYYY-MM-DD, the capture start date plus 14 days
  VOICE_RECORDINGS_DIR   host directory HA records into (default ~/kronk-voice-recordings)
  STATE_DIRECTORY        set by systemd (StateDirectory=); holds the last push date
  KRONK_REPO_DIR, HA_NOTIFY_SERVICE   as for scripts/lib/notify.sh

  scripts/voice_capture_check.py               # the daily check
  scripts/voice_capture_check.py --test-push   # one push now, marked TEST (install check)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(os.getenv("KRONK_REPO_DIR", str(Path(__file__).resolve().parent.parent)))
NOTIFY_LIB = REPO / "scripts" / "lib" / "notify.sh"
TAG = "kronk-voice-capture"
RUNBOOK = "docs/features/voice-capture.md, 'Turning audio capture off'"
HA_CONTAINER = "homeassistant"

_SECTION = re.compile(r"^assist_pipeline:\s*(#.*)?$")
_KEY = re.compile(r"^\s+debug_recording_dir:\s*\S")


def capture_configured(yaml_text: str) -> bool:
    """True when the file has `debug_recording_dir:` set under a top-level,
    uncommented `assist_pipeline:` block."""
    inside = False
    for line in yaml_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            inside = bool(_SECTION.match(line))
            continue
        if inside and _KEY.match(line):
            return True
    return False


def _docker(*args: str) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or f"docker {args[0]} failed")
    return out.stdout


def read_live_config() -> str:
    return _docker("exec", HA_CONTAINER, "cat", "/config/configuration.yaml")


def ha_started_at() -> float:
    s = _docker("inspect", "-f", "{{.State.StartedAt}}", HA_CONTAINER).strip()
    s = re.sub(r"(\.\d{6})\d*", r"\1", s).replace("Z", "+00:00")
    return dt.datetime.fromisoformat(s).timestamp()


def newest_recording(recordings: Path) -> float | None:
    if not recordings.is_dir():
        return None
    return max((p.stat().st_mtime for p in recordings.rglob("*.wav")), default=None)


def capture_state(recordings: Path, read_config=read_live_config, started_at=ha_started_at) -> tuple[bool | None, str]:
    """(on?, why). on is None when nothing could be read."""
    why = []
    configured = None
    try:
        configured = capture_configured(read_config())
        why.append("set in the live configuration.yaml" if configured else "not in the live configuration.yaml")
    except Exception as e:
        why.append(f"live configuration.yaml unreadable ({e})")
    recording = None
    try:
        newest = newest_recording(recordings)
        recording = newest is not None and newest > started_at()
        if recording:
            why.append("HA has recorded since it last started")
    except Exception as e:
        why.append(f"could not compare recordings with HA's start ({e})")
    if configured or recording:
        return True, "; ".join(why)
    if configured is None:              # the file decides; without it, no answer
        return None, "; ".join(why)
    return False, "; ".join(why)


def decide(today: dt.date, end: dt.date, on: bool | None, last_notified: str | None) -> str:
    """notify | before_end | off | already_today."""
    if today < end:
        return "before_end"
    if on is False:
        return "off"
    if last_notified == today.isoformat():
        return "already_today"
    return "notify"


def message(end: dt.date, on: bool | None, why: str) -> tuple[str, str]:
    if on is None:
        return ("Kronk: check voice audio capture",
                f"The two-week capture ended {end.isoformat()}, and this check could not tell whether "
                f"HA is still recording ({why}). Check, and turn it off: {RUNBOOK}.")
    return ("Kronk: turn voice audio capture off",
            f"The two-week capture ended {end.isoformat()}; every puck is still being recorded ({why}). "
            f"To stop: restore ha/configuration.yaml with the assist_pipeline lines commented, install "
            f"it, check_config, one HA restart. Steps: {RUNBOOK}. Recordings are kept until Drew decides.")


def notify_sh(tag: str, title: str, body: str) -> bool:
    """One push through scripts/lib/notify.sh (the existing alert path)."""
    r = subprocess.run(["bash", "-c", 'source "$0"; load_ha_token && ha_notify "$1" "$2" "$3"',
                        str(NOTIFY_LIB), tag, title, body], capture_output=True, text=True, timeout=60)
    sys.stderr.write(r.stdout + r.stderr)
    return r.returncode == 0


def state_path(arg: str | None) -> Path:
    d = arg or os.getenv("STATE_DIRECTORY")
    if not d:
        raise SystemExit("voice_capture_check: no state directory (--state, or StateDirectory= in the unit)")
    return Path(d.split(":")[0]) / "voice_capture_check.json"


def run(end: dt.date, today: dt.date, state: Path, recordings: Path, notify=notify_sh,
        read_config=read_live_config, started_at=ha_started_at) -> str:
    try:
        last = json.loads(state.read_text()).get("last_notified")
    except (FileNotFoundError, ValueError):
        last = None
    if today < end:
        print(f"voice_capture_check: before the end date {end}; nothing to do")
        return "before_end"
    on, why = capture_state(recordings, read_config, started_at)
    action = decide(today, end, on, last)
    if action == "notify":
        title, body = message(end, on, why)
        if not notify(TAG, title, body):
            print(f"voice_capture_check: push FAILED: {title}", file=sys.stderr)
            return "notify_failed"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"last_notified": today.isoformat(), "why": why}) + "\n")
    print(f"voice_capture_check: end {end}, today {today}, capture "
          f"{ {True: 'on', False: 'off', None: 'unknown'}[on] } ({why}): {action}")
    return action


def main(argv=None, today: dt.date | None = None, notify=notify_sh) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", help="state directory (default $STATE_DIRECTORY)")
    ap.add_argument("--test-push", action="store_true", help="send one push now, marked TEST, and exit")
    args = ap.parse_args(argv)
    raw = os.getenv("VOICE_CAPTURE_END", "")
    try:
        end = dt.date.fromisoformat(raw)
    except ValueError:
        print(f"voice_capture_check: VOICE_CAPTURE_END={raw!r} is not a date (YYYY-MM-DD); "
              "set it in the installed unit", file=sys.stderr)
        return 2
    if args.test_push:
        ok = notify(TAG, "Kronk: TEST voice capture reminder",
                    f"Install check. From {end.isoformat()} this arrives daily while capture is on.")
        return 0 if ok else 1
    recordings = Path(os.getenv("VOICE_RECORDINGS_DIR", str(Path.home() / "kronk-voice-recordings")))
    action = run(end, today or dt.date.today(), state_path(args.state), recordings, notify)
    return 1 if action == "notify_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
