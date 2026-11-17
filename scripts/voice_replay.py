#!/usr/bin/env python3
"""Voice regression corpus, layer two: live replay through Home Assistant.

Sprint item M10. Sends rows of tests/voice_corpus.jsonl to HA's
`assist_pipeline/run` (start_stage and end_stage `intent`, so no audio and no
TTS) as a chosen satellite, and compares what happened with each row's
expected outcome. Default pipeline: `kronk-dev` (the Hey Jarvis dev path).

    python3 scripts/voice_replay.py [--label NAME] [--id ID ...] [--tag TAG ...]
    python3 scripts/voice_replay.py --check-only        # read-only checks, runs nothing
    tests/.venv/bin/python scripts/voice_replay.py --answers --label m11-say   # M11 answer check

Needs: HA_TOKEN (environment or .env), the `websockets` package (system
python3 and tests/.venv both have it), PyYAML for trigger names (optional),
and access to `docker` (read-only: HA's config entries, the blueprint, and
the dev containers' logs).

Silent and safe by construction:

1. Dry-run gate. Before anything runs, the script follows the pipeline to the
   Kronk behind it (pipeline -> conversation entity -> Ollama config entry URL
   -> `<base>/api/status`) and requires `tool_service.music_dry_run: true`
   there. It re-checks before every row it executes and refuses or stops when
   the answer is anything else.
2. Production guard. The `kronk` pipeline, HA's preferred pipeline, or any
   pipeline whose Kronk URL is not under `/dev/` is refused unless
   `--allow-production` is given, and the override still needs dry-run on.
3. HA-local rows are never executed. `prefer_local_intents` is on, and
   MUSIC_DRY_RUN cannot reach HA's own handlers: a sentence trigger (the Kronk
   music blueprint, which plays through Music Assistant directly or hands off
   to *production* Kronk) or a built-in intent (pause, next, timers) acts for
   real. Each row is first checked with HA's sentence-match debug
   (`conversation/agent/homeassistant/debug`, read-only). A trigger match or a
   strict built-in match is compared there and not executed. A built-in match
   that is fuzzy only is executed, because the pipeline's local pass is strict
   (`async_handle_intents` -> `strict_intents_only=True`, which returns before
   the fuzzy stage; HA 2026.5.4 `conversation/default_agent.py`); as a second
   guard such a row is skipped when any player is playing.
4. Player check. Every `media_player` state and media title is snapshotted
   before the run and after each executed row. Any change stops the run.
5. Rows tagged `needs-context` (a follow-up with no history in a replay) or
   `url` (a typed link) are skipped and reported.

Answer check (sprint M11, --answers). Instead of HA, every play_music row and
every row tagged non-music is sent to the dev tool_service directly: POST
/music/say with the sentence and the row's satellite (dry_run forced on), and
POST /music with the stand-in model arguments (tests/music_snapshot.live_args)
for today's live path. Each answer says what would play; the script compares
the M11 path's with the row's expectation (as layer one does) and with the
live path's. Most "play ..." sentences are taken by the HA blueprint locally,
so they are never sent through HA; this mode calls the endpoints with the
sentence instead. Dev only (the tool_service URL must be the dev port 8103),
dry run required as above. Statuses use xfail.parser.

Writes docs/bench/voice_replay_<UTC time>_<label>.json (or --out) with, per
row: status, expected against actual, latency, trace id and pipeline run id.
Statuses: pass, fail, xfail (a row marked expected-failure that failed),
xpass (marked, but passed: remove the marker), skip. Exit status: 0 when no
row that should pass failed; 1 when one did; 2 when a safety precondition
refused the run; 3 when the run was stopped by the player check or dry-run.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("voice_replay: needs the 'websockets' package (python3 or tests/.venv/bin/python)")

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "voice_corpus.jsonl"
HA_WS = os.environ.get("HA_WS_URL", "ws://localhost:8123/api/websocket")
HA_REST = os.environ.get("HA_URL", "http://localhost:8123")
HA_CONTAINER = "homeassistant"
BLUEPRINT = "/config/blueprints/automation/kronk/mass_assist_kronk.yaml"

# The satellites (M17 e2e). Verified against HA's registries at start-up.
DEVICES = {
    "kitchen_pe": {"device_id": "e0949788663255952ce7778975527882", "area": "Kitchen",
                   "player": "media_player.home_assistant_voice_0ac919_pe_01"},
    "office_sat1": {"device_id": "ac6dd1f1c8cf1d229cda0f42224a2013", "area": "Office",
                    "player": "media_player.satellite1_d8d1bc"},
    "basement_pe": {"device_id": "1b6bd3b03f90e52f08dd98dca41eac82", "area": "Basement Theater",
                    "player": "media_player.home_assistant_voice_0ab117"},
}
DEFAULT_DEVICE = "office_sat1"       # the office speaker is the sanctioned test speaker
SKIP_TAGS = {"needs-context": "follow-up that needs conversation history; a replay has none",
             "url": "typed link; a spoken request carries no URL"}
ACTIVE_STATES = {"playing", "buffering"}
MUSIC_TOOLS = {"play_music", "control_music", "rate_music", "now_playing"}
# update_magicmirror runs only for the pinned sentence (sprint M4b); a
# refusal still logs its tool_call, plus an mm_update_refused event.
MIRROR_TOOL = "update_magicmirror"
# Rows tagged `mirror` are never replayed against production: the pinned row
# would start a real mirror update, and MUSIC_DRY_RUN does not cover it.
PRODUCTION_SKIP_TAGS = {"mirror": "mirror row; on production it could start a real mirror update"}


class Refused(Exception):
    """A safety precondition failed; nothing more is sent."""


class Stopped(Exception):
    """A check during the run failed; the run stops where it is."""


# ── small helpers ────────────────────────────────────────────────────────────

def ha_token() -> str:
    tok = os.environ.get("HA_TOKEN")
    if not tok and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("HA_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip("'\"")
    if not tok:
        raise Refused("HA_TOKEN not set (environment or .env)")
    return tok


def http_json(url: str, token: str | None = None, timeout: float = 10):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def docker(*args: str) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise Refused(f"docker {' '.join(args[:2])} failed: {out.stderr.strip()[:200]}")
    return out.stdout + out.stderr


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def player_snapshot(token: str) -> dict:
    states = http_json(f"{HA_REST}/api/states", token)
    snap = {}
    for s in states:
        if s["entity_id"].startswith("media_player."):
            a = s.get("attributes", {})
            snap[s["entity_id"]] = {"state": s["state"], "media_title": a.get("media_title"),
                                    "media_artist": a.get("media_artist"),
                                    "media_content_id": a.get("media_content_id")}
    return snap


def snapshot_diff(a: dict, b: dict) -> list[dict]:
    out = []
    for eid in sorted(set(a) | set(b)):
        if a.get(eid) != b.get(eid):
            out.append({"entity_id": eid, "before": a.get(eid), "after": b.get(eid)})
    return out


def trigger_map() -> dict[str, str]:
    """Blueprint sentence template (whitespace-normalised) -> trigger id."""
    try:
        import yaml
    except ImportError:
        return {}

    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!input", lambda loader, node: {"!input": loader.construct_scalar(node)})
    doc = yaml.load(docker("exec", HA_CONTAINER, "cat", BLUEPRINT), Loader=Loader)
    defaults: dict[str, list[str]] = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict) and k.endswith("_trigger") and "default" in v:
                    defaults[k] = v["default"]
                walk(v)
    walk(doc.get("blueprint", {}))
    out = {}
    for trig in doc.get("triggers") or doc.get("trigger") or []:
        cmd = trig.get("command")
        if isinstance(cmd, dict) and cmd.get("!input") in defaults:
            for sentence in defaults[cmd["!input"]]:
                out[norm(sentence)] = trig.get("id")
    return out


# ── Home Assistant websocket ─────────────────────────────────────────────────

class HA:
    def __init__(self, token: str):
        self.token = token
        self.ws = None
        self.n = 0

    async def __aenter__(self):
        self.ws = await websockets.connect(HA_WS, max_size=None)
        await self.ws.recv()
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        if json.loads(await self.ws.recv()).get("type") != "auth_ok":
            raise Refused("HA websocket auth failed")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def call(self, type_: str, **kw):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "type": type_, **kw}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == mid and m.get("type") == "result":
                if not m.get("success"):
                    raise RuntimeError(f"{type_}: {m.get('error')}")
                return m.get("result")

    async def run_intent(self, text: str, pipeline_id: str, device_id: str, timeout: float):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({
            "id": mid, "type": "assist_pipeline/run", "start_stage": "intent", "end_stage": "intent",
            "input": {"text": text}, "pipeline": pipeline_id, "device_id": device_id,
            "timeout": int(timeout)}))
        events = []
        async def collect():
            while True:
                m = json.loads(await self.ws.recv())
                if m.get("id") != mid:
                    continue
                if m.get("type") == "result" and not m.get("success"):
                    raise RuntimeError(f"assist_pipeline/run: {m.get('error')}")
                if m.get("type") == "event":
                    events.append(m["event"])
                    if m["event"]["type"] in ("run-end", "error"):
                        return events
        return await asyncio.wait_for(collect(), timeout + 15)


def classify_debug(r: dict | None, tmap: dict[str, str]) -> dict:
    """HA's sentence-match debug result -> what the pipeline's local pass would do."""
    if not r or not r.get("match"):
        return {"local": False, "summary": "no local match" if not r else
                f"partial {((r.get('intent') or {}).get('name'))} (match false)"}
    if r.get("source") == "trigger":
        tpl = r.get("sentence_template") or ""
        return {"local": True, "source": "trigger", "trigger": tmap.get(norm(tpl)), "template": tpl,
                "summary": f"trigger {tmap.get(norm(tpl)) or '?'}"}
    intent = (r.get("intent") or {}).get("name")
    if r.get("fuzzy_match"):
        return {"local": False, "fuzzy": True, "intent": intent,
                "summary": f"fuzzy {intent} only (strict pass misses)"}
    return {"local": True, "source": "builtin", "intent": intent, "template": r.get("sentence_template"),
            "summary": f"builtin {intent}"}


# ── Kronk side: what the dev orchestrator and tool_service logged ────────────

def _log_lines(container: str, t_start: float) -> list[str]:
    """Log lines written at or after t_start (epoch seconds). `docker logs
    --since` is second-granular, so filter on the nanosecond timestamp too."""
    since = dt.datetime.fromtimestamp(t_start - 1, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for line in docker("logs", "--timestamps", "--since", since, container).splitlines():
        stamp, _, rest = line.partition(" ")
        try:
            ts = dt.datetime.strptime(stamp[:26], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue
        if ts >= t_start:
            out.append(rest)
    return out


def kronk_evidence(orch: str, tools: str, t_start: float, text: str, wait: float = 6.0) -> dict:
    """Events of the request whose text matches, from the orchestrator log;
    dry-run and music-request lines from the tool_service log, both limited
    to lines written after the row was sent."""
    want = norm(text)[:60]
    deadline = time.monotonic() + wait
    ev = {"rid": None, "route": None, "rule": None, "tools": [], "direct": [], "trace_id": None,
          "events": []}
    while True:
        lines = _log_lines(orch, t_start)
        reqs, by_rid = [], {}
        for line in lines:
            if "kronk.events: " not in line:
                continue
            try:
                e = json.loads(line.split("kronk.events: ", 1)[1])
            except ValueError:
                continue
            by_rid.setdefault(e.get("rid"), []).append(e)
            if e.get("event") == "request" and norm(e.get("text_preview"))[:60] == want:
                reqs.append(e)
        if reqs:
            rid = reqs[-1]["rid"]
            evs = by_rid.get(rid, [])
            if any(e.get("event") == "request_complete" for e in evs) or time.monotonic() > deadline:
                ev.update(rid=rid, trace_id=reqs[-1].get("trace_id"), events=[e.get("event") for e in evs])
                for e in evs:
                    if e.get("event") in ("route", "route_shortcut") and ev["rule"] is None:
                        ev["route"], ev["rule"] = e.get("route"), e.get("rule")
                    if e.get("event") == "tool_call":
                        ev["tools"].append(f"{e.get('agent')}:{e.get('tool')}")
                        if e.get("direct"):       # run with no model call (sprint M7)
                            ev["direct"].append(e.get("tool"))
                break
        if time.monotonic() > deadline:
            break
        time.sleep(0.5)
    dry, reqlines = [], []
    for line in _log_lines(tools, t_start):
        m = re.search(r"music dry run: (\S+) on (\[.*?\]): (.*)$", line)
        if m:
            dry.append({"action": m.group(1),
                        "targets": re.findall(r"media_player\.[a-z0-9_]+", m.group(2)),
                        "detail": m.group(3)[:300]})
        elif "music request" in line:
            reqlines.append(line.split("music request", 1)[1].strip()[:300])
    ev["dry_run"] = dry
    ev["music_requests"] = reqlines
    return ev


# ── comparison ───────────────────────────────────────────────────────────────

def expected_player(row: dict, device: dict) -> str | None:
    p = row["expect"].get("player")
    if p == "origin":
        return device["player"]
    if p and p.startswith("entity:"):
        return p.split(":", 1)[1]
    return None    # area:/unknown: are recorded, not asserted live


def compare_local(row: dict, dbg: dict) -> list[dict]:
    e = row["expect"]
    checks = []
    if e["tier"] == "kronk":
        checks.append({"check": "tier", "ok": False, "expected": "kronk", "actual": f"ha_local ({dbg['summary']})"})
        return checks
    checks.append({"check": "tier", "ok": True, "expected": "ha_local", "actual": "ha_local"})
    want = e["ha"]
    ok = dbg.get("source") == want["source"]
    checks.append({"check": "ha.source", "ok": ok, "expected": want["source"], "actual": dbg.get("source")})
    if want["source"] == "trigger" and want.get("trigger"):
        checks.append({"check": "ha.trigger", "ok": dbg.get("trigger") == want["trigger"],
                       "expected": want["trigger"], "actual": dbg.get("trigger")})
    if want["source"] == "builtin":
        checks.append({"check": "ha.intent", "ok": dbg.get("intent") == want["intent"],
                       "expected": want["intent"], "actual": dbg.get("intent")})
    return checks


def compare_kronk(row: dict, device: dict, processed_locally: bool, kev: dict) -> list[dict]:
    e = row["expect"]
    checks = []
    if e["tier"] != "kronk":
        checks.append({"check": "tier", "ok": False, "expected": f"ha_local ({e['ha']})",
                       "actual": "kronk"})
    else:
        checks.append({"check": "tier", "ok": not processed_locally and bool(kev["rid"]),
                       "expected": "kronk",
                       "actual": "ha_local" if processed_locally else ("kronk" if kev["rid"] else "no Kronk request seen")})
    checks.append({"check": "rule", "ok": (kev["route"], kev["rule"]) == (e["route"], e["rule"]),
                   "expected": f"{e['route']}/{e['rule']}", "actual": f"{kev['route']}/{kev['rule']}"})
    tool = e.get("tool")
    names = [t.split(":", 1)[1] for t in kev["tools"]]
    if tool:
        checks.append({"check": "tool", "ok": tool in names, "expected": tool, "actual": kev["tools"]})
    elif "tool" in e:
        # tool: null means no existing tool can do it; a music tool firing is
        # a claim the code cannot back.
        fired = [n for n in names if n in MUSIC_TOOLS]
        checks.append({"check": "no music tool", "ok": not fired, "expected": [], "actual": fired})
    if e.get("direct"):
        # The M7 rules run their tool directly, with no model call.
        checks.append({"check": "direct", "ok": tool in kev.get("direct", []),
                       "expected": tool, "actual": kev.get("direct", [])})
    refused = "mm_update_refused" in (kev.get("events") or [])
    if tool == MIRROR_TOOL:
        checks.append({"check": "mirror update not refused", "ok": not refused,
                       "expected": False, "actual": refused})
    elif MIRROR_TOOL in names:
        # Reached by any other sentence: the pin must have refused it.
        checks.append({"check": "mirror update refused", "ok": refused,
                       "expected": True, "actual": refused})
    if kev["dry_run"]:
        d = kev["dry_run"][-1]
        want_action = e.get("action") or ({"up": "thumb_up", "down": "thumb_down"}.get(e.get("thumb")))
        if tool == "play_music":
            want_action = "play"
        if want_action:
            checks.append({"check": "action", "ok": d["action"] == want_action,
                           "expected": want_action, "actual": d["action"]})
        want_p = expected_player(row, device)
        if want_p:
            checks.append({"check": "player", "ok": want_p in d["targets"],
                           "expected": want_p, "actual": d["targets"]})
    return checks


def status_of(row: dict, checks: list[dict]) -> tuple[str, str | None]:
    xf = row.get("xfail", {})
    reason = xf.get("live") or xf.get("routing")
    ok = all(c["ok"] for c in checks)
    if reason:
        return ("xpass" if ok else "xfail"), reason
    return ("pass" if ok else "fail"), None


# ── M11 answer check: the new endpoint against the row and the live path ────

DEV_TOOL_SERVICE = os.environ.get("KRONK_DEV_TOOL_SERVICE", "http://127.0.0.1:8103")
_PROVIDER_NAMES = {"ytmusic": "youtube_music", "filesystem_local": "nas", "pandora": "pandora"}


def fold(s: str | None) -> str:
    """music_resolve.fold: lowercase, no accents or punctuation, '&' as 'and',
    no leading 'the'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    s = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()
    return s[4:] if s.startswith("the ") else s


