#!/usr/bin/env python3
"""Text-to-speech bench over Wyoming (sprint M20).

Speaks Wyoming to one TTS service on a loopback host and port, the way Home
Assistant 2026.5 does (homeassistant/components/wyoming/tts.py), writes every
reply to a WAV file and plays nothing. Run with the test venv (numpy):

  # the sentence set, N times, per-sentence timing and failures
  tests/.venv/bin/python scripts/tts_bench.py run --port 10210 --engine piper \\
      --voice en_US-hfc_male-medium --reps 5 --wav-dir DIR --out RESULT.json \\
      [--service-pid PID ...] [--chunking whole|sentence] [--rest-every-s 150 --rest-s 75]

  # cold start: from the service process's start to the first audio
  tests/.venv/bin/python scripts/tts_bench.py cold --port 10210 --service-pid PID --out COLD.json

  # stand-in for "garbled or extra audio": transcribe every WAV with the dev
  # Whisper (127.0.0.1:10301; the production port 10300 is refused) and check
  # length, clipping and sound at the edges
  tests/.venv/bin/python scripts/tts_bench.py check RESULT.json ... --out CHECK.json

  # model slowdown: llama.cpp tokens per second alone, then while a TTS
  # service synthesizes (bursts under two minutes; refuses unless HA shows no
  # voice activity in the last five minutes)
  tests/.venv/bin/python scripts/tts_bench.py llm --label piper --seconds 60 \\
      [--tts-port 10210 --tts-voice V] --out LLM.json

  # GPU busy, clocks, power and memory at a fixed interval, to a JSONL file
  tests/.venv/bin/python scripts/tts_bench.py sample --interval 10 --duration 3600 \\
      --out FILE.jsonl [--pid PID ...] [--guard-pid PID ...] [--drop-cache DIR]

  # a loopback TCP port in front of a service that listens on a Unix socket
  # (a service started with no network at all, e.g. under bwrap --unshare-net);
  # --listen-unix PATH --to-port P is the reverse, run inside such a sandbox
  tests/.venv/bin/python scripts/tts_bench.py relay --port 10210 --unix PATH

  # one summary per sampler file (busy, clock, power, memory)
  tests/.venv/bin/python scripts/tts_bench.py idle FILE.jsonl ... [--skip-min 5]

  # tables for the write-up, and the blind listening-test set
  tests/.venv/bin/python scripts/tts_bench.py table RESULT.json ...
  tests/.venv/bin/python scripts/tts_bench.py blind RESULT.json ... --out DIR --key FILE

What one request is (`run`): if the service's `describe` says
`supports_synthesize_streaming`, the HA streaming sequence: `synthesize-start`,
the text as one `synthesize-chunk` (production: VOICE_STREAM off) or one chunk
per sentence (`--chunking sentence`, M8's streamed reply), the whole text again
as `synthesize`, `synthesize-stop`; audio is read until `synthesize-stopped` or
the connection closes after audio. Otherwise one `synthesize`, read until
`audio-stop`. One connection per request, as HA opens one.

Timing: t0 is taken before the connection opens. Time to first audio is the
first `audio-chunk` with samples; total is the end event; audio duration is
samples / rate; real-time factor is total / audio duration (below 1 is faster
than real time). Percentiles are nearest-rank.

Failures, by kind: refused (the connection is refused), timeout (no event for
--timeout seconds), stream-stopped (the connection closed before the end),
empty (the end arrived with no audio), server-error (an `error` event),
bad-format (audio that is not 16-bit mono). Every failure is a row.

Only loopback hosts are accepted. The Wyoming framing, the transcriber and
the word scoring are scripts/stt_prompt_bench.py's (sprint M13).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
import urllib.request
import wave
from pathlib import Path
from typing import Awaitable, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import stt_prompt_bench as spb  # noqa: E402  (framing, transcribe, scoring)

SCHEMA = "tts_bench/1"
DEFAULT_SENTENCES = REPO / "scripts" / "tts_bench_sentences.json"
PRODUCTION_STT_PORT = 10300
DEV_STT_PORT = 10301
LLM_URL = "http://127.0.0.1:11438"          # llama-gemma4-e4b
LLM_PROMPT = ("Explain in plain words how a loaf of bread rises in the oven, "
              "step by step, for someone who has never baked.")
LLM_MAX_BURST_S = 115.0                      # ruling 4: each burst under two minutes
QUIET_WINDOW_S = 300.0                       # no voice activity in the last five minutes
FAILURE_KINDS = ("refused", "timeout", "stream-stopped", "empty", "server-error", "bad-format")
ROW_KEYS = ("id", "category", "rep", "ok", "failure", "detail", "ttfa_s", "total_s", "audio_s",
            "rtf", "rate", "audio_starts", "end", "chunks_sent", "last_chunk_s", "wav")

BenchError = spb.BenchError
check_host = spb.check_host


# ── sentences ───────────────────────────────────────────────────────────────

def load_sentences(path: Path, categories: list[str] | None = None) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = doc["sentences"]
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise BenchError(f"{path}: duplicate sentence ids")
    if categories:
        rows = [r for r in rows if r["category"] in categories]
    return {"version": doc.get("version"), "sentences": rows}


_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    """Sentence pieces as M8's stream would send them: each piece but the
    first keeps its leading space, so the pieces join back to the text."""
    parts = _SENT_END.split(text.strip())
    return [p if i == 0 else " " + p for i, p in enumerate(parts)]


# ── statistics ──────────────────────────────────────────────────────────────

def percentile(values, p: float):
    vals = [v for v in values if v is not None]
    return spb.percentile(vals, p) if vals else None


def rnd(x, n=3):
    return None if x is None else round(x, n)


def stats(values) -> dict:
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "p50": rnd(percentile(vals, 50)), "p95": rnd(percentile(vals, 95)),
            "max": rnd(max(vals)) if vals else None}


def summarize_rows(rows: list[dict]) -> dict:
    out: dict = {"by_category": {}, "overall": {}, "failures": {}}
    for r in rows:
        if not r["ok"]:
            out["failures"][r["failure"]] = out["failures"].get(r["failure"], 0) + 1
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    for name, rs in sorted(groups.items()) + [("_all", rows)]:
        ok = [r for r in rs if r["ok"]]
        s = {"requests": len(rs), "ok": len(ok),
             "ttfa_s": stats([r["ttfa_s"] for r in ok]),
             "total_s": stats([r["total_s"] for r in ok]),
             "audio_s": stats([r["audio_s"] for r in ok]),
             "rtf": stats([r["rtf"] for r in ok])}
        if name == "_all":
            out["overall"] = s
        else:
            out["by_category"][name] = s
    return out


# ── one Wyoming request ─────────────────────────────────────────────────────

Connect = Callable[[], Awaitable[tuple]]


def tcp_connect(host: str, port: int) -> Connect:
    check_host(host)

    async def go():
        return await asyncio.open_connection(host, port)
    return go


async def describe(connect: Connect, timeout: float = 15) -> dict:
    reader, writer = await asyncio.wait_for(connect(), timeout)
    try:
        await spb.write_event(writer, "describe")
        while True:
            ev = await asyncio.wait_for(spb.read_event(reader), timeout)
            if ev is None:
                raise BenchError("connection closed before info")
            if ev[0] == "info":
                return ev[1]
    finally:
        writer.close()


def tts_program(info: dict) -> dict:
    progs = info.get("tts") or []
    if not progs:
        raise BenchError("describe: the service offers no TTS program")
    return progs[0]


def make_row(sentence: dict, rep: int) -> dict:
    row = {k: None for k in ROW_KEYS}
    row.update(id=sentence["id"], category=sentence["category"], rep=rep, ok=False,
               audio_starts=0, chunks_sent=0)
    return row


async def synth_once(connect: Connect, sentence: dict, rep: int, *, voice: str | None,
                     streaming: bool, chunking: str = "whole", chunk_gap_s: float = 0.0,
                     timeout: float = 60.0, clock: Callable[[], float] = time.perf_counter
                     ) -> tuple[dict, bytes]:
    """One request. Returns (row, pcm). Never raises for a service failure:
    the failure is the row's `failure` and `detail`."""
    row = make_row(sentence, rep)
    text = sentence["text"]
    vdata = {"name": voice} if voice else None
    audio = bytearray()
    fmt: dict = {}
    t0 = clock()
    try:
        reader, writer = await asyncio.wait_for(connect(), timeout)
    except (ConnectionRefusedError, FileNotFoundError) as e:
        row.update(failure="refused", detail=f"{type(e).__name__}: {e}")
        return row, b""
    except asyncio.TimeoutError:
        row.update(failure="timeout", detail="connect")
        return row, b""
    except OSError as e:
        row.update(failure="refused", detail=f"{type(e).__name__}: {e}")
        return row, b""

    async def send():
        if not streaming:
            data = {"text": text}
            if vdata:
                data["voice"] = vdata
            await spb.write_event(writer, "synthesize", data)
            row["chunks_sent"] = 1
            row["last_chunk_s"] = clock() - t0
            return
        await spb.write_event(writer, "synthesize-start", {"voice": vdata} if vdata else None)
        pieces = split_sentences(text) if chunking == "sentence" else [text]
        for i, piece in enumerate(pieces):
            if i and chunk_gap_s:
                await asyncio.sleep(chunk_gap_s)
            await spb.write_event(writer, "synthesize-chunk", {"text": piece})
            row["chunks_sent"] += 1
        row["last_chunk_s"] = clock() - t0
        data = {"text": text}
        if vdata:
            data["voice"] = vdata
        await spb.write_event(writer, "synthesize", data)
        await spb.write_event(writer, "synthesize-stop")

    sender = asyncio.ensure_future(send())
    end_types = ("synthesize-stopped",) if streaming else ("audio-stop",)
    try:
        while True:
            try:
                ev = await asyncio.wait_for(spb.read_event(reader), timeout)
            except asyncio.TimeoutError:
                row.update(failure="timeout", detail=f"no event for {timeout:g} s")
                break
            except (asyncio.IncompleteReadError, ConnectionResetError) as e:
                ev = None
                row["detail"] = f"{type(e).__name__}"
            if ev is None:
                # HA's streaming reader also ends on a closed connection; that
                # counts as the end only once some audio has finished
                if streaming and audio and row["audio_starts"] and fmt.get("_stopped"):
                    row["end"] = "eof"
                else:
                    row.update(failure="stream-stopped",
                               detail=row["detail"] or "connection closed before the end")
                break
            etype, data, payload = ev
            if etype == "error":
                row.update(failure="server-error", detail=str(data.get("text") or data)[:300])
                break
            if etype == "audio-start":
                row["audio_starts"] += 1
                fmt.update({k: data[k] for k in ("rate", "width", "channels") if k in data})
            elif etype == "audio-chunk":
                fmt.update({k: data[k] for k in ("rate", "width", "channels") if k in data})
                if payload and row["ttfa_s"] is None:
                    row["ttfa_s"] = clock() - t0
                audio.extend(payload)
            elif etype == "audio-stop":
                fmt["_stopped"] = True
            if etype in end_types:
                row["end"] = etype
                break
    finally:
        if not sender.done():
            sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, Exception):
            pass
        writer.close()
    if row["failure"]:
        return row, bytes(audio)
    row["total_s"] = clock() - t0
    if fmt.get("width") not in (None, 2) or fmt.get("channels") not in (None, 1):
        row.update(failure="bad-format", detail=f"width {fmt.get('width')} channels {fmt.get('channels')}")
        return row, bytes(audio)
    if not audio:
        row.update(failure="empty", detail="the end arrived with no audio")
        return row, b""
    rate = int(fmt.get("rate") or 0)
    if rate <= 0:
        row.update(failure="bad-format", detail="no sample rate")
        return row, bytes(audio)
    row["rate"] = rate
    row["audio_s"] = len(audio) / 2 / rate
    row["rtf"] = row["total_s"] / row["audio_s"]
    row["ok"] = True
    for k in ("ttfa_s", "total_s", "audio_s", "rtf", "last_chunk_s"):
        row[k] = rnd(row[k], 4)
    return row, bytes(audio)


