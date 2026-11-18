#!/usr/bin/env python3
"""Speech-recognition bench for the Whisper vocabulary prompt (sprint M13).

Sends audio files to a Wyoming STT port on this host and records the text
and the time; scores word error rate and command-form errors (the
transcript routes to a different rule or call than the reference text, by
orchestrator/routing.decide). Makes no sound: audio goes from files to a
socket and nowhere else.

Run with the test venv (numpy, and the orchestrator's imports for routing):

  # synthetic set: corpus sentences and household names rendered by Piper
  # (Wyoming port 10200, straight to files), plus degraded and non-speech audio
  tests/.venv/bin/python scripts/stt_prompt_bench.py render --out DIR \\
      [--names FILE] [--voices en_US-lessac-medium,...]

  # one pass over a sheet (the render's labels.csv, or an M12 labelling
  # sheet: scripts/voice_label_sheet.py's labels.csv) against one port
  tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet DIR/labels.csv \\
      --port 10300 --label prod-before --out RESULT.json [--prompt-file FILE]

  # tables from two or more results; --same A B lists rows whose text differs
  tests/.venv/bin/python scripts/stt_prompt_bench.py compare A.json B.json ...

Sheet format: labels.csv as written by scripts/voice_label_sheet.py
(docs/features/voice-capture.md, "File format, for the M13 bench"): UTF-8,
RFC 4180, header row; `sample_id`, `audio_file` (relative to the sheet's
directory, or absolute) and `correct_text` are read. A row is scored when
`correct_text` is non-empty. The render adds a `variant` column (`clean`,
`noise10`, `room`, `hazard-<kind>`); hazard rows have no reference text
and are counted, not scored: did the port return any text, and did it
return a name from the prompt (--prompt-file: the EnvironmentFile that
scripts/build_stt_prompt.py writes).

Only loopback hosts are accepted. Piper voices must already be installed
in the Piper container (the render checks Piper's describe answer and
refuses a voice that would be downloaded).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import re
import sys
import time
import unicodedata
import wave
from pathlib import Path
from typing import Awaitable, Callable, Iterable

REPO = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RATE = 16000
CHUNK_BYTES = 2048 * 2
SHEET_COLUMNS = [  # voice_label_sheet.py's columns, then the bench's own
    "sample_id", "audio_file", "correct_text", "heard", "time", "satellite", "device_id",
    "pipeline", "tier", "rule", "wrong_action", "duration_s", "sample_rate", "run_id",
    "trace_id", "source_audio", "notes", "variant", "origin",
]
DEFAULT_VOICES = ("en_US-lessac-medium", "en_US-hfc_male-medium", "en_US-amy-medium")
HAZARDS = ("silence", "hiss", "white", "pink", "hum", "water", "clatter", "beeps", "music", "reversed")


class BenchError(Exception):
    pass


def check_host(host: str) -> str:
    if host not in LOOPBACK:
        raise BenchError(f"refusing {host!r}: this bench talks to loopback only")
    return host


# ── Wyoming framing (stdlib; the protocol of wyoming 1.x) ───────────────────

async def write_event(writer, etype: str, data: dict | None = None, payload: bytes | None = None) -> None:
    header: dict = {"type": etype}
    data_bytes = json.dumps(data).encode() if data else b""
    if data_bytes:
        header["data_length"] = len(data_bytes)
    if payload:
        header["payload_length"] = len(payload)
    writer.write(json.dumps(header).encode() + b"\n" + data_bytes + (payload or b""))
    await writer.drain()


async def read_event(reader) -> tuple[str, dict, bytes] | None:
    line = await reader.readline()
    if not line:
        return None
    header = json.loads(line)
    data = dict(header.get("data") or {})
    if header.get("data_length"):
        data.update(json.loads(await reader.readexactly(header["data_length"])))
    payload = await reader.readexactly(header["payload_length"]) if header.get("payload_length") else b""
    return header["type"], data, payload


async def _until(reader, wanted: str, timeout: float, collect: Callable | None = None) -> dict:
    while True:
        ev = await asyncio.wait_for(read_event(reader), timeout)
        if ev is None:
            raise BenchError(f"connection closed before {wanted}")
        etype, data, payload = ev
        if etype == "error":
            raise BenchError(f"server error: {data.get('text') or data}")
        if collect:
            collect(etype, data, payload)
        if etype == wanted:
            return data


async def transcribe(host: str, port: int, pcm: bytes, language: str = "en",
                     timeout: float = 60) -> tuple[str, float]:
    """(text, seconds from audio-stop to transcript) for 16 kHz mono 16-bit PCM."""
    reader, writer = await asyncio.open_connection(check_host(host), port)
    try:
        fmt = {"rate": RATE, "width": 2, "channels": 1}
        await write_event(writer, "transcribe", {"language": language})
        await write_event(writer, "audio-start", fmt)
        for i in range(0, len(pcm), CHUNK_BYTES):
            await write_event(writer, "audio-chunk", fmt, pcm[i:i + CHUNK_BYTES])
        await write_event(writer, "audio-stop")
        t0 = time.perf_counter()
        data = await _until(reader, "transcript", timeout)
        return (data.get("text") or ""), time.perf_counter() - t0
    finally:
        writer.close()


async def piper_voices(host: str, port: int) -> dict[str, bool]:
    reader, writer = await asyncio.open_connection(check_host(host), port)
    try:
        await write_event(writer, "describe")
        info = await _until(reader, "info", 15)
    finally:
        writer.close()
    out = {}
    for prog in info.get("tts") or []:
        for v in prog.get("voices") or []:
            out[v.get("name")] = bool(v.get("installed"))
    return out


async def synthesize(host: str, port: int, text: str, voice: str) -> tuple[bytes, int]:
    """(16-bit mono PCM, rate) from Piper."""
    reader, writer = await asyncio.open_connection(check_host(host), port)
    audio = bytearray()
    fmt = {}

    def collect(etype, data, payload):
        if etype in ("audio-start", "audio-chunk"):
            fmt.update({k: data[k] for k in ("rate", "width", "channels") if k in data})
        if etype == "audio-chunk":
            audio.extend(payload)
    try:
        await write_event(writer, "synthesize", {"text": text, "voice": {"name": voice}})
        await _until(reader, "audio-stop", 60, collect)
    finally:
        writer.close()
    if fmt.get("width") != 2 or fmt.get("channels") != 1:
        raise BenchError(f"unexpected Piper audio format {fmt}")
    return bytes(audio), int(fmt["rate"])


# ── audio (numpy) ───────────────────────────────────────────────────────────

def _np():
    import numpy
    return numpy


def to_float(pcm: bytes):
    np = _np()
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0


def to_pcm(x) -> bytes:
    np = _np()
    return (np.clip(x, -1.0, 32767 / 32768) * 32768.0).round().astype("<i2").tobytes()


def resample(x, sr_in: int, sr_out: int):
    """Band-limited resample of a whole signal (FFT truncation / zero-pad)."""
    np = _np()
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    spec = np.fft.rfft(x)
    keep = n_out // 2 + 1
    if keep <= len(spec):
        spec = spec[:keep]
    else:
        spec = np.concatenate([spec, np.zeros(keep - len(spec), dtype=complex)])
    return np.fft.irfft(spec, n_out) * (n_out / len(x))


def rms(x) -> float:
    np = _np()
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def pink(n: int, rng):
    np = _np()
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1.0
    y = np.fft.irfft(spec / np.sqrt(f), n)
    return y / (rms(y) or 1.0)


def add_noise(x, snr_db: float, noise):
    return x + noise[:len(x)] * (rms(x) / (10 ** (snr_db / 20)))


def degrade(x, variant: str, rng):
    """`noise10`: white noise at 10 dB SNR. `room`: a synthetic reverberant
    room (exponential-decay impulse, about 0.5 s), pink noise at 15 dB SNR,
    12 dB quieter, as a puck across a kitchen might hear it."""
    np = _np()
    if variant == "noise10":
        y = add_noise(x, 10, rng.standard_normal(len(x)))
    elif variant == "room":
        t = np.arange(int(0.5 * RATE)) / RATE
        ir = rng.standard_normal(len(t)) * np.exp(-6.9 * t / 0.5) * 0.3
        ir[0] = 1.0
        n = len(x) + len(ir) - 1
        wet = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(ir, n), n)[:len(x) + RATE // 4]
        wet *= rms(x) / (rms(wet) or 1.0)
        y = add_noise(wet, 15, pink(len(wet), rng)) * 10 ** (-12 / 20)
    else:
        raise BenchError(f"unknown variant {variant}")
    peak = float(np.max(np.abs(y))) or 1.0
    return y / peak * 0.9 if peak > 0.9 else y


def hazard(kind: str, seconds: float, rng, speech=None):
    """Non-speech audio, generated. `reversed` is a Piper sentence played
    backwards: speech-like, but no words."""
    np = _np()
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    if kind == "silence":
        return np.zeros(n)
    if kind == "hiss":
        return rng.standard_normal(n) * 10 ** (-55 / 20)
    if kind == "white":
        return rng.standard_normal(n) * 10 ** (-20 / 20)
    if kind == "pink":
        return pink(n, rng) * 10 ** (-20 / 20)
    if kind == "hum":
        f0 = rng.choice([50.0, 60.0])
        y = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3, 5))
        return y * 0.1 + rng.standard_normal(n) * 0.003
    if kind == "water":
        y = pink(n, rng)
        env = 0.6 + 0.4 * np.sin(2 * np.pi * rng.uniform(0.5, 2.0) * t)
        y = np.diff(y, prepend=0.0) * env                 # brighter, like a running tap
        return y / (rms(y) or 1.0) * 0.05
    if kind == "clatter":
        y = np.zeros(n)
        for _ in range(int(seconds * rng.uniform(2, 5))):
            at = int(rng.uniform(0, max(1, n - RATE // 5)))
            m = min(RATE // 5, n - at)
            tt = np.arange(m) / RATE
            y[at:at + m] += rng.standard_normal(m) * np.exp(-tt * rng.uniform(30, 80)) * rng.uniform(0.1, 0.5)
        y *= 0.5 / (float(np.max(np.abs(y))) or 1.0)       # loud knocks, never clipped
        return y + rng.standard_normal(n) * 0.002
    if kind == "beeps":
        y = np.zeros(n)
        f = rng.choice([880.0, 1000.0, 2000.0])
        for k in range(3):
            a, b = int((0.2 + 0.5 * k) * RATE), int((0.45 + 0.5 * k) * RATE)
            y[a:min(b, n)] = 0.2 * np.sin(2 * np.pi * f * t[a:min(b, n)])
        return y
    if kind == "music":
        y = np.zeros(n)
        root = rng.choice([220.0, 246.9, 261.6])
        beat = int(0.5 * RATE)
        for i, start in enumerate(range(0, n, beat)):
            m = min(beat, n - start)
            tt = np.arange(m) / RATE
            chord = (1.0, 1.26, 1.5) if i % 2 == 0 else (1.122, 1.335, 1.682)
            y[start:start + m] = sum(np.sin(2 * np.pi * root * c * tt) for c in chord) * np.exp(-3 * tt) * 0.1
        return y
    if kind == "reversed":
        if speech is None:
            raise BenchError("reversed needs a speech sample")
        return speech[::-1][:n] if len(speech) >= n else np.concatenate([speech[::-1], np.zeros(n - len(speech))])
    raise BenchError(f"unknown hazard {kind}")


def write_wav(path: Path, x) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(to_pcm(x))
    return len(x) / RATE


def read_wav_16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise BenchError(f"{path}: {8 * width}-bit audio; 16-bit only")
    if ch == 1 and rate == RATE:
        return frames
    np = _np()
    x = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return to_pcm(resample(x, rate, RATE))


# ── scoring ─────────────────────────────────────────────────────────────────

def normalize(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = t.replace("’", "'").replace("‘", "'").replace("&", " and ")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return [w.strip("'") for w in t.split() if w.strip("'")]


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


_ROUTING = None


def route_of(text: str) -> tuple:
    """(rule, direct call) Kronk's routing table gives the text. HA's local
    tier (intents, the music blueprint) is not modelled: it cannot be asked
    without running a pipeline."""
    global _ROUTING
    if _ROUTING is None:
        os.environ.setdefault("KRONK_CONFIG_DIR", str(REPO / "config"))
        if str(REPO / "orchestrator") not in sys.path:
            sys.path.append(str(REPO / "orchestrator"))
        import routing
        _ROUTING = routing
    d = _ROUTING.decide(text or "")
    return (d.rule, json.dumps(d.direct, sort_keys=True) if d.direct else None)


def prompt_names(path: Path | None) -> list[str]:
    if not path:
        return []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("WHISPER_INITIAL_PROMPT="):
            value = line.split("=", 1)[1].strip().strip('"').rstrip(".")
            return [n.strip() for n in value.split(",") if n.strip()]
    raise BenchError(f"{path}: no WHISPER_INITIAL_PROMPT line")


def names_in(text: str, names: Iterable[str]) -> list[str]:
    words = " " + " ".join(normalize(text)) + " "
    return sorted({n for n in names if normalize(n) and (" " + " ".join(normalize(n)) + " ") in words})


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def score_row(row: dict, hyp: str, latency: float, names: list[str]) -> dict:
    ref = row.get("correct_text") or ""
    out = {"sample_id": row["sample_id"], "variant": row.get("variant") or "clip",
           "origin": row.get("origin") or "", "ref": ref, "hyp": hyp, "latency_s": round(latency, 4)}
    if ref.strip():
        r, h = normalize(ref), normalize(hyp)
        out.update(ref_words=len(r), word_errors=edit_distance(r, h))
        rr, hr = route_of(ref), route_of(hyp)
        out.update(ref_route=list(rr), hyp_route=list(hr), command_error=rr != hr)
        out["names_added"] = [n for n in names_in(hyp, names) if n not in names_in(ref, names)]
    else:
        out["returned_text"] = bool(normalize(hyp))
        out["names_hit"] = names_in(hyp, names)
    return out


def summarize(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["variant"], []).append(r)
    out = {}
    for variant, rs in sorted(by.items()):
        lat = [r["latency_s"] for r in rs]
        s = {"n": len(rs), "p50_s": percentile(lat, 50), "p95_s": percentile(lat, 95)}
        scored = [r for r in rs if "ref_words" in r]
        if scored:
            words = sum(r["ref_words"] for r in scored)
            s.update(wer=round(sum(r["word_errors"] for r in scored) / words, 4) if words else None,
                     command_errors=sum(r["command_error"] for r in scored),
                     rows_with_names_added=sum(bool(r["names_added"]) for r in scored))
        hz = [r for r in rs if "returned_text" in r]
        if hz:
            s.update(returned_text=sum(r["returned_text"] for r in hz),
                     returned_prompt_names=sum(bool(r["names_hit"]) for r in hz))
        out[variant] = s
    return out


# ── sheets and runs ─────────────────────────────────────────────────────────

def read_sheet(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and not {"sample_id", "audio_file", "correct_text"} <= set(rows[0]):
        raise BenchError(f"{path}: not a labelling sheet (needs sample_id, audio_file, correct_text)")
    base = Path(path).parent
    out = []
    for r in rows:
        variant = r.get("variant") or ""
        if not (r.get("correct_text") or "").strip() and not variant.startswith("hazard-"):
            continue                                    # not labelled yet
        audio = Path(r["audio_file"])
        r["_path"] = str(audio if audio.is_absolute() else base / audio)
        out.append(r)
    return out


Transcriber = Callable[[bytes], Awaitable[tuple[str, float]]]


async def run_sheet(rows: list[dict], stt: Transcriber, names: list[str],
                    progress: Callable[[int, int], None] | None = None) -> list[dict]:
    out = []
    for i, row in enumerate(rows):
        hyp, latency = await stt(read_wav_16k(Path(row["_path"])))
        out.append(score_row(row, hyp, latency, names))
        if progress:
            progress(i + 1, len(rows))
    return out


def corpus_sentences(path: Path) -> list[tuple[str, str]]:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    seen, out = set(), []
    for r in rows:
        text = " ".join((r.get("text") or "").split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append((f"corpus:{r['id']}", text))
    return out


def name_sentences(path: Path | None) -> list[tuple[str, str]]:
    """Carrier sentences for a names file: one `kind<TAB>name` per line,
    kind station, artist or room."""
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        kind, _, name = line.partition("\t")
        name = name.strip()
        text = {"station": f"Play {name}.", "artist": f"Play some {name}.",
                "room": f"Play music in the {name}."}.get(kind.strip())
        if not text or not name:
            raise BenchError(f"{path}: bad line {line!r} (kind<TAB>name, kind station|artist|room)")
        out.append((f"{kind.strip()}", text))
    return out


async def render(out_dir: Path, sentences: list[tuple[str, str]], voices: list[str],
                 piper: tuple[str, int], seed: int, hazards_per_kind: int) -> int:
    np = _np()
    installed = await piper_voices(*piper)
    missing = [v for v in voices if not installed.get(v)]
    if missing:
        raise BenchError(f"Piper voices not installed (would be downloaded): {missing}")
    if (out_dir / "labels.csv").exists():
        raise BenchError(f"{out_dir}/labels.csv exists; render into a new directory")
    rng = np.random.default_rng(seed)
    rows, speech_samples = [], []
    for k, (origin, text) in enumerate(sentences):
        voice = voices[k % len(voices)]
        pcm, rate = await synthesize(*piper, text, voice)
        x = resample(to_float(pcm), rate, RATE)
        x = np.concatenate([np.zeros(RATE // 5), x, np.zeros(RATE // 5)])
        if len(speech_samples) < hazards_per_kind:
            speech_samples.append(x)
        for variant in ("clean", "noise10", "room"):
            y = x if variant == "clean" else degrade(x, variant, rng)
            sid = f"m13-{k:04d}-{variant}"
            dur = write_wav(out_dir / "audio" / f"{sid}.wav", y)
            rows.append({"sample_id": sid, "audio_file": f"audio/{sid}.wav", "correct_text": text,
                         "duration_s": f"{dur:.2f}", "sample_rate": RATE, "variant": variant,
                         "origin": origin, "notes": f"piper {voice}"})
        print(f"render: {k + 1}/{len(sentences)}", file=sys.stderr, end="\r")
    for kind in HAZARDS:
        for j in range(hazards_per_kind):
            seconds = 2.0 + j % 3
            y = hazard(kind, seconds, rng, speech_samples[j % len(speech_samples)] if speech_samples else None)
            sid = f"m13-hazard-{kind}-{j}"
            dur = write_wav(out_dir / "audio" / f"{sid}.wav", y)
            rows.append({"sample_id": sid, "audio_file": f"audio/{sid}.wav", "correct_text": "",
                         "duration_s": f"{dur:.2f}", "sample_rate": RATE, "variant": f"hazard-{kind}",
                         "origin": "generated", "notes": "generated non-speech"})
    with open(out_dir / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(file=sys.stderr)
    return len(rows)


# ── compare ─────────────────────────────────────────────────────────────────

def _fmt(v, pct=False):
    if v is None:
        return "-"
    return f"{100 * v:.1f} %" if pct else (f"{v:.3f}" if isinstance(v, float) else str(v))


def compare_tables(results: list[dict]) -> str:
    variants = sorted({v for r in results for v in r["summary"]})
    speech = [v for v in variants if not v.startswith("hazard-")]
    hz = [v for v in variants if v.startswith("hazard-")]
    lines = ["| Variant | Run | n | WER | Command-form errors | Rows with prompt names added | p50 s | p95 s |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for v in speech:
        for r in results:
            s = r["summary"].get(v)
            if s:
                lines.append(f"| {v} | {r['label']} | {s['n']} | {_fmt(s.get('wer'), True)} | "
                             f"{_fmt(s.get('command_errors'))} | {_fmt(s.get('rows_with_names_added'))} | "
                             f"{_fmt(s['p50_s'])} | {_fmt(s['p95_s'])} |")
    if hz:
        lines += ["", "| Non-speech | Run | n | Returned text | Returned a prompt name | p50 s |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for v in hz:
            for r in results:
                s = r["summary"].get(v)
                if s:
                    lines.append(f"| {v.removeprefix('hazard-')} | {r['label']} | {s['n']} | "
                                 f"{s['returned_text']} | {s['returned_prompt_names']} | {_fmt(s['p50_s'])} |")
    return "\n".join(lines)


def rescore_names(result: dict, names: list[str]) -> dict:
    """The result with its prompt-name counts recomputed from the stored
    text against `names`, so runs made with and without --prompt-file
    compare on the same list."""
    rows = []
    for r in result["rows"]:
        r = dict(r)
        if "ref_words" in r:
            r["names_added"] = [n for n in names_in(r["hyp"], names) if n not in names_in(r["ref"], names)]
        elif "returned_text" in r:
            r["names_hit"] = names_in(r["hyp"], names)
        rows.append(r)
    return {**result, "rows": rows, "summary": summarize(rows)}


def differing_rows(a: dict, b: dict) -> list[tuple[str, str, str]]:
    hb = {r["sample_id"]: r["hyp"] for r in b["rows"]}
    return [(r["sample_id"], r["hyp"], hb.get(r["sample_id"], "<missing>"))
            for r in a["rows"] if hb.get(r["sample_id"]) != r["hyp"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--corpus", type=Path, default=REPO / "tests/voice_corpus.jsonl")
    r.add_argument("--names", type=Path, default=None)
    r.add_argument("--voices", default=",".join(DEFAULT_VOICES))
    r.add_argument("--piper-port", type=int, default=10200)
    r.add_argument("--seed", type=int, default=13)
    r.add_argument("--hazards", type=int, default=4, help="clips per non-speech kind")
    u = sub.add_parser("run")
    u.add_argument("--sheet", type=Path, required=True)
    u.add_argument("--port", type=int, required=True)
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--label", required=True)
    u.add_argument("--out", type=Path, required=True)
    u.add_argument("--prompt-file", type=Path, default=None)
    c = sub.add_parser("compare")
    c.add_argument("results", type=Path, nargs="+")
    c.add_argument("--same", nargs=2, type=Path, default=None)
    c.add_argument("--prompt-file", type=Path, default=None,
                   help="recount prompt names in every result against this prompt")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "render":
            sentences = corpus_sentences(a.corpus) + name_sentences(a.names)
            n = asyncio.run(render(a.out, sentences, a.voices.split(","), ("127.0.0.1", a.piper_port),
                                   a.seed, a.hazards))
            print(f"render: {n} files in {a.out}; sheet {a.out / 'labels.csv'}")
        elif a.cmd == "run":
            check_host(a.host)
            if a.out.exists():
                raise BenchError(f"{a.out} exists")
            rows = read_sheet(a.sheet)
            names = prompt_names(a.prompt_file)
            stt = lambda pcm: transcribe(a.host, a.port, pcm)  # noqa: E731
            t0 = time.time()
            scored = asyncio.run(run_sheet(rows, stt, names,
                                           lambda i, n: print(f"run: {i}/{n}", file=sys.stderr, end="\r")))
            result = {"label": a.label, "port": a.port, "sheet": str(a.sheet),
                      "prompt_file": str(a.prompt_file) if a.prompt_file else None,
                      "started": t0, "seconds": round(time.time() - t0, 1),
                      "summary": summarize(scored), "rows": scored}
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
            print(file=sys.stderr)
            print(compare_tables([result]))
        else:
            results = [json.loads(p.read_text()) for p in a.results]
            if a.prompt_file:
                names = prompt_names(a.prompt_file)
                results = [rescore_names(r, names) for r in results]
            print(compare_tables(results))
            if a.same:
                x, y = (json.loads(p.read_text()) for p in a.same)
                diff = differing_rows(x, y)
                print(f"\nrows with different text, {x['label']} vs {y['label']}: {len(diff)} of {len(x['rows'])}")
                for sid, h1, h2 in diff:
                    print(f"  {sid}")
    except BenchError as e:
        print(f"stt_prompt_bench: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
