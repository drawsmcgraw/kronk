#!/usr/bin/env python3
"""Spoken-answer timing on the dev path (sprint M8).

Two modes, both dev only and silent:

  shim (default): POST /dev/voice/api/chat with stream=true, one question at a
      time, and time each reply: send, first content chunk, `done` record.
      Records reply length (characters, sentences), chunk count, and from the
      dev orchestrator's event log the request id, trace id and the
      coordinator's generation time (sum of its `agent_round` durations).

          python3 scripts/voice_stream_bench.py --label m8-pre [--runs 5] [--questions N]

  ha: HA's `assist_pipeline/run` on the `kronk-dev` pipeline with
      start_stage `intent` and end_stage `tts`, no device id, so the audio is
      rendered into HA's TTS cache and sent to no satellite or speaker (nothing
      fetches the URL). Every sentence is first checked with HA's read-only
      sentence-match debug and is not run if HA would handle it locally.
      Records the arrival time of every pipeline event, the time of
      `intent-progress` with `tts_start_streaming`, of the first
      `intent-progress` carrying assistant content, and of `intent-end`.

          python3 scripts/voice_stream_bench.py --mode ha --label m8-ha-pre [--runs 1]

Guards: the base URL must end in /dev (the production chat routes are
refused), the HA pipeline must resolve to a Kronk URL under /dev with music
dry-run on (scripts/voice_replay.py's resolve_target / require_dry_run).
Output: docs/bench/voice_stream_<UTC time>_<label>.json (or --out).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Questions that drew long spoken answers before M8: general knowledge the
# coordinator answers itself. The first five are the criterion-17 set.
QUESTIONS = [
    "explain how a heat pump works",
    "how does a refrigerator keep food cold",
    "why is the sky blue",
    "how do vaccines work",
    "what causes the seasons on earth",
    "how does a solar panel make electricity",
    "explain how a car engine works",
    "how do airplanes stay in the air",
    "what is the difference between a virus and a bacterium",
    "how does a microwave oven heat food",
]

# A fixed, simple sentence count so before and after are counted the same
# way: terminal punctuation followed by whitespace or the end, plus line
# breaks between non-empty lines that do not end in punctuation.
_SENT = re.compile(r"[.!?]+(?=\s|$)")


def count_sentences(text: str) -> int:
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        k = len(_SENT.findall(line))
        n += k if k and line[-1] in ".!?" else k + 1
    return n


def orchestrator_events(container: str, since: float) -> list[dict]:
    out = subprocess.run(["docker", "logs", "--since", str(int(since) - 1), container],
                         capture_output=True, text=True, timeout=30)
    evs = []
    for line in (out.stdout + out.stderr).splitlines():
        i = line.find('{"ts"')
        if i < 0:
            continue
        try:
            evs.append(json.loads(line[i:]))
        except json.JSONDecodeError:
            continue
    return evs


def log_evidence(container: str, since: float, question: str) -> dict:
    """The request's rid, trace id and round durations from kronk.events."""
    for _ in range(10):
        evs = orchestrator_events(container, since)
        req = [e for e in evs if e.get("event") == "request" and e.get("ts", 0) >= since - 1
               and (e.get("text_preview") or "") == question[:80]]
        if req:
            rid = req[-1]["rid"]
            mine = [e for e in evs if e.get("rid") == rid]
            if any(e.get("event") == "request_complete" for e in mine):
                rounds = [e for e in mine if e.get("event") == "agent_round"]
                coord = [e for e in rounds if e.get("agent") == "coordinator"]
                done = next(e for e in mine if e.get("event") == "request_complete")
                stream = next((e for e in mine if e.get("event") == "voice_stream"), None)
                return {
                    "rid": rid,
                    "trace_id": next((e.get("trace_id") for e in mine if e.get("trace_id")), None),
                    "route": done.get("route"),
                    "generation_s": round(sum(e.get("duration_s", 0) for e in coord), 2),
                    "rounds": [(e.get("agent"), e.get("phase"), e.get("duration_s")) for e in rounds],
                    "pipeline_s": done.get("duration_s"),
                    "voice_stream": stream,
                }
        time.sleep(0.5)
    return {}