def write_pcm_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# ── process memory (read-only /proc and /sys) ───────────────────────────────

def proc_mem(pid: int) -> dict | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    rss_kb = next((int(l.split()[1]) for l in status.splitlines() if l.startswith("VmRSS:")), 0)
    gtt_kib, seen = 0, set()
    try:
        fds = os.listdir(f"/proc/{pid}/fdinfo")
    except OSError:
        fds = []
    for fd in fds:
        try:
            txt = Path(f"/proc/{pid}/fdinfo/{fd}").read_text()
        except OSError:
            continue
        cid = None
        for line in txt.splitlines():
            if line.startswith("drm-client-id:"):
                cid = line.split()[1]
            elif line.startswith("drm-memory-gtt:") and cid not in seen:
                seen.add(cid)
                gtt_kib += int(line.split()[1])
    kfd = 0
    kdir = Path(f"/sys/class/kfd/kfd/proc/{pid}")
    if kdir.is_dir():
        for f in kdir.glob("vram_*"):
            try:
                kfd += int(f.read_text().strip() or 0)
            except (OSError, ValueError):
                pass
    return {"rss_gb": round(rss_kb / 1048576, 3), "gtt_gb": round(gtt_kib / 1048576, 3),
            "kfd_gb": round(kfd / 1e9, 3)}


