"""Shadow mode for the M11 music decision (sprint M11, criteria 14 to 17).

With MUSIC_SHADOW on, every request that reaches the live /music path (the
`music` rule through the home agent, play_music from the coordinator,
/music/voice) is answered by the live path exactly as before. After it has
been answered, the user's sentence (`said`) also runs through the M11 parser
and decision order, and one comparison record is appended to
MUSIC_SHADOW_LOG (default /data/music_shadow.jsonl) and sent as a
`music.shadow` span. Nothing the user hears changes.

Playing is impossible by construction, not by setting: this module imports
nothing that talks to Home Assistant or Music Assistant. It is handed one
function, main.decide_sentence, which reads HA's players template and
decides through main.ReadOnlyMusic (Music Assistant read commands only,
through ma_read; the taste store read-only). Neither has a play_media,
queue, transport or rating call to reach. The shadow never asks the model:
where the decision would, the record says "would_ask".

The record format and the review script (scripts/music_shadow_report.py)
are in docs/features/voice-music-control.md, "Shadow mode".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

try:
    from . import music_resolve, telemetry
except ImportError:
    import music_resolve
    import telemetry

logger = logging.getLogger("tool_service.music_shadow")

ENABLED = os.getenv("MUSIC_SHADOW", "").strip().lower() in ("1", "true", "yes", "on")
LOG_PATH = Path(os.getenv("MUSIC_SHADOW_LOG", "/data/music_shadow.jsonl"))
TIMEOUT_S = float(os.getenv("MUSIC_SHADOW_TIMEOUT_S", "30"))
RECORD_VERSION = 1

_tasks: set[asyncio.Task] = set()       # strong references until each finishes
_sem: tuple | None = None               # (loop, Semaphore(1)): one shadow decision in flight


def _one_at_a_time() -> asyncio.Semaphore:
    global _sem
    loop = asyncio.get_running_loop()
    if _sem is None or _sem[0] is not loop:
        _sem = (loop, asyncio.Semaphore(1))
    return _sem[1]


def live_summary(req: dict, result: dict | None, error: str | None, sent: dict, ms: float) -> dict:
    """What the live path did, from its answer and what it sent (or would
    have sent) to Music Assistant."""
    out: dict = {"ms": round(ms)}
    if error is not None:
        out.update(status="error", error=str(error)[:300])
        return out
    result = result or {}
    out["status"] = result.get("status")
    data = result.get("would_play") or sent.get("service_data") or {}
    out["uri"] = data.get("media_id")
    out["targets"] = result.get("targets") or sent.get("entities")
    out["player"] = result.get("player")
    if (pick := result.get("pick")):
        out.update(kind="genre", name=pick.get("phrase") or None, pick=pick.get("label"))
    elif (res := result.get("resolved")):
        out.update(kind="named", media_type=res.get("media_type"), name=res.get("name"), artist=res.get("artist"))
    elif (station := result.get("station")):
        out.update(kind="named", media_type="radio", name=station.get("name"))
    else:
        out.update(kind="named", media_type=data.get("media_type"), name=data.get("media_id"),
                   literal=bool(data.get("media_id")) and "://" not in str(data.get("media_id")))
    return {k: v for k, v in out.items() if v is not None}


def new_summary(decided, ms: float) -> dict:
    o = decided.outcome
    out = {**o.as_dict(), "targets": decided.entities, "player": decided.label, "ms": round(ms),
           "parsed": decided.parsed.as_dict()}
    if decided.error:
        out["target_error"] = decided.error          # the speaker or room failed, as the live path reports it
    tr = decided.trace or {}
    out["model"] = tr.get("model") or {}
    out["searches"] = tr.get("searches", 0)
    out["candidates"] = (tr.get("candidates") or [])[:5]
    return {k: v for k, v in out.items() if v is not None}


def compare(live: dict, new: dict) -> tuple[bool, str]:
    """(agree, why). A style agrees on the phrase (the pick itself is a
    weighted random choice); a named item on the URI, or on type and name."""
    live_ok = live.get("status") in ("playing", "dry_run")
    new_ok = new.get("action") == "play" and not new.get("target_error") and not new.get("error")
    if not live_ok and not new_ok:
        return True, "both fail"
    if live_ok != new_ok:
        return False, "only the new path plays" if new_ok else "only the live path plays"
    if sorted(live.get("targets") or []) != sorted(new.get("targets") or []):
        return False, "different speaker"
    if live.get("kind") != new.get("kind"):
        return False, f"live read {live.get('kind')}, new read {new.get('kind')}"
    if new.get("kind") == "genre":
        same = music_resolve.fold(live.get("name")) == music_resolve.fold(new.get("name"))
        return same, "same style" if same else "different style"
    if live.get("uri") and live.get("uri") == new.get("uri"):
        return True, "same item"
    same = (live.get("media_type") == new.get("media_type")
            and music_resolve.fold(live.get("name")) == music_resolve.fold(new.get("name")))
    return same, "same type and name" if same else "different item"


def write(record: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning("music shadow: could not write %s: %s", LOG_PATH, e)


async def run(req: dict, result: dict | None, error: str | None, sent: dict, live_ms: float,
              decide, trace_id: str | None = None) -> dict:
    """Decide the sentence and log the comparison. Never raises."""
    record: dict = {"v": RECORD_VERSION, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "trace_id": trace_id, "sentence": req.get("said"),
                    "origin": {"area": req.get("origin_area"), "device": req.get("origin_device")},
                    "live_request": {k: req.get(k) for k in ("query", "media_type", "kind", "player", "shuffle")
                                     if req.get(k) is not None}}
    try:
        record["live"] = live_summary(req, result, error, sent, live_ms)
        if not req.get("said"):
            record.update(skipped="no sentence (the caller sent no said)")
        elif (req.get("kind") or "").lower() == "another":
            record.update(skipped="'not that' replays the last genre list; nothing to decide")
        else:
            t0 = time.monotonic()
            async with _one_at_a_time():
                try:
                    decided = await asyncio.wait_for(
                        decide(req["said"], req.get("origin_device"), req.get("origin_area")), timeout=TIMEOUT_S)
                except asyncio.TimeoutError:
                    decided = None
                    record["new"] = {"error": f"timed out after {TIMEOUT_S:g}s",
                                     "ms": round((time.monotonic() - t0) * 1000)}
            if decided is not None:
                record["new"] = new_summary(decided, (time.monotonic() - t0) * 1000)
                record["agree"], record["why"] = compare(record["live"], record["new"])
    except Exception as e:                       # the shadow never surfaces a failure
        logger.warning("music shadow failed for %r: %s", telemetry.text(req.get("said"), 120), e, exc_info=True)
        record["new"] = {**record.get("new", {}), "error": f"{type(e).__name__}: {str(e)[:200]}"}
    new = record.get("new") or {}
    with telemetry.span("music.shadow", **{
            "music.shadow.agree": record.get("agree"), "music.shadow.why": record.get("why"),
            "music.shadow.skipped": record.get("skipped"), "music.decided_by": new.get("decided_by"),
            "music.reading": new.get("reading"),
            "music.model_would_ask": bool((new.get("model") or {}).get("would_ask")),
            "music.shadow.error": new.get("error"), "music.shadow.ms": new.get("ms")}):
        pass
    write(record)
    logger.info("music shadow %s", json.dumps({k: record.get(k) for k in ("agree", "why", "skipped")}
                                              | {"decided_by": new.get("decided_by"), "ms": new.get("ms")}))
    return record


def schedule(req: dict, result: dict | None, error: str | None, sent: dict, live_ms: float, decide) -> None:
    """Start the shadow after the live answer exists. Returns at once."""
    try:
        task = asyncio.get_running_loop().create_task(
            run(req, result, error, sent, live_ms, decide, telemetry.current_trace_id()))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as e:
        logger.warning("music shadow not started: %s", e)


async def drain() -> None:
    """Wait for every shadow task in flight (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
