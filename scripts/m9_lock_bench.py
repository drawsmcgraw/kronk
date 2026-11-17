#!/usr/bin/env python3
"""Lock bench on the dev path (sprint M9, criterion 2).

Measures what M9's hard constraint is about, on the dev orchestrator only:

  single   one request at a time: client latency per route on the voice
           mount (/voice/api/chat, buffered, the production path) and one
           display route (/api/chat), with llama.cpp's prompt-cache split
           per generation (prompt tokens, cached tokens) read from the dev
           orchestrator's `agent_round` events, which carry LiteLLM's
           usage.prompt_tokens_details.cached_tokens (llama.cpp's own count).
  two      a research query on the display route, then a voice weather
           query one second later; records the voice request's latency.
  load     four voice and four display requests started together, mixed
           routes; each request's lock wait (when the build reports it) and
           total time, and any slot/context error in the llama.cpp and
           LiteLLM logs during the run.

    python3 scripts/m9_lock_bench.py --label m9-base-a            # single + two
    python3 scripts/m9_lock_bench.py --label m9-load --mode load
    python3 scripts/m9_lock_bench.py --compare A.json B.json [C.json …]

Guards: the base URL must be http://localhost/dev or http://127.0.0.1/dev
(nothing else is accepted); the dev tool_service must report music dry-run
on; before each block the production orchestrator's log is read (read-only)
and the bench waits while production has a request in flight (at most ten
minutes, then stops). Nothing is cleared: the display route is /api/chat,
which keeps no history, and no session is touched. Requests at once: at
most 8 (load mode). Output: docs/bench/m9_lock_<UTC time>_<label>.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEV_CONTAINER = "kronk-orchestrator-dev-1"
PROD_CONTAINER = "kronk-orchestrator-1"
ALLOWED_BASES = {"http://localhost/dev", "http://127.0.0.1/dev"}
STAMP = "[kronk-origin] device=m9bench_fake_office area=Office"

# route key -> utterance. Each lands on the route named (routing.decide,
# checked at build time): direct dispatch (read-only now_playing), the home
# specialist (weather rule), the coordinator answering alone, the
# coordinator delegating to home (read-only now_playing in a named room),
# and the research specialist with a web search (SearXNG; kept few).
ROUTES = {
    "direct":     "what song is this",
    "specialist": "what's the weather like",
    "coord":      "what is the capital of France",
    "delegate":   "what's playing in the kitchen",
    "search":     "search the web for when the eiffel tower was built",
}
MOUNTS = ("voice", "display")

TWO_RESEARCH = "search the web for who designed the golden gate bridge"
TWO_VOICE = "what's the weather like"

LOAD = [  # (mount, utterance): distinct texts so events can be matched
    ("voice", "what's the weather like"),
    ("voice", "what is the capital of Spain"),
    ("voice", "what's playing in the office"),
    ("voice", "what song is this"),
    ("display", "what is the capital of Italy"),
    ("display", "what is the weather going to be like tomorrow"),
    ("display", "search the web for how tall the statue of liberty is"),
    ("display", "what's playing in the kitchen"),
]


class Refused(SystemExit):
    pass


def check_base(base: str) -> str:
    base = base.rstrip("/")
    parts = urlsplit(base)
    if base not in ALLOWED_BASES or parts.scheme != "http" or parts.path != "/dev":
        raise Refused(f"m9_lock_bench: refusing {base!r}: only {sorted(ALLOWED_BASES)}")
    return base


def http_json(url: str, body: dict | None = None, timeout: float = 180) -> tuple[int, dict | None, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        return e.code, None, raw
    except Exception as e:  # timeout, refused
        return 0, None, f"{type(e).__name__}: {e}"


def require_dry_run(base: str) -> dict:
    code, st, raw = http_json(f"{base}/api/status", timeout=60)
    ts = (st or {}).get("tool_service") or {}
    if code != 200 or ts.get("music_dry_run") is not True:
        raise Refused(f"m9_lock_bench: dev tool_service does not report music dry-run on "
                      f"({code} {raw[:200]}); refusing")
    return ts


def container_events(container: str, since: float) -> list[dict]:
    out = subprocess.run(["docker", "logs", "--since", str(int(since) - 1), container],
                         capture_output=True, text=True, timeout=60)
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


def prod_in_flight() -> list[str]:
    """Production requests started in the last 90 s with no request_complete
    (read-only: the production orchestrator's log)."""
    now = time.time()
    evs = container_events(PROD_CONTAINER, now - 120)
    started = {e["rid"]: e for e in evs if e.get("event") == "request" and e.get("ts", 0) > now - 90}
    done = {e["rid"] for e in evs if e.get("event") in ("request_complete", "request_ended")}
    return [f"{rid}: {e.get('text_preview', '')[:40]!r}" for rid, e in started.items() if rid not in done]


def wait_for_quiet_production(max_wait_s: float = 600) -> None:
    t0 = time.time()
    while True:
        busy = prod_in_flight()
        if not busy:
            return
        if time.time() - t0 > max_wait_s:
            raise Refused(f"m9_lock_bench: production busy for {max_wait_s:.0f} s ({busy}); stopping")
        print(f"  production has a request in flight ({busy}); waiting", flush=True)
        time.sleep(5)


def send(base: str, mount: str, text: str) -> dict:
    """One request; returns client timing and the reply."""
    if mount == "voice":
        url = f"{base}/voice/api/chat"
        msgs = [{"role": "system", "content": STAMP}, {"role": "user", "content": text}]
    else:
        url = f"{base}/api/chat"
        msgs = [{"role": "user", "content": text}]
    body = {"model": "kronk", "stream": False, "messages": msgs}
    t_wall = time.time()
    t0 = time.perf_counter()
    code, obj, raw = http_json(url, body)
    s = time.perf_counter() - t0
    reply = ((obj or {}).get("message") or {}).get("content")
    return {"mount": mount, "text": text, "sent_at": t_wall, "client_s": round(s, 3),
            "http": code, "reply": (reply if reply is not None else raw)[:300]}


def evidence(since: float, text: str, after: float | None = None) -> dict:
    """The dev request's events: rid, trace id, route, rounds with the cache
    split, lock waits, server duration, and how it ended."""
    for _ in range(20):
        evs = container_events(DEV_CONTAINER, since)
        req = [e for e in evs if e.get("event") == "request" and (e.get("text_preview") or "") == text[:80]
               and e.get("ts", 0) >= (after if after is not None else since) - 0.5]
        if req:
            rid = req[0]["rid"]
            mine = [e for e in evs if e.get("rid") == rid]
            end = next((e for e in mine if e.get("event") in ("request_complete", "request_ended")), None)
            if end is not None:
                rounds = [e for e in mine if e.get("event") == "agent_round"]
                waits = [e for e in mine if e.get("event") == "model_lock"]
                return {
                    "rid": rid,
                    "trace_id": next((e.get("trace_id") for e in mine if e.get("trace_id")), None),
                    "route": end.get("route"),
                    "server_s": end.get("duration_s"),
                    "ended": end.get("reason") if end.get("event") == "request_ended" else "complete",
                    "rounds": [{"agent": e.get("agent"), "phase": e.get("phase"), "s": e.get("duration_s"),
                                "prompt": e.get("prompt_tokens"), "cached": e.get("cached_tokens"),
                                "completion": e.get("completion_tokens")}
                               for e in rounds],
                    "lock_waits": [{"lock": e.get("lock"), "class": e.get("priority"),
                                    "wait_ms": e.get("wait_ms")} for e in waits],
                }
        time.sleep(0.5)
    return {}


def cache_ratio(rows: list[dict]) -> dict:
    prompt = cached = n = 0
    for r in rows:
        for g in (r.get("ev") or {}).get("rounds", []):
            if g.get("prompt") is None or g.get("cached") is None:
                continue
            prompt += g["prompt"]
            cached += g["cached"]
            n += 1
    return {"generations": n, "prompt_tokens": prompt, "cached_tokens": cached,
            "evaluated_tokens": prompt - cached,
            "reuse": round(cached / prompt, 4) if prompt else None}


def run_single(base: str, reps: int, search_reps: int) -> list[dict]:
    rows = []
    for rep in range(reps):
        wait_for_quiet_production()
        for key, text in ROUTES.items():
            if key == "search" and rep >= search_reps:
                continue
            for mount in MOUNTS:
                since = time.time()
                r = send(base, mount, text)
                r.update(rep=rep, route_key=key, ev=evidence(since, text))
                rows.append(r)
                print(f"  {rep} {key:10s} {mount:7s} {r['client_s']:6.2f}s http={r['http']} "
                      f"route={r['ev'].get('route')} cache={cache_ratio([r])['reuse']}", flush=True)
                time.sleep(0.5)
    return rows


def run_two(base: str, reps: int) -> list[dict]:
    rows = []
    for rep in range(reps):
        wait_for_quiet_production()
        since = time.time()
        with cf.ThreadPoolExecutor(2) as ex:
            fr = ex.submit(send, base, "display", TWO_RESEARCH)
            time.sleep(1.0)
            fv = ex.submit(send, base, "voice", TWO_VOICE)
            research, voice = fr.result(), fv.result()
        research["ev"] = evidence(since, TWO_RESEARCH)
        voice["ev"] = evidence(since, TWO_VOICE)
        voice["started_after_research_s"] = round(voice["sent_at"] - research["sent_at"], 3)
        rows.append({"rep": rep, "research": research, "voice": voice})
        print(f"  two {rep}: voice {voice['client_s']:.2f}s (http {voice['http']}), research "
              f"{research['client_s']:.2f}s (http {research['http']}); voice lock waits "
              f"{voice['ev'].get('lock_waits')}", flush=True)
        time.sleep(1.0)
    return rows


def log_errors(since: float) -> dict:
    """Slot or context errors in the llama.cpp journals and LiteLLM's log
    since `since` (read-only)."""
    out = {}
    pat = ("error", "failed", "exceed", "context shift", "n_ctx", "no slot", "slot unavailable")
    j = subprocess.run(["journalctl", "--user", "-u", "llama-gemma4-e4b", "-u", "llama-devstral-q4",
                        "-u", "llama-gemma3-4b", "--since", f"@{int(since)}", "--no-pager"],
                       capture_output=True, text=True, timeout=60)
    out["llama"] = [ln[:240] for ln in j.stdout.splitlines()
                    if any(p in ln.lower() for p in pat) and "reasoning-budget" not in ln][:40]
    lg = subprocess.run(["docker", "logs", "--since", str(int(since)), "kronk-litellm-1"],
                        capture_output=True, text=True, timeout=60)
    out["litellm"] = [ln[:240] for ln in (lg.stdout + lg.stderr).splitlines()
                      if "error" in ln.lower() or "exception" in ln.lower()][:40]
    return out


def run_load(base: str) -> dict:
    wait_for_quiet_production()
    since = time.time()
    with cf.ThreadPoolExecutor(len(LOAD)) as ex:
        futs = [ex.submit(send, base, m, t) for m, t in LOAD]
        rows = [f.result() for f in futs]
    for r in rows:
        r["ev"] = evidence(since, r["text"])
        print(f"  load {r['mount']:7s} {r['client_s']:6.2f}s http={r['http']} {r['text'][:40]!r} "
              f"waits={r['ev'].get('lock_waits')}", flush=True)
    time.sleep(2)
    return {"requests": rows, "log_errors": log_errors(since)}


def summarize_single(rows: list[dict]) -> list[dict]:
    out = []
    for key in ROUTES:
        for mount in MOUNTS:
            rs = [r for r in rows if r["route_key"] == key and r["mount"] == mount]
            if not rs:
                continue
            xs = [r["client_s"] for r in rs if r["http"] == 200]
            out.append({
                "route": key, "mount": mount, "n": len(rs), "ok": len(xs),
                "median_s": round(statistics.median(xs), 3) if xs else None,
                "mean_s": round(statistics.mean(xs), 3) if xs else None,
                "min_s": round(min(xs), 3) if xs else None,
                "max_s": round(max(xs), 3) if xs else None,
                "cache": cache_ratio(rs),
            })
    return out


def _samples(run: dict, key: str, mount: str) -> list[dict]:
    return [r for r in run.get("single", []) if r["route_key"] == key and r["mount"] == mount
            and r["http"] == 200]


def overhead(r: dict) -> float:
    """Client time not spent in a model generation: routing, tools, lock
    waits, HTTP. Generation time varies with what the model writes."""
    return r["client_s"] - sum((g.get("s") or 0) for g in (r.get("ev") or {}).get("rounds", []))


def evaluated(r: dict) -> int | None:
    """Prompt tokens the server evaluated (not taken from its cache) for
    one request, over all its generations; None if any did not report."""
    gens = (r.get("ev") or {}).get("rounds", [])
    if any(g.get("prompt") is None or g.get("cached") is None for g in gens):
        return None
    return sum(g["prompt"] - g["cached"] for g in gens)


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def compare(paths: list[str]) -> None:
    """The first two files are the baseline (two runs of the same code);
    each later file is judged against them, per route and mount, on what a
    lock change can move: the orchestrator's overhead (client time minus
    generation time) and the prompt tokens the server had to evaluate
    (cache reuse). Worse: the step's median is beyond the band the two
    baselines span, widened by their difference (at least 0.05 s, or 10%
    of the evaluated tokens). Client latency is printed alongside; it also
    moves with how many tokens the model chose to write (temperature 1.0)."""
    runs = [json.loads(Path(p).read_text()) for p in paths]
    a, b = runs[0], runs[1]
    print(f"baseline: {a['label']} + {b['label']}   (latency s | overhead s | evaluated tokens | cache reuse)")
    for key in ROUTES:
        for mount in MOUNTS:
            sa, sb = _samples(a, key, mount), _samples(b, key, mount)
            if not sa or not sb:
                continue
            la, lb = (_med(x["client_s"] for x in s) for s in (sa, sb))
            oa, ob = (_med(overhead(x) for x in s) for s in (sa, sb))
            ea, eb = (_med(evaluated(x) for x in s) for s in (sa, sb))
            ca, cb = cache_ratio(sa)["reuse"], cache_ratio(sb)["reuse"]
            o_hi = max(oa, ob) + max(abs(oa - ob), 0.05)
            e_hi = None if ea is None or eb is None else max(ea, eb) + max(abs(ea - eb), 0.1 * max(ea, eb))
            line = (f"{key:10s} {mount:7s} base {la:5.2f}/{lb:5.2f} | {oa:4.2f}/{ob:4.2f} | "
                    f"{ea}/{eb} | {ca}/{cb}")
            for r in runs[2:]:
                s = _samples(r, key, mount)
                if not s:
                    continue
                lat, o, e, c = (_med(x["client_s"] for x in s), _med(overhead(x) for x in s),
                                _med(evaluated(x) for x in s), cache_ratio(s)["reuse"])
                flags = []
                if o > o_hi:
                    flags.append("OVERHEAD")
                if e is not None and e_hi is not None and e > e_hi:
                    flags.append("CACHE")
                line += f" || {r['label']}: {lat:5.2f} | {o:4.2f} | {e} | {c} {' '.join(flags) or 'ok'}"
            print(line)
    for r in runs:
        tw = r.get("two_client") or []
        vs = [x["voice"]["client_s"] for x in tw]
        rs = [x["research"]["client_s"] for x in tw]
        waits = [sum(w.get("wait_ms") or 0 for w in x["voice"]["ev"].get("lock_waits", [])) for x in tw]
        ev = [evaluated(x["research"]) for x in tw]
        if vs:
            print(f"two-client {r['label']}: voice median {statistics.median(vs):.2f}s {vs}; "
                  f"voice lock wait ms {waits}; research s {rs}; research evaluated tokens {ev}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost/dev")
    ap.add_argument("--label", default="run")
    ap.add_argument("--mode", choices=["all", "single", "two", "load"], default="all")
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--search-reps", type=int, default=2)
    ap.add_argument("--two-reps", type=int, default=4)
    ap.add_argument("--compare", nargs="+")
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.compare:
        compare(args.compare)
        return 0
    base = check_base(args.url)
    ts = require_dry_run(base)
    started = time.time()
    result: dict = {"label": args.label, "base": base, "started_utc": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
                    "git": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                          capture_output=True, text=True).stdout.strip(),
                    "tool_service": {"music_dry_run": ts.get("music_dry_run")}}
    if args.mode in ("all", "single"):
        print("single-request runs", flush=True)
        rows = run_single(base, args.reps, args.search_reps)
        result["single"] = rows
        result["summary"] = summarize_single(rows)
        result["cache_all_single"] = cache_ratio(rows)
    if args.mode in ("all", "two"):
        print("two-client runs", flush=True)
        result["two_client"] = run_two(base, args.two_reps)
    if args.mode == "load":
        print("load check", flush=True)
        result["load"] = run_load(base)
    if args.mode in ("all", "single", "two"):
        result["log_errors"] = log_errors(started)
    result["finished_utc"] = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d_%H%M%S")
    out = Path(args.out) if args.out else ROOT / "docs" / "bench" / f"m9_lock_{stamp}_{args.label}.json"
    out.write_text(json.dumps(result, indent=1) + "\n")
    for s in result.get("summary", []):
        print(f"{s['route']:10s} {s['mount']:7s} median {s['median_s']}s  (n={s['n']}, ok={s['ok']}) "
              f"cache reuse {s['cache']['reuse']}")
    if "two_client" in result:
        vs = [x["voice"]["client_s"] for x in result["two_client"]]
        print(f"two-client voice latency: median {statistics.median(vs):.2f}s runs {vs}")
    print(f"log errors: llama {len(result.get('log_errors', result.get('load', {}).get('log_errors', {})).get('llama', []))}")
    print(f"m9_lock_bench: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