def post_json(url: str, body: dict, timeout: float = 60) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def dev_players(token: str) -> list[dict]:
    """The Music Assistant players, from the same read-only template /music
    uses (tool_service.main._players_template)."""
    import tempfile
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("KRONK_CONFIG_DIR", str(ROOT / "config"))
    tmp = tempfile.gettempdir()          # importing tool_service must never reach /data
    os.environ.setdefault("MUSIC_TASTE_DB", os.path.join(tmp, "voice-replay-no-taste.db"))
    os.environ.setdefault("MUSIC_TASTE_FILE", os.path.join(tmp, "voice-replay-no-taste.yaml"))
    from tool_service import main as ts  # noqa: E402  (tests/.venv/bin/python)
    req = urllib.request.Request(f"{HA_REST}/api/template",
                                 data=json.dumps({"template": ts._players_template(None)}).encode(),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        players, _ = ts.parse_player_payload(r.read().decode())
    return players


def expected_targets(row: dict, players: list[dict], device: dict) -> list[str] | str:
    want = row["expect"].get("player") or "origin"
    if want == "origin":
        return [device["player"]]
    kind, _, val = want.partition(":")
    if kind == "entity":
        return [val]
    if kind == "area":
        return sorted(p["entity_id"] for p in players if (p.get("area") or "").lower() == val.lower())
    return want                                     # unknown:<words>


def answer_of(code: int, body: dict) -> dict:
    """What an answer says would play: {action, kind, media_type, name, artist, uri, targets, provider}."""
    if "decision" in body or "say" in body:          # /music/say
        d = body.get("decision") or {}
        res = body.get("result") or {}
        return {"ok": bool(body.get("ok")), "action": d.get("action"), "kind": d.get("kind"),
                "media_type": d.get("media_type"), "name": d.get("name"), "artist": d.get("artist"),
                "provider": d.get("provider"), "named_provider": (body.get("parsed") or {}).get("provider"),
                "uri": (res.get("would_play") or {}).get("media_id") or d.get("uri"),
                "targets": res.get("targets") or body.get("targets"), "say": body.get("say"),
                "decided_by": d.get("decided_by")}
    if code != 200:                                  # /music failed
        return {"ok": False, "error": body.get("detail")}
    res = body.get("resolved") or {}
    pick = body.get("pick") or {}
    station = body.get("station") or {}
    wp = body.get("would_play") or {}
    return {"ok": True, "kind": "genre" if pick else "named",
            "media_type": None if pick else (res.get("media_type") or wp.get("media_type")),
            "name": pick.get("phrase") or res.get("name") or station.get("name") or wp.get("media_id"),
            "artist": res.get("artist"), "uri": wp.get("media_id"), "targets": body.get("targets"),
            "detail": body.get("detail")}


def compare_answer(row: dict, new: dict, players: list[dict], device: dict) -> list[dict]:
    e, checks = row["expect"], []
    for k in ("kind", "media_type"):
        if k in e:
            checks.append({"check": k, "ok": new.get(k) == e[k], "expected": e[k], "actual": new.get(k)})
    if "name" in e:
        ok = (e["name"] is None and new.get("name") is None) or \
            (e["name"] is not None and fold(e["name"]) == fold(new.get("name")))
        checks.append({"check": "name", "ok": ok, "expected": e["name"], "actual": new.get("name")})
    if "artist" in e:
        got = new.get("artist") or ""
        ok = fold(e["artist"]) == fold(got) or fold(e["artist"]) in [fold(a) for a in got.split(" and ")]
        checks.append({"check": "artist", "ok": ok, "expected": e["artist"], "actual": new.get("artist")})
    if "provider" in e:
        named = _PROVIDER_NAMES.get(new.get("named_provider"))
        chosen = _PROVIDER_NAMES.get(new.get("provider"))
        ok = named is None if e["provider"] is None else e["provider"] in (named, chosen)
        checks.append({"check": "provider", "ok": ok, "expected": e["provider"],
                       "actual": {"named": named, "chosen": chosen}})
    want = expected_targets(row, players, device)
    if isinstance(want, str):
        ok = not new.get("ok") and want.split(":", 1)[1].lower() in (new.get("say") or "").lower()
    else:
        ok = sorted(new.get("targets") or []) == sorted(want)
    checks.append({"check": "player", "ok": ok, "expected": want, "actual": new.get("targets")})
    return checks


def agree_with_live(new: dict, live: dict) -> tuple[bool, str]:
    if not new.get("ok") and not live.get("ok"):
        return True, "both fail"
    if bool(new.get("ok")) != bool(live.get("ok")):
        return False, "only the new path plays" if new.get("ok") else "only the live path plays"
    if sorted(new.get("targets") or []) != sorted(live.get("targets") or []):
        return False, "different speaker"
    if new.get("kind") != live.get("kind"):
        return False, f"live read {live.get('kind')}, new read {new.get('kind')}"
    if new.get("kind") == "genre":
        same = fold(new.get("name")) == fold(live.get("name"))
        return same, "same style" if same else "different style"
    if new.get("uri") and new.get("uri") == live.get("uri"):
        return True, "same item"
    same = new.get("media_type") == live.get("media_type") and fold(new.get("name")) == fold(live.get("name"))
    return same, "same type and name" if same else "different item"


def lead_in_stripped(text: str) -> str:
    """The sentence as origin.said carries it: without "ask Kronk to" (M19)."""
    sys.path.insert(0, str(ROOT / "orchestrator"))
    import routing  # noqa: E402  (needs the orchestrator's deps: run with tests/.venv/bin/python)
    return routing.lead_in_request(text) or text


def answers(args) -> int:
    url = args.tool_service_url.rstrip("/")
    if not re.match(r"^https?://(127\.0\.0\.1|localhost):8103$", url):
        raise Refused(f"--answers runs against the dev tool_service only (port 8103), not {url}")
    def dry_on() -> dict:
        h = http_json(f"{url}/health")
        if h.get("music_dry_run") is not True:
            raise Refused(f"music dry-run is not on at {url} ({h}); refusing")
        return h
    health = dry_on()
    token = ha_token()
    sys.path.insert(0, str(ROOT))
    from tests import music_snapshot as snapmod      # the stand-in model arguments
    snap = {"players": dev_players(token)}
    rows = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    selected, not_selected = select_rows(rows, args)
    selected = [r for r in selected if r["expect"].get("tool") == "play_music" or "non-music" in r["tags"]]
    started = dt.datetime.now(dt.timezone.utc)
    before = player_snapshot(token)
    result = {"meta": {"started": started.isoformat(timespec="seconds"), "label": args.label, "mode": "answers",
                       "tool_service": url, "tool_service_health": health,
                       "git_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                                    capture_output=True, text=True).stdout.strip(),
                       "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest()[:16],
                       "selected": len(selected)}, "rows": []}
    print(f"voice_replay --answers: {url}, dry-run on, {len(selected)} rows")
    for i, row in enumerate(selected):
        if i and i % 20 == 0:
            dry_on()
        dkey = row["origin"].get("device") or args.device
        device = DEVICES[dkey]
        o_area, o_dev = DEVICES[dkey]["area"], DEVICES[dkey]["device_id"]
        said = lead_in_stripped(row["text"])
        t0 = time.monotonic()
        code, body = post_json(f"{url}/music/say", {"sentence": said, "origin_device": o_dev,
                                                    "origin_area": o_area, "dry_run": True})
        new_ms = round((time.monotonic() - t0) * 1000)
        new = answer_of(code, body)
        rec = {"id": row["id"], "text": row["text"], "said": said, "device": dkey, "expected": row["expect"],
               "xfail": row.get("xfail"), "new": new, "new_ms": new_ms,
               "new_trace": {k: (body.get("trace") or {}).get(k) for k in ("steps", "model", "searches", "ms")}}
        if "non-music" in row["tags"]:
            ok = new.get("action") == "not_music"
            rec["checks"] = [{"check": "not music", "ok": ok, "expected": "not_music", "actual": new.get("action")}]
        else:
            rec["checks"] = compare_answer(row, new, snap["players"], device)
            largs = snapmod.live_args(row, {"players": snap["players"]})
            if largs is not None:
                largs.update(origin_device=o_dev, origin_area=o_area, said=said)    # as origin.said carries it
                t0 = time.monotonic()
                lcode, lbody = post_json(f"{url}/music", largs)
                rec["live_ms"] = round((time.monotonic() - t0) * 1000)
                rec["live"] = answer_of(lcode, lbody)
                rec["agree_with_live"], rec["agree_why"] = agree_with_live(new, rec["live"])
        ok = all(c["ok"] for c in rec["checks"])
        reason = (row.get("xfail") or {}).get("parser")
        rec["status"] = ("xpass" if ok else "xfail") if reason else ("pass" if ok else "fail")
        result["rows"].append(rec)
        mark = {"pass": ".", "fail": "F", "xfail": "x", "xpass": "X"}[rec["status"]]
        live_note = "" if "agree_with_live" not in rec else (" =live" if rec["agree_with_live"] else
                                                             f" !=live ({rec['agree_why']})")
        print(f"  {mark} {rec['status']:5s} {row['id']}{live_note}"
              + (f"  [{'; '.join(c['check'] + ' ' + str(c['actual']) for c in rec['checks'] if not c['ok'])}]"
                 if rec["status"] in ("fail", "xpass") else ""))
    after = player_snapshot(token)
    changed = snapshot_diff(before, after)
    result["players"] = {"changed": changed, "unchanged": not changed, "count": len(before)}
    counts: dict = {}
    for r in result["rows"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    compared = [r for r in result["rows"] if "agree_with_live" in r]
    result["summary"] = {"counts": counts, "compared_with_live": len(compared),
                         "agree_with_live": sum(1 for r in compared if r["agree_with_live"]),
                         "failed": [r["id"] for r in result["rows"] if r["status"] == "fail"],
                         "xpassed": [r["id"] for r in result["rows"] if r["status"] == "xpass"],
                         "disagree_with_live": [{"id": r["id"], "why": r["agree_why"]}
                                                for r in compared if not r["agree_with_live"]]}
    result["meta"]["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = Path(args.out) if args.out else (
        ROOT / "docs" / "bench" / f"voice_replay_{started:%Y-%m-%d_%H%M%S}_{args.label}.json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    s = result["summary"]
    print(f"voice_replay --answers: {counts}; agree with live {s['agree_with_live']}/{s['compared_with_live']}; "
          f"players {'unchanged' if not changed else 'CHANGED: ' + str(changed)}")
    print(f"voice_replay: wrote {out}")
    if changed:
        return 3
    return 1 if s["failed"] else 0


# ── main ─────────────────────────────────────────────────────────────────────

async def resolve_target(ha: HA, token: str, args) -> dict:
    pl = await ha.call("assist_pipeline/pipeline/list")
    match = [p for p in pl["pipelines"] if args.pipeline in (p["name"], p["id"])]
    if len(match) != 1:
        raise Refused(f"pipeline {args.pipeline!r}: {len(match)} matches")
    p = match[0]
    engine = p["conversation_engine"]
    if not engine or engine == "conversation.home_assistant":
        raise Refused(f"pipeline {p['name']} uses {engine}: everything would run locally in HA")
    ent = await ha.call("config/entity_registry/get", entity_id=engine)
    store = json.loads(docker("exec", HA_CONTAINER, "cat", "/config/.storage/core.config_entries"))
    entry = next((c for c in store["data"]["entries"] if c["entry_id"] == ent.get("config_entry_id")), None)
    if not entry or entry.get("domain") != "ollama":
        raise Refused(f"{engine} is not an Ollama (Kronk) conversation entity")
    url = (entry.get("data") or {}).get("url", "").rstrip("/")
    if not url.endswith("/voice"):
        raise Refused(f"unexpected Kronk URL {url!r} for {engine}")
    base = url[: -len("/voice")]
    status_url = base + "/api/status"
    production = (p["name"] == "kronk" or p["id"] == pl.get("preferred_pipeline")
                  or not base.endswith("/dev"))
    if production and not args.allow_production:
        raise Refused(f"pipeline {p['name']} is production (Kronk at {url}); pass --allow-production "
                      "to replay against it (dry-run is still required)")
    dev = base.endswith("/dev")
    return {"pipeline": p["name"], "pipeline_id": p["id"], "engine": engine, "kronk_url": url,
            "status_url": status_url, "production": production,
            "prefer_local_intents": p.get("prefer_local_intents"),
            "orchestrator": args.orchestrator_container or ("kronk-orchestrator-dev-1" if dev else "kronk-orchestrator-1"),
            "tool_service": args.tool_service_container or ("kronk-tool_service-dev-1" if dev else "kronk-tool_service-1")}


def require_dry_run(target: dict) -> dict:
    try:
        st = http_json(target["status_url"])
    except Exception as e:
        raise Refused(f"cannot read {target['status_url']}: {e}")
    ts = st.get("tool_service") or {}
    if ts.get("music_dry_run") is not True:
        raise Refused(f"music dry-run is not on behind {target['pipeline']} "
                      f"({target['status_url']} → tool_service {ts}); refusing")
    return ts


async def verify_devices(ha: HA, snap: dict):
    devs = {d["id"]: d for d in await ha.call("config/device_registry/list")}
    areas = {a["area_id"]: a["name"] for a in await ha.call("config/area_registry/list")}
    for key, d in DEVICES.items():
        reg = devs.get(d["device_id"])
        if not reg:
            raise Refused(f"device {key} ({d['device_id']}) not in HA's device registry")
        if areas.get(reg.get("area_id")) != d["area"]:
            raise Refused(f"device {key} is in area {areas.get(reg.get('area_id'))!r}, table says {d['area']!r}")
        if d["player"] not in snap:
            raise Refused(f"player {d['player']} for {key} not found in HA states")


def require_text_logs(target: dict) -> None:
    """The replay finds each row's request in the orchestrator's event log by
    its text (kronk_evidence). Since sprint M12 that text is logged only
    with TELEMETRY_RECORD_TEXT=true, which docker-compose.yml sets on the
    dev pair. Refuse, rather than report every row as unmatched."""
    for c in (target["orchestrator"], target["tool_service"]):
        env = docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", c).splitlines()
        # the services read the value with .lower() == "true"; the last
        # assignment wins, as in a process environment
        vals = [line.split("=", 1)[1] for line in env if line.startswith("TELEMETRY_RECORD_TEXT=")]
        if not vals or vals[-1].lower() != "true":
            raise Refused(f"{c} has TELEMETRY_RECORD_TEXT off; the replay matches requests by the "
                          "text in its event log (docker-compose.yml sets it on for the dev pair)")


def select_rows(rows: list[dict], args) -> tuple[list[dict], int]:
    sel = rows
    if args.id:
        unknown = set(args.id) - {r["id"] for r in rows}
        if unknown:
            raise Refused(f"unknown row id(s): {sorted(unknown)}")
        sel = [r for r in sel if r["id"] in set(args.id)]
    if args.tag:
        sel = [r for r in sel if set(args.tag) & set(r["tags"])]
    return sel, len(rows) - len(sel)


async def replay(args) -> int:
    token = ha_token()
    rows = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    selected, not_selected = select_rows(rows, args)
    started = dt.datetime.now(dt.timezone.utc)
    result = {"meta": {"started": started.isoformat(timespec="seconds"), "label": args.label,
                       "check_only": args.check_only,
                       "git_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                                    capture_output=True, text=True).stdout.strip(),
                       "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest()[:16],
                       "corpus_rows": len(rows), "selected": len(selected), "not_selected": not_selected,
                       "selection": {"id": args.id, "tag": args.tag}},
              "rows": []}
    exit_code = 0
    async with HA(token) as ha:
        target = await resolve_target(ha, token, args)
        ts = require_dry_run(target)
        require_text_logs(target)
        result["meta"].update(target=target, tool_service_health=ts)
        before = player_snapshot(token)
        await verify_devices(ha, before)
        tmap = trigger_map()
        result["meta"]["trigger_templates"] = len(tmap)
        print(f"voice_replay: pipeline {target['pipeline']} → {target['kronk_url']}, "
              f"dry-run on, {len(selected)} rows ({not_selected} not selected)")
        last = before
        try:
            for row in selected:
                rec = await replay_row(ha, token, target, row, tmap, args, before)
                result["rows"].append(rec)
                mark = {"pass": ".", "fail": "F", "xfail": "x", "xpass": "X", "skip": "s"}[rec["status"]]
                print(f"  {mark} {rec['status']:5s} {rec['method']:10s} {row['id']}"
                      + (f"  [{'; '.join(c['check'] + ' ' + str(c['actual']) for c in rec['checks'] if not c['ok'])}]"
                         if rec["status"] in ("fail", "xpass") else ""))
                if rec["method"] == "executed":
                    last = player_snapshot(token)
                    changed = snapshot_diff(before, last)
                    if changed:
                        rec["player_change"] = changed
                        raise Stopped(f"media_player state changed after {row['id']}: {changed}")
        except Stopped as e:
            result["meta"]["stopped"] = str(e)
            print(f"voice_replay: STOPPED: {e}")
            exit_code = 3
        after = player_snapshot(token)
    changed = snapshot_diff(before, after)
    result["players"] = {"before": before, "after": after, "changed": changed,
                         "unchanged": not changed, "count": len(before)}
    counts = {}
    for r in result["rows"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    result["summary"] = {"counts": counts,
                         "executed": sum(1 for r in result["rows"] if r["method"] == "executed"),
                         "checked_read_only": sum(1 for r in result["rows"] if r["method"] == "checked"),
                         "skipped": [{"id": r["id"], "reason": r["skip_reason"]}
                                     for r in result["rows"] if r["status"] == "skip"],
                         "not_selected": not_selected,
                         "failed": [r["id"] for r in result["rows"] if r["status"] == "fail"],
                         "xpassed": [r["id"] for r in result["rows"] if r["status"] == "xpass"]}
    result["meta"]["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = Path(args.out) if args.out else (
        ROOT / "docs" / "bench" / f"voice_replay_{started:%Y-%m-%d_%H%M%S}_{args.label}.json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(f"voice_replay: {counts}; executed {result['summary']['executed']}, "
          f"checked read-only {result['summary']['checked_read_only']}; players "
          f"{'unchanged' if not changed else 'CHANGED: ' + str(changed)} ({len(before)} checked)")
    if result["summary"]["skipped"]:
        for s in result["summary"]["skipped"]:
            print(f"  skipped {s['id']}: {s['reason']}")
    print(f"voice_replay: wrote {out}")
    if changed and exit_code == 0:
        exit_code = 3
    if exit_code == 0 and result["summary"]["failed"]:
        exit_code = 1
    return exit_code


async def replay_row(ha: HA, token: str, target: dict, row: dict, tmap: dict, args, baseline: dict) -> dict:
    dkey = row["origin"].get("device") or args.device
    device = DEVICES[dkey]
    rec = {"id": row["id"], "text": row["text"], "device": dkey, "expected": row["expect"],
           "xfail": row.get("xfail"), "checks": [], "latency_s": None,
           "trace_id": None, "pipeline_run_id": None, "conversation_id": None}
    for tag, why in SKIP_TAGS.items():
        if tag in row["tags"]:
            return {**rec, "method": "skipped", "status": "skip", "skip_reason": why}
    if target.get("production"):
        for tag, why in PRODUCTION_SKIP_TAGS.items():
            if tag in row["tags"]:
                return {**rec, "method": "skipped", "status": "skip", "skip_reason": why}

    t0 = time.monotonic()
    res = await ha.call("conversation/agent/homeassistant/debug", sentences=[row["text"]],
                        language="en", device_id=device["device_id"])
    dbg = classify_debug(res["results"][0], tmap)
    rec["ha_debug"] = dbg
    rec["check_ms"] = round((time.monotonic() - t0) * 1000)

    if dbg["local"]:
        rec["method"] = "checked"
        rec["checks"] = compare_local(row, dbg)
        rec["status"], rec["xfail_reason"] = status_of(row, rec["checks"])
        return rec
    if args.check_only:
        return {**rec, "method": "skipped", "status": "skip",
                "skip_reason": f"--check-only (would reach Kronk: {dbg['summary']})"}

    # About to execute: the gates, every time.
    try:
        require_dry_run(target)
    except Refused as e:
        raise Stopped(str(e))
    now = player_snapshot(token)
    if snapshot_diff(baseline, now):
        raise Stopped(f"media_player state changed before {row['id']}: {snapshot_diff(baseline, now)}")
    if dbg.get("fuzzy") and any(v["state"] in ACTIVE_STATES for v in now.values()):
        return {**rec, "method": "skipped", "status": "skip",
                "skip_reason": f"fuzzy local match ({dbg['intent']}) while a player is active; not executed"}

    t_start = time.time()
    t0 = time.monotonic()
    events = await ha.run_intent(row["text"], target["pipeline_id"], device["device_id"], args.timeout)
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    rec["method"] = "executed"
    start = next((e for e in events if e["type"] == "run-start"), {})
    end = next((e for e in events if e["type"] == "intent-end"), None)
    err = next((e for e in events if e["type"] == "error"), None)
    rec["conversation_id"] = (start.get("data") or {}).get("conversation_id")
    processed_locally = bool(end and end["data"].get("processed_locally"))
    speech = ((((end or {}).get("data") or {}).get("intent_output") or {}).get("response") or {}) \
        .get("speech", {}).get("plain", {}).get("speech")
    rec["speech"] = (speech or "")[:400]
    if err:
        rec["pipeline_error"] = err.get("data")
    kev = kronk_evidence(target["orchestrator"], target["tool_service"], t_start, row["text"])
    rec["kronk"] = kev
    rec["trace_id"] = kev["trace_id"]
    try:
        runs = (await ha.call("assist_pipeline/pipeline_debug/list",
                              pipeline_id=target["pipeline_id"]))["pipeline_runs"]
        ts0 = start.get("timestamp", "")
        cands = [r for r in runs if r.get("timestamp", "") >= ts0[:19]]
        if cands:
            rec["pipeline_run_id"] = sorted(cands, key=lambda r: r["timestamp"])[0]["pipeline_run_id"]
    except Exception as e:  # informational only
        rec["pipeline_run_id_error"] = str(e)[:200]
    rec["checks"] = compare_kronk(row, device, processed_locally, kev)
    rec["status"], rec["xfail_reason"] = status_of(row, rec["checks"])
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pipeline", default="kronk-dev", help="HA pipeline name or id (default kronk-dev)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit the production pipeline; dry-run is still required")
    ap.add_argument("--device", default=DEFAULT_DEVICE, choices=sorted(DEVICES),
                    help="satellite for rows with no recorded origin (default office_sat1)")
    ap.add_argument("--id", action="append", help="replay only this row id (repeatable)")
    ap.add_argument("--tag", action="append", help="replay only rows with this tag (repeatable)")
    ap.add_argument("--check-only", action="store_true", help="run the read-only checks; execute nothing")
    ap.add_argument("--label", default="replay", help="label in the result file name")
    ap.add_argument("--out", help="result file (default docs/bench/voice_replay_<time>_<label>.json)")
    ap.add_argument("--timeout", type=float, default=60, help="per-row pipeline timeout, seconds")
    ap.add_argument("--orchestrator-container")
    ap.add_argument("--tool-service-container")
    ap.add_argument("--answers", action="store_true",
                    help="M11 answer check: POST /music/say and /music on the dev tool_service; no HA run")
    ap.add_argument("--tool-service-url", default=DEV_TOOL_SERVICE, help="dev tool_service for --answers")
    args = ap.parse_args()
    try:
        if args.answers:
            return answers(args)
        return asyncio.run(replay(args))
    except Refused as e:
        print(f"voice_replay: REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
