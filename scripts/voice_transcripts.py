#!/usr/bin/env python3
"""List the household's voice runs from Tempo, with what was heard (sprint M12).

One row per voice run over a period: time, satellite, pipeline, what Whisper
heard, which tier answered (HA's local intents, the music blueprint, Kronk),
Kronk's routing rule and the tools it called, the reply, and the
wrong-action mark voice_tracer set (corrected by "not that", a thumbs down, a
stop, or a near-repeat within 60 s). Dev runs (Hey Jarvis, the kronk-dev
pipeline) are left out unless --include-dev.

The words are there only for runs traced with TELEMETRY_RECORD_TEXT=true;
other rows have the lengths only. Rules and tools come from the voice run
itself (voice_tracer copies them since M12) or, for runs traced before
that, from the joined Kronk trace.

Reads Tempo on loopback (default http://127.0.0.1:3200) and writes only to
--out, or to stdout. Household speech: --out is refused inside the repo.

  scripts/voice_transcripts.py --since 7d
  scripts/voice_transcripts.py --since 2026-11-17 --until 2026-12-01 --out ~/m12/runs.csv
  scripts/voice_transcripts.py --since 24h --wrong-only

Wrong-action rate, without this script (TraceQL, Grafana Explore → Tempo):
  { name = "voice.run" && resource.service.name = "kronk-voice-tracer"
    && span.ha.pipeline_name != "kronk-dev" && span.voice.corrected_by != nil }
over the count of
  { name = "voice.run" && resource.service.name = "kronk-voice-tracer"
    && span.ha.pipeline_name != "kronk-dev" && span.voice.stages =~ "stt.*" }
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_tracer import marks  # noqa: E402  (routing_from_trace: rule and tools, never text)

TEMPO = "http://127.0.0.1:3200"
DEV_SERVICE = "kronk-voice-tracer-dev"
DEV_PIPELINES = ("kronk-dev",)
DAY_S = 86400
SEARCH_LIMIT = 5000       # traces per Tempo search
MIN_WINDOW_S = 60         # a search window is halved while it returns SEARCH_LIMIT traces, down to this

# The voice.run attributes the listing reads (Tempo select()).
ATTRS = (
    "ha.pipeline_name", "ha.pipeline_run_id", "ha.satellite_id", "kronk.origin.device_id",
    "voice.transcript", "voice.transcript_chars", "voice.speech", "voice.speech_chars",
    "voice.handled_by", "voice.stages", "voice.error_code", "kronk.trace_id", "kronk.rule",
    "kronk.direct_action", "kronk.tools", "voice.corrected_by", "voice.corrected_after_s",
    "voice.corrected_by_run_id", "voice.correction_kind", "voice.correction_of_run_id",
    "voice.mark_basis", "ha.blueprint_run_id",
)

COLUMNS = ("time", "satellite", "pipeline", "heard", "heard_chars", "tier", "rule", "direct_action",
           "tools", "reply", "wrong_action", "wrong_action_after_s", "corrected_by_run",
           "is_correction_of", "mark_basis", "error", "run_id", "trace_id", "kronk_trace_id",
           "device_id", "start_s", "end_s", "service")


class Refused(Exception):
    pass


# ── time and paths ──────────────────────────────────────────────────────────

_REL = re.compile(r"^(\d+(?:\.\d+)?)([mhd])$")


def parse_when(s: str | None, now: float) -> float:
    """Epoch seconds from "7d", "24h", "90m", an ISO date or date-time
    (local time when no zone is given), or None (now)."""
    if not s:
        return now
    m = _REL.match(s.strip())
    if m:
        n, unit = float(m.group(1)), m.group(2)
        return now - n * {"m": 60, "h": 3600, "d": DAY_S}[unit]
    t = dt.datetime.fromisoformat(s.strip())
    if t.tzinfo is None:
        t = t.astimezone()          # local
    return t.timestamp()


def refuse_repo_path(path: str | os.PathLike) -> Path:
    """Household speech never lands in the repo (and so never in git)."""
    p = Path(path).expanduser().resolve()
    if p == ROOT or ROOT in p.parents:
        raise Refused(f"{p} is inside the repo; write household transcripts and audio outside it")
    return p


def require_loopback(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise Refused(f"Tempo must be read on loopback, not {host!r}")
    return url.rstrip("/")


# ── Tempo ───────────────────────────────────────────────────────────────────

def http_get_json(url: str, timeout: float = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 (loopback only)
        return json.loads(r.read())


def search_query() -> str:
    sel = ", ".join(f"span.{a}" for a in ATTRS) + ", resource.service.name"
    return '{ name = "voice.run" } | select(' + sel + ")"


def _value(v: dict):
    return marks._attr_value(v)


def rows_from_search(payload: dict) -> list[dict]:
    """voice.run spans from a Tempo search response, one dict each:
    attributes plus start_s, end_s, trace_id, service."""
    out = []
    for t in (payload or {}).get("traces") or []:
        sets = t.get("spanSets") or ([t["spanSet"]] if t.get("spanSet") else [])
        for ss in sets:
            for sp in ss.get("spans") or []:
                a = {x["key"]: _value(x.get("value")) for x in sp.get("attributes") or []}
                start = int(sp.get("startTimeUnixNano") or t.get("startTimeUnixNano") or 0) / 1e9
                dur = int(sp.get("durationNanos") or 0) / 1e9
                a["_start_s"], a["_end_s"] = start, start + dur
                a["_trace_id"] = (t.get("traceID") or "").lower().zfill(32)
                a["_service"] = a.pop("service.name", None) or t.get("rootServiceName")
                out.append(a)
    return out


def is_dev(a: dict, dev_pipelines=DEV_PIPELINES) -> bool:
    return a.get("_service") == DEV_SERVICE or a.get("ha.pipeline_name") in dev_pipelines


def _search(tempo: str, t: float, t2: float, get_json, truncated: list) -> list[dict]:
    """voice.run rows for [t, t2]. A search that returns SEARCH_LIMIT traces
    may have dropped some (dev replays can fill a day): the window is halved
    and searched again, down to MIN_WINDOW_S; a window that small still at
    the limit is reported on stderr and in `truncated`, never dropped
    silently (M12 fix 1, F9)."""
    qs = urllib.parse.urlencode({"q": search_query(), "start": int(t), "end": int(t2) + 1,
                                 "limit": SEARCH_LIMIT, "spss": 3})
    payload = get_json(f"{tempo}/api/search?{qs}")
    if len((payload or {}).get("traces") or []) >= SEARCH_LIMIT:
        if t2 - t > MIN_WINDOW_S:
            mid = t + (t2 - t) / 2
            return _search(tempo, t, mid, get_json, truncated) + _search(tempo, mid, t2, get_json, truncated)
        truncated.append((t, t2))
        print(f"voice_transcripts: WARNING: Tempo returned its limit of {SEARCH_LIMIT} traces for "
              f"{dt.datetime.fromtimestamp(t).isoformat(timespec='seconds')} to "
              f"{dt.datetime.fromtimestamp(t2).isoformat(timespec='seconds')}; runs in that window may be "
              "missing", file=sys.stderr)
    return rows_from_search(payload)


def fetch_runs(since: float, until: float, tempo: str = TEMPO, include_dev: bool = False,
               get_json=http_get_json, dev_pipelines=DEV_PIPELINES, enrich: bool = True,
               truncated: list | None = None) -> list[dict]:
    """Every voice.run in [since, until], one Tempo search per day (search
    results are capped per query; a full one is split, _search),
    deduplicated by run id, oldest first. Windows where the cap was still
    hit are appended to `truncated`."""
    tempo = require_loopback(tempo)
    truncated = truncated if truncated is not None else []
    seen, runs = set(), []
    t = since
    while t < until:
        t2 = min(t + DAY_S, until)
        for a in _search(tempo, t, t2, get_json, truncated):
            rid = a.get("ha.pipeline_run_id") or a["_trace_id"]
            if rid in seen or not (since <= a["_start_s"] <= until):
                continue
            if not include_dev and is_dev(a, dev_pipelines):
                continue
            seen.add(rid)
            runs.append(a)
        t = t2
    runs.sort(key=lambda a: a["_start_s"])
    if enrich:
        for a in runs:
            if a.get("kronk.trace_id") and not a.get("kronk.rule"):
                try:
                    r = marks.routing_from_trace(get_json(f"{tempo}/api/v2/traces/{a['kronk.trace_id']}"))
                except Exception as e:   # one missing trace must not sink the listing
                    a["_enrich_error"] = f"{type(e).__name__}"
                    continue
                a["kronk.rule"] = r.get("rule")
                a["kronk.direct_action"] = r.get("direct_action")
                a["kronk.tools"] = r.get("tools")
    return runs


def to_row(a: dict) -> dict:
    tools = a.get("kronk.tools")
    if isinstance(tools, str):
        tools = [tools]
    return {
        "time": dt.datetime.fromtimestamp(a["_start_s"]).astimezone().isoformat(timespec="seconds"),
        "satellite": a.get("ha.satellite_id") or "",
        "pipeline": a.get("ha.pipeline_name") or "",
        "heard": a.get("voice.transcript") or "",
        "heard_chars": a.get("voice.transcript_chars") if a.get("voice.transcript_chars") is not None else "",
        "tier": a.get("voice.handled_by") or "",
        "rule": a.get("kronk.rule") or "",
        "direct_action": a.get("kronk.direct_action") or "",
        "tools": " ".join(tools or []),
        "reply": a.get("voice.speech") or "",
        "wrong_action": a.get("voice.corrected_by") or "",
        "wrong_action_after_s": a.get("voice.corrected_after_s") if a.get("voice.corrected_after_s") is not None else "",
        "corrected_by_run": a.get("voice.corrected_by_run_id") or "",
        "is_correction_of": a.get("voice.correction_of_run_id") or "",
        "mark_basis": a.get("voice.mark_basis") or "",
        "error": a.get("voice.error_code") or "",
        "run_id": a.get("ha.pipeline_run_id") or "",
        "trace_id": a.get("_trace_id") or "",
        "kronk_trace_id": a.get("kronk.trace_id") or "",
        "device_id": a.get("kronk.origin.device_id") or "",
        "start_s": round(a["_start_s"], 3),
        "end_s": round(a["_end_s"], 3),
        "service": a.get("_service") or "",
    }


def write_rows(rows: list[dict], fh, fmt: str = "csv") -> None:
    if fmt == "jsonl":
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return
    w = csv.DictWriter(fh, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows)


def main(argv=None, now: float | None = None, get_json=http_get_json, stdout=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", default="7d", help='start: "7d", "24h", or an ISO date/time (default 7d)')
    ap.add_argument("--until", help="end (default now)")
    ap.add_argument("--include-dev", action="store_true", help="include kronk-dev runs")
    ap.add_argument("--wrong-only", action="store_true", help="only runs marked as corrected")
    ap.add_argument("--voice-only", action="store_true",
                    help="only runs with audio (STT stage), not typed or scripted text runs")
    ap.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    ap.add_argument("--out", help="file to write (default stdout); refused inside the repo")
    ap.add_argument("--tempo", default=TEMPO, help="Tempo query API on loopback (default %(default)s)")
    args = ap.parse_args(argv)
    now = now if now is not None else dt.datetime.now().timestamp()
    stdout = stdout or sys.stdout
    try:
        out = refuse_repo_path(args.out) if args.out else None
        since, until = parse_when(args.since, now), parse_when(args.until, now)
        truncated: list = []
        runs = fetch_runs(since, until, args.tempo, args.include_dev, get_json, truncated=truncated)
    except Refused as e:
        print(f"voice_transcripts: REFUSED: {e}", file=sys.stderr)
        return 2
    rows = [to_row(a) for a in runs
            if (not args.wrong_only or a.get("voice.corrected_by"))
            and (not args.voice_only or "stt" in (a.get("voice.stages") or ""))]
    if out:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            write_rows(rows, fh, args.format)
    else:
        write_rows(rows, stdout, args.format)
    marked = sum(1 for r in rows if r["wrong_action"])
    print(f"voice_transcripts: {len(rows)} runs, {marked} marked wrong-action"
          + (f" -> {out}" if out else ""), file=sys.stderr)
    if truncated:
        print(f"voice_transcripts: {len(truncated)} window(s) hit Tempo's search limit (above); the "
              "listing may be missing runs", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