def proc_cmd(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()[:200]
    except OSError:
        return ""


def proc_start_epoch(pid: int) -> float:
    """Wall-clock start of a process: /proc/PID/stat field 22 plus btime."""
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rindex(")") + 2:].split()
    start_ticks = int(fields[19])
    btime = next(int(l.split()[1]) for l in Path("/proc/stat").read_text().splitlines()
                 if l.startswith("btime "))
    return btime + start_ticks / os.sysconf("SC_CLK_TCK")


class MemTrack:
    def __init__(self, pids: list[int]):
        self.pids = pids
        self.peak: dict[int, dict] = {}

    def sample(self) -> None:
        for pid in self.pids:
            m = proc_mem(pid)
            if m is None:
                continue
            p = self.peak.setdefault(pid, {"cmd": proc_cmd(pid), "rss_gb_max": 0, "gtt_gb_max": 0,
                                           "kfd_gb_max": 0, "first": m})
            p["rss_gb_max"] = max(p["rss_gb_max"], m["rss_gb"])
            p["gtt_gb_max"] = max(p["gtt_gb_max"], m["gtt_gb"])
            p["kfd_gb_max"] = max(p["kfd_gb_max"], m["kfd_gb"])
            p["last"] = m


# ── run ─────────────────────────────────────────────────────────────────────

def git_rev() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def wav_name(engine: str, sentence_id: str, rep: int) -> str:
    return f"{engine}_{sentence_id}_r{rep}.wav"


async def run_set(connect: Connect, sentences: list[dict], *, engine: str, voice: str | None,
                  reps: int, wav_dir: Path | None, streaming: bool, chunking: str = "whole",
                  chunk_gap_s: float = 0.0, timeout: float = 60.0, mem: MemTrack | None = None,
                  rest_every_s: float = 0.0, rest_s: float = 0.0,
                  log: Callable[[str], None] = print,
                  sleep: Callable[[float], Awaitable] = asyncio.sleep,
                  clock: Callable[[], float] = time.perf_counter) -> tuple[list[dict], list[dict]]:
    rows, rests = [], []
    since_rest = clock()
    for rep in range(1, reps + 1):
        for s in sentences:
            if rest_every_s and clock() - since_rest >= rest_every_s:
                log(f"  rest {rest_s:g} s")
                rests.append({"after": f"{s['id']} r{rep}", "s": rest_s})
                await sleep(rest_s)
                since_rest = clock()
            row, pcm = await synth_once(connect, s, rep, voice=voice, streaming=streaming,
                                        chunking=chunking, chunk_gap_s=chunk_gap_s,
                                        timeout=timeout, clock=clock)
            if row["ok"] and wav_dir is not None:
                path = Path(wav_dir) / wav_name(engine, s["id"], rep)
                write_pcm_wav(path, pcm, row["rate"])
                row["wav"] = str(path)
            if mem:
                mem.sample()
            rows.append(row)
            log(f"  {engine} r{rep} {s['id']}: " + (
                f"first {row['ttfa_s']} s total {row['total_s']} s audio {row['audio_s']} s "
                f"rtf {row['rtf']}" if row["ok"] else f"FAIL {row['failure']}: {row['detail']}"))
    return rows, rests


def cmd_run(args) -> int:
    doc = load_sentences(args.sentences, args.category)
    connect = tcp_connect(args.host, args.port)
    info = asyncio.run(describe(connect))
    prog = tts_program(info)
    streaming = bool(prog.get("supports_synthesize_streaming")) if args.mode == "auto" else args.mode == "stream"
    if args.chunking == "sentence" and not streaming:
        raise SystemExit("tts_bench: --chunking sentence needs a service that streams")
    mem = MemTrack(args.service_pid or [])
    mem.sample()
    t = dt.datetime.now(dt.timezone.utc)
    rows, rests = asyncio.run(run_set(
        connect, doc["sentences"], engine=args.engine, voice=args.voice, reps=args.reps,
        wav_dir=Path(args.wav_dir).resolve() if args.wav_dir else None, streaming=streaming,
        chunking=args.chunking, chunk_gap_s=args.chunk_gap_s, timeout=args.timeout, mem=mem,
        rest_every_s=args.rest_every_s, rest_s=args.rest_s))
    mem.sample()
    result = {
        "schema": SCHEMA, "kind": "run", "engine": args.engine, "label": args.label or args.engine,
        "voice": args.voice, "host": args.host, "port": args.port,
        "mode": "stream" if streaming else "plain", "chunking": args.chunking,
        "chunk_gap_s": args.chunk_gap_s, "reps": args.reps, "timeout_s": args.timeout,
        "sentences_file": str(args.sentences), "sentences_version": doc["version"],
        "started_utc": t.isoformat(timespec="seconds"), "git": git_rev(),
        "describe": {k: prog.get(k) for k in ("name", "version", "supports_synthesize_streaming",
                                              "description")}
                    | {"voices": [v.get("name") for v in prog.get("voices") or []][:40]},
        "rests": rests, "memory": {str(k): v for k, v in mem.peak.items()},
        "rows": rows, "summary": summarize_rows(rows),
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"tts_bench: wrote {args.out}: {json.dumps(result['summary']['overall'])} "
          f"failures {result['summary']['failures']}")
    return 0


# ── cold start ──────────────────────────────────────────────────────────────

