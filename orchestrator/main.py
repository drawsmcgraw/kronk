import asyncio
import io
import json
import logging
import os
import time
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

import agents
import client_watch
import config_api
import config_loader
import errors
import render
import metrics
import model_lock
import origin
import routing
import run_state
import servers
import sessions
import settings_api
import upgrades_api
import backups_api
import work_api
import voice_runs_api
import speech_stream
import telemetry
import tools
from events import current_request_id, emit, new_request_id
from llm import LLM_SERVICE_URL
from tools import TOOL_DEFINITIONS  # re-exported for tests/backward-compat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _log_config_status() -> None:
    """config/ was loaded when agents was imported, before logging was set
    up above; say the outcome again where the log will keep it."""
    st = config_loader.status()
    if st["ok"]:
        logger.info("config: %s from %s (%d files)", st["source"], st["dir"], len(st["files"]))

    def where(e):
        return f"{e['file']}:{e['line']}" if e.get("line") else e["file"]
    for e in st.get("errors", []):
        logger.error("config at startup rejected: %s: %s (running on built-in defaults)",
                     where(e), e["message"])
    for w in st.get("warnings", []):
        logger.warning("config at startup: %s: %s", where(w), w["message"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_config_status()
    metrics.init_db()
    sessions.init_db()
    pruned = sessions.prune_idle()
    if pruned:
        logger.info("sessions: pruned %d idle messages at startup", pruned)
    yield


app = FastAPI(title="Kronk Orchestrator", lifespan=lifespan)
telemetry.instrument_app(app)   # server spans + httpx traceparent; no-op when off
# A chat request whose client disconnects is cancelled (sprint M9 step 4).
app.add_middleware(client_watch.ClientWatch)
# Reload isolation (sprint M21 step 2): each request keeps the config it
# started with. Added last, so it is the outermost middleware and the pin is
# set before ClientWatch starts the app's task (tasks copy the context).
app.add_middleware(config_loader.PinConfig)

COORDINATOR_MODEL = agents.COORDINATOR_MODEL
STATE_FILE        = Path("/data/kronk_state.md")

# NOTE (2026-06-12): the coordinator persona moved to agents.COORDINATOR as
# part of agents-as-tools — its old "say so plainly" instruction conflicted
# with "delegate when you need live data". The SYSTEM_PROMPT env var is no
# longer read; persona changes happen in agents.py now.

# Conversation history lives in sessions.py (SQLite-backed, per-client).
# The web UI is a single fixed session; voice clients carry their own history
# in each request (HA resends the conversation) and never touch the store.
WEBUI_SESSION = "webui"

# Uploaded file contexts — injected as system messages on every request
file_contexts: list[dict] = []

# The pipeline-wide lock, used only with MODEL_LOCK=pipeline (rollback):
# one request's model phases at a time. By default each model server has
# its own lock, held for one generation inside llm.stream (sprint M9 step 2;
# model_lock.py). Each llama.cpp server runs 4 slots with the full context
# each, so neither lock is a server limit; they are a scheduling choice.
_llm_lock = asyncio.Lock()

TOKEN_WARNING_THRESHOLD = 2000


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _load_state() -> str | None:
    try:
        if STATE_FILE.exists():
            content = STATE_FILE.read_text().strip()
            return content if content else None
    except Exception as e:
        logger.warning("Could not read state file: %s", e)
    return None


# Backward-compat alias for tests that mock orchestrator.main._execute_tool:
# the result's text (tools.execute also says whether it failed).
async def _execute_tool(name: str, args: dict) -> str:
    return (await tools.execute(name, args)).text


# ── HTML routes ──────────────────────────────────────────────────────────────

def _serve(path: str) -> str:
    with open(f"/app/static/{path}") as f:
        return f.read()


@app.get("/",            response_class=HTMLResponse)
async def root():          return _serve("index.html")
@app.get("/services",    response_class=HTMLResponse)
async def services_page(): return _serve("services.html")
@app.get("/agents",      response_class=HTMLResponse)
async def agents_page():   return _serve("agents.html")
@app.get("/resources",   response_class=HTMLResponse)
async def resources_page():return _serve("resources.html")
@app.get("/performance", response_class=HTMLResponse)
async def performance_page(): return _serve("performance.html")
@app.get("/solar",       response_class=HTMLResponse)
async def solar_page():    return _serve("solar.html")
@app.get("/work",        response_class=HTMLResponse)
async def work_page():     return _serve("work.html")


# ── API: metrics / system / status / agents ──────────────────────────────────

@app.get("/api/metrics")
async def get_metrics():
    try:
        return metrics.dashboard_payload()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/system")
async def system_info():
    """The host block of /resources (M25): RAM from /proc/meminfo and the
    GPU's GTT and busy figures from sysfs, both read now; the model files'
    disk figures from llama_props.json, with that file's age."""
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, val = line.partition(":")
            mem[key.strip()] = int(val.split()[0]) * 1024  # kB → bytes
    # GTT (Graphics Translation Table) — the iGPU's memory pool, carved from
    # system RAM. Models loaded into llama.cpp servers live here. This is the
    # pool that ran tight in the 2026-05-31 incident (see docs/INCIDENT_2026-05-31.md).
    gpu = servers.read_gpu()
    props = servers.llama_props_source()
    return {
        "mem_total":     mem.get("MemTotal", 0),
        "mem_available": mem.get("MemAvailable", 0),
        "mem_free":      mem.get("MemFree", 0),
        "swap_total":    mem.get("SwapTotal", 0),
        "swap_free":     mem.get("SwapFree", 0),
        "gtt_total":     gpu["gtt_total"],
        "gtt_used":      gpu["gtt_used"],
        # One instant: the GPU's busy figure at the moment of this request.
        "gpu_busy_percent": gpu["gpu_busy_percent"],
        "gpu_reasons":   gpu["reasons"],
        "read_at":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **servers.disk_block(props),
        "sources": {
            "memory": "/proc/meminfo, read at this request",
            "gpu": (f"/sys/class/drm/{gpu['card']}/device, read at this request; "
                    "gpu_busy_percent is one instant") if gpu["card"] else None,
            "disk": {k: v for k, v in props.items() if k != "data"},
        },
    }


@app.get("/api/agents")
async def agent_roster():
    return {"agents": agents.roster()}


@app.get("/api/servers")
async def server_catalog():
    """Each catalog model server with what the host exporters measured
    (M25; servers.build_servers). Calls no model server and not LiteLLM."""
    return servers.build_servers(servers.load_catalog(), servers.gpu_mem_source(),
                                 servers.llama_props_source(), servers.agents_by_model())


@app.get("/api/activity")
async def model_activity():
    """Calls, prompt and completion tokens per model in the last 24 hours,
    and when each was last used, from this orchestrator's llm_metrics."""
    try:
        return metrics.activity([e["name"] for e in servers.load_catalog()])
    except Exception as e:
        logger.warning("api/activity: %s", e)
        return JSONResponse(status_code=503, content={
            "error": f"{metrics.METRICS_DB} could not be read: {type(e).__name__}: {e}"})


@app.get("/api/status")
async def health_probe():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            llm_h  = await client.get(f"{LLM_SERVICE_URL}/health")
            tool_h = await client.get(f"{tools.TOOL_SERVICE_URL}/health")
            return {"status": "ok", "llm_service": llm_h.json(), "tool_service": tool_h.json()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── config/ (sprint M21 step 1): POST /config/reload, GET /api/config ───────
# In their own router: the reload refusal reads a request header, and main.py
# keeps its no-raw-Request rule (tests/test_voice_signal.py, sprint M2).
app.include_router(config_api.router)
# The /settings page (sprint M21 step 2): its own router, like config_api.
app.include_router(settings_api.router)
# The upgrade cadence's status for /services (sprint M18), read-only.
app.include_router(upgrades_api.router)
# The backup jobs' status for /services (sprint M16), read-only.
app.include_router(backups_api.router)
# The work items' state for /work (work item M27), read-only.
app.include_router(work_api.router)
# The voice runs of the last 90 days for /work's "Heard" section (work item
# M30 step C), read-only: the published copy and its status file.
app.include_router(voice_runs_api.router)


# ── /message: the main chat endpoint ─────────────────────────────────────────

class MessageRequest(BaseModel):
    text: str
    model: str | None = None
    session_id: str | None = None  # defaults to the web UI session


def _clear_history_stream(session_id: str):
    """SSE confirmation for a spoken/typed 'clear my history' request."""
    async def stream():
        sessions.clear(session_id)
        if session_id == WEBUI_SESSION:
            file_contexts.clear()
        emit("history_cleared", session=session_id)
        yield f"data: {json.dumps({'token': 'Done — fresh start.'})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


# ── The transport-agnostic pipeline core ─────────────────────────────────────
#
# One async generator runs route → specialist/coordinator for every
# transport; thin framers adapt its semantic events to SSE (/message),
# OpenAI chunks (/v1/chat/completions), and Ollama NDJSON (/api/chat).
# Yielded events:
#   {"type": "stage",     "name": str, "label": str} — UI progress hints (SSE only)
#   {"type": "narration", "text": str}    — human-readable status line
#   {"type": "token",     "text": str}    — assistant content (incl. errors)
#   {"type": "timing",    "data": dict}   — final timing payload (SSE only)

# ── Stage/timing display text — authored at the emitter ─────────────────────
# The pipeline says what it's doing IN WORDS; the UI just renders. The old
# client-side label tables drifted (the UI still knew a 'fetching_delegate_
# assistant' agent that doesn't exist) and taxed every new tool/agent with
# a second edit in a second file. Generic fallbacks mean additions get a
# serviceable label automatically; polished text is a one-line entry here.

_STAGE_LABELS = {
    "thinking":   "thinking...",
    "waiting":    "waiting for Kronk...",
    "generating": "generating...",
}
_DELEGATE_FLAVOR = {"research": "research agent searching..."}


def _stage(name: str) -> dict:
    if name in _STAGE_LABELS:
        label = _STAGE_LABELS[name]
    elif name.startswith("fetching_delegate_"):
        agent = name[len("fetching_delegate_"):]
        label = _DELEGATE_FLAVOR.get(agent, f"{agent} agent working...")
    elif name.startswith("fetching_"):
        label = f"fetching {name[len('fetching_'):].replace('_', ' ')}..."
    else:
        label = name.replace("_", " ") + "..."
    return {"type": "stage", "name": name, "label": label}


def _timing_stage(tool: str, **fields) -> dict:
    """Timing-breakdown entry with its display label and owning service."""
    if tool == "routing":
        label, service = "routing", "orchestrator"
    elif tool.startswith("delegate_"):
        label, service = f"{tool[len('delegate_'):]} agent", "orchestrator"
    else:
        label, service = tool.replace("_", " "), ""
    return {"tool": tool, "label": label, "service": service, **fields}


async def _run_pipeline(
    text: str,
    history: list[dict],
    *,
    said: str | None,
    voice: bool,
    transport: str,
    pipeline_name: str,
    session_id: str | None = None,        # set → persist the exchange
    include_local_context: bool = False,  # shared state + uploaded files
):
    rid = new_request_id()
    t_request = time.monotonic()
    emit("request", text_preview=telemetry.text(text, 80), model=COORDINATOR_MODEL)
    if (o := origin.current.get()):
        # Findable in one step when music lands in the wrong room (tenet 7).
        emit("origin", device=o.device_id, area=o.area)

    assistant_reply: list[str] = []
    stages: list[dict] = []
    agent_name = "?"  # set by routing; referenced in the finally block
    pipeline_error: str | None = None  # marks the trace ERROR in the finally
    ended: str | None = None  # stopped early: deadline | client_gone | cancelled
    # How failures are RENDERED for this request (debug|friendly), keyed on
    # the speech mount, not the transport. Capture (logs, trace) always
    # keeps full detail — see errors.py.
    error_style = errors.style_for(voice)

    # Direct dispatch (sprint M7): when the routing rule's match fixes the
    # whole tool call, that tool runs with no model call, so the request
    # never waits for the model lock. routing.decide is pure (regex only);
    # routing.classify below records the same decision.
    try:
        decision = routing.decide(text)
        direct, request = decision.direct, decision.request
    except Exception:
        direct = request = None   # classify() below raises it again and reports it

    # The spoken lead-in (sprint M19): "ask Kronk to <request>" goes to the
    # coordinator, and the model is given <request> without the lead-in.
    # origin.said becomes the request too, so tool_service reads the
    # sentence as it would without the lead-in (music_resolve.radio_name()
    # anchors on the request verb). It is still the user's own words: a
    # suffix of this turn's last message, so it holds the pinned mirror
    # sentence exactly when that message does (M4b). When the turn has no
    # words of its own (said is None, see _turn_said) it stays None.
    # History, the trace input and the `request` event keep the utterance.
    model_text = text if request is None else request
    if request is not None and said == text:
        said = request

    # The model lock is taken below, after routing (sprint M9 step 1); the
    # exit stack releases it after the finally block, as before.
    async with AsyncExitStack() as held:
        trace = telemetry.start_pipeline(
            pipeline_name, text, rid=rid, transport=transport,
            origin=origin.current.get(), voice=voice,
        )
        # origin.said is the user's own words for this turn, which may be
        # None while `text` (routing input) is not: see _turn_said.
        said_token = origin.said.set(said)
        # origin.voice: this request came in on the speech mount. Set here,
        # once, from the mount's argument; read anywhere below (sprint M2).
        voice_token = origin.voice.set(voice)
        try:
            # Extra system context for the coordinator run. The persona
            # itself lives in agents.COORDINATOR; these are the dynamic
            # parts: weather block (2026-06-12 incident — misrouted
            # follow-ups must not invent forecasts), and for the web UI
            # also shared state and uploaded-file contexts.
            # A direct call has no model to read them, so it skips them.
            extra_parts: list[str] = []
            if direct is None:
                wx_ctx = await agents.weather_context()
                if wx_ctx:
                    extra_parts.append(wx_ctx)
                if include_local_context:
                    state = _load_state()
                    if state:
                        extra_parts.append(f"[Kronk shared state]\n{state}")
                    for fc in file_contexts:
                        extra_parts.append(f"[Attached file: {fc['name']}]\n{fc['content']}")

            # ── Phase 1: route ────────────────────────────────────────────
            yield _stage("thinking")
            t0 = time.monotonic()
            try:
                agent_name = await routing.classify(text, history)
            except Exception as e:
                logger.error("Router failed (%s): %s", transport, e)
                pipeline_error = f"routing: {e}"
                # No speculative cause here — the old message guessed "is the
                # server still loading?" for ANY failure, misdirecting
                # troubleshooting when the real cause was e.g. a 400.
                err = errors.render("routing", str(e), rid, error_style)
                assistant_reply.append(err)
                yield {"type": "token", "text": err}
                return
            stages.append(_timing_stage("routing", s=round(time.monotonic() - t0, 2)))

            # ── Direct dispatch (sprint M7) ───────────────────────────────
            # The tool and its arguments came from the match. It runs
            # through tools.execute, so every guard inside the handlers
            # still applies (origin targeting, the mirror pin, dry-run), and
            # its result is spoken by the same function the agent path uses
            # for a terminal tool. A failure is spoken and the turn ends:
            # no fall-through to a model, which could repeat a half-applied
            # action. tools.execute never raises; anything else that does
            # is caught by the last-resort guard below.
            if direct is not None:
                async with aclosing(_direct_dispatch(direct, agent_name, error_style,
                                                     stages, t_request)) as dispatch:
                    async for ev in dispatch:
                        if ev["type"] == "token":
                            assistant_reply.append(ev["text"])
                        yield ev
                return

            # ── The model lock (sprint M9) ────────────────────────────────
            # Everything above needs no model: routing, the weather and
            # local context, direct dispatch (step 1). By default nothing is
            # held here: each generation takes its model server's lock in
            # llm.stream (step 2), so tool calls hold no lock. With
            # MODEL_LOCK=pipeline the request's model phases below hold the
            # pipeline-wide lock, as before step 2. Either wait is a
            # `lock.wait` span and a `model_lock` event (model_lock.held).
            if model_lock.mode() == "pipeline":
                await held.enter_async_context(model_lock.held(_llm_lock, "pipeline"))

            # ── Phase 2: specialist agent (if routed to one) ──────────────
            if agent_name in agents.AGENTS:
                agent_cfg = agents.AGENTS[agent_name]
                yield _stage(f"fetching_delegate_{agent_name}")
                yield {"type": "narration", "text": f"let me ask the {agent_name} agent about that"}
                t_agent = time.monotonic()

                agent_first_token_t: float | None = None
                agent_error: str | None = None
                agent_escalation: str | None = None
                agent_model_used = agent_cfg.model
                stage_sent = False
                reply_mark = len(assistant_reply)

                async with aclosing(agents.run_stream(agent_cfg, model_text, list(history[-5:]),
                                                      error_style=error_style,
                                                      allow_escalation=True)) as run:
                    async for ev in run:
                        etype = ev.get("type")
                        if etype == "narration":
                            yield {"type": "narration", "text": ev["text"]}
                        elif etype == "token":
                            if not stage_sent:
                                yield _stage("generating")
                                yield {"type": "narration", "text": ""}
                                stage_sent = True
                            if agent_first_token_t is None:
                                agent_first_token_t = time.monotonic()
                            assistant_reply.append(ev["text"])
                            yield {"type": "token", "text": ev["text"]}
                        elif etype == "retract":
                            _trim_reply(assistant_reply, ev["chars"])
                            yield {"type": "retract", "chars": ev["chars"]}
                        elif etype == "escalated":
                            agent_escalation = ev["note"]
                        elif etype == "error":
                            agent_error = ev["message"]
                        elif etype == "done":
                            agent_model_used = ev.get("model", agent_model_used)

                agent_ok = agent_error is None and agent_escalation is None
                t_end = time.monotonic()
                stages.append(_timing_stage(
                    f"delegate_{agent_name}",
                    s=round(t_end - t_agent, 2),
                    ok=agent_ok,
                ))
                emit("agent_complete", agent=agent_name, ok=agent_ok,
                     escalated=agent_escalation is not None,
                     duration_s=round(t_end - t_agent, 2))

                if agent_ok:
                    ttft = round(agent_first_token_t - t_agent, 2) if agent_first_token_t else 0.0
                    gen_s = round(t_end - (agent_first_token_t or t_agent), 2)
                    yield {"type": "timing", "data": {
                        "model":        agent_model_used,
                        "stages":       stages,
                        "ttft_s":       ttft,
                        "generation_s": gen_s,
                    }}
                    emit("request_complete", route=agent_name,
                         duration_s=round(time.monotonic() - t_request, 2))
                    return

                if agent_escalation is not None:
                    # Phase-2 escalation (COORDINATOR_ROUTING_PLAN): the
                    # specialist handed the request back — part of it is
                    # outside its domain. NOT an error: fall through to the
                    # coordinator with the note. Any partial specialist tokens
                    # are dropped from the reply record (already-streamed SSE
                    # tokens can't be unsent; in practice escalate fires in
                    # round 1 before prose).
                    del assistant_reply[reply_mark:]
                    yield {"type": "narration",
                           "text": f"the {agent_name} agent handed this back — coordinating"}
                    extra_parts.append(
                        f"[The {agent_name} specialist escalated this request back to "
                        f"you with the note: \"{agent_escalation}\". Complete the FULL "
                        f"request: delegate each part to the right ask_* specialist "
                        f"(ask_{agent_name} still answers its own domain), then combine "
                        "the results into one answer.]"
                    )
                else:
                    # Agent errored — fall through to the coordinator, honestly
                    # labeled. The old block called this a "specialist result —
                    # use this to answer": the coordinator was never told it was
                    # a failure and would apologize vaguely or invent an answer,
                    # swallowing detail like "provider may need re-authentication"
                    # (review P1.1). The trace is marked ERROR even if the
                    # coordinator recovers, so the failure stays findable.
                    pipeline_error = f"{agent_name} specialist: {agent_error}"
                    if not agent_cfg.coordinator_fallback:
                        # Asked for by name (talkie, M24): no other model
                        # can answer as this agent, so the failure is the
                        # reply, in the request's error style.
                        msg = errors.render("llm", agent_error, rid, error_style)
                        if len(assistant_reply) > reply_mark:
                            msg = " " + msg
                        assistant_reply.append(msg)
                        yield {"type": "token", "text": msg}
                        emit("request_complete", route=agent_name,
                             duration_s=round(time.monotonic() - t_request, 2))
                        return
                    extra_parts.append(
                        errors.specialist_failed_block(agent_name, agent_error, error_style)
                    )

            # ── Phase 3: coordinator (direct answers, delegation via ask_*,
            #    and specialist-failure fallback). Same run_stream loop as
            #    the agents; telemetry/metrics come from inside it.
            t_llm_start = time.monotonic()
            yield _stage("waiting")

            # The speech mount's spoken-reply line (sprint M8,
            # config/voice.yaml): the answer will be read aloud, so keep it
            # short and plain. Added last, after every other block, so the
            # system text before it (and llama.cpp's cached prefix) is the
            # same as a typed request's. Coordinator only.
            coordinator_extra = list(extra_parts)
            if voice and (spoken := config_loader.current().voice.spoken_reply):
                coordinator_extra.append(spoken)

            first_token = True
            t_first_token: float | None = None
            async with aclosing(agents.run_stream(
                    agents.COORDINATOR,
                    model_text,
                    [],  # no embedded context — history goes in as real messages
                    system_extra="\n\n".join(coordinator_extra) or None,
                    history_messages=history,
                    error_style=error_style,
                )) as run:
                async for ev in run:
                    etype = ev.get("type")
                    if etype == "narration":
                        yield {"type": "narration", "text": ev["text"]}
                    elif etype == "delegating":
                        # Structured delegation signal from the loop → the UI's
                        # stage log (regression fix: the Aug-18 routing collapse
                        # moved delegation inside run_stream, which had no stage
                        # vocabulary, and the "thinking" indicators went dark).
                        yield _stage(f"fetching_delegate_{ev['agent']}")
                    elif etype == "token":
                        if first_token:
                            t_first_token = time.monotonic()
                            yield _stage("generating")
                            yield {"type": "narration", "text": ""}
                            first_token = False
                        assistant_reply.append(ev["text"])
                        yield {"type": "token", "text": ev["text"]}
                    elif etype == "retract":
                        _trim_reply(assistant_reply, ev["chars"])
                        yield {"type": "retract", "chars": ev["chars"]}
                    elif etype == "error":
                        pipeline_error = f"coordinator: {ev['message'][:200]}"
                        msg = errors.render("llm", ev["message"], rid, error_style)
                        assistant_reply.append(msg)
                        yield {"type": "token", "text": msg}

            t_done = time.monotonic()
            timing: dict = {"model": agents.COORDINATOR.model}
            if stages:
                timing["stages"] = stages
            if t_first_token is not None:
                timing["ttft_s"] = round(t_first_token - t_llm_start, 2)
                timing["generation_s"] = round(t_done - t_first_token, 2)
            yield {"type": "timing", "data": timing}
            emit("request_complete", route=agent_name,
                 duration_s=round(time.monotonic() - t_request, 2))

        except (asyncio.CancelledError, GeneratorExit):
            # Stopped from outside (sprint M9 step 4): the voice deadline,
            # the client going away, or the consumer closing the stream.
            # Nothing is retried; a tool call already sent stays sent. The
            # finally below closes the trace (and the exit stack releases
            # any lock) as for any other end.
            ended = _why_stopped()
            telemetry.root().set(**{"kronk.ended": ended})
            if ended == run_state.DEADLINE:
                pipeline_error = "deadline: the request passed its voice deadline"
            emit("request_ended", reason=ended, route=agent_name,
                 tools_sent=list((run_state.current.get() or run_state.Run()).tools_sent),
                 duration_s=round(time.monotonic() - t_request, 2))
            raise

        except Exception as e:
            # Last-resort guard: without it an unexpected raise kills the
            # stream mid-flight — /message never sends [DONE], the Ollama shim
            # never sends done:true, and HA speaks a generic "unexpected
            # error" with nothing in the logs or trace to find it by.
            logger.exception("Pipeline failed (%s, rid=%s)", transport, rid)
            pipeline_error = f"{type(e).__name__}: {e}"
            err = errors.render("pipeline", pipeline_error, rid, error_style)
            assistant_reply.append(err)
            yield {"type": "token", "text": err}

        finally:
            origin.voice.reset(voice_token)
            origin.said.reset(said_token)
            telemetry.end_pipeline(
                trace,
                output="".join(assistant_reply) or None,
                route=agent_name,
                level="ERROR" if pipeline_error else None,
                status_message=pipeline_error,
            )
            # Persist the exchange only when an answer was produced —
            # a failed turn leaves the stored conversation untouched, and
            # so does a turn stopped part way (deadline, client gone).
            if session_id and assistant_reply and ended is None:
                sessions.append_exchange(session_id, [("user", text),
                                                      ("assistant", "".join(assistant_reply))])


def _why_stopped() -> str:
    """Why a pipeline was cancelled or closed: the voice deadline, the
    client disconnecting (client_watch), or neither (`cancelled`)."""
    run = run_state.current.get()
    if run is None:
        return run_state.CANCELLED
    if run.ended:
        return run.ended
    if run.client_gone:
        return run_state.CLIENT_GONE
    if run.deadline_at is not None and asyncio.get_running_loop().time() >= run.deadline_at:
        return run_state.DEADLINE
    return run_state.CANCELLED


async def _with_deadline(events, seconds: float):
    """A speech-mount request's pipeline events, stopped at its deadline
    (sprint M9 step 4; config/voice.yaml deadline_s).

    Events pass through until `seconds` after the request started. Then the
    pipeline is stopped: cancelled at the await it is in (a generation, the
    model lock, a tool call) if it is running, closed if it is between
    events. Its finally blocks close the trace and release any lock; a tool
    call already sent is neither retried nor undone. The last event is
    {"type": "deadline", "text": <one sentence>} (errors.deadline_text),
    which the speech writers send in place of anything not yet sent."""
    run = run_state.ensure()
    loop = asyncio.get_running_loop()
    run.deadline_at = loop.time() + seconds
    stopped = False
    try:
        while True:
            if loop.time() >= run.deadline_at:
                run.ended, stopped = run_state.DEADLINE, True
                break
            try:
                async with asyncio.timeout_at(run.deadline_at):
                    ev = await events.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError:
                run.ended, stopped = run_state.DEADLINE, True
                break
            yield ev
    finally:
        await events.aclose()
    if stopped:
        text = errors.deadline_text(run.tools_sent, tools.READ_ONLY_TOOLS, seconds,
                                    current_request_id(), errors.current_style())
        yield {"type": "deadline", "text": text}


async def _direct_dispatch(call: routing.DirectCall, route: str, error_style: str,
                           stages: list[dict], t_request: float):
    """Run a routing rule's direct call (sprint M7) and speak its result.

    Same events, span and sentence as the home agent's terminal-tool path
    (agents.run_stream), minus the model round: a `tool.<name>` span under
    the pipeline root, `tool_call` / `tool_complete` events (direct=true),
    the sentence from agents._terminal_speech, then the timing payload and
    `request_complete`."""
    name, args = call
    yield {"type": "narration", "text": agents._tool_narration(name, args)}
    t_tool = time.monotonic()
    emit("tool_call", agent=route, tool=name, args=list(args.keys()), direct=True)
    tool_span = telemetry.root().child_span(f"tool.{name}", input=args,
                                            metadata={"direct": True})
    try:
        with tool_span.current():   # tool_service joins via traceparent
            outcome = await tools.execute(name, args)
    except (asyncio.CancelledError, GeneratorExit):
        tool_span.end(status_message="stopped: the request ended (deadline or client gone)")
        raise
    except Exception as e:
        tool_span.end(level="ERROR", status_message=str(e)[:200])
        raise
    tool_span.end(output=outcome.text[:2000])
    tool_s = round(time.monotonic() - t_tool, 2)
    emit("tool_complete", agent=route, tool=name, duration_s=tool_s, direct=True)
    stages.append(_timing_stage(name, s=tool_s, ok=not outcome.failed))

    speech = agents._terminal_speech(outcome.text, error_style, tool=name)
    yield _stage("generating")
    yield {"type": "narration", "text": ""}
    yield {"type": "token", "text": speech}
    yield {"type": "timing", "data": {"model": "none (direct call)", "stages": stages}}
    emit("request_complete", route=route, duration_s=round(time.monotonic() - t_request, 2))


@app.post("/message")
async def message(req: MessageRequest):
    session_id = req.session_id or WEBUI_SESSION

    # Deterministic pre-route intercept: same behavior as the UI's clear
    # button, reachable by voice/text ("clear my history"). Not for a messy
    # request (sprint M14): that one goes to the pipeline.
    if routing.asks_to_clear_history(req.text):
        return _clear_history_stream(session_id)

    async def stream():
        # Capped, boundary-aligned window — prompt order stays append-only
        # so llama.cpp's prompt cache (--swa-full) reuses the prefix.
        history = sessions.window(session_id)
        async with aclosing(_run_pipeline(
                req.text, history, said=req.text, voice=False,
                transport="webui", pipeline_name="pipeline.message",
                session_id=session_id, include_local_context=True,
            )) as events:
            async for ev in events:
                etype = ev["type"]
                if etype == "stage":
                    yield f"data: {json.dumps({'stage': ev['name'], 'stage_label': ev['label']})}\n\n"
                elif etype == "narration":
                    yield f"data: {json.dumps({'narration': ev['text']})}\n\n"
                elif etype == "token":
                    yield f"data: {json.dumps({'token': ev['text']})}\n\n"
                elif etype == "retract":
                    # The UI trims the last N streamed chars from the bubble and
                    # parks them in the stage log (tool-round text, see agents.py).
                    yield f"data: {json.dumps({'retract': ev['chars']})}\n\n"
                elif etype == "timing":
                    yield f"data: {json.dumps({'timing': ev['data']})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/history")
async def get_history():
    return {"history": sessions.window(WEBUI_SESSION)}


# ── External API shims: OpenAI + Ollama ──────────────────────────────────────
#
# Lets external clients reach Kronk's router → specialist → coordinator
# pipeline using whichever envelope they speak natively. Supported:
#   - OpenAI Chat Completions: /v1/chat/completions, /v1/models
#   - Ollama:                   /api/chat, /api/tags, /api/version, /api/show
#
# Design choices:
#   - Stateless server-side: the shims never touch the web UI session or
#     `file_contexts`. Conversation context comes from the CLIENT's message
#     array (HA's Ollama integration resends the whole conversation each
#     call), flowing into the router, agents, and coordinator synthesis.
#   - Client-supplied `model` is ignored — Kronk picks its own model per
#     agent. The shims report COORDINATOR_MODEL in response metadata so
#     strict clients don't choke.
#   - Tools, tool_choice, temperature, etc. are accepted but ignored;
#     Kronk's agents own tool-calling internally.
#
# All three transports share `_run_pipeline()` (extracted 2026-06-12);
# the shims just filter its event stream down to tokens.

class _OpenAIMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None


class _OpenAIChatRequest(BaseModel):
    model: str | None = None
    messages: list[_OpenAIMessage]
    stream: bool = False
    # Tolerated but ignored: temperature, max_tokens, top_p, n, stop,
    # presence_penalty, frequency_penalty, user, response_format, seed,
    # tools, tool_choice, logprobs, stream_options.

    class Config:
        extra = "allow"


def _trim_reply(parts: list[str], chars: int) -> None:
    """Drop the last `chars` characters from a list of streamed token strings
    in place (a retract: tool-round text that must not be stored or spoken)."""
    remaining = chars
    while remaining > 0 and parts:
        last = parts[-1]
        if len(last) <= remaining:
            remaining -= len(last)
            parts.pop()
        else:
            parts[-1] = last[:-remaining]
            remaining = 0


def _turn_said(messages) -> str | None:
    """The user's own words for this turn on a shim request, or None.

    Only the request's LAST message counts, and only when its role is
    exactly "user" and it has content. The shims route on the last
    non-empty user message anywhere in the array (`text`), so for a request
    ending in an empty user message, a tool/assistant message or another
    role, `text` is an earlier turn. That is kept for routing and answers,
    but it is not what the user said now: origin.said gets None, and
    update_magicmirror (which reads origin.said) refuses. Sprint M4b fix 1;
    docs/sprint/M4b-fix1.md.
    """
    last = messages[-1] if messages else None
    if last is not None and last.role == "user" and last.content:
        return last.content
    return None


def _shim_context(messages, current_text: str) -> list[dict]:
    """Prior user/assistant turns from a shim request's message array.

    HA's Ollama integration resends the whole conversation each call
    (verified against HA source 2026-06-12) — honoring it gives voice
    clients real multi-turn memory with no server-side store. System
    messages are dropped (Kronk owns its own persona) and the current user
    message is excluded.
    """
    ctx = [
        {"role": m.role, "content": m.content}
        for m in messages
        if m.role in ("user", "assistant") and m.content
    ]
    if ctx and ctx[-1]["role"] == "user" and ctx[-1]["content"] == current_text:
        ctx = ctx[:-1]
    return ctx


async def _kronk_pipeline_tokens(text: str, model: str, context: list[dict] | None = None,
                                 req_origin: origin.Origin | None = None,
                                 said: str | None = None, voice: bool = False):
    """Shim wrapper around _run_pipeline: token events only.

    context: prior conversation turns supplied by the client (HA resends the
    whole conversation each request — the voice path's history lives there).
    req_origin: the voice satellite that heard the request (origin.py) —
    held in a request-scoped ContextVar for the whole run so play_music can
    target it, three async layers down, without a parameter at every hop.
    said: the user's own words for this turn (_turn_said); None when the
    request does not end in a non-empty user message.
    voice: the request arrived on the speech mount (/voice/api/chat). Only
    ollama_chat_voice passes True; _run_pipeline holds it in origin.voice.
    """
    # Voice "clear my history": confirm and stop. There is no Kronk-side
    # store for shim clients (HA owns and resends voice history; its window
    # expires between conversations), so confirmation is the whole job.
    # Not for a messy request (sprint M14): that one goes to the pipeline.
    if routing.asks_to_clear_history(text):
        emit("history_cleared", session="shim")
        yield {"type": "token", "text": "Done — fresh start."}
        return

    with origin.scope(req_origin):
        events = _run_pipeline(
            text, context or [], said=said, voice=voice,
            transport="shim", pipeline_name="pipeline.shim",
        )
        # The speech mount's deadline (sprint M9 step 4). origin.voice is
        # set inside the pipeline, so the style for the sentence is set
        # here from the same `voice` value for the wrapper's last event.
        seconds = config_loader.current().voice.deadline_s if voice else None
        if seconds:
            events = _with_deadline(events, seconds)
        voice_token = origin.voice.set(voice)
        try:
            async for ev in events:
                if ev["type"] in ("token", "retract", "deadline"):
                    yield ev
        finally:
            await events.aclose()
            origin.voice.reset(voice_token)


def _openai_chunk(rid: str, model: str, delta: dict, finish_reason: str | None = None) -> str:
    payload = {
        "id":      rid,
        "object":  "chat.completion.chunk",
        "created": int(time.time()),
        "model":   model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _openai_pipeline_stream(text: str, model: str, rid: str,
                                  context: list[dict] | None = None,
                                  said: str | None = None):
    """OpenAI Chat Completions SSE framing around the core pipeline."""
    yield _openai_chunk(rid, model, {"role": "assistant", "content": ""})
    async with aclosing(_kronk_pipeline_tokens(text, model, context, said=said)) as events:
        async for ev in events:
            if ev["type"] != "token":
                continue        # SSE can't retract already-sent text; see agents.py
            yield _openai_chunk(rid, model, {"content": ev["text"]})
    yield _openai_chunk(rid, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _ollama_pipeline_stream(text: str, model: str,
                                  context: list[dict] | None = None,
                                  req_origin: origin.Origin | None = None,
                                  said: str | None = None, voice: bool = False):
    """Ollama /api/chat NDJSON framing around the core pipeline. A streaming
    client can't take back text already sent, so `retract` events are
    dropped here (the buffered speech path in _ollama_collect honours them)."""
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    async with aclosing(_kronk_pipeline_tokens(text, model, context, req_origin, said, voice)) as events:
        async for ev in events:
            if ev["type"] != "token":
                continue
            chunk = {
                "model":      model,
                "created_at": created,
                "message":    {"role": "assistant", "content": ev["text"]},
                "done":       False,
            }
            yield json.dumps(chunk) + "\n"
    # Terminal chunk — Ollama protocol requires done=true with stats.
    final = {
        "model":      model,
        "created_at": created,
        "message":    {"role": "assistant", "content": ""},
        "done":       True,
        "done_reason":       "stop",
        "total_duration":    0,
        "load_duration":     0,
        "prompt_eval_count": _estimate_tokens(text),
        "eval_count":        0,
    }
    yield json.dumps(final) + "\n"


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: _OpenAIChatRequest):
    user_msgs = [m for m in req.messages if m.role == "user" and m.content]
    if not user_msgs:
        raise HTTPException(400, "no user message with content")
    text = user_msgs[-1].content
    said = _turn_said(req.messages)
    model = COORDINATOR_MODEL  # shim ignores client-requested model name
    rid = f"chatcmpl-{new_request_id()}"
    context = _shim_context(req.messages, text)

    if req.stream:
        return StreamingResponse(
            _openai_pipeline_stream(text, model, rid, context, said),
            media_type="text/event-stream",
        )

    # Non-streaming: collect deltas and return a single chat.completion object.
    parts: list[str] = []
    async for line in _openai_pipeline_stream(text, model, rid, context, said):
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        tok = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
        if tok:
            parts.append(tok)

    full = "".join(parts)
    return {
        "id":      rid,
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   model,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": full},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     _estimate_tokens(text),
            "completion_tokens": _estimate_tokens(full),
            "total_tokens":      _estimate_tokens(text) + _estimate_tokens(full),
        },
    }


@app.get("/v1/models")
async def openai_models():
    """Single 'kronk' model — the pipeline picks its own model per agent."""
    return {
        "object": "list",
        "data": [{"id": "kronk", "object": "model", "created": 0, "owned_by": "kronk"}],
    }


# ── Ollama-compatible endpoints ──────────────────────────────────────────────

class _OllamaMessage(BaseModel):
    role: str
    content: str | None = None

    class Config:
        extra = "allow"


class _OllamaChatRequest(BaseModel):
    model: str | None = None
    messages: list[_OllamaMessage]
    stream: bool = True  # Ollama default is streaming
    # Tolerated but ignored: options, tools, format, keep_alive, etc.

    class Config:
        extra = "allow"


@app.get("/api/version")
async def ollama_version():
    return {"version": "kronk-shim"}


@app.get("/api/tags")
async def ollama_tags():
    """One synthetic model that maps to Kronk's full pipeline."""
    return {
        "models": [{
            "name":        "kronk:latest",
            "model":       "kronk:latest",
            "modified_at": "1970-01-01T00:00:00Z",
            "size":        0,
            "digest":      "kronk",
            "details": {
                "parent_model":       "",
                "format":             "kronk",
                "family":             "kronk",
                "families":           ["kronk"],
                "parameter_size":     "n/a",
                "quantization_level": "n/a",
            },
        }],
    }


@app.post("/api/show")
async def ollama_show():
    """Capability probe — HA pings this to learn what the model supports."""
    return {
        "modelfile":  "",
        "parameters": "",
        "template":   "",
        "details": {
            "family":             "kronk",
            "families":           ["kronk"],
            "parameter_size":     "n/a",
            "quantization_level": "n/a",
        },
        "model_info":   {},
        "capabilities": ["completion"],
    }


async def _ollama_collect(text: str, model: str,
                          context: list[dict] | None,
                          req_origin: origin.Origin | None = None,
                          said: str | None = None, voice: bool = False) -> str:
    """Run the pipeline to completion and return the full reply text —
    with tool-round text retracted (this is the speech path; nothing has
    been sent yet, so retracts are honoured exactly)."""
    reply = ""
    async with aclosing(_kronk_pipeline_tokens(text, model, context, req_origin, said, voice)) as events:
        async for ev in events:
            if ev["type"] == "token":
                reply += ev["text"]
            elif ev["type"] == "retract":
                reply = reply[:-ev["chars"]] if ev["chars"] <= len(reply) else ""
            elif ev["type"] == "deadline":
                reply = ev["text"]      # nothing was sent yet: the sentence alone
    return reply


async def _ollama_single_chunk_stream(full: str, text: str, model: str):
    """Protocol-valid NDJSON stream carrying one pre-rendered content chunk.

    The speech profile with VOICE_STREAM=off (the default): it buffers the
    whole reply before scrubbing (markdown markers can straddle token
    boundaries), then answers a streaming request with a single content
    chunk + the terminal chunk. HA can speak a streamed reply as it arrives
    (2026.5.4: tts_start_streaming past 60 characters), so this costs the
    whole generation time before first audio; VOICE_STREAM=on sends units
    as they complete instead (_ollama_speech_stream, sprint M8)."""
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    yield json.dumps({
        "model": model, "created_at": created,
        "message": {"role": "assistant", "content": full},
        "done": False,
    }) + "\n"
    yield json.dumps({
        "model": model, "created_at": created,
        "message": {"role": "assistant", "content": ""},
        "done": True, "done_reason": "stop",
        "total_duration": 0, "load_duration": 0,
        "prompt_eval_count": _estimate_tokens(text),
        "eval_count": _estimate_tokens(full),
    }) + "\n"


_VOICE_STREAM_ON = {"on", "true", "1", "yes"}
_VOICE_STREAM_OFF = {"off", "false", "0", "no", ""}
_voice_stream_warned: set[str] = set()


def _voice_stream_on() -> bool:
    """VOICE_STREAM (env, read per request): on sends a streaming
    /voice/api/chat reply sentence by sentence (sprint M8); off, the
    default, sends it whole as one chunk, as before M8. An unknown value is
    off, and is logged once."""
    raw = os.getenv("VOICE_STREAM", "off")
    v = raw.strip().lower()
    if v in _VOICE_STREAM_ON:
        return True
    if v not in _VOICE_STREAM_OFF and raw not in _voice_stream_warned:
        _voice_stream_warned.add(raw)
        logger.error("VOICE_STREAM=%r is not on or off; streaming stays off", raw)
    return False


def _ndjson_chunk(model: str, created: str, content: str) -> str:
    return json.dumps({
        "model": model, "created_at": created,
        "message": {"role": "assistant", "content": content},
        "done": False,
    }) + "\n"


async def _ollama_speech_stream(text: str, model: str, context: list[dict] | None,
                                req_origin: origin.Origin | None, said: str | None):
    """The speech profile, streamed (VOICE_STREAM=on; sprint M8).

    Same pipeline and the same to_speech as the buffered path, but the reply
    leaves in units (speech_stream.split_units: sentences, whole lines,
    whole blocks) as they complete, one held back so a retract can still
    remove it, each unit its own NDJSON content chunk. The chunks joined are
    what _ollama_single_chunk_stream would have sent (whitespace between
    chunks aside), and the closing `done` record has the same fields.
    Failure is still the pipeline's: a failed turn ends with its friendly
    sentence as the last content. Stats go to a `voice_stream` span under
    the pipeline root and a `voice_stream` event."""
    t0 = time.monotonic()
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    hold = config_loader.current().voice.stream.hold_sentences
    buf = speech_stream.SpeechBuffer(hold=hold)
    root, trace_id, first_ms, error = None, None, None, None
    # Set if the buffer itself fails (a bug): from then on the rest of the
    # reply is collected as the buffered path collects it and sent as one
    # last chunk. The pipeline is always run to its end, so it finishes its
    # trace and releases its model lock as it would for the buffered path.
    fallback: str | None = None
    deadline: str | None = None   # the deadline sentence (sprint M9 step 4)
    sent: list[str] = []
    pipeline = _kronk_pipeline_tokens(text, model, context, req_origin, said, True)
    try:
        async for ev in pipeline:
            if root is None:
                root, trace_id = telemetry.root(), telemetry.current_trace_id()
                root.set(**{"kronk.voice_stream": True})
            out: list[str] = []
            if ev["type"] == "deadline":
                deadline = ev["text"]
                continue
            if fallback is not None:
                if ev["type"] == "token":
                    fallback += ev["text"]
                elif ev["type"] == "retract":
                    fallback = fallback[:-ev["chars"]] if ev["chars"] <= len(fallback) else ""
                continue
            try:
                if ev["type"] == "token":
                    out = buf.feed(ev["text"])
                elif ev["type"] == "retract":
                    lost = buf.retract(ev["chars"])
                    if lost:
                        # Already spoken; cannot be taken back. Counted so
                        # the hold can be widened from data (voice.yaml).
                        emit("voice_stream_retract_unhonoured", chars=ev["chars"],
                             unhonoured=lost, hold=hold, preview=telemetry.text(ev.get("preview", ""), 160))
            except Exception as e:
                logger.exception("voice stream buffer failed after %d chunks; "
                                 "sending the rest as one chunk", len(sent))
                error = f"{type(e).__name__}: {e}"
                fallback, buf.pending = buf.pending, ""
            for chunk in out:
                if first_ms is None:
                    first_ms = round((time.monotonic() - t0) * 1000)
                sent.append(chunk)
                yield _ndjson_chunk(model, created, chunk)
    finally:
        await pipeline.aclose()
    if deadline is not None:
        # Held text was never spoken and is dropped; what was sent stays.
        buf.pending, fallback = "", None
        out = [(" " if sent else "") + deadline]
    elif fallback is None:
        try:
            out = buf.finish()
        except Exception as e:
            logger.exception("voice stream buffer failed at the end")
            error = f"{type(e).__name__}: {e}"
            fallback, buf.pending = buf.pending, ""
    if deadline is None and fallback is not None:
        last = render.to_speech(fallback)
        out = [(" " if sent and last else "") + last] if last else []
    for chunk in out:
        if first_ms is None:
            first_ms = round((time.monotonic() - t0) * 1000)
        sent.append(chunk)
        yield _ndjson_chunk(model, created, chunk)
    if not sent:
        # An empty reply: the same shape as the buffered path's.
        yield _ndjson_chunk(model, created, "")
    full = "".join(sent)
    yield json.dumps({
        "model": model, "created_at": created,
        "message": {"role": "assistant", "content": ""},
        "done": True, "done_reason": "stop",
        "total_duration": 0, "load_duration": 0,
        "prompt_eval_count": _estimate_tokens(text),
        "eval_count": _estimate_tokens(full),
    }) + "\n"

    stats = {
        "chunks": len(sent),
        "first_chunk_ms": first_ms,
        "done_ms": round((time.monotonic() - t0) * 1000),
        "hold": hold,
        "retracts": buf.retracts,
        "retract_held_chars": buf.retracted_held,
        "retract_unhonoured": len(buf.unhonoured),
        "retract_unhonoured_chars": sum(u[2] for u in buf.unhonoured),
        "spoken_chars": len(full),
    }
    emit("voice_stream", trace_id=trace_id, error=error, **stats)
    span = (root or telemetry.root()).child_span("voice_stream", metadata=stats)
    span.end(level="ERROR" if error else None, status_message=error)


async def _ollama_chat_impl(req: _OllamaChatRequest, voice: bool):
    """Shared /api/chat implementation. voice=False (/api/chat, the display
    profile) streams tokens untouched; voice=True (/voice/api/chat, the
    speech profile) runs render.to_speech so no markdown ever reaches a
    speaker (RENDER_PROFILES_PLAN): on the whole reply, buffered, or with
    VOICE_STREAM=on and a streaming request, unit by unit as it completes
    (_ollama_speech_stream, sprint M8). The same `voice` value goes
    to _run_pipeline as origin.voice, so the speech profile and the
    pipeline's voice signal cannot disagree (sprint M2)."""
    user_msgs = [m for m in req.messages if m.role == "user" and m.content]
    if not user_msgs:
        raise HTTPException(400, "no user message with content")
    text = user_msgs[-1].content
    said = _turn_said(req.messages)
    model = COORDINATOR_MODEL
    context = _shim_context(req.messages, text)
    # The voice satellite that heard the request, stamped by HA onto its
    # system prompt (origin.py). None for clients that don't stamp.
    req_origin = origin.from_messages(req.messages)

    if voice:
        if req.stream and _voice_stream_on():
            return StreamingResponse(
                _ollama_speech_stream(text, model, context, req_origin, said),
                media_type="application/x-ndjson",
            )
        full = render.to_speech(
            await _ollama_collect(text, model, context, req_origin, said, voice))
        if req.stream:
            return StreamingResponse(
                _ollama_single_chunk_stream(full, text, model),
                media_type="application/x-ndjson",
            )
    else:
        if req.stream:
            return StreamingResponse(
                _ollama_pipeline_stream(text, model, context, req_origin, said),
                media_type="application/x-ndjson",
            )
        full = await _ollama_collect(text, model, context, req_origin, said)
    return {
        "model":      model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime()),
        "message":    {"role": "assistant", "content": full},
        "done":       True,
        "done_reason": "stop",
        "total_duration":    0,
        "load_duration":     0,
        "prompt_eval_count": _estimate_tokens(text),
        "eval_count":        _estimate_tokens(full),
    }


@app.post("/api/chat")
async def ollama_chat(req: _OllamaChatRequest):
    return await _ollama_chat_impl(req, voice=False)


# ── /voice mount: same Ollama protocol, speech render profile ────────────────
# Speech is a client DECLARATION (the mount), never an inference from the
# protocol — a future Ollama-dialect client may be a screen and gets the
# display default. HA's integration base URL points at /voice; nginx's
# catch-all forwards the prefix, so no nginx change. The mount mirrors the
# full protocol surface HA touches (chat, tags, version, show).
# This handler is the only place voice=True is decided: it becomes
# origin.voice for the whole pipeline run (sprint M2).

@app.post("/voice/api/chat")
async def ollama_chat_voice(req: _OllamaChatRequest):
    return await _ollama_chat_impl(req, voice=True)


@app.get("/voice/api/tags")
async def ollama_tags_voice():
    return await ollama_tags()


@app.get("/voice/api/version")
async def ollama_version_voice():
    return await ollama_version()


@app.post("/voice/api/show")
async def ollama_show_voice():
    return await ollama_show()


@app.delete("/history")
async def clear_history():
    sessions.clear(WEBUI_SESSION)
    file_contexts.clear()
    return {"status": "cleared"}


# ── File upload: text or PDF → in-memory context for the conversation ───────

@app.post("/files")
async def upload_file(file: UploadFile = File(...)):
    data = await file.read()
    name = file.filename or "upload"

    # Text or PDF → in-memory context for the current conversation.
    # Archives are refused by name or magic bytes: a small stored zip can
    # decode as UTF-8 and would otherwise land in the prompt as junk.
    if name.lower().endswith(".zip") or data[:4] == b"PK\x03\x04":
        raise HTTPException(
            status_code=422,
            detail="Zip archives aren't supported. Upload a UTF-8 text file or a PDF.")
    if name.lower().endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            content = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    else:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=422, detail="File must be UTF-8 text or a PDF")

    if not content:
        raise HTTPException(status_code=422, detail="No text could be extracted from the file")

    tokens = _estimate_tokens(content)
    for i, fc in enumerate(file_contexts):
        if fc["name"] == name:
            file_contexts[i] = {"name": name, "content": content, "tokens": tokens}
            break
    else:
        file_contexts.append({"name": name, "content": content, "tokens": tokens})

    total_tokens = sum(fc["tokens"] for fc in file_contexts)
    return {
        "name":         name,
        "tokens":       tokens,
        "total_tokens": total_tokens,
        "warning":      total_tokens > TOKEN_WARNING_THRESHOLD,
    }


