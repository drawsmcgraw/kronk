import asyncio
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

import agents
import errors
import metrics
import routing
import servers
import sessions
import telemetry
import tools
from events import emit, new_request_id
from llm import LLM_SERVICE_URL
from tools import TOOL_DEFINITIONS  # re-exported for tests/backward-compat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics.init_db()
    sessions.init_db()
    pruned = sessions.prune_idle()
    if pruned:
        logger.info("sessions: pruned %d idle messages at startup", pruned)
    yield


app = FastAPI(title="Kronk Orchestrator", lifespan=lifespan)

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

# Serialise all LLM calls — llama.cpp handles one request at a time.
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


# Backward-compat alias for tests that mock orchestrator.main._execute_tool
async def _execute_tool(name: str, args: dict) -> str:
    return await tools.execute(name, args)


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
@app.get("/finances",    response_class=HTMLResponse)
async def finances_page(): return _serve("finances.html")
@app.get("/health",      response_class=HTMLResponse)
async def health_page():   return _serve("health.html")
@app.get("/resources",   response_class=HTMLResponse)
async def resources_page():return _serve("resources.html")
@app.get("/performance", response_class=HTMLResponse)
async def performance_page(): return _serve("performance.html")


# ── API: metrics / system / status / agents ──────────────────────────────────

@app.get("/api/metrics")
async def get_metrics():
    try:
        return metrics.dashboard_payload()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/system")
async def system_info():
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, val = line.partition(":")
            mem[key.strip()] = int(val.split()[0]) * 1024  # kB → bytes

    # GTT (Graphics Translation Table) — the iGPU's memory pool, carved from
    # system RAM. Models loaded into llama.cpp servers live here. This is the
    # pool that ran tight in the 2026-05-31 incident (see docs/INCIDENT_2026-05-31.md).
    # We scan /sys/class/drm for any amdgpu card with mem_info_gtt_* nodes.
    gtt_total = 0
    gtt_used  = 0
    try:
        from pathlib import Path as _P
        for card_dir in sorted(_P("/sys/class/drm").glob("card[0-9]*")):
            dev = card_dir / "device"
            total_file = dev / "mem_info_gtt_total"
            used_file  = dev / "mem_info_gtt_used"
            if total_file.exists() and used_file.exists():
                try:
                    gtt_total = int(total_file.read_text().strip())
                    gtt_used  = int(used_file.read_text().strip())
                    break  # first GPU with GTT counters wins
                except (ValueError, OSError):
                    continue
    except Exception:
        pass  # GTT info is best-effort; CPU memory still useful without it

    return {
        "mem_total":     mem.get("MemTotal", 0),
        "mem_available": mem.get("MemAvailable", 0),
        "mem_free":      mem.get("MemFree", 0),
        "swap_total":    mem.get("SwapTotal", 0),
        "swap_free":     mem.get("SwapFree", 0),
        "gtt_total":     gtt_total,
        "gtt_used":      gtt_used,
    }


@app.get("/api/agents")
async def agent_roster():
    return {"agents": agents.roster()}


@app.get("/api/servers")
async def server_catalog():
    """Model servers configured in litellm + live health + agent assignments
    + measured GPU memory (joined by port; vram_gb stays as the static
    fallback estimate when no measurement is available)."""
    catalog = servers.load_catalog()
    health  = await servers.fetch_health(LLM_SERVICE_URL)
    by_model = servers.agents_by_model()
    gpu_mem = servers.load_gpu_mem()

    def measured(entry: dict) -> float | None:
        m = gpu_mem.get(entry.get("port"))
        if not m:
            return None
        return round((m.get("gtt_bytes", 0) + m.get("vram_bytes", 0)) / 1e9, 1)

    return {
        "measured_age_s": gpu_mem.get("_age_s"),
        "servers": [
            {
                **entry,
                "agents":      by_model.get(entry["name"], []),
                "healthy":     health.get(entry["name"],
                                          health.get(entry["api_base"])),
                "measured_gb": measured(entry),
            }
            for entry in catalog
        ],
    }