async def cold_start(connect: Connect, sentence: dict, *, voice: str | None, start_epoch: float,
                     wait_s: float, timeout: float, poll_s: float = 0.25,
                     sleep=asyncio.sleep, wall=time.time) -> dict:
    ready = None
    attempts, last_err = 0, None
    deadline = wall() + wait_s
    info = None
    while wall() < deadline:
        attempts += 1
        try:
            info = await describe(connect, timeout=5)
            ready = wall()
            break
        except Exception as e:     # not up yet
            last_err = f"{type(e).__name__}: {e}"
            await sleep(poll_s)
    if ready is None:
        return {"ok": False, "failure": "timeout", "detail": f"not ready after {wait_s:g} s: {last_err}",
                "attempts": attempts}
    streaming = bool(tts_program(info).get("supports_synthesize_streaming"))
    t_req = wall()
    first, _ = await synth_once(connect, sentence, 1, voice=voice, streaming=streaming, timeout=timeout)
    second, _ = await synth_once(connect, sentence, 2, voice=voice, streaming=streaming, timeout=timeout)
    out = {"ok": first["ok"], "attempts": attempts, "ready_after_start_s": rnd(ready - start_epoch),
           "first_request": first, "second_request": second}
    if first["ok"]:
        out["first_audio_after_start_s"] = rnd(t_req + first["ttfa_s"] - start_epoch)
    return out


def cmd_cold(args) -> int:
    doc = load_sentences(args.sentences)
    s = next(x for x in doc["sentences"] if x["id"] == args.sentence)
    start = args.started_at if args.started_at else proc_start_epoch(args.service_pid)
    res = asyncio.run(cold_start(tcp_connect(args.host, args.port), s, voice=args.voice,
                                 start_epoch=start, wait_s=args.wait_s, timeout=args.timeout))
    mem = proc_mem(args.service_pid) if args.service_pid else None
    out = {"schema": SCHEMA, "kind": "cold", "engine": args.engine, "port": args.port,
           "service_pid": args.service_pid, "start_epoch": start, "memory_after": mem, **res}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"tts_bench: cold {args.engine}: ready {res.get('ready_after_start_s')} s, first audio "
          f"{res.get('first_audio_after_start_s')} s after start; memory {mem}")
    return 0 if res["ok"] else 1


# ── stand-in check (ruling 5) ───────────────────────────────────────────────

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORDINALS = {"first": "one", "second": "two", "third": "three", "fourth": "four", "fifth": "five",
             "sixth": "six", "seventh": "seven", "eighth": "eight", "ninth": "nine", "tenth": "ten",
             "eleventh": "eleven", "twelfth": "twelve", "twentieth": "twenty", "thirtieth": "thirty"}