@app.get("/files")
async def list_files():
    total_tokens = sum(fc["tokens"] for fc in file_contexts)
    return {
        "files": [{"name": fc["name"], "tokens": fc["tokens"]} for fc in file_contexts],
        "total_tokens": total_tokens,
        "warning":      total_tokens > TOKEN_WARNING_THRESHOLD,
    }


@app.delete("/files/{filename}")
async def delete_file(filename: str):
    for i, fc in enumerate(file_contexts):
        if fc["name"] == filename:
            file_contexts.pop(i)
            return {"status": "removed"}
    raise HTTPException(status_code=404, detail="File not found")


# ── Shopping list ────────────────────────────────────────────────────────────

@app.get("/shopping_list", response_class=HTMLResponse)
async def shopping_list_page():
    return _serve("shopping_list.html")


@app.get("/api/shopping_list")
async def shopping_list_data():
    # A failure here must NOT render as an empty list — the page shows
    # "Nothing on the list." for [] but has an offline state for non-200s
    # (review P1.8: tool_service down looked identical to an empty list).
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{tools.TOOL_SERVICE_URL}/shopping_list")
    except Exception as e:
        logger.warning("shopping_list proxy failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach tool_service: {type(e).__name__}: {e}",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"tool_service returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    return resp.json()


# ── Static mounts ────────────────────────────────────────────────────────────

_generated_dir = os.getenv("GENERATED_DIR", "/data/generated")
try:
    os.makedirs(_generated_dir, exist_ok=True)
    app.mount("/static/generated", StaticFiles(directory=_generated_dir), name="generated")
except (OSError, PermissionError):
    # Tests / local imports without /data mounted — skip static mount.
    pass

_static_dir = os.getenv("STATIC_DIR", "/app/static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