def shim_once(base: str, question: str, timeout: float) -> dict:
    body = json.dumps({"model": "kronk", "stream": True,
                       "messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(base + "/voice/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t_send = time.monotonic()
    wall = time.time()
    chunks, t_first, t_done, done = [], None, None, None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line:
                continue
            obj = json.loads(line)   # every line must be one JSON object
            now = time.monotonic() - t_send
            if obj.get("done"):
                t_done, done = now, obj
                break
            content = (obj.get("message") or {}).get("content") or ""
            if content:
                if t_first is None:
                    t_first = now
                chunks.append({"t": round(now, 3), "chars": len(content), "text": content})
    reply = "".join(c["text"] for c in chunks)
    return {"wall_start": wall, "first_chunk_s": round(t_first, 3) if t_first is not None else None,
            "done_s": round(t_done, 3) if t_done is not None else None,
            "chunks": len(chunks), "chunk_times": [c["t"] for c in chunks],
            "chars": len(reply), "sentences": count_sentences(reply), "reply": reply,
            "done_record": {k: v for k, v in (done or {}).items() if k != "message"}}


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for q in dict.fromkeys(r["question"] for r in rows):
        rs = [r for r in rows if r["question"] == q and "error" not in r]
        if not rs:
            continue
        def med(key):
            vals = [r[key] for r in rs if r.get(key) is not None]
            return round(statistics.median(vals), 2) if vals else None
        out.append({"question": q, "n": len(rs), "chars_median": med("chars"),
                    "sentences_median": med("sentences"), "generation_s_median": med("generation_s"),
                    "first_chunk_s_median": med("first_chunk_s"), "done_s_median": med("done_s"),
                    "chunks_median": med("chunks")})
    return out


def run_shim(args) -> dict:
    base = args.url.rstrip("/")
    if not base.endswith("/dev"):
        sys.exit(f"voice_stream_bench: refusing {base}: only the dev path (…/dev) is allowed")
    qs = QUESTIONS[: args.questions]
    rows = []
    for run in range(args.runs):
        for q in qs:
            try:
                r = shim_once(base, q, args.timeout)
            except Exception as e:
                rows.append({"question": q, "run": run + 1, "error": f"{type(e).__name__}: {e}"})
                print(f"  run {run + 1} {q!r}: ERROR {e}", flush=True)
                continue
            ev = log_evidence(args.container, r["wall_start"], q)
            r.update({"question": q, "run": run + 1, **ev})
            rows.append(r)
            print(f"  run {run + 1} {q[:40]!r}: first {r['first_chunk_s']}s done {r['done_s']}s "
                  f"chunks {r['chunks']} chars {r['chars']} sent {r['sentences']} "
                  f"gen {r.get('generation_s')}s trace {r.get('trace_id')}", flush=True)
    return {"mode": "shim", "url": base + "/voice/api/chat", "rows": rows, "summary": summarize(rows)}


async def run_ha(args) -> dict:
    import voice_replay as vr
    token = vr.ha_token()
    rows = []
    async with vr.HA(token) as ha:
        ns = argparse.Namespace(pipeline=args.pipeline, allow_production=False,
                                orchestrator_container=None, tool_service_container=None)
        target = await vr.resolve_target(ha, token, ns)
        if target["production"] or not target["kronk_url"].endswith("/dev/voice"):
            raise SystemExit(f"voice_stream_bench: refusing pipeline {target}")
        vr.require_dry_run(target)
        pl = await ha.call("assist_pipeline/pipeline/list")
        pdef = next(p for p in pl["pipelines"] if p["id"] == target["pipeline_id"])
        for run in range(args.runs):
            for q in QUESTIONS[: args.questions]:
                res = await ha.call("conversation/agent/homeassistant/debug", sentences=[q],
                                    language="en")
                dbg = vr.classify_debug(res["results"][0], {})
                if dbg["local"] or dbg.get("fuzzy"):
                    rows.append({"question": q, "run": run + 1, "skipped": dbg["summary"]})
                    continue
                ha.n += 1
                mid = ha.n
                t0 = time.monotonic()
                wall = time.time()
                await ha.ws.send(json.dumps({
                    "id": mid, "type": "assist_pipeline/run",
                    "start_stage": "intent", "end_stage": "tts",
                    "input": {"text": q}, "pipeline": target["pipeline_id"],
                    "timeout": int(args.timeout)}))
                events = []
                while True:
                    m = json.loads(await asyncio.wait_for(ha.ws.recv(), args.timeout + 15))
                    if m.get("id") != mid:
                        continue
                    if m.get("type") == "result" and not m.get("success"):
                        events.append({"t": round(time.monotonic() - t0, 3), "type": "result-error",
                                       "data": m.get("error")})
                        break
                    if m.get("type") == "event":
                        e = m["event"]
                        events.append({"t": round(time.monotonic() - t0, 3), "type": e["type"],
                                       "timestamp": e.get("timestamp"), "data": e.get("data")})
                        if e["type"] in ("run-end", "error"):
                            break

                def first(pred):
                    return next((e["t"] for e in events if pred(e)), None)
                prog = [e for e in events if e["type"] == "intent-progress"]
                content_t = [e["t"] for e in prog
                             if ((e.get("data") or {}).get("chat_log_delta") or {}).get("content")]
                start = next((e for e in events if e["type"] == "run-start"), {})
                ev = log_evidence(args.container, wall, q)
                row = {
                    "question": q, "run": run + 1,
                    "run_start_tts_output": ((start.get("data") or {}).get("tts_output")),
                    "tts_start_streaming_s": first(lambda e: e["type"] == "intent-progress" and
                                                   (e.get("data") or {}).get("tts_start_streaming")),
                    "first_content_delta_s": content_t[0] if content_t else None,
                    "content_deltas": len(content_t),
                    "content_delta_times": content_t,
                    "intent_end_s": first(lambda e: e["type"] == "intent-end"),
                    "tts_start_s": first(lambda e: e["type"] == "tts-start"),
                    "tts_end_s": first(lambda e: e["type"] == "tts-end"),
                    "run_end_s": first(lambda e: e["type"] == "run-end"),
                    "error": next((e.get("data") for e in events if e["type"] in ("error", "result-error")), None),
                    "event_types": [(e["t"], e["type"]) for e in events],
                    **ev,
                }
                rows.append(row)
                print(f"  run {run + 1} {q[:40]!r}: tts_start_streaming {row['tts_start_streaming_s']} "
                      f"first delta {row['first_content_delta_s']} deltas {row['content_deltas']} "
                      f"intent-end {row['intent_end_s']} tts-start {row['tts_start_s']} "
                      f"trace {row.get('trace_id')}", flush=True)
    return {"mode": "ha", "pipeline": target["pipeline"], "kronk_url": target["kronk_url"],
            "pipeline_tts": {k: pdef.get(k) for k in ("tts_engine", "tts_voice", "conversation_engine",
                                                      "prefer_local_intents")},
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["shim", "ha"], default="shim")
    ap.add_argument("--label", required=True)
    ap.add_argument("--url", default="http://localhost/dev")
    ap.add_argument("--pipeline", default="kronk-dev")
    ap.add_argument("--container", default="kronk-orchestrator-dev-1")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--questions", type=int, default=len(QUESTIONS))
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--out")
    args = ap.parse_args()
    t = dt.datetime.now(dt.timezone.utc)
    result = run_shim(args) if args.mode == "shim" else asyncio.run(run_ha(args))
    result.update({"label": args.label, "started_utc": t.isoformat(timespec="seconds"),
                   "git": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                         capture_output=True, text=True).stdout.strip()})
    out = Path(args.out) if args.out else (
        ROOT / "docs" / "bench" / f"voice_stream_{t.strftime('%Y-%m-%d_%H%M%S')}_{args.label}.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"voice_stream_bench: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