def int_words(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (int_words(n % 100) if n % 100 else [])
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            return int_words(n // size) + [name] + (int_words(n % size) if n % size else [])
    return [str(n)]


def canon(text: str) -> list[str]:
    """Words for comparison: accents dropped, digit groups joined, times,
    a.m./p.m., ordinals and digits spelled the same way on both sides."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"(\d)\s?([ap])\.?\s?m\b\.?", r"\1 \2m", t, flags=re.I)
    t = re.sub(r"\b([ap])\.?\s?m\b\.?", r"\1m", t, flags=re.I)
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", t, flags=re.I)
    t = re.sub(r"(\d+):00\b", r"\1", t)
    t = re.sub(r"(\d+)%", r"\1 percent", t)
    t = t.replace("$", " dollars ")
    words = []
    for w in spb.normalize(t):
        w = w.replace("-", " ")
        for p in w.split():
            if p.isdigit() and len(p) <= 12:
                words.extend(int_words(int(p)))
            else:
                words.append(_ORDINALS.get(p, p))
    return [w for w in words if w not in ("and", "dollars")]


def spoken_words(text: str) -> float:
    """A rough count of spoken words: a digit group reads as about one word
    per digit (`425` → four hundred twenty five)."""
    n = 0.0
    for tok in re.findall(r"[\w'$.,:]+", text):
        digits = re.sub(r"\D", "", tok)
        n += (1 + 1.2 * (len(digits) - 1)) if digits else 1
    return n


LENGTH_BAND = (0.5, 2.0)            # speech span vs words / 2.7 per second
EDGE_GAP_S = 0.6                    # a sound this far from the speech at an edge is extra
EDGE_BLIP_MAX_S = 0.35


def audio_checks(x, rate: int, text: str) -> dict:
    """Length against expected speaking time, clipping, and extra sound at
    the edges, from the float samples (-1..1)."""
    import numpy as np
    n = len(x)
    frame = max(1, int(0.02 * rate))
    nf = n // frame
    if nf == 0:
        return {"speech_s": 0.0, "flags": ["no-audio"]}
    fr = np.sqrt(np.mean(np.square(x[: nf * frame].reshape(nf, frame)), axis=1))
    thr = max(0.02 * float(fr.max()), 1e-3)
    act = fr > thr
    idx = np.flatnonzero(act)
    flags: list[str] = []
    if not len(idx):
        return {"speech_s": 0.0, "flags": ["silent"]}
    # segments of activity, joining gaps shorter than 0.3 s
    segs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if (i - prev) * 0.02 > 0.3:
            segs.append((start, prev))
            start = i
        prev = i
    segs.append((start, prev))
    lead_s = idx[0] * frame / rate
    trail_s = (n - (idx[-1] + 1) * frame) / rate
    first, last = segs[0], segs[-1]
    lead_blip = len(segs) > 1 and (first[1] - first[0] + 1) * 0.02 <= EDGE_BLIP_MAX_S and \
        (segs[1][0] - first[1]) * 0.02 >= EDGE_GAP_S
    trail_blip = len(segs) > 1 and (last[1] - last[0] + 1) * 0.02 <= EDGE_BLIP_MAX_S and \
        (last[0] - segs[-2][1]) * 0.02 >= EDGE_GAP_S
    if lead_blip:
        flags.append("leading-sound")
    if trail_blip:
        flags.append("trailing-sound")
    core = segs[1:] if lead_blip else segs
    core = core[:-1] if trail_blip and len(core) > 1 else core
    speech_s = (core[-1][1] - core[0][0] + 1) * 0.02
    expected_s = spoken_words(text) / 2.7
    ratio = speech_s / expected_s if expected_s else None
    if ratio is not None and not (LENGTH_BAND[0] <= ratio <= LENGTH_BAND[1]):
        flags.append("length")
    full = np.abs(x) >= 32766 / 32768
    clipped = int(full.sum())
    run = 0
    longest = 0
    if clipped:
        for v in full:
            run = run + 1 if v else 0
            longest = max(longest, run)
    if clipped > max(3, 1e-4 * n) or longest >= 3:
        flags.append("clipping")
    quiet = fr[~act]
    noise_db = round(20 * math.log10(float(np.median(quiet)) + 1e-9), 1) if len(quiet) else None
    return {"speech_s": round(speech_s, 3), "expected_s": round(expected_s, 3),
            "length_ratio": rnd(ratio), "lead_silence_s": round(lead_s, 3),
            "trail_silence_s": round(trail_s, 3), "segments": len(segs), "clipped_samples": clipped,
            "longest_clip_run": longest, "noise_floor_dbfs": noise_db, "flags": flags}


def read_wav_float(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2 or ch != 1:
        raise BenchError(f"{path}: not 16-bit mono")
    return np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0, rate


def check_row(row: dict, text: str, hyp: str, metrics: dict) -> dict:
    ref_w, hyp_w = canon(text), canon(hyp)
    errors = spb.edit_distance(ref_w, hyp_w)
    flags = list(metrics.get("flags", []))
    if errors:
        flags.append("words")
    return {"id": row["id"], "rep": row["rep"], "category": row["category"], "wav": row["wav"],
            "ref": text, "hyp": hyp, "ref_words": len(ref_w), "word_errors": errors,
            "wer": round(errors / len(ref_w), 4) if ref_w else None, **metrics, "flags": flags}


def check_stt_port(port: int) -> int:
    if port == PRODUCTION_STT_PORT:
        raise BenchError("refusing port 10300: the stand-in check uses the dev Whisper only")
    return port


async def check_result(result: dict, texts: dict[str, str], stt_host: str, stt_port: int,
                       transcribe=spb.transcribe, log=print) -> list[dict]:
    check_stt_port(stt_port)
    check_host(stt_host)
    out = []
    for row in result["rows"]:
        if not row["ok"] or not row.get("wav"):
            continue
        x, rate = read_wav_float(Path(row["wav"]))
        metrics = audio_checks(x, rate, texts[row["id"]])
        pcm = spb.read_wav_16k(Path(row["wav"]))
        hyp, _ = await transcribe(stt_host, stt_port, pcm)
        c = check_row(row, texts[row["id"]], hyp, metrics)
        out.append(c)
        if c["flags"]:
            log(f"  {result['engine']} {row['id']} r{row['rep']}: {c['flags']} hyp={hyp!r}")
    return out


def cmd_check(args) -> int:
    doc = load_sentences(args.sentences)
    texts = {s["id"]: s["text"] for s in doc["sentences"]}
    report = {"schema": SCHEMA, "kind": "check", "stt": f"{args.stt_host}:{args.stt_port}",
              "length_band": LENGTH_BAND, "engines": {}}
    for f in args.results:
        res = json.loads(Path(f).read_text())
        rows = asyncio.run(check_result(res, texts, args.stt_host, args.stt_port))
        flagged = [r for r in rows if r["flags"]]
        counts: dict[str, int] = {}
        for r in flagged:
            for fl in r["flags"]:
                counts[fl] = counts.get(fl, 0) + 1
        report["engines"][res["label"]] = {"result": str(f), "checked": len(rows),
                                           "flagged": len(flagged), "by_flag": counts, "rows": rows}
        print(f"tts_bench: check {res['label']}: {len(rows)} checked, {len(flagged)} flagged {counts}")
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    return 0


# ── model slowdown (ruling 4) ───────────────────────────────────────────────

def ha_quiet(states: list[dict], now: float, window_s: float = QUIET_WINDOW_S) -> tuple[bool, list[str]]:
    """Quiet when every voice satellite is idle and none changed state in the
    window. A satellite is `listening`, `processing` or `responding` for the
    whole of a voice request, its spoken reply included, and returns to
    `idle` after it, so `last_changed` dates the last voice request. Media
    players are not read: music on a speaker, a puck's included, does not
    stop a burst (the brief)."""
    reasons = []
    for s in states:
        if not s["entity_id"].startswith("assist_satellite."):
            continue
        state = s.get("state")
        if state == "unavailable":
            continue
        if state != "idle":
            reasons.append(f"{s['entity_id']} is {state}")
        changed = s.get("last_changed")
        if changed:
            age = now - dt.datetime.fromisoformat(changed).timestamp()
            if age < window_s:
                reasons.append(f"{s['entity_id']} changed {age:.0f} s ago")
    return (not reasons), reasons


def ha_token() -> str:
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise BenchError(".env has no HA_TOKEN")


def ha_states(url: str = "http://127.0.0.1:8123") -> list[dict]:
    req = urllib.request.Request(url + "/api/states", headers={"Authorization": f"Bearer {ha_token()}"})
    with urllib.request.urlopen(req, timeout=15) as r:   # GET only: read-only
        return json.loads(r.read())


def llm_once(url: str, n_predict: int, timeout: float = 60) -> dict:
    body = json.dumps({"prompt": LLM_PROMPT, "n_predict": n_predict, "temperature": 0, "seed": 42,
                       "cache_prompt": False}).encode()
    req = urllib.request.Request(url + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    tm = d.get("timings") or {}
    return {"wall_s": round(time.perf_counter() - t0, 3), "predicted_n": tm.get("predicted_n"),
            "predicted_per_second": tm.get("predicted_per_second"),
            "prompt_per_second": tm.get("prompt_per_second")}


SUSTAINED_AFTER_S = 15.0     # the APU holds a higher clock for the first ~13 s of load


def token_rates(reqs: list[dict]) -> dict:
    """Token rates over the whole burst and over the requests that start
    after SUSTAINED_AFTER_S: on this host the first ~13 s of load run at a
    boost clock (99 then 85 tok/s, 2026-11-18), so the sustained part is
    what two bursts are compared on."""
    tps = [r["predicted_per_second"] for r in reqs if r.get("predicted_per_second")]
    sus = [r["predicted_per_second"] for r in reqs
           if r.get("predicted_per_second") and r.get("t_s", 0) >= SUSTAINED_AFTER_S]
    return {"tok_s": stats(tps), "tok_s_mean": rnd(sum(tps) / len(tps)) if tps else None,
            "tok_s_sustained": stats(sus),
            "tok_s_sustained_mean": rnd(sum(sus) / len(sus)) if sus else None}


async def llm_burst(url: str, seconds: float, n_predict: int, tts=None, log=print) -> dict:
    if seconds > LLM_MAX_BURST_S:
        raise BenchError(f"a burst is at most {LLM_MAX_BURST_S:g} s (ruling 4)")
    check_host(re.sub(r"^https?://([^:/]+).*", r"\1", url))
    stop = asyncio.Event()
    tts_rows: list[dict] = []

    async def tts_loop():
        connect, sentences, voice, streaming = tts
        i = 0
        while not stop.is_set():
            s = sentences[i % len(sentences)]
            row, _ = await synth_once(connect, s, i // len(sentences) + 1, voice=voice,
                                      streaming=streaming, timeout=60)
            tts_rows.append(row)
            i += 1

    task = asyncio.ensure_future(tts_loop()) if tts else None
    if task:
        await asyncio.sleep(1.0)               # the engine is synthesizing before the first token
    reqs = []
    t_start = time.monotonic()
    t_end = t_start + seconds
    while time.monotonic() < t_end:
        t_req = time.monotonic() - t_start
        r = await asyncio.to_thread(llm_once, url, n_predict)
        r["t_s"] = round(t_req, 2)
        reqs.append(r)
        log(f"  llm {r['predicted_per_second']:.1f} tok/s ({r['predicted_n']} tokens)"
            + (f", tts requests so far {len(tts_rows)}" if tts else ""))
    stop.set()
    if task:
        await task
    return {"requests": reqs, **token_rates(reqs),
            "tts_requests": len(tts_rows), "tts_failures": sum(not r["ok"] for r in tts_rows),
            "tts_rows": tts_rows}


def cmd_llm(args) -> int:
    ok, reasons = ha_quiet(ha_states(), time.time())
    if not ok and not args.dry_check:
        print("tts_bench: not quiet, no burst: " + "; ".join(reasons))
        return 3
    if args.dry_check:
        print("tts_bench: quiet" if ok else "tts_bench: not quiet: " + "; ".join(reasons))
        return 0 if ok else 3
    tts = None
    if args.tts_port:
        connect = tcp_connect(args.tts_host, args.tts_port)
        prog = tts_program(asyncio.run(describe(connect)))
        sentences = load_sentences(args.sentences)["sentences"]
        tts = (connect, sentences, args.tts_voice, bool(prog.get("supports_synthesize_streaming")))
    t = dt.datetime.now(dt.timezone.utc)
    res = asyncio.run(llm_burst(args.url, args.seconds, args.n_predict, tts))
    out = {"schema": SCHEMA, "kind": "llm", "label": args.label, "url": args.url,
           "with_tts": f"{args.tts_host}:{args.tts_port}" if args.tts_port else None,
           "started_utc": t.isoformat(timespec="seconds"), "seconds": args.seconds,
           "n_predict": args.n_predict, "prompt": LLM_PROMPT, "quiet_check": reasons or "quiet", **res}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"tts_bench: llm {args.label}: sustained {res['tok_s_sustained']} mean "
          f"{res['tok_s_sustained_mean']} tok/s (all {res['tok_s_mean']}), "
          f"tts requests {res['tts_requests']} failures {res['tts_failures']}")
    return 0


# ── sampler (criterion 8) ───────────────────────────────────────────────────

DRM = Path("/sys/class/drm/card1/device")


def _read(p: Path, conv=float):
    try:
        return conv(p.read_text().strip())
    except (OSError, ValueError):
        return None


def fleet_engine_ns() -> int:
    """drm-engine-* time across llama-server and wyoming processes, as
    scripts/memwatch.sh counts it (deduplicated per client and engine)."""
    total = 0
    out = subprocess.run(["pgrep", "-f", "llama-server|wyoming"], capture_output=True, text=True).stdout
    for pid in out.split():
        seen = set()
        try:
            fds = os.listdir(f"/proc/{pid}/fdinfo")
        except OSError:
            continue
        for fd in fds:
            try:
                txt = Path(f"/proc/{pid}/fdinfo/{fd}").read_text()
            except OSError:
                continue
            cid = None
            for line in txt.splitlines():
                if line.startswith("drm-client-id:"):
                    cid = line.split()[1]
                elif line.startswith("drm-engine-"):
                    key = (cid, line.split(":")[0])
                    if key not in seen:
                        seen.add(key)
                        total += int(line.split()[1])
    return total


def host_sample(pids: list[int]) -> dict:
    hw = next(iter(sorted((DRM / "hwmon").glob("hwmon*"))), None)
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        if k in ("MemFree", "MemAvailable", "Cached"):
            mem[k] = round(int(v.split()[0]) / 1048576, 3)
    psi = Path("/proc/pressure/memory").read_text().split()[1].split("=")[1]
    row = {"t": round(time.time(), 1), "busy": _read(DRM / "gpu_busy_percent", int),
           "sclk_mhz": (_read(hw / "freq1_input") or 0) / 1e6 if hw else None,
           "power_w": (_read(hw / "power1_average") or 0) / 1e6 if hw else None,
           "temp_c": (_read(hw / "temp1_input") or 0) / 1000 if hw else None,
           "vram_gb": round((_read(DRM / "mem_info_vram_used") or 0) / 1e9, 3),
           "gtt_gb": round((_read(DRM / "mem_info_gtt_used") or 0) / 1e9, 3),
           "memfree_gb": mem.get("MemFree"), "memavail_gb": mem.get("MemAvailable"),
           "psi_some_avg10": float(psi), "fleet_engine_ns": fleet_engine_ns(),
           "pids": {str(p): proc_mem(p) for p in pids}}
    return row


def drop_cache(root: Path) -> int:
    """posix_fadvise(DONTNEED) on every regular file under root (the trial's
    own downloads and venvs): drops their clean page-cache pages, no root."""
    n = 0
    os.sync()                   # dirty pages are written first, or DONTNEED keeps them
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                if os.path.islink(p):
                    continue
                fd = os.open(p, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                n += 1
            except OSError:
                pass
            finally:
                os.close(fd)
    return n


class SpinGuard:
    """Stops the guarded processes when the GPU stays busy at or above
    `busy` for `hold_s` while the model servers do no work (the idle-spin of
    INVESTIGATION_2026-06-09_hangs.md), before memwatch's fifth poll."""

    def __init__(self, pids: list[int], busy: int = 90, hold_s: float = 180,
                 engine_min_frac: float = 0.01, kill=os.kill):
        self.pids, self.busy, self.hold_s, self.min_frac, self.kill = pids, busy, hold_s, engine_min_frac, kill
        self.since: float | None = None
        self.prev: tuple[float, int] | None = None
        self.fired = False

    def feed(self, t: float, busy: int | None, engine_ns: int) -> str | None:
        prev, self.prev = self.prev, (t, engine_ns)
        if not self.pids or self.fired or busy is None or prev is None:
            return None
        dt_s = t - prev[0]
        model_busy = dt_s > 0 and (engine_ns - prev[1]) / 1e9 >= self.min_frac * dt_s
        if busy >= self.busy and not model_busy:
            self.since = self.since if self.since is not None else prev[0]
            if t - self.since >= self.hold_s:
                for p in self.pids:
                    try:
                        self.kill(p, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                self.fired = True
                return f"guard: GPU at {busy}% for {t - self.since:.0f} s with no model work; SIGTERM {self.pids}"
        else:
            self.since = None
        return None


def cmd_sample(args) -> int:
    guard = SpinGuard(args.guard_pid or [], busy=args.guard_busy, hold_s=args.guard_s)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.time() + args.duration
    last_drop = 0.0
    with out.open("a") as f:
        f.write(json.dumps({"event": "start", "t": time.time(), "label": args.label,
                            "interval": args.interval, "duration": args.duration,
                            "pids": args.pid or [], "guard_pids": args.guard_pid or []}) + "\n")
        while time.time() < t_end:
            row = host_sample(args.pid or [])
            ev = guard.feed(row["t"], row["busy"], row["fleet_engine_ns"])
            if ev:
                row["event"] = ev
                print(ev, flush=True)
            if args.drop_cache and (row["memfree_gb"] or 99) < args.drop_below_gb and time.time() - last_drop > 10:
                n = drop_cache(Path(args.drop_cache))
                row["event"] = (row.get("event", "") + f" drop-cache {n} files").strip()
                last_drop = time.time()
            f.write(json.dumps(row) + "\n")
            f.flush()
            time.sleep(max(0.0, args.interval - (time.time() - row["t"])))
        f.write(json.dumps({"event": "end", "t": time.time()}) + "\n")
    return 0


def summarize_samples(rows: list[dict], t_from: float | None = None, t_to: float | None = None) -> dict:
    rs = [r for r in rows if "busy" in r and (t_from is None or r["t"] >= t_from)
          and (t_to is None or r["t"] <= t_to)]
    if not rs:
        return {"n": 0}
    def col(k):
        return [r[k] for r in rs if r.get(k) is not None]
    busy = col("busy")
    eng = [b["fleet_engine_ns"] - a["fleet_engine_ns"] for a, b in zip(rs, rs[1:])]
    span = rs[-1]["t"] - rs[0]["t"]
    return {"n": len(rs), "minutes": round(span / 60, 1),
            "busy_mean": rnd(sum(busy) / len(busy), 2), "busy_p50": percentile(busy, 50),
            "busy_p95": percentile(busy, 95), "busy_max": max(busy),
            "busy_ge_90_samples": sum(b >= 90 for b in busy),
            "sclk_p50": percentile(col("sclk_mhz"), 50), "sclk_max": max(col("sclk_mhz")),
            "sclk_at_max_samples": sum(s >= 2800 for s in col("sclk_mhz")),
            "power_mean_w": rnd(sum(col("power_w")) / len(col("power_w")), 2),
            "power_p50_w": rnd(percentile(col("power_w"), 50), 2),
            "power_p95_w": rnd(percentile(col("power_w"), 95), 2),
            "gtt_gb_p50": percentile(col("gtt_gb"), 50), "memavail_gb_min": min(col("memavail_gb")),
            "memfree_gb_min": min(col("memfree_gb")),
            "fleet_engine_s": round(sum(e for e in eng if e > 0) / 1e9, 1)}


def cmd_idle(args) -> int:
    """One summary per sampler file; --skip-min drops the first minutes
    (the load itself), so the figures are the idle part."""
    for f in args.files:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        data = [r for r in rows if "busy" in r]
        t0 = data[0]["t"] + args.skip_min * 60 if data else None
        events = [r for r in rows if r.get("event") and "busy" in r]
        s = summarize_samples(data, t_from=t0)
        s["events"] = [r["event"] for r in events]
        print(f"{f}: {json.dumps(s)}")
    return 0


# ── relay ───────────────────────────────────────────────────────────────────

async def _pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        w.close()


async def relay_main(host: str, port: int, unix_path: str) -> None:
    """Loopback TCP in front of a Unix socket (a service with no network)."""
    check_host(host)

    async def handle(cr, cw):
        try:
            ur, uw = await asyncio.open_unix_connection(unix_path)
        except OSError:
            cw.close()          # the client sees a closed connection, as if refused
            return
        await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))

    server = await asyncio.start_server(handle, host, port)
    print(f"tts_bench: relay {host}:{port} -> unix:{unix_path}", flush=True)
    async with server:
        await server.serve_forever()


async def bridge_main(unix_path: str, host: str, port: int) -> None:
    """The reverse, run inside a no-network sandbox beside a service that
    can only listen on TCP: a Unix socket in front of the sandbox's own
    loopback port."""
    check_host(host)

    async def handle(ur, uw):
        try:
            tr, tw = await asyncio.open_connection(host, port)
        except OSError:
            uw.close()
            return
        await asyncio.gather(_pipe(ur, tw), _pipe(tr, uw))

    server = await asyncio.start_unix_server(handle, unix_path)
    print(f"tts_bench: bridge unix:{unix_path} -> {host}:{port}", flush=True)
    async with server:
        await server.serve_forever()


def cmd_relay(args) -> int:
    if args.listen_unix:
        asyncio.run(bridge_main(args.listen_unix, args.host, args.to_port))
    else:
        asyncio.run(relay_main(args.host, args.port, args.unix))
    return 0


# ── tables and the blind set ────────────────────────────────────────────────

def table_rows(results: list[dict], category: str | None = None) -> list[str]:
    lines = ["| Engine | Mode | Requests | Failures | First audio p50 / p95 (s) | Total p50 / p95 (s) "
             "| Audio p50 (s) | RTF p50 / p95 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        s = r["summary"]["by_category"].get(category) if category else r["summary"]["overall"]
        if not s:
            continue
        fails = sum(r["summary"]["failures"].values()) if not category else s["requests"] - s["ok"]
        lines.append(f"| {r['label']} | {r['mode']}/{r['chunking']} | {s['requests']} | {fails} | "
                     f"{s['ttfa_s']['p50']} / {s['ttfa_s']['p95']} | {s['total_s']['p50']} / "
                     f"{s['total_s']['p95']} | {s['audio_s']['p50']} | {s['rtf']['p50']} / {s['rtf']['p95']} |")
    return lines


def cmd_table(args) -> int:
    results = [json.loads(Path(f).read_text()) for f in args.results]
    cats = sorted({c for r in results for c in r["summary"]["by_category"]})
    print("All sentences\n")
    print("\n".join(table_rows(results)))
    for c in cats:
        print(f"\n{c}\n")
        print("\n".join(table_rows(results, c)))
    return 0


SHEET = """# Kronk voice listening test: rating sheet

Listener: ____________________   Date: ____________   Speaker or headphones: ____________

Each folder entry `<sentence>-<letter>.wav` is one sentence read by one voice. The
letters are shuffled per sentence, so "B" is not the same voice from one sentence
to the next. Listen to every letter of a sentence before rating it. The sentence
text is in `sentences.txt`.

For every file: naturalness 1 to 5 (1 robotic or hard to follow, 3 fine, 5 like a
person), the number of mispronounced words, and the number of garbled or extra
sounds (clicks, mumbles, repeated or swallowed syllables, noise before or after the
speech). Write the word or the moment if you can.

| File | Natural 1-5 | Mispronounced | Garbled / extra | Notes |
| --- | --- | --- | --- | --- |
{rows}

At the end: which voice would you want Kronk to have? Name the file you liked best
for three different sentences: ____________  ____________  ____________
"""


def cmd_blind(args) -> int:
    import numpy as np
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = [json.loads(Path(f).read_text()) for f in args.results]
    doc = load_sentences(args.sentences)
    rng = random.Random(args.seed)
    letters = "ABCDEFGH"
    key: dict = {"seed": args.seed, "files": {}}
    sheet_rows = []
    for s in doc["sentences"]:
        entries = []
        for res in results:
            row = next((r for r in res["rows"] if r["id"] == s["id"] and r["ok"] and r.get("wav")
                        and r["rep"] == args.rep), None)
            if row:
                entries.append((res["label"], row["wav"]))
        rng.shuffle(entries)
        for letter, (label, src) in zip(letters, entries):
            name = f"{s['id']}-{letter}.wav"
            x, rate = read_wav_float(Path(src))
            peak_rms = float(np.sqrt(np.mean(np.square(x)))) or 1.0
            y = x * min(10 ** (-20 / 20) / peak_rms, 0.98 / (float(np.max(np.abs(x))) or 1.0))
            write_pcm_wav(out / name, spb.to_pcm(y), rate)
            key["files"][name] = {"engine": label, "source": src}
            sheet_rows.append(f"| {name} |  |  |  |  |")
    (out / "sentences.txt").write_text("".join(f"{s['id']}: {s['text']}\n" for s in doc["sentences"]))
    (out / "rating_sheet.md").write_text(SHEET.format(rows="\n".join(sheet_rows)))
    Path(args.key).parent.mkdir(parents=True, exist_ok=True)
    Path(args.key).write_text(json.dumps(key, indent=2) + "\n")
    print(f"tts_bench: blind set of {len(key['files'])} files in {out}; key in {args.key}")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--engine", required=True)
    p.add_argument("--label")
    p.add_argument("--voice")
    p.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    p.add_argument("--category", action="append")
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--wav-dir")
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=["auto", "stream", "plain"], default="auto")
    p.add_argument("--chunking", choices=["whole", "sentence"], default="whole")
    p.add_argument("--chunk-gap-s", type=float, default=0.0)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--service-pid", type=int, action="append")
    p.add_argument("--rest-every-s", type=float, default=0.0)
    p.add_argument("--rest-s", type=float, default=0.0)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("cold")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--engine", required=True)
    p.add_argument("--voice")
    p.add_argument("--service-pid", type=int)
    p.add_argument("--started-at", type=float)
    p.add_argument("--sentence", default="c01")
    p.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    p.add_argument("--wait-s", type=float, default=600)
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_cold)

    p = sub.add_parser("check")
    p.add_argument("results", nargs="+")
    p.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    p.add_argument("--stt-host", default="127.0.0.1")
    p.add_argument("--stt-port", type=int, default=DEV_STT_PORT)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("llm")
    p.add_argument("--label", required=True)
    p.add_argument("--url", default=LLM_URL)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--n-predict", type=int, default=128)
    p.add_argument("--tts-host", default="127.0.0.1")
    p.add_argument("--tts-port", type=int)
    p.add_argument("--tts-voice")
    p.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    p.add_argument("--dry-check", action="store_true", help="only report whether HA is quiet")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_llm)

    p = sub.add_parser("sample")
    p.add_argument("--label", default="")
    p.add_argument("--interval", type=float, default=10)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pid", type=int, action="append")
    p.add_argument("--guard-pid", type=int, action="append")
    p.add_argument("--guard-busy", type=int, default=90)
    p.add_argument("--guard-s", type=float, default=180)
    p.add_argument("--drop-cache")
    p.add_argument("--drop-below-gb", type=float, default=3.5)
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("idle")
    p.add_argument("files", nargs="+")
    p.add_argument("--skip-min", type=float, default=0)
    p.set_defaults(fn=cmd_idle)

    p = sub.add_parser("relay")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, help="loopback TCP port to listen on (with --unix)")
    p.add_argument("--unix", help="the service's Unix socket")
    p.add_argument("--listen-unix", help="bridge mode: listen on this Unix socket ...")
    p.add_argument("--to-port", type=int, help="... and forward to this loopback port")
    p.set_defaults(fn=cmd_relay)

    p = sub.add_parser("table")
    p.add_argument("results", nargs="+")
    p.set_defaults(fn=cmd_table)

    p = sub.add_parser("blind")
    p.add_argument("results", nargs="+")
    p.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    p.add_argument("--rep", type=int, default=1)
    p.add_argument("--seed", type=int, default=20)
    p.add_argument("--out", required=True)
    p.add_argument("--key", required=True)
    p.set_defaults(fn=cmd_blind)

    args = ap.parse_args(argv)
    if args.cmd == "llm" and not args.dry_check and not args.out:
        ap.error("llm needs --out")
    if args.cmd == "relay" and not ((args.port and args.unix) or (args.listen_unix and args.to_port)):
        ap.error("relay needs --port and --unix, or --listen-unix and --to-port")
    try:
        return args.fn(args)
    except BenchError as e:
        print(f"tts_bench: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
