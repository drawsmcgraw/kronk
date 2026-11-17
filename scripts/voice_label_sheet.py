#!/usr/bin/env python3
"""Build the speech-recognition labelling sheet from the puck recordings (sprint M12).

Reads Home Assistant's debug recordings (assist_pipeline debug_recording_dir,
HA 2026.5.4 layout, assist_pipeline/pipeline.py):

  <recordings>/<device_id>/<pipeline name>/<monotonic ns>/01_stt-<engine>.wav
  <recordings>/<pipeline name>/<monotonic ns>/...        (runs with no device)

16 kHz, 16-bit, mono PCM. Wake-word audio (00_wake-*.wav) exists only when HA
runs the wake word; the pucks detect it on the device, so there is none. The
run directory is named by a monotonic clock, not HA's run id, so a recording
is joined to its voice run by device, pipeline and time: the WAV's last write
(its mtime) falls inside the voice run (HA closes the file when the run ends
or 5 s after the last audio, whichever is first).

A join is used only when it is certain (M12 fix 1, F8). Recordings and the
runs that could be theirs are paired one to one, and a pairing is kept only
when every pairing that matches the most recordings agrees on it. When it
does not (the recording's own run is missing from Tempo and another run on
the same pipeline is in range, say), the row keeps its audio and device,
`heard` and the run's fields stay empty, and `notes` says "ambiguous join"
with the candidate run ids. A certain join that rests on time and pipeline
alone (one side has no device id) says so in `notes` too.

Writes, under --out (a directory outside the repo, created if missing):

  labels.csv       one row per sampled recording; fill in `correct_text`
  audio/<id>.wav   a copy of each sampled recording (--no-copy to skip), so
                   the labelled set outlives the two-week capture directory
  manifest.json    what was read, when, the counts, the sampling seed

--out is refused inside the repo and inside the recordings directory (copied
WAVs there would read as new recordings to voice_capture_check.py).

Never modifies or deletes a recording: they are opened read-only. Refuses to
overwrite an existing labels.csv (the labels typed into it are the work).

Sampling (--sample N, default 200; 0 = all): recordings are grouped by
(day, satellite, tier) and taken round-robin across the groups in a seeded
random order, so the sample spreads over days, pucks and tiers.

  scripts/voice_label_sheet.py --recordings ~/kronk-voice-recordings --out ~/kronk-voice-labels/2026-12-01

File format for the M13 bench: docs/features/voice-capture.md ("The labelling sheet").
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import shutil
import sys
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import voice_transcripts as vt  # noqa: E402

RECORDINGS = Path.home() / "kronk-voice-recordings"
COLUMNS = ("sample_id", "audio_file", "correct_text", "heard", "time", "satellite", "device_id",
           "pipeline", "tier", "rule", "wrong_action", "duration_s", "sample_rate", "run_id",
           "trace_id", "source_audio", "notes")
JOIN_BEFORE_S = 2.0      # the WAV's last write can precede the run's recorded start by clock jitter
JOIN_AFTER_S = 5.0       # ... and follows STT end; the run ends after intent and TTS


@dataclass
class Recording:
    path: Path
    device_id: str | None
    pipeline: str
    run_dir: str
    mtime: float
    duration_s: float | None
    sample_rate: int | None
    run: dict | None = field(default=None)
    note: str = ""


def _wav_info(p: Path) -> tuple[float | None, int | None]:
    try:
        with wave.open(str(p), "rb") as w:          # read-only
            rate = w.getframerate()
            return (w.getnframes() / rate if rate else None), rate
    except (wave.Error, EOFError, OSError):
        return None, None


def scan(recordings: Path) -> list[Recording]:
    """Every STT recording under the directory, in HA's layout. A run
    directory with no STT file (a run that died first) is skipped."""
    out = []
    for wav in sorted(recordings.rglob("01_stt-*.wav")):
        rel = wav.relative_to(recordings).parts      # [device,] pipeline, run, file
        if len(rel) == 4:
            device, pipeline, run_dir = rel[0], rel[1], rel[2]
        elif len(rel) == 3:
            device, pipeline, run_dir = None, rel[0], rel[1]
        else:
            continue
        dur, rate = _wav_info(wav)
        out.append(Recording(wav, device, pipeline, run_dir, wav.stat().st_mtime, dur, rate))
    return out


MATCH_SEARCH_MAX = 20000   # pairings explored per group before the whole group is called ambiguous


def _candidates(r: Recording, runs: list[dict]) -> list[int]:
    """Runs this recording could belong to: same pipeline, an STT stage when
    the run says what stages it had, the same device when both know it, the
    WAV's mtime inside [start - 2 s, end + 5 s]."""
    out = []
    for i, a in enumerate(runs):
        if a.get("ha.pipeline_name") != r.pipeline:
            continue
        stages = a.get("voice.stages")
        if stages is not None and "stt" not in stages:
            continue                              # a text run records nothing
        dev = a.get("kronk.origin.device_id")
        if r.device_id and dev and dev != r.device_id:
            continue
        if a["_start_s"] - JOIN_BEFORE_S <= r.mtime <= a["_end_s"] + JOIN_AFTER_S:
            out.append(i)
    return out