@app.get("/api/status")
async def health_probe():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            llm_h  = await client.get(f"{LLM_SERVICE_URL}/health")
            tool_h = await client.get(f"{tools.TOOL_SERVICE_URL}/health")
            return {"status": "ok", "llm_service": llm_h.json(), "tool_service": tool_h.json()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


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
#   {"type": "stage",     "name": str}    — UI progress hints (SSE only)
#   {"type": "narration", "text": str}    — human-readable status line
#   {"type": "token",     "text": str}    — assistant content (incl. errors)
#   {"type": "timing",    "data": dict}   — final timing payload (SSE only)

async def _run_pipeline(
    text: str,
    history: list[dict],
    *,
    transport: str,
    pipeline_name: str,
    session_id: str | None = None,        # set → persist the exchange
    include_local_context: bool = False,  # shared state + uploaded files
):
    rid = new_request_id()
    t_request = time.monotonic()
    emit("request", text_preview=text[:80], model=COORDINATOR_MODEL)

    assistant_reply: list[str] = []
    stages: list[dict] = []
    agent_name = "?"  # set by routing; referenced in the finally block
    pipeline_error: str | None = None  # marks the trace ERROR in the finally
    # How failures are RENDERED for this request (debug|friendly). Capture
    # (logs, trace) always keeps full detail — see errors.py.
    error_style = errors.style_for(transport)

    async with _llm_lock:
        trace = telemetry.start_pipeline(
            pipeline_name, text, rid=rid, tags=[f"transport:{transport}"],
        )
        try:
            # Extra system context for the coordinator run. The persona
            # itself lives in agents.COORDINATOR; these are the dynamic
            # parts: weather block (2026-06-12 incident — misrouted
            # follow-ups must not invent forecasts), and for the web UI
            # also shared state and uploaded-file contexts.
            extra_parts: list[str] = []
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
            yield {"type": "stage", "name": "thinking"}
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
            stages.append({"tool": "routing", "s": round(time.monotonic() - t0, 2)})

            # ── Phase 2: specialist agent (if routed to one) ──────────────
            if agent_name in agents.AGENTS:
                agent_cfg = agents.AGENTS[agent_name]
                yield {"type": "stage", "name": f"fetching_delegate_{agent_name}"}
                yield {"type": "narration", "text": f"let me ask the {agent_name} agent about that"}
                t_agent = time.monotonic()

                agent_first_token_t: float | None = None
                agent_error: str | None = None
                agent_model_used = agent_cfg.model
                stage_sent = False

                async for ev in agents.run_stream(agent_cfg, text, list(history[-5:]),
                                                  error_style=error_style):
                    etype = ev.get("type")
                    if etype == "narration":
                        yield {"type": "narration", "text": ev["text"]}
                    elif etype == "token":
                        if not stage_sent:
                            yield {"type": "stage", "name": "generating"}
                            yield {"type": "narration", "text": ""}
                            stage_sent = True
                        if agent_first_token_t is None:
                            agent_first_token_t = time.monotonic()
                        assistant_reply.append(ev["text"])
                        yield {"type": "token", "text": ev["text"]}
                    elif etype == "error":
                        agent_error = ev["message"]
                    elif etype == "done":
                        agent_model_used = ev.get("model", agent_model_used)

                agent_ok = agent_error is None
                t_end = time.monotonic()
                stages.append({
                    "tool": f"delegate_{agent_name}",
                    "s":    round(t_end - t_agent, 2),
                    "ok":   agent_ok,
                })
                emit("agent_complete", agent=agent_name, ok=agent_ok,
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

                # Agent errored — fall through to the coordinator, honestly
                # labeled. The old block called this a "specialist result —
                # use this to answer": the coordinator was never told it was
                # a failure and would apologize vaguely or invent an answer,
                # swallowing detail like "provider may need re-authentication"
                # (review P1.1). The trace is marked ERROR even if the
                # coordinator recovers, so the failure stays findable.
                pipeline_error = f"{agent_name} specialist: {agent_error}"
                extra_parts.append(
                    errors.specialist_failed_block(agent_name, agent_error, error_style)
                )

            # ── Phase 3: coordinator (direct answers, delegation via ask_*,
            #    and specialist-failure fallback). Same run_stream loop as
            #    the agents; telemetry/metrics come from inside it.
            t_llm_start = time.monotonic()
            yield {"type": "stage", "name": "waiting"}

            first_token = True
            t_first_token: float | None = None
            async for ev in agents.run_stream(
                agents.COORDINATOR,
                text,
                [],  # no embedded context — history goes in as real messages
                system_extra="\n\n".join(extra_parts) or None,
                history_messages=history,
                error_style=error_style,
            ):
                etype = ev.get("type")
                if etype == "narration":
                    yield {"type": "narration", "text": ev["text"]}
                elif etype == "token":
                    if first_token:
                        t_first_token = time.monotonic()
                        yield {"type": "stage", "name": "generating"}
                        yield {"type": "narration", "text": ""}
                        first_token = False
                    assistant_reply.append(ev["text"])
                    yield {"type": "token", "text": ev["text"]}
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
            telemetry.end_pipeline(
                trace,
                output="".join(assistant_reply) or None,
                route=agent_name,
                level="ERROR" if pipeline_error else None,
                status_message=pipeline_error,
            )
            # Persist the exchange only when an answer was produced —
            # a failed turn leaves the stored conversation untouched.
            if session_id and assistant_reply:
                sessions.append(session_id, "user", text)
                sessions.append(session_id, "assistant", "".join(assistant_reply))


@app.post("/message")
async def message(req: MessageRequest):
    session_id = req.session_id or WEBUI_SESSION

    # Deterministic pre-route intercept: same behavior as the UI's clear
    # button, reachable by voice/text ("clear my history").
    if routing.CLEAR_HISTORY_RE.search(req.text):
        return _clear_history_stream(session_id)

    async def stream():
        # Capped, boundary-aligned window — prompt order stays append-only
        # so llama.cpp's prompt cache (--swa-full) reuses the prefix.
        history = sessions.window(session_id)
        async for ev in _run_pipeline(
            req.text, history,
            transport="webui", pipeline_name="pipeline.message",
            session_id=session_id, include_local_context=True,
        ):
            etype = ev["type"]
            if etype == "stage":
                yield f"data: {json.dumps({'stage': ev['name']})}\n\n"
            elif etype == "narration":
                yield f"data: {json.dumps({'narration': ev['text']})}\n\n"
            elif etype == "token":
                yield f"data: {json.dumps({'token': ev['text']})}\n\n"
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


async def _kronk_pipeline_tokens(text: str, model: str, context: list[dict] | None = None):
    """Shim wrapper around _run_pipeline: token events only.

    context: prior conversation turns supplied by the client (HA resends the
    whole conversation each request — the voice path's history lives there).
    """
    # Voice "clear my history": confirm and stop. There is no Kronk-side
    # store for shim clients (HA owns and resends voice history; its window
    # expires between conversations), so confirmation is the whole job.
    if routing.CLEAR_HISTORY_RE.search(text):
        emit("history_cleared", session="shim")
        yield {"type": "token", "text": "Done — fresh start."}
        return

    async for ev in _run_pipeline(
        text, context or [],
        transport="shim", pipeline_name="pipeline.shim",
    ):
        if ev["type"] == "token":
            yield {"type": "token", "text": ev["text"]}


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
                                  context: list[dict] | None = None):
    """OpenAI Chat Completions SSE framing around the core pipeline."""
    yield _openai_chunk(rid, model, {"role": "assistant", "content": ""})
    async for ev in _kronk_pipeline_tokens(text, model, context):
        yield _openai_chunk(rid, model, {"content": ev["text"]})
    yield _openai_chunk(rid, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _ollama_pipeline_stream(text: str, model: str,
                                  context: list[dict] | None = None):
    """Ollama /api/chat NDJSON framing around the core pipeline."""
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    async for ev in _kronk_pipeline_tokens(text, model, context):
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
    model = COORDINATOR_MODEL  # shim ignores client-requested model name
    rid = f"chatcmpl-{new_request_id()}"
    context = _shim_context(req.messages, text)

    if req.stream:
        return StreamingResponse(
            _openai_pipeline_stream(text, model, rid, context),
            media_type="text/event-stream",
        )

    # Non-streaming: collect deltas and return a single chat.completion object.
    parts: list[str] = []
    async for line in _openai_pipeline_stream(text, model, rid, context):
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


@app.post("/api/chat")
async def ollama_chat(req: _OllamaChatRequest):
    user_msgs = [m for m in req.messages if m.role == "user" and m.content]
    if not user_msgs:
        raise HTTPException(400, "no user message with content")
    text = user_msgs[-1].content
    model = COORDINATOR_MODEL
    context = _shim_context(req.messages, text)

    if req.stream:
        return StreamingResponse(
            _ollama_pipeline_stream(text, model, context),
            media_type="application/x-ndjson",
        )

    parts: list[str] = []
    async for line in _ollama_pipeline_stream(text, model, context):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        tok = obj.get("message", {}).get("content", "")
        if tok and not obj.get("done"):
            parts.append(tok)

    full = "".join(parts)
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


@app.delete("/history")
async def clear_history():
    sessions.clear(WEBUI_SESSION)
    file_contexts.clear()
    return {"status": "cleared"}


# ── File upload: Garmin CSV/zip → health_service, everything else → context ─

_GARMIN_CSV_SIGNATURES = [
    lambda h: "activitytype" in h and ("avghr" in h or "activityid" in h),
    lambda h: "calendardate" in h and "deepsleepseconds" in h,
    lambda h: "weeklyavghrv" in h or "lastnightavg" in h,
    lambda h: "calendardate" in h and "bodybattery" in h,
    lambda h: "calendardate" in h and "totalsteps" in h,
]


def _is_garmin_csv(data: bytes, filename: str) -> bool:
    if not filename.lower().endswith(".csv"):
        return False
    try:
        first_line = data.decode("utf-8-sig").splitlines()[0].lower().replace(" ", "").replace("_", "")
    except Exception:
        return False
    return any(sig(first_line) for sig in _GARMIN_CSV_SIGNATURES)


def _is_garmin_export_zip(data: bytes, filename: str) -> bool:
    if not filename.lower().endswith(".zip"):
        return False
    return data[:2] == b"PK"


def _is_labcorp_report(data: bytes, filename: str) -> bool:
    if not filename.lower().endswith(".pdf"):
        return False
    try:
        reader = PdfReader(io.BytesIO(data))
        text = " ".join(page.extract_text() or "" for page in reader.pages[:2])
        return any(sig in text for sig in ["LabCorp", "LABCORP", "Laboratory Corporation", "LabCorp Patient"])
    except Exception:
        return False


async def _forward_to_health(path: str, name: str, data: bytes,
                             content_type: str, timeout: float,
                             reject_msg: str) -> dict:
    """Forward an uploaded file to health_service; returns its JSON result."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{tools.HEALTH_SERVICE_URL}{path}",
                files={"file": (name, data, content_type)},
            )
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"{reject_msg}: {resp.text[:200]}",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Health service unreachable: {e}")


@app.post("/files")
async def upload_file(file: UploadFile = File(...)):
    data = await file.read()
    name = file.filename or "upload"

    # Garmin export zip → forward to health_service for full import
    if _is_garmin_export_zip(data, name):
        result = await _forward_to_health(
            "/api/import/export", name, data, "application/zip",
            timeout=300, reject_msg="Health service rejected the zip",
        )
        return {
            "name": name,
            "routed_to": "health_service",
            "files_processed":    result.get("files_processed", 0),
            "files_unrecognized": result.get("files_unrecognized", 0),
            "total_inserted":     result.get("total_inserted", 0),
            "detail":             result.get("detail", []),
        }

    # Garmin CSV → forward to health_service for SQLite persistence
    if _is_garmin_csv(data, name):
        result = await _forward_to_health(
            "/api/import/csv", name, data, "text/csv",
            timeout=30, reject_msg="Health service rejected the file",
        )
        return {
            "name":     name,
            "routed_to": "health_service",
            "type":     result.get("type"),
            "inserted": result.get("inserted", 0),
            "skipped":  result.get("skipped", 0),
        }

    # LabCorp bloodwork PDF → forward to health_service for structured ingestion
    if _is_labcorp_report(data, name):
        result = await _forward_to_health(
            "/api/import/bloodwork", name, data, "application/pdf",
            timeout=30, reject_msg="Bloodwork import failed",
        )
        return {
            "name": name,
            "routed_to": "health_service",
            "type": "bloodwork",
            "date": result["date"],
            "markers_parsed": result["markers_parsed"],
            "panels_found": result["panels_found"],
        }

    # Everything else → in-memory context for the current conversation
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
