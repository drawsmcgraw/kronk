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
import metrics
import routing
import servers
import tools
from events import current_request_id, emit, new_request_id
from llm import LLM_SERVICE_URL
from tools import TOOL_DEFINITIONS  # re-exported for tests/backward-compat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics.init_db()
    yield


app = FastAPI(title="Kronk Orchestrator", lifespan=lifespan)

COORDINATOR_MODEL = agents.COORDINATOR_MODEL
STATE_FILE        = Path("/data/kronk_state.md")

_DEFAULT_SYSTEM_PROMPT = (
    "You are Kronk, a helpful home assistant. Be direct and concise. "
    "Do not use action text, emotes, or filler expressions. "
    "Never fabricate real-time information — if no tool result is present, say so plainly."
)
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", _DEFAULT_SYSTEM_PROMPT)

# In-memory conversation history (wiped on restart)
history: list[dict] = []

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
    return {
        "mem_total":     mem.get("MemTotal", 0),
        "mem_available": mem.get("MemAvailable", 0),
        "mem_free":      mem.get("MemFree", 0),
        "swap_total":    mem.get("SwapTotal", 0),
        "swap_free":     mem.get("SwapFree", 0),
    }


@app.get("/api/agents")
async def agent_roster():
    return {"agents": agents.roster()}


