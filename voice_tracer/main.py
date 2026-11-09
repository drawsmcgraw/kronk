"""voice_tracer — turns Home Assistant's voice-pipeline and blueprint runs
into OpenTelemetry traces (Telemetry v2, docs/plans/TELEMETRY_V2_PLAN.md).

HA emits no telemetry and we do not change HA (plan R5). This service
reads what HA already keeps, over its websocket with the existing
long-lived token, read-only:

  subscribe_events state_changed      → when an assist_satellite returns
                                        to idle, or the music blueprint's
                                        automation finishes, fetch:
  assist_pipeline/pipeline_debug/list+get → the voice run (STT, intent, TTS)
  trace/list + trace/get              → the blueprint run (play_media, wait,
                                        hand-off to Kronk)

Each run becomes one trace (convert.py). Runs that Kronk handled are
joined to Kronk's own trace by the satellite's device id and the intent
time window (Tempo search API): a span link plus `kronk.trace_id` on the
voice.intent span. A blueprint run that started inside a voice run's
intent window is linked to it the same way.

Timing: Tempo makes a trace searchable once the live-store cuts its block
(~30–90 s after ingest), so the join is attempted at increasing delays and
the voice trace is emitted once, when the join resolves or gives up.
Expect voice traces in Grafana about two minutes after the utterance.

Trace and span ids derive from HA's run ids, so re-emitting a run (after a
restart, or the periodic backstop poll) is idempotent in Tempo. The set of
emitted run ids is also kept in /data/voice_tracer_state.json.

Env:
  HA_URL, HA_TOKEN             Home Assistant (host networking → host.docker.internal)
  TEMPO_URL                    http://tempo:3200 (search API for the join)
  TELEMETRY_ENABLED            "false" → connect and log, export nothing
  TELEMETRY_RECORD_TEXT        "true" → transcripts/answers on spans
  OTEL_EXPORTER_OTLP_ENDPOINT  http://tempo:4318
  VOICE_TRACER_AUTOMATIONS     comma-separated automation entity ids to trace
  VOICE_TRACER_POLL_S          backstop poll interval (default 60)
  VOICE_TRACER_STATE           state file (default /data/voice_tracer_state.json)
  VOICE_TRACER_HEALTH_PORT     /health listener (default 8010)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode

import httpx
import websockets

try:
    from . import convert
except ImportError:
    import convert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("voice_tracer")
logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per Tempo query otherwise

HA_URL = os.getenv("HA_URL", "http://host.docker.internal:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:3200").rstrip("/")
ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"
RECORD_TEXT = os.getenv("TELEMETRY_RECORD_TEXT", "false").lower() == "true"
AUTOMATIONS = [a.strip() for a in os.getenv(
    "VOICE_TRACER_AUTOMATIONS", "automation.kronk_music_assistant_voice_device_first").split(",") if a.strip()]
POLL_S = float(os.getenv("VOICE_TRACER_POLL_S", "60"))
STATE_FILE = os.getenv("VOICE_TRACER_STATE", "/data/voice_tracer_state.json")
HEALTH_PORT = int(os.getenv("VOICE_TRACER_HEALTH_PORT", "8010"))
# Join attempts after a run ends: Tempo search lags ingest by one live-store
# block cut (max_block_duration 30 s) plus the orchestrator's export delay.
JOIN_DELAYS_S = [15, 30, 45, 60]
BACKFILL_S = 600         # at start, emit runs from the last 10 min not yet seen —
                         # older ones would land outside Tempo's ingestion slack
                         # (telemetry/tempo.yaml) and be invisible to ranged search
SEEN_KEEP = 2000

HEALTH = {"connected": False, "last_event": None, "emitted": 0, "joined": 0,
          "ambiguous": 0, "errors": 0, "started": time.time()}


# ── Emitter: SpanSpec trees → OTel spans with explicit timestamps ───────────

class Emitter:
    def __init__(self, exporter=None, batch: bool = True):
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.trace.id_generator import IdGenerator

        class _Seeded(IdGenerator):
            """Ids come from convert.assign_ids (sha256 of HA's run id), set
            on the generator right before each start_span."""
            next_trace_id = 1
            next_span_id = 1

            def generate_trace_id(self) -> int:
                return self.next_trace_id

            def generate_span_id(self) -> int:
                return self.next_span_id

        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter()
        self._ids = _Seeded()
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "kronk-voice-tracer")}),
            id_generator=self._ids,
        )
        self._provider.add_span_processor(
            BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter))
        self._trace = trace
        self._tracer = self._provider.get_tracer("kronk.voice_tracer")

    def emit(self, specs: list[convert.SpanSpec]) -> None:
        """Emit a tree whose ids were assigned by convert.assign_ids."""
        if not specs:
            return
        trace = self._trace
        by_key: dict[str, object] = {}
        # parents first: the root has parent None, children reference keys
        order = sorted(specs, key=lambda s: (s.parent is not None, s.start_ns))
        for s in order:
            parent_ctx = None
            if s.parent is not None and s.parent in by_key:
                parent_ctx = trace.set_span_in_context(by_key[s.parent])
            links = []
            for tid_hex in s.links:
                try:
                    links.append(trace.Link(trace.SpanContext(
                        trace_id=int(tid_hex, 16), span_id=1, is_remote=True,
                        trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED))))
                except ValueError:
                    pass
            self._ids.next_trace_id = s.trace_id
            self._ids.next_span_id = s.span_id
            span = self._tracer.start_span(s.name, context=parent_ctx, start_time=s.start_ns,
                                           links=links, attributes=_otel_attrs(s.attrs))
            if s.status == convert.STATUS_ERROR:
                span.set_status(trace.Status(trace.StatusCode.ERROR, s.status_message))
            span.end(end_time=max(s.end_ns, s.start_ns))
            by_key[s.key] = span

    def flush(self, timeout_ms: int = 5000) -> None:
        self._provider.force_flush(timeout_ms)


def _otel_attrs(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = [x if isinstance(x, str) else json.dumps(x, default=str) for x in v]
        elif isinstance(v, (str, bool, int, float)):
            out[k] = v
        else:
            out[k] = json.dumps(v, default=str)[:500]
    return out


# ── Tempo join ──────────────────────────────────────────────────────────────

async def find_kronk_traces(client: httpx.AsyncClient, device_id: str, start_s: float, end_s: float,
                            slack_s: float = 5.0) -> list[str]:
    """Trace ids of Kronk pipeline roots for this satellite device that
    started inside the intent window (± slack)."""
    q = ('{ resource.service.name = "kronk-orchestrator" && name =~ "pipeline\\\\..*" '
         f'&& span.kronk.origin.device_id = "{device_id}" }}')
    params = {"q": q, "start": int(start_s - slack_s), "end": int(end_s + slack_s) + 1, "limit": 10}
    r = await client.get(f"{TEMPO_URL}/api/search", params=params, timeout=20)
    r.raise_for_status()
    out = []
    for t in r.json().get("traces") or []:
        t0 = int(t.get("startTimeUnixNano", 0)) / 1e9
        if start_s - slack_s <= t0 <= end_s + slack_s:
            # Tempo drops leading zeros from ids in search results.
            out.append(t["traceID"].lower().zfill(32))
    return out


# ── HA websocket client (read-only) ─────────────────────────────────────────

class HAClient:
    def __init__(self, url: str, token: str):
        self._ws_url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/websocket"
        self._token = token
        self._ws = None
        self._n = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._ws_url, open_timeout=20, max_size=None)
        hello = json.loads(await self._ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected hello {hello.get('type')}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        auth = json.loads(await self._ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the token")

    async def call(self, **msg) -> dict:
        self._n += 1
        mid = self._n
        msg["id"] = mid
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, 30)
        finally:
            self._pending.pop(mid, None)

    async def result(self, **msg):
        m = await self.call(**msg)
        if not m.get("success", False):
            raise RuntimeError(f"HA {msg.get('type')} failed: {m.get('error')}")
        return m.get("result")

    async def reader(self) -> None:
        """Dispatch replies to callers and events to the queue."""
        async for raw in self._ws:
            m = json.loads(raw)
            if m.get("type") == "event":
                await self.events.put(m.get("event") or {})
                continue
            fut = self._pending.get(m.get("id"))
            if fut and not fut.done():
                fut.set_result(m)

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()


# ── The tracer ──────────────────────────────────────────────────────────────

class VoiceTracer:
    def __init__(self, emitter: Emitter | None):
        self.emitter = emitter
        self.seen: set[str] = set()
        self.seen_order: list[str] = []
        self.pipelines: dict[str, str] = {}      # id → name
        self.pending_voice: dict[str, dict] = {}  # run_id → {"specs", "ends_at", "attempt"}
        self.recent_bp: dict[str, list[convert.SpanSpec]] = {}   # run_id → specs (for joins)
        self.recent_voice: dict[str, list[convert.SpanSpec]] = {}
        self._load_state()

    # state
    def _load_state(self) -> None:
        try:
            with open(STATE_FILE) as f:
                ids = json.load(f).get("seen") or []
            self.seen, self.seen_order = set(ids), list(ids)
            logger.info("state: %d run ids loaded from %s", len(ids), STATE_FILE)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("state: could not load %s: %s", STATE_FILE, e)

    def _mark_seen(self, key: str) -> None:
        if key in self.seen:
            return
        self.seen.add(key)
        self.seen_order.append(key)
        if len(self.seen_order) > SEEN_KEEP:
            for old in self.seen_order[:-SEEN_KEEP]:
                self.seen.discard(old)
            self.seen_order = self.seen_order[-SEEN_KEEP:]
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"seen": self.seen_order}, f)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.debug("state: could not save: %s", e)

    # fetching
    async def refresh_pipelines(self, ha: HAClient) -> None:
        res = await ha.result(type="assist_pipeline/pipeline/list")
        self.pipelines = {p["id"]: p.get("name", p["id"]) for p in res.get("pipelines", [])}

    async def fetch_voice_runs(self, ha: HAClient, since_s: float) -> None:
        for pid, pname in list(self.pipelines.items()):
            try:
                lst = await ha.result(type="assist_pipeline/pipeline_debug/list", pipeline_id=pid)
            except Exception as e:
                logger.warning("pipeline_debug/list %s failed: %s", pname, e)
                continue
            for r in lst.get("pipeline_runs") or []:
                rid = r.get("pipeline_run_id")
                key = f"pipeline:{rid}"
                if not rid or key in self.seen or rid in self.pending_voice:
                    continue
                if convert._ns(r.get("timestamp")) / 1e9 < since_s:
                    continue
                try:
                    g = await ha.result(type="assist_pipeline/pipeline_debug/get",
                                        pipeline_id=pid, pipeline_run_id=rid)
                except Exception as e:
                    logger.warning("pipeline_debug/get %s failed: %s", rid, e)
                    continue
                specs = convert.pipeline_run_spans(g, rid, pname, record_text=RECORD_TEXT)
                if not specs:
                    self._mark_seen(key)
                    continue
                if not any(e.get("type") == "run-end" for e in g.get("events") or []) \
                        and time.time() - specs[0].end_ns / 1e9 < 120:
                    continue   # still running; pick it up next time
                convert.assign_ids("pipeline", rid, specs)
                self.pending_voice[rid] = {"specs": specs, "ends_at": specs[0].end_ns / 1e9,
                                           "attempt": 0, "key": key}
                logger.info("voice run %s (%s): %s, handled_by=%s", rid, pname,
                            specs[0].attrs.get("voice.stages"), specs[0].attrs.get("voice.handled_by"))

    async def fetch_blueprint_runs(self, ha: HAClient, since_s: float) -> None:
        for entity in AUTOMATIONS:
            item_id = self._automation_ids.get(entity)
            if not item_id:
                continue
            try:
                runs = await ha.result(type="trace/list", domain="automation", item_id=item_id)
            except Exception as e:
                logger.warning("trace/list %s failed: %s", item_id, e)
                continue
            for r in runs or []:
                rid = r.get("run_id")
                key = f"automation:{rid}"
                if not rid or key in self.seen:
                    continue
                ts = (r.get("timestamp") or {})
                if not ts.get("finish"):
                    continue   # still running
                if convert._ns(ts.get("start")) / 1e9 < since_s:
                    continue
                try:
                    tr = await ha.result(type="trace/get", domain="automation", item_id=item_id, run_id=rid)
                except Exception as e:
                    logger.warning("trace/get %s failed: %s", rid, e)
                    continue
                specs = convert.automation_trace_spans(tr, record_text=RECORD_TEXT)
                if not specs:
                    self._mark_seen(key)
                    continue
                convert.assign_ids("automation", rid, specs)
                # join to a voice run already fetched (pending or emitted)
                for v in list(self.pending_voice.values()):
                    if convert.join_blueprint(v["specs"], specs):
                        break
                else:
                    for v_specs in self.recent_voice.values():
                        if convert.join_blueprint(v_specs, specs):
                            break
                self.recent_bp[rid] = specs
                self._emit(specs, key)
                logger.info("blueprint run %s: trigger=%s outcome=%s %.2fs", rid,
                            specs[0].attrs.get("bp.trigger_id"), specs[0].attrs.get("bp.outcome"),
                            (specs[0].end_ns - specs[0].start_ns) / 1e9)
        # prune
        for d in (self.recent_bp, self.recent_voice):
            for k in list(d)[:-50]:
                d.pop(k, None)

    async def resolve_joins(self, http: httpx.AsyncClient) -> None:
        """Try the Kronk join for pending voice runs whose delay has elapsed;
        emit when resolved or out of attempts."""
        now = time.time()
        for rid, p in list(self.pending_voice.items()):
            specs = p["specs"]
            attempt = p["attempt"]
            # A blueprint run inside this intent window decides who answered
            # (blueprint, or blueprint_then_kronk for the catch-all hand-off)
            # — before deciding whether a Kronk trace must be looked up.
            for bp_specs in self.recent_bp.values():
                if convert.join_blueprint(specs, bp_specs):
                    break
            handled = specs[0].attrs.get("voice.handled_by")
            needs_join = handled in ("kronk", "blueprint_then_kronk", "unknown")
            if not needs_join:
                self._finish_voice(rid, p, [])
                continue
            if attempt >= len(JOIN_DELAYS_S):
                self._finish_voice(rid, p, [])
                continue
            if now - p["ends_at"] < JOIN_DELAYS_S[attempt]:
                continue
            p["attempt"] = attempt + 1
            win = convert.intent_window(specs)
            if not win or not win[0]:
                specs[0].attrs["kronk.join"] = "no_device_id"
                self._finish_voice(rid, p, [])
                continue
            dev, s, e = win
            try:
                ids = await find_kronk_traces(http, dev, s, e)
            except Exception as ex:
                logger.warning("tempo search failed (attempt %d): %s", p["attempt"], ex)
                ids = []
            if ids:
                self._finish_voice(rid, p, ids)

    def _finish_voice(self, rid: str, p: dict, kronk_ids: list[str]) -> None:
        specs = p["specs"]
        if specs[0].attrs.get("voice.handled_by") in ("kronk", "blueprint_then_kronk", "unknown"):
            convert.apply_kronk_join(specs, kronk_ids)
            if kronk_ids:
                HEALTH["joined"] += 1
            if len(kronk_ids) > 1:
                HEALTH["ambiguous"] += 1
                logger.warning("voice run %s matched %d Kronk traces: %s", rid, len(kronk_ids), kronk_ids)
            if not kronk_ids:
                specs[0].attrs.setdefault("kronk.join", "none")
        # a blueprint run that arrived before this voice run was fetched
        for bp_specs in self.recent_bp.values():
            if convert.join_blueprint(specs, bp_specs):
                break
        self.recent_voice[rid] = specs
        self.pending_voice.pop(rid, None)
        self._emit(specs, p["key"])
        logger.info("voice run %s emitted: handled_by=%s kronk=%s trace=%032x", rid,
                    specs[0].attrs.get("voice.handled_by"), kronk_ids or "-", specs[0].trace_id)

    def _emit(self, specs: list[convert.SpanSpec], key: str) -> None:
        if self.emitter is not None:
            try:
                self.emitter.emit(specs)
            except Exception as e:
                HEALTH["errors"] += 1
                logger.error("emit failed for %s: %s", key, e)
        HEALTH["emitted"] += 1
        self._mark_seen(key)

    _automation_ids: dict[str, str] = {}

    async def refresh_automation_ids(self, ha: HAClient) -> None:
        """automation entity → its `id` attribute (what trace/list wants)."""
        states = await ha.result(type="get_states")
        for st in states:
            if st.get("entity_id") in AUTOMATIONS:
                aid = (st.get("attributes") or {}).get("id")
                if aid:
                    self._automation_ids[st["entity_id"]] = aid
        missing = [a for a in AUTOMATIONS if a not in self._automation_ids]
        if missing:
            logger.warning("automation(s) not found in HA: %s", missing)

    # main loop
    async def run_once(self) -> None:
        ha = HAClient(HA_URL, HA_TOKEN)
        await ha.connect()
        HEALTH["connected"] = True
        reader = asyncio.create_task(ha.reader())
        try:
            await self.refresh_pipelines(ha)
            await self.refresh_automation_ids(ha)
            await ha.result(type="subscribe_events", event_type="state_changed")
            logger.info("connected to HA; pipelines=%s automations=%s",
                        list(self.pipelines.values()), list(self._automation_ids))
            since = time.time() - BACKFILL_S
            await self.fetch_voice_runs(ha, since)
            await self.fetch_blueprint_runs(ha, since)
            fetch_voice_at: float | None = None
            fetch_bp_at: float | None = None
            last_poll = time.time()
            async with httpx.AsyncClient() as http:
                while True:
                    try:
                        ev = await asyncio.wait_for(ha.events.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        ev = None
                    if reader.done():
                        raise RuntimeError("HA websocket closed")
                    now = time.time()
                    if ev:
                        HEALTH["last_event"] = now
                        d = ev.get("data") or {}
                        eid = d.get("entity_id", "")
                        new = d.get("new_state") or {}
                        old = d.get("old_state") or {}
                        if eid.startswith("assist_satellite.") and new.get("state") == "idle" \
                                and old.get("state") not in (None, "idle", "unavailable"):
                            fetch_voice_at = now + 2.0
                        elif eid in self._automation_ids:
                            cur_new = (new.get("attributes") or {}).get("current", 0)
                            if (new.get("attributes") or {}).get("last_triggered") != (old.get("attributes") or {}).get("last_triggered") \
                                    or cur_new == 0:
                                fetch_bp_at = now + 1.0
                    if fetch_voice_at and now >= fetch_voice_at:
                        fetch_voice_at = None
                        await self.fetch_voice_runs(ha, now - 600)
                    if fetch_bp_at and now >= fetch_bp_at:
                        fetch_bp_at = None
                        await self.fetch_blueprint_runs(ha, now - 600)
                    if now - last_poll >= POLL_S:
                        last_poll = now
                        await self.refresh_pipelines(ha)
                        await self.fetch_voice_runs(ha, now - 600)
                        await self.fetch_blueprint_runs(ha, now - 600)
                    if self.pending_voice:
                        await self.resolve_joins(http)
        finally:
            HEALTH["connected"] = False
            reader.cancel()
            await ha.close()


# ── /health ─────────────────────────────────────────────────────────────────

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({**HEALTH, "enabled": ENABLED}).encode()
        self.send_response(200 if HEALTH["connected"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve_health() -> None:
    HTTPServer(("0.0.0.0", HEALTH_PORT), _Health).serve_forever()


async def main() -> None:
    if not HA_TOKEN:
        logger.error("HA_TOKEN not set — nothing to read; exiting")
        return
    threading.Thread(target=_serve_health, daemon=True).start()
    emitter = None
    if ENABLED:
        try:
            emitter = Emitter()
            logger.info("exporting to %s", os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "(default)"))
        except Exception as e:
            logger.error("OpenTelemetry init failed — running without export: %s", e)
    else:
        logger.info("TELEMETRY_ENABLED=false — reading HA, exporting nothing")
    tracer = VoiceTracer(emitter)
    backoff = 2
    while True:
        try:
            await tracer.run_once()
            backoff = 2
        except Exception as e:
            HEALTH["errors"] += 1
            logger.warning("HA connection lost (%s: %s); retrying in %ds", type(e).__name__, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