def _best_pairings(group: list[int], cands: list[list[int]]) -> list[dict[int, int | None]] | None:
    """Every one-to-one pairing of the group's recordings with their
    candidate runs that pairs the most recordings. None when the search is
    too large to be sure."""
    best: list[dict[int, int | None]] = []
    best_n = -1
    steps = 0

    def walk(k: int, used: set[int], cur: dict[int, int | None], n: int) -> bool:
        nonlocal best_n, steps
        steps += 1
        if steps > MATCH_SEARCH_MAX:
            return False
        if n + (len(group) - k) < best_n:
            return True                           # cannot reach the best any more
        if k == len(group):
            if n > best_n:
                best_n, best[:] = n, []
            if n == best_n:
                best.append(dict(cur))
            return True
        ri = group[k]
        for i in cands[ri]:
            if i not in used:
                used.add(i)
                cur[ri] = i
                if not walk(k + 1, used, cur, n + 1):
                    return False
                used.discard(i)
        cur[ri] = None
        return walk(k + 1, used, cur, n)

    return best if walk(0, set(), {}, 0) else None


def join(recs: list[Recording], runs: list[dict]) -> tuple[int, int]:
    """Attach each recording to its voice run when that is certain (header).
    Sets Recording.run, or Recording.note for an ambiguous join. Returns
    (recordings with no trace, traces with no audio)."""
    cands = [_candidates(r, runs) for r in recs]
    # groups: recordings linked through a shared candidate run
    parent = list(range(len(recs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_run: dict[int, int] = {}
    for ri, cs in enumerate(cands):
        for i in cs:
            if i in by_run:
                parent[find(ri)] = find(by_run[i])
            else:
                by_run[i] = ri
    groups: dict[int, list[int]] = {}
    for ri in range(len(recs)):
        groups.setdefault(find(ri), []).append(ri)

    paired: set[int] = set()                      # runs some best pairing gives audio
    for group in groups.values():
        pairings = _best_pairings(group, cands)
        for ri in group:
            r = recs[ri]
            options = {p.get(ri) for p in pairings} if pairings is not None else {"?"}
            if options == {None}:
                continue                          # no trace
            if len(options) == 1 and "?" not in options:
                i = options.pop()
                r.run = runs[i]
                if not (r.device_id and runs[i].get("kronk.origin.device_id")):
                    r.note = "joined by time and pipeline only (no device id on one side)"
                continue
            ids = [runs[i].get("ha.pipeline_run_id") or runs[i].get("_trace_id") or "?" for i in cands[ri]]
            others = len(group) - 1
            r.note = (f"ambiguous join: candidate runs {' '.join(ids)}"
                      + (f"; {others} other recording(s) fit the same run(s)" if others else "")
                      + "; heard left empty")
        for p in pairings or []:
            paired.update(i for i in p.values() if i is not None)
    no_trace = sum(1 for r in recs if r.run is None and not r.note.startswith("ambiguous"))
    audio_runs = [i for i, a in enumerate(runs) if "stt" in (a.get("voice.stages") or "")]
    no_audio = sum(1 for i in audio_runs if i not in paired)
    return no_trace, no_audio


def _group_key(r: Recording) -> tuple:
    day = dt.datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d")
    sat = (r.run or {}).get("ha.satellite_id") or r.device_id or "-"
    tier = (r.run or {}).get("voice.handled_by") or "no_trace"
    return day, sat, tier


def sample(recs: list[Recording], n: int, seed: int) -> list[Recording]:
    """Round-robin over (day, satellite, tier) groups in seeded order."""
    if n <= 0 or n >= len(recs):
        return sorted(recs, key=lambda r: r.mtime)
    rng = random.Random(seed)
    groups: dict[tuple, list[Recording]] = defaultdict(list)
    for r in recs:
        groups[_group_key(r)].append(r)
    keys = sorted(groups)
    rng.shuffle(keys)
    for k in keys:
        rng.shuffle(groups[k])
    picked: list[Recording] = []
    while len(picked) < n:
        progressed = False
        for k in keys:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop())
                progressed = True
        if not progressed:
            break
    return sorted(picked, key=lambda r: r.mtime)


def _short_sat(r: Recording) -> str:
    s = (r.run or {}).get("ha.satellite_id") or r.device_id or "nodevice"
    return s.replace("assist_satellite.", "").replace("_assist_satellite", "")[:24]


def refuse_inside_recordings(out: Path, recordings: Path) -> None:
    """Copied WAVs inside the recordings directory would read as new
    recordings (voice_capture_check.py would report capture on after it is
    off), and the sheet would sit in HA's directory."""
    rec = recordings.expanduser().resolve()
    if out == rec or rec in out.parents:
        raise vt.Refused(f"{out} is inside the recordings directory {rec}; write the sheet elsewhere")


def build(recordings: Path, out: Path, runs: list[dict], n: int = 200, seed: int = 12,
          copy: bool = True, since: float | None = None, until: float | None = None,
          include_dev: bool = False, dev_pipelines=vt.DEV_PIPELINES) -> dict:
    out = vt.refuse_repo_path(out)
    refuse_inside_recordings(out, recordings)
    sheet = out / "labels.csv"
    if sheet.exists():
        raise vt.Refused(f"{sheet} exists; it may hold labels. Pick a new --out directory")
    if not recordings.is_dir():
        raise vt.Refused(f"no recordings directory at {recordings}")
    recs = [r for r in scan(recordings)
            if (include_dev or r.pipeline not in dev_pipelines)
            and (since is None or r.mtime >= since) and (until is None or r.mtime <= until)]
    no_trace, no_audio = join(recs, runs)
    picked = sample(recs, n, seed)
    out.mkdir(parents=True, exist_ok=True)
    if copy:
        (out / "audio").mkdir(exist_ok=True)
    rows = []
    for i, r in enumerate(picked, 1):
        sid = f"{dt.datetime.fromtimestamp(r.mtime).strftime('%Y%m%d')}-{_short_sat(r)}-{i:03d}"
        audio_file = str(r.path)
        if copy:
            dst = out / "audio" / f"{sid}.wav"
            shutil.copyfile(r.path, dst)             # reads the source; never writes it
            audio_file = f"audio/{sid}.wav"
        a = r.run or {}
        rows.append({
            "sample_id": sid, "audio_file": audio_file, "correct_text": "",
            "heard": a.get("voice.transcript") or "",
            "time": dt.datetime.fromtimestamp(r.mtime).astimezone().isoformat(timespec="seconds"),
            "satellite": a.get("ha.satellite_id") or "", "device_id": r.device_id or "",
            "pipeline": r.pipeline, "tier": a.get("voice.handled_by") or "",
            "rule": a.get("kronk.rule") or "", "wrong_action": a.get("voice.corrected_by") or "",
            "duration_s": round(r.duration_s, 2) if r.duration_s is not None else "",
            "sample_rate": r.sample_rate or "", "run_id": a.get("ha.pipeline_run_id") or "",
            "trace_id": a.get("_trace_id") or "", "source_audio": str(r.path),
            "notes": r.note or ("" if r.run else "no trace"),
        })
    with open(sheet, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    manifest = {"created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "recordings": str(recordings), "recordings_found": len(recs),
                "recordings_without_trace": no_trace, "voice_runs_without_audio": no_audio,
                "recordings_ambiguous": sum(1 for r in recs if r.note.startswith("ambiguous")),
                "sampled": len(rows), "sample_target": n, "seed": seed, "audio_copied": copy,
                "include_dev": include_dev, "format": "docs/features/voice-capture.md#the-labelling-sheet"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv=None, now: float | None = None, get_json=vt.http_get_json) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--recordings", default=str(RECORDINGS), help="HA's debug_recording_dir on the host")
    ap.add_argument("--out", required=True, help="output directory, outside the repo")
    ap.add_argument("--sample", type=int, default=200, help="recordings to sample (0 = all; default 200)")
    ap.add_argument("--seed", type=int, default=12)
    ap.add_argument("--since", help='only recordings from: "14d" or an ISO date/time')
    ap.add_argument("--until", help="... up to")
    ap.add_argument("--include-dev", action="store_true", help="include kronk-dev (Hey Jarvis) runs")
    ap.add_argument("--no-copy", action="store_true", help="point at the recordings instead of copying them")
    ap.add_argument("--tempo", default=vt.TEMPO)
    args = ap.parse_args(argv)
    now = now if now is not None else dt.datetime.now().timestamp()
    try:
        since = vt.parse_when(args.since, now) if args.since else None
        until = vt.parse_when(args.until, now) if args.until else None
        rec_dir = Path(args.recordings).expanduser()
        mtimes = [p.stat().st_mtime for p in rec_dir.rglob("01_stt-*.wav")] if rec_dir.is_dir() else []
        t0 = (since if since is not None else min(mtimes, default=now)) - 60
        t1 = (until if until is not None else max(mtimes, default=now)) + 60
        truncated: list = []
        runs = vt.fetch_runs(t0, t1, args.tempo, include_dev=True, get_json=get_json, truncated=truncated)
        m = build(rec_dir, Path(args.out), runs, args.sample, args.seed, not args.no_copy,
                  since, until, args.include_dev)
    except vt.Refused as e:
        print(f"voice_label_sheet: REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"voice_label_sheet: {m['sampled']} of {m['recordings_found']} recordings -> "
          f"{Path(args.out).expanduser()}/labels.csv ({m['recordings_without_trace']} without a trace, "
          f"{m['voice_runs_without_audio']} voice runs without audio, "
          f"{m['recordings_ambiguous']} ambiguous joins)", file=sys.stderr)
    if truncated:
        print("voice_label_sheet: Tempo's search limit was hit (above); rows marked 'no trace' may have "
              "a run Tempo did not return", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