@app.get("/api/servers")
async def server_catalog():
    """Model servers configured in litellm + their live health + agent assignments."""
    catalog = servers.load_catalog()
    health  = await servers.fetch_health(LLM_SERVICE_URL)
    by_model = servers.agents_by_model()
    return {
        "servers": [
            {
                **entry,
                "agents":  by_model.get(entry["name"], []),
                "healthy": health.get(entry["api_base"]),
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


@app.post("/message")
async def message(req: MessageRequest):
    model = req.model or COORDINATOR_MODEL

    async def stream():
        rid = new_request_id()
        t_request = time.monotonic()
        emit("request", text_preview=req.text[:80], model=model)

        assistant_reply: list[str] = []
        first_token = True
        stages: list[dict] = []
        t_first_token: float | None = None

        async with _llm_lock:
            history_snapshot = len(history)
            history.append({"role": "user", "content": req.text})
            try:
                # Persona messages used for the coordinator synthesis path.
                system_parts = [SYSTEM_PROMPT]
                state = _load_state()
                if state:
                    system_parts.append(f"[Kronk shared state]\n{state}")
                for fc in file_contexts:
                    system_parts.append(f"[Attached file: {fc['name']}]\n{fc['content']}")
                persona_messages: list[dict] = [
                    {"role": "system", "content": "\n\n".join(system_parts)}
                ]
                persona_messages.extend(history)

                # ── Phase 1: route ────────────────────────────────────────
                yield f"data: {json.dumps({'stage': 'thinking'})}\n\n"
                t0 = time.monotonic()
                try:
                    agent_name = await routing.classify(req.text, history[:-1])
                except Exception as e:
                    logger.error("Router failed: %s", e)
                    err = f"Error: could not reach the language model ({e}). Is the server still loading?"
                    assistant_reply.append(err)
                    yield f"data: {json.dumps({'token': err})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                stages.append({"tool": "routing", "s": round(time.monotonic() - t0, 2)})

                # ── Phase 2: specialist agent (if routed to one) ──────────
                synthesis_messages = list(persona_messages)

                if agent_name in agents.AGENTS:
                    agent_cfg = agents.AGENTS[agent_name]
                    yield f"data: {json.dumps({'stage': f'fetching_delegate_{agent_name}'})}\n\n"
                    t_agent = time.monotonic()
                    agent_context = list(history[-6:-1])

                    agent_first_token_t: float | None = None
                    agent_error: str | None = None
                    agent_model_used = agent_cfg.model
                    stage_sent = False

                    yield f"data: {json.dumps({'narration': f'let me ask the {agent_name} agent about that'})}\n\n"

                    async for ev in agents.run_stream(agent_cfg, req.text, agent_context):
                        etype = ev.get("type")
                        if etype == "narration":
                            yield f"data: {json.dumps({'narration': ev['text']})}\n\n"
                        elif etype == "token":
                            if not stage_sent:
                                yield f"data: {json.dumps({'stage': 'generating'})}\n\n"
                                yield f"data: {json.dumps({'narration': ''})}\n\n"
                                stage_sent = True
                            if agent_first_token_t is None:
                                agent_first_token_t = time.monotonic()
                            text = ev["text"]
                            assistant_reply.append(text)
                            yield f"data: {json.dumps({'token': text})}\n\n"
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
                    emit(
                        "agent_complete",
                        agent=agent_name,
                        ok=agent_ok,
                        duration_s=round(t_end - t_agent, 2),
                    )

                    if agent_ok:
                        ttft = round(agent_first_token_t - t_agent, 2) if agent_first_token_t else 0.0
                        gen_s = round(t_end - (agent_first_token_t or t_agent), 2)
                        timing = {
                            "model":        agent_model_used,
                            "stages":       stages,
                            "ttft_s":       ttft,
                            "generation_s": gen_s,
                        }
                        yield f"data: {json.dumps({'timing': timing})}\n\n"
                        yield "data: [DONE]\n\n"
                        emit("request_complete", route=agent_name, duration_s=round(time.monotonic() - t_request, 2))
                        return

                    # Agent errored — fall through to coordinator with the error context.
                    synthesis_messages = [
                        {"role": "system", "content": (
                            persona_messages[0]["content"]
                            + f"\n\n[{agent_name} specialist result — use this to answer]\n{agent_error}"
                        )}
                    ] + persona_messages[1:]

                # ── Phase 3: coordinator synthesis (direct answers + agent failures) ──
                t_llm_start = time.monotonic()
                yield f"data: {json.dumps({'stage': 'waiting'})}\n\n"

                stream_prompt_tokens = 0
                stream_completion_tokens = 0
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "POST",
                        f"{LLM_SERVICE_URL}/v1/chat/completions",
                        json={
                            "model":          model,
                            "messages":       synthesis_messages,
                            "stream":         True,
                            "stream_options": {"include_usage": True},
                        },
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:"):].strip()
                            if payload == "[DONE]":
                                t_done = time.monotonic()
                                ttft_ms = round((t_first_token - t_llm_start) * 1000, 1) if t_first_token else None
                                gen_ns = int((t_done - (t_first_token or t_llm_start)) * 1e9)
                                metrics.record(
                                    agent="coordinator",
                                    model=model,
                                    prompt_tokens=stream_prompt_tokens,
                                    completion_tokens=stream_completion_tokens,
                                    eval_duration_ns=gen_ns,
                                    ttft_ms=ttft_ms,
                                )
                                timing: dict = {"model": model}
                                if stages:
                                    timing["stages"] = stages
                                if t_first_token is not None:
                                    timing["ttft_s"] = round(t_first_token - t_llm_start, 2)
                                    timing["generation_s"] = round(t_done - t_first_token, 2)
                                yield f"data: {json.dumps({'timing': timing})}\n\n"
                                yield "data: [DONE]\n\n"
                                emit("request_complete", route=agent_name, duration_s=round(time.monotonic() - t_request, 2))
                                break
                            try:
                                chunk = json.loads(payload)
                                if chunk.get("usage"):
                                    stream_prompt_tokens = chunk["usage"].get("prompt_tokens", 0)
                                    stream_completion_tokens = chunk["usage"].get("completion_tokens", stream_completion_tokens)
                                    continue
                                choices = chunk.get("choices")
                                if not choices:
                                    continue
                                token = choices[0].get("delta", {}).get("content", "")
                                if token:
                                    if first_token:
                                        t_first_token = time.monotonic()
                                        yield f"data: {json.dumps({'stage': 'generating'})}\n\n"
                                        first_token = False
                                    stream_completion_tokens += 1
                                    assistant_reply.append(token)
                                    yield f"data: {json.dumps({'token': token})}\n\n"
                            except json.JSONDecodeError:
                                continue

            finally:
                if assistant_reply:
                    history.append({"role": "assistant", "content": "".join(assistant_reply)})
                else:
                    del history[history_snapshot:]

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/history")
async def get_history():
    return {"history": history}


# ── External API shims: OpenAI + Ollama ──────────────────────────────────────
#
# Lets external clients reach Kronk's router → specialist → coordinator
# pipeline using whichever envelope they speak natively. Supported:
#   - OpenAI Chat Completions: /v1/chat/completions, /v1/models
#   - Ollama:                   /api/chat, /api/tags, /api/version, /api/show
#
# Design choices:
#   - Stateless. The shims do NOT touch the global `history` or
#     `file_contexts` so external callers can't pollute the chat-UI state.
#   - Only the last user message is used. Router and agents don't yet take
#     conversation context; multi-turn follow-ups would need a refactor.
#   - Client-supplied `model` is ignored — Kronk picks its own model per
#     agent. The shims report COORDINATOR_MODEL in response metadata so
#     strict clients don't choke.
#   - Tools, tool_choice, temperature, etc. are accepted but ignored;
#     Kronk's agents own tool-calling internally.
#
# Tech debt: the routing/agent/coordinator pattern is duplicated from
# /message. The right cleanup is to extract a single `_run_pipeline()`
# adapted by all three transports.

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


async def _kronk_pipeline_tokens(text: str, model: str):
    """Transport-agnostic core pipeline.

    Yields semantic events:
      {"type": "token", "text": "..."}   — one token of assistant content
      {"type": "error", "text": "..."}   — fatal failure; downstream should
                                           still emit a clean terminator
    Caller is responsible for transport framing (OpenAI SSE / Ollama NDJSON).
    """
    async with _llm_lock:
        try:
            agent_name = await routing.classify(text, [])
        except Exception as e:
            logger.error("Router failed (shim): %s", e)
            yield {"type": "error", "text": f"Error: router unreachable ({e})."}
            return

        synth_msgs: list[dict] | None = None

        if agent_name in agents.AGENTS:
            agent_cfg = agents.AGENTS[agent_name]
            agent_error: str | None = None
            async for ev in agents.run_stream(agent_cfg, text, []):
                etype = ev.get("type")
                if etype == "token":
                    yield {"type": "token", "text": ev["text"]}
                elif etype == "error":
                    agent_error = ev["message"]
            if agent_error is None:
                return
            synth_msgs = [
                {"role": "system", "content": (
                    SYSTEM_PROMPT
                    + f"\n\n[{agent_name} specialist result — use this to answer]\n{agent_error}"
                )},
                {"role": "user", "content": text},
            ]
        else:
            synth_msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ]

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{LLM_SERVICE_URL}/v1/chat/completions",
                    json={"model": model, "messages": synth_msgs, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        token = choices[0].get("delta", {}).get("content", "")
                        if token:
                            yield {"type": "token", "text": token}
        except Exception as e:
            logger.error("Coordinator stream failed (shim): %s", e)
            yield {"type": "error", "text": f"\nError: coordinator unreachable ({e})."}


def _openai_chunk(rid: str, model: str, delta: dict, finish_reason: str | None = None) -> str:
    payload = {
        "id":      rid,
        "object":  "chat.completion.chunk",
        "created": int(time.time()),
        "model":   model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _openai_pipeline_stream(text: str, model: str, rid: str):
    """OpenAI Chat Completions SSE framing around the core pipeline."""
    yield _openai_chunk(rid, model, {"role": "assistant", "content": ""})
    async for ev in _kronk_pipeline_tokens(text, model):
        yield _openai_chunk(rid, model, {"content": ev["text"]})
    yield _openai_chunk(rid, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _ollama_pipeline_stream(text: str, model: str):
    """Ollama /api/chat NDJSON framing around the core pipeline."""
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    async for ev in _kronk_pipeline_tokens(text, model):
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

    if req.stream:
        return StreamingResponse(
            _openai_pipeline_stream(text, model, rid),
            media_type="text/event-stream",
        )

    # Non-streaming: collect deltas and return a single chat.completion object.
    parts: list[str] = []
    async for line in _openai_pipeline_stream(text, model, rid):
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

    if req.stream:
        return StreamingResponse(
            _ollama_pipeline_stream(text, model),
            media_type="application/x-ndjson",
        )

    parts: list[str] = []
    async for line in _ollama_pipeline_stream(text, model):
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
    history.clear()
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


@app.post("/files")
async def upload_file(file: UploadFile = File(...)):
    data = await file.read()
    name = file.filename or "upload"

    # Garmin export zip → forward to health_service for full import
    if _is_garmin_export_zip(data, name):
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{tools.HEALTH_SERVICE_URL}/api/import/export",
                    files={"file": (name, data, "application/zip")},
                )
                if resp.status_code == 200:
                    result = resp.json()
                    return {
                        "name": name,
                        "routed_to": "health_service",
                        "files_processed":    result.get("files_processed", 0),
                        "files_unrecognized": result.get("files_unrecognized", 0),
                        "total_inserted":     result.get("total_inserted", 0),
                        "detail":             result.get("detail", []),
                    }
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Health service rejected the zip: {resp.text[:200]}",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Health service unreachable: {e}")

    # Garmin CSV → forward to health_service for SQLite persistence
    if _is_garmin_csv(data, name):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{tools.HEALTH_SERVICE_URL}/api/import/csv",
                    files={"file": (name, data, "text/csv")},
                )
                if resp.status_code == 200:
                    result = resp.json()
                    return {
                        "name":     name,
                        "routed_to": "health_service",
                        "type":     result.get("type"),
                        "inserted": result.get("inserted", 0),
                        "skipped":  result.get("skipped", 0),
                    }
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Health service rejected the file: {resp.text[:200]}",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Health service unreachable: {e}")

    # LabCorp bloodwork PDF → forward to health_service for structured ingestion
    if _is_labcorp_report(data, name):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{tools.HEALTH_SERVICE_URL}/api/import/bloodwork",
                    files={"file": (name, data, "application/pdf")},
                )
                if resp.status_code == 200:
                    result = resp.json()
                    return {
                        "name": name,
                        "routed_to": "health_service",
                        "type": "bloodwork",
                        "date": result["date"],
                        "markers_parsed": result["markers_parsed"],
                        "panels_found": result["panels_found"],
                    }
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Bloodwork import failed: {resp.text[:200]}",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Health service unreachable: {e}")

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
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{tools.TOOL_SERVICE_URL}/shopping_list")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning("shopping_list proxy failed: %s", e)
    return {"items": [], "updated_at": None}


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
