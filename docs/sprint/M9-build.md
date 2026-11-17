# M9 build: narrow the global lock

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M9-criteria.md`. Tree before the build: `b762fe9`.

This file was first committed with the bench, before any lock change, and
held only the check-first findings below. The rest is added by the last
commit.

## Check first (criterion 1), read-only

All read-only, 2026-11-17 15:55–16:10 UTC: `/props` and `/slots` on the
three running llama.cpp servers, their journals (`journalctl --user`),
LiteLLM on 127.0.0.1:8002 (two one-line test completions), Tempo on
127.0.0.1:3200, the dev orchestrator's log, and the code at `b762fe9`.

### Slots and context per server

| Server (unit, port) | Build | Model | Slots | Context per slot | Flags that matter |
|---|---|---|---|---|---|
| `llama-gemma4-e4b`, 11438 | b9611 | gemma-4-E4B Q4_K_XL (coordinator, home, research, assistant, talkie-less agents) | 4 | 32,768 | `--cache-reuse 256 --swa-full --reasoning-budget 256`, MTP draft |
| `llama-gemma3-4b`, 11439 | b9585 | gemma-3-4b Q4_K_M (`ROUTER_MODEL`; no orchestrator caller since the LLM router was removed) | 4 | 8,192 | `--swa-full` |
| `llama-devstral-q4`, 11440 | b9611 | Devstral-Small-2 24B Q4_K_M (coding, devops) | 4 | 131,072 | `--cache-reuse 256` |

`/props` reports `total_slots: 4` and `n_ctx` equal to `--ctx-size` on
every slot, so the context is not divided between slots (the plan's M9
line). The units set no `--parallel`, `--slot-prompt-similarity` or
`--cache-ram`, so the build defaults apply.

### How a slot is chosen, and the prompt cache

From the gemma-4-e4b journal (b9611):

- A request without `id_slot` goes to the idle slot with the best
  longest-common-prefix similarity to its prompt, if above 0.10
  (`selected slot by LCP similarity, sim_best = 0.661 (> 0.100 thold),
  f_keep = 0.618`); otherwise the least recently used idle slot.
- A host-memory prompt cache sits behind the slots: `cache state: 108
  prompts, 8191.102 MiB (limits: 8192.000 MiB, 32768 tokens)`. When a slot
  starts a task, the other idle slots are saved to it and cleared
  (`saving idle slot to prompt cache`, `clearing prompt`), and a new task
  whose prompt matches a cached one restores it (`updating prompt cache`).
  So a conversation whose slot was taken by another conversation does not
  lose its prefix; it is restored from RAM. This is the main reason the
  plan's eviction hazard is smaller on this build than the plan assumed.
  Measured below.
- `cache_prompt` is on by default; the orchestrator does not send it.
- LiteLLM passes llama.cpp's own count through: the usage block of a
  streamed completion carries `prompt_tokens_details.cached_tokens`. Test
  completion: `prompt_tokens 19, cached_tokens 11`; the journal for the
  same task: `prompt eval time = 25.20 ms / 8 tokens`. 19 − 11 = 8: the
  field is llama.cpp's cached-versus-evaluated split. The bench reads it
  from there (the orchestrator now records it per generation, see the
  bench section).
- Pinning a slot: `id_slot` passes through LiteLLM both as a top-level
  field and inside `extra_body` (journal: `launch_slot_: id 2` and `id 3`
  for the two test requests). So the orchestrator could pin a slot per
  conversation from the request alone. In the one test, the request pinned
  to a cleared slot reported `cached_tokens 0` for a prompt another slot
  had just processed: a pinned request appears to skip the prompt-cache
  lookup that slot selection does. Not built; see "Slot pinning" below.

### What relies on the lock: every piece of shared mutable state the pipeline touches

The orchestrator is one process, one event loop; every route handler in
`main.py` and `config_api.py` is `async def`, so no request code runs on a
worker thread. Code between two `await`s cannot interleave with another
request. The question for each item is whether an `await` sits between a
read and a write, and whether the value is per request.

| State | Where | Safe without the pipeline lock? | Why |
|---|---|---|---|
| `sessions._cache` and its SQLite writes | `sessions.py:42`, `append`, `window`, `clear` | Yes today, by accident; made explicit in step 5 | `append` and `window` are synchronous (no `await`), and `_run_pipeline` appends the user and assistant messages in its `finally` with no `await` between them, so the pair cannot be split. Two requests on one session read the same window and each appends its own pair: a valid interleaving. The comment "safe under the orchestrator's `_llm_lock`" is the only documented reliance, and `DELETE /history` and the "clear my history" stream never took the lock. Fragile: one `await` (or a thread) between the two appends would split the pair. Step 5 writes the pair as one call and one transaction under a lock. |
| `file_contexts` (uploaded files) | `main.py:82` | Yes | Read in `_run_pipeline` in one synchronous loop; mutated by `/files` handlers that never took the lock. |
| `kronk_state.md` | `main.py:94` `_load_state` | Yes | Read-only, synchronous. |
| Telemetry root | `telemetry._current_root` (ContextVar) | Yes | Per task; already documented as not needing the lock. |
| OTel context attach/detach | `telemetry.start_pipeline`, `_Obs.end` | Yes | The token is per span object; contexts are per task. |
| Request id | `events._request_id` (ContextVar) | Yes | Per task. |
| Origin, `said`, `voice` | `origin.current`, `origin.said`, `origin.voice` (ContextVars) | Yes | Per task; `tests/test_voice_signal.py` already runs five pipelines interleaved with the lock lifted. |
| Error style | `errors.style_for` / `current_style` | Yes | Derived from `origin.voice`; no module state. |
| Metrics | `metrics.record` | Yes | One synchronous INSERT per call, own connection. |
| Config in effect | `config_loader._current`, `_status`, `_listeners`; `agents.apply_config` | Same as today | `POST /config/reload` never took the lock. A reload swaps `_current` and rewrites `agent.system_prompt` / `max_rounds` synchronously; a request already in `run_stream` keeps the prompt it built and the round count `range()` already took. Unchanged by M9. |
| Agent registry | `agents.AGENTS`, `COORDINATOR` | Yes | Read-only during a request (only `apply_config` writes). |
| Routing and the lead-in | `routing.RULES`, `_lead_in_pattern` (`lru_cache`), M14 gate (`transcript_gate`) | Yes | Pure functions of the text and config; the cache holds compiled patterns keyed by the config tuple. |
| Direct-dispatch arguments | `routing.Decision.direct` | Yes | A new tuple per call. |
| Mirror pin (M4b) | `tools._tool_update_magicmirror` reads `origin.said` | Yes | ContextVar, per task. |
| Devops tool set (M4) | `run_stream`'s `offered` set | Yes | Local to the run. |
| Fetch address block (M5) | tool_service | Yes | Per request in tool_service. |
| Dry-run | tool_service env | Yes | Not request state. |
| M2 failure flag | `ToolResult.failed`, `run_delegated` return | Yes | Values on the call stack, not module state. |
| Tool HTTP clients | `tools.execute` | Yes | A new `httpx.AsyncClient` per call. |
| `_voice_stream_warned` | `main.py:1030` | Yes | Log-once set; a duplicate add is harmless. |
| M8 speech buffer | `_ollama_speech_stream` | Yes | One `SpeechBuffer` per request. |

Nothing besides `sessions` was found that relies on the lock.

### What happens today when a client disconnects

Container: Starlette 0.38.6, uvicorn 0.30.6 (the test venv has Starlette
0.46.2; see departures).

| Route | Today |
|---|---|
| `/message` (SSE), `/api/chat` with `stream: true`, `/v1/chat/completions` with `stream: true`, `/voice/api/chat` streamed (`VOICE_STREAM=on`, dev only) | `StreamingResponse` listens for `http.disconnect` and cancels the body task. The pipeline gets `CancelledError` at its current `await`, its `finally` runs (trace closed, lock released). No attribute says the client went away, and no `request_complete` (M8-e2e O10). `/message` stores the partial reply in the session if any token was produced. |
| `/voice/api/chat` buffered (production: `VOICE_STREAM=off`, every HA request), `/api/chat` and `/v1/chat/completions` with `stream: false` | The handler awaits the whole pipeline; nothing reads `receive()`, so a disconnect is never seen. The pipeline runs to its end holding `_llm_lock`, and the response goes to a closed socket. Shown on dev: a `/dev/voice/api/chat` request dropped by the client at 1.0 s (`curl` exit 28) ran on and logged `request_complete` at 1.33 s (trace `f3a38958e50decbaea5d203206665306`). |

### Voice request durations (for the deadline)

Tempo, root `pipeline.*` spans with `kronk.transport = "voice"`:

- Production (`kronk-orchestrator`, 2026-11-08 21:58 to 2026-11-17 16:00,
  n = 54): p50 2.70 s, p90 9.66 s, p95 11.68 s, max 17.36 s (home agent,
  music, 2026-11-10, `2d29bd5fa033cfe246ae33b6008ceef8`). None over 20 s.
  These spans start inside the lock, so they exclude any lock wait.
- Dev (`kronk-orchestrator-dev`, from 2026-11-16 00:20, n = 5000, the
  search limit; bench and replay traffic): p50 3.21 s, p95 8.71 s, p99
  17.86 s, max 82.36 s; 38 over 20 s. The long ones are the coordinator
  delegating to research two to four times, each with web searches and a
  7,000 to 14,000 token synthesis (`d8ee80c3…` 82 s, `ad376fcf…` 60 s,
  `e276f9a8…` 39 s), and one devstral `ask_coding` (`a2edae5f…` 28 s).
