# M9 build: narrow the global lock

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M9-criteria.md`. Tree before the build: `b762fe9`.

The check-first section below was committed on its own (`634fb5b`) before
the bench and before any code change; the rest was added by the last
commit. No push, no tag. The dev pair is running the final code, healthy.

## Summary

- All five steps built in the plan's order, one commit each, each measured
  on dev against a two-run baseline and **kept**. None slowed a request run
  alone or lowered cache reuse beyond what the two baseline runs differ by,
  once the measure is what a lock change can move (see "How 'no slower' is
  judged"). The plan's stopping rule (stop after steps 1 and 4) did not
  apply: with the per-server lock, the research request in the two-client
  case kept its cache (1350 of 1351 prompt tokens cached on its synthesis).
- Two-client (research on `/api/chat`, voice weather one second later): the
  voice request's wait for the model fell from 2.0-7.7 s (step 1, the
  pipeline lock) to 0.08-2.2 s (steps 2-5): it now waits for at most the
  generation in progress, never for research's tool calls. Its end-to-end
  median (7.7-9.0 s) stays inside the baseline's range (10.17 / 6.51 s):
  the bench's research query is short, and the voice request now shares
  the server with research's synthesis instead of running after it.
- Voice deadline 25 s (`config/voice.yaml`), from Tempo: slowest production
  voice request in nine days 17.4 s. Fired live once on dev during the
  replay (a routing-miss row that wandered into three web searches): stopped
  at 25.0 s, spoke the sentence, trace marked.
- Disconnects now cancel the request on every route. Before, a dropped
  buffered voice request (all of production's) ran to its end holding the
  lock. Live on dev: dropped at 1.0 s, ended at 1.0 s, llama.cpp cancelled
  the generation, the next request was answered in 1.04 s.
- `./scripts/run_tests.sh`: `4516 passed, 3 skipped, 53 xfailed` (baseline
  4435 / 3 / 53; 81 new tests). Replay: every M10 baseline row has its
  status, apart from the known flapper `inc-0930-search-deep-house-ytm`
  (fail -> pass).
- Rollback switch: `MODEL_LOCK=pipeline` (env, per request) restores the
  pipeline-wide lock; tested.

## Check first (criterion 1), read-only

All read-only, 2026-11-17 15:55–16:10 UTC: `/props` and `/slots` on the
three running llama.cpp servers, their journals (`journalctl --user`),
LiteLLM on 127.0.0.1:8002 (two one-line test completions), Tempo on
127.0.0.1:3200, the dev orchestrator's log, and the code at `b762fe9`.

### Slots and context per server

| Server (unit, port) | Build | Model | Slots | Context per slot | Flags that matter |
|---|---|---|---|---|---|
| `llama-gemma4-e4b`, 11438 | b9611 | gemma-4-E4B Q4_K_XL (coordinator, research, home, assistant) | 4 | 32,768 | `--cache-reuse 256 --swa-full --reasoning-budget 256`, MTP draft |
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


## Commits

| Commit | What |
|---|---|
| `634fb5b` | Check-first findings and the shared-state table (this file, first part), before any change. |
| `6fcdaca` | The bench (`scripts/m9_lock_bench.py`) and llama.cpp's cache split per generation on `agent_round` events and generation spans. No behaviour change. |
| `36a1a2b` | Baseline, two runs on the pre-M9 code (dev). |
| `f79b68a` | Step 1: routing, context and direct dispatch before the lock. |
| `0861e1b` | Step 1 measured (kept); bench compare judges overhead and evaluated tokens; completion tokens on `agent_round`. |
| `9591cc6` | Step 2: one lock per model server, held for one generation; `MODEL_LOCK` switch. |
| `be9de0b` | Step 2 measured (kept); overhead leaves out tool time read from Tempo. |
| `5830770` | Step 3: the per-server lock is a priority queue. |
| `c17c269` | Step 3 measured (kept). |
| `11db421` | Step 4: voice deadline; cancellation on client disconnect. |
| `d4d3dae` | Step 4 measured (kept). |
| `3b79209` | Step 5: session writes safe without the pipeline lock. |
| `fd5b35f` | Step 5 measured (kept). |
| `87379f8`, `3a6af81` | Tests for requests interleaving (criteria 10-13); the byte pin from `b762fe9`. |
| `edfb3d9` | Load check on dev. |
| `4354170` | Voice replay on the dev path. |
| `4a3f31e` | Docs. |
| this one | This report. |

Step 5 was not needed before step 2 (see the shared-state table: the
session pair was already written by two synchronous calls with no `await`
between them), so the plan's order was kept.

## What changed

Line numbers at HEAD.

| Where | Change |
|---|---|
| `orchestrator/model_lock.py` (new) | `PriorityLock` (`:64`; `acquire(cls)`, `release`, `waiting`, `_pick`), `mode()` (`:131`, `MODEL_LOCK`), `server_for` / `for_model` (`:150`, `:168`; host:port of the model's `api_base` from `servers.load_catalog`), `priority()` (`:177`, from `origin.voice`), `held()` (`:183`: takes the lock, records the `lock.wait` span and `model_lock` event, releases in `finally`). |
| `orchestrator/llm.py:17-33` | `stream()` holds `model_lock.held(for_model(model))` around `_stream_body` (`:36`, the old body, unchanged) unless `MODEL_LOCK=pipeline`. |
| `orchestrator/main.py:89-94` | `_llm_lock` comment: now only the rollback lock. |
| `orchestrator/main.py:71` | `app.add_middleware(client_watch.ClientWatch)`. |
| `orchestrator/main.py:365-441` | `_run_pipeline`: an `AsyncExitStack` replaces `async with (nullcontext() if direct else _llm_lock)`; trace start, weather and local context, routing and direct dispatch run first; with `MODEL_LOCK=pipeline` the pipeline lock is entered at `:440` (step 1's place). |
| `orchestrator/main.py:601-614` | `except (CancelledError, GeneratorExit)`: `kronk.ended`, ERROR for a deadline, `request_ended` event, re-raise. |
| `orchestrator/main.py:637-642` | No session write for a stopped turn; the exchange goes in through `sessions.append_exchange`. |
| `orchestrator/main.py:645-695` | `_why_stopped()`, `_with_deadline()` (`asyncio.timeout_at` around each `__anext__`; the last event is `{"type": "deadline"}`). |
| `orchestrator/main.py:715` | `_direct_dispatch` ends its tool span on cancellation. |
| `orchestrator/main.py:890-909` | `_kronk_pipeline_tokens` wraps voice requests in `_with_deadline`, passes `deadline` events, closes the pipeline in `finally`. |
| `orchestrator/main.py:1104-1113`, `:1195-1245` | `_ollama_collect` replies with the sentence on a deadline; the streamed writer keeps what it sent, drops held text, sends the sentence last. |
| `orchestrator/main.py`, `orchestrator/agents.py` | Every nested generator loop (`run_stream`, `llm.stream`, `_direct_dispatch`, `run_delegated`, the shim framers) wrapped in `contextlib.aclosing`, so a close cascades at once (`agents.py:633`, `:874`, `:952`; `main.py:424`, `:457`, `:555`, `:750`, `:925`, `:942`, `:1104`). |
| `orchestrator/agents.py:514-531` | `_cached_tokens`, `_usage`: llama.cpp's cache split on the generation span (`kronk.cached_tokens`) and on `agent_round` (`prompt_tokens`, `cached_tokens`, `completion_tokens`). |
| `orchestrator/agents.py:646`, `:780`, `:881` | Generation and tool spans end ("stopped: the request ended") on cancellation. |
| `orchestrator/client_watch.py` (new) | `ClientWatch`, ASGI middleware on `/message`, `/api/chat`, `/voice/api/chat`, `/v1/chat/completions`. |
| `orchestrator/run_state.py` (new) | `Run` (tools sent, client gone, deadline, how it ended) in a ContextVar; `ensure()`, `note_tool_sent()`. |
| `orchestrator/tools.py:1048-1064` | `READ_ONLY_TOOLS`; `execute()` records the tool before calling it. |
| `orchestrator/errors.py:174-195` | `FRIENDLY_DEADLINE`, `FRIENDLY_DEADLINE_SENT`, `deadline_text()` (through `failure_text`, so the debug text is logged and put on the span). |
| `orchestrator/sessions.py:42-49`, `:89-145` | `_write_lock` (`threading.RLock`), `append_exchange()`; `append`, `window`, `clear` under the lock; the comment relying on `_llm_lock` is gone. |
| `orchestrator/config_loader.py:211-215`, `:274-276` | `Tuning.model_lock.max_overtakes` (3, 0-100); `Voice.deadline_s` (25, 5-120 or null). Defaults equal the files. |
| `config/tuning.yaml:88-97`, `config/voice.yaml:34-45` | The two keys, documented in the files. |
| `docker-compose.yml:244-247`, `:388-389` | `MODEL_LOCK=server` on `orchestrator` (not deployed) and `orchestrator-dev`, with a comment. |
| `scripts/m9_lock_bench.py` (new) | The bench. |
| Tests | `tests/test_m9_cache_tokens.py` (6), `test_m9_bench.py` (17), `test_m9_lock.py` (25), `test_m9_deadline.py` (21), `test_m9_sessions.py` (5), `test_m9_interleave.py` (7), `tests/fixtures/m9/replies.{py,json}`; `tests/test_direct_dispatch.py::test_direct_request_does_not_wait_for_the_model_lock` pins `MODEL_LOCK=pipeline` (the only mode its lock exists in). |
| Docs | `docs/features/model-locks.md` (new); `README.md` (design decision, last-updated line); `TECH_DEBT.md` (both lock entries); pointers in `docs/features/direct-dispatch.md`, `voice-pipeline.md`, `news-brief.md`, `config-directory.md`. |

## The bench (criterion 2)

`scripts/m9_lock_bench.py`, dev only: the base URL must be exactly
`http://localhost/dev` or `http://127.0.0.1/dev` (anything else exits;
`tests/test_m9_bench.py` checks 12 other shapes); it requires the dev
tool_service to report music dry-run on; before each block it reads the
production orchestrator's log and waits while production has a request in
flight; it clears nothing (the display route is `/api/chat`, which keeps no
history). Requests go to `/dev/voice/api/chat` (`stream: false`, the
buffered path production uses, with a fake origin stamp, Office) and
`/dev/api/chat`.

- Single: five routes x two mounts, 6 runs each (web search 2), one at a
  time, voice and display alternating: `direct` "what song is this"
  (read-only `now_playing`, direct dispatch), `specialist` "what's the
  weather like" (home agent, `get_weather`), `coord` "what is the capital of
  France" (coordinator alone), `delegate` "what's playing in the kitchen"
  (coordinator -> `ask_home` -> `now_playing`), `search` "search the web for
  when the eiffel tower was built" (research, SearXNG).
- Cache reuse per generation: `agent_round` events carry LiteLLM's
  `usage.prompt_tokens_details.cached_tokens`, which is llama.cpp's own
  count (check-first: 19 prompt, 11 cached, journal "8 tokens" evaluated).
- Two-client: "search the web for who designed the golden gate bridge" on
  `/dev/api/chat`, then "what's the weather like" on `/dev/voice/api/chat`
  one second later; 4 runs.
- Load: `--mode load`, eight requests at once (below).
- Production was idle through every run (the bench checked before each
  block; no wait was needed).

### How "no slower" is judged

The first comparison, on client latency medians with the two baselines'
difference as the noise band, flagged step 1 on two routes. Step 1 only
moves code that runs before the model; the flags traced to the model, not
the code:

- The model samples at temperature 1.0, so how many tokens it writes
  varies: the coordinator/voice prompt (2058 tokens, never cached) was
  evaluated in 1.19-1.23 s in every run (journal), but the answer was 8
  tokens in most baseline runs and 45-80 in step 1's.
- Two lines in the prompt change on their own minute clocks (the time in
  `agents.kronk_facts`, "fetched N minutes ago" in the weather block). A
  request in a new minute re-processes the prompt from there, and on
  gemma-4 (SWA) a partial match restored from llama.cpp's RAM cache
  re-processes all of it ("forcing full prompt re-processing due to lack
  of cache data"). Whether a run crosses a minute between its voice and
  display twins decides that route's cache figure.
- The specialist route's latency includes the weather API's (the home
  agent calls `get_weather`), 0.6 s normally, 2.5-6.3 s at times.

So each step is judged, per route and mount, on what a lock change can
move: the orchestrator's overhead (client time minus generation time minus
service tool time, the last read from the `tool.*` spans in Tempo), and the
prompt tokens the server had to evaluate (prompt minus cached, per request,
which is cache reuse). A step fails if its median is above the band the two
baselines span, widened by their difference (at least 0.05 s, or 10% of
the evaluated tokens). Latency and reuse are printed beside them. This is a
departure from the criteria's wording (departure 1).

## Each step against the baseline (criteria 9, 14)

Cells: client latency median s · overhead median s · prompt tokens
evaluated per request, median · cache reuse (cached / prompt over all
generations). n = 6 per cell (search 2). Flags are the bench's
`--compare` output; each is explained below the table.

| Route / mount | Base A | Base B | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 |
|---|---|---|---|---|---|---|---|
| direct / voice | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - |
| direct / display | 0.03 · 0.00 · 0 · - | 0.02 · 0.00 · 0 · - | 0.03 · 0.00 · 0 · - | 0.03 · 0.00 · 0 · - | 0.03 · 0.00 · 0 · - | 0.03 · 0.00 · 0 · - | 0.03 · 0.00 · 0 · - |
| specialist / voice | 6.94 · 0.05 · 4120 · 0.488 | 6.89 · 0.05 · 4119 · 0.4446 | 5.63 · 0.05 · 2158 · 0.7292 | 6.57 · 0.04 · 4120 · 0.4446 | 7.19 · 0.04 · 4120 · 0.4879 | 7.15 · 0.04 · 4120 · 0.4362 | 6.89 · 0.05 · 4545 · 0.5022 **CACHE** |
| specialist / display | 5.14 · 0.05 · 149 · 0.9223 | 5.13 · 0.06 · 149 · 0.8799 | 6.32 · 0.06 · 3628 · 0.6137 **CACHE** | 5.59 · 0.06 · 149 · 0.9812 | 5.79 · 0.05 · 149 · 0.8726 | 6.54 · 0.04 · 150 · 0.9535 | 4.79 · 0.04 · 621 · 0.8724 **CACHE** |
| coord / voice | 1.34 · 0.02 · 2058 · 0.0 | 1.35 · 0.02 · 2058 · 0.1079 | 1.81 · 0.03 · 2058 · 0.0 | 1.78 · 0.03 · 2058 · 0.0 | 1.35 · 0.03 · 2058 · 0.1079 | 1.79 · 0.03 · 2058 · 0.1662 | 1.37 · 0.03 · 2058 · 0.0 |
| coord / display | 0.58 · 0.03 · 1 · 0.8812 | 0.14 · 0.02 · 1 · 0.9995 | 0.38 · 0.02 · 1 · 0.9995 | 0.35 · 0.03 · 1 · 0.8812 | 1.17 · 0.02 · 662 · 0.6532 **CACHE** | 0.68 · 0.02 · 1 · 0.8329 | 0.37 · 0.02 · 1 · 0.9995 |
| delegate / voice | 6.78 · 0.06 · 4467 · 0.2294 | 5.30 · 0.04 · 4467 · 0.3367 | 5.75 · 0.05 · 4468 · 0.2294 | 5.05 · 0.06 · 4467 · 0.3365 | 6.07 · 0.06 · 4467 · 0.2294 | 6.11 · 0.05 · 4467 · 0.2294 | 5.60 · 0.05 · 4467 · 0.3365 |
| delegate / display | 5.56 · 0.05 · 15 · 0.8401 | 5.09 · 0.05 · 12 · 0.8898 | 3.93 · 0.05 · 13 · 0.8315 | 4.73 · 0.05 · 14 · 0.9394 | 5.04 · 0.05 · 14 · 0.9978 | 4.45 · 0.05 · 15 · 0.8314 | 4.26 · 0.04 · 15 · 0.6651 |
| search / voice | 4.73 · 0.04 · 1345 · 0.358 | 4.36 · 0.03 · 1366 · 0.3544 | 4.88 · 0.04 · 1366 · 0.3544 | 4.69 · 0.05 · 1366 · 0.3544 | 4.78 · 0.04 · 1374 · 0.3531 | 4.37 · 0.04 · 1376 · 0.3528 | 3.70 · 0.04 · 1348 · 0.3574 |
| search / display | 5.85 · 0.04 · 835 · 0.6029 | 3.92 · 0.04 · 617 · 0.7084 | 6.08 · 0.03 · 621 · 0.7069 | 4.35 · 0.05 · 848 · 0.599 | 4.21 · 0.04 · 622 · 0.7067 | 3.93 · 0.04 · 627 · 0.7051 | 4.36 · 0.03 · 617 · 0.7084 |

| Two-client | Voice latency median (runs) | Voice lock wait ms | Research s | Research tokens evaluated |
|---|---|---|---|---|
| Base A | 10.17 (13.34, 9.23, 6.40, 11.12) | not measurable (wait before the trace) | 8.30, 3.37, 3.05, 6.02 | 1356, 1351, 1351, 1351 |
| Base B | 6.51 (9.47, 6.11, 5.94, 6.92) | not measurable | 3.49, 2.99, 3.56, 3.80 | 1351 x 4 |
| Step 1 | 10.57 (16.55, 11.12, 5.95, 10.01) | 7674, 2082, 2015, 2826 | 8.69, 3.09, 3.04, 3.85 | 1307, 1771, 1351, 1351 |
| Step 2 | 8.16 (9.34, 7.94, 8.38, 6.53) | 76, 497, 976, 648 | 12.94, 5.62, 5.40, 6.25 | 1356, 2, 2, 2 |
| Step 3 | 7.70 (7.49, 3.59, 11.05, 7.91) | 751, 474, 2193, 522 | 5.77, 5.93, 10.05, 6.39 | 1361, 1640, 1362, 1361 |
| Step 4 | 8.35 (8.12, 8.79, 8.59, 5.16) | 646, 1177, 1390, 372 | 6.25, 7.36, 7.31, 7.41 | 1361 x 4 |
| Step 5 | 8.95 (8.04, 9.85, 10.34, 8.05) | 498, 1866, 1873, 1307 | 7.03, 7.65, 8.77, 6.50 | 1351, 1770, 1351, 1302 |

(Voice lock wait: the sum of the voice request's `model_lock` waits. In
the baseline the lock was taken before the trace started, so its wait was
not recorded; it is the voice request's time minus its own work, 3-7 s.)

- **Step 1, kept.** Overhead and evaluated tokens equal the baseline on
  every route but specialist/display (evaluated 3628 vs 150): runs 3-5 of
  that route crossed a minute boundary between the voice and display twin
  (per-run detail: display plan_1 cached 0 at 11:23:31, 11:24:01, 11:24:28
  after its voice twin cached 3925). Step 2, which contains step 1's code,
  was back to 150. The two-client voice wait is still 2-7.7 s: the
  research request's web search runs under the pipeline lock.
- **Step 2, kept.** Every route inside the band. The two-client voice wait
  fell to 76-976 ms: research's tool calls no longer hold anything. The
  research request is slower (5.4-12.9 s vs 3.0-8.3 s): the voice request
  now takes the server between research's rounds. Research's cache did not
  drop: its rounds evaluated 2 tokens in three runs of four (749 of 749
  and 1350 of 1351 cached), so the plan's interleaving hazard did not show
  in this case; the rule "stop after steps 1 and 4" did not apply.
- **Step 3, kept.** One flag, coordinator/display evaluated 662 (base 1):
  three of six runs crossed a minute between the voice twin and the
  display one (11:36:59 -> 11:37:00 cached 582; 11:37:58 -> 11:38:00
  cached 582; 11:38:28 cached 688). Serial runs cannot be reordered by a
  priority queue; no request waited in this bench.
- **Step 4, kept.** Every route inside the band.
- **Step 5, kept.** No bench route writes a session. Two flags on the
  specialist route: the `get_weather` result grew by about 420 tokens from
  11:52 (synthesis prompt 4120 -> 4546 tokens; a fresh tool result is
  evaluated, never cached), so each request evaluates about 420 more.

## Load check (criterion 15)

`python3 scripts/m9_lock_bench.py --label m9-load --mode load` at 17:03 UTC,
step-5 code: four voice and four display requests started together
(`docs/bench/m9_lock_2026-11-17_170328_m9-load.json`). All eight HTTP 200.
The llama.cpp journals and LiteLLM's log for the run contain no line with
error, failed, exceed, context shift, n_ctx or no slot (the bench greps
them; empty lists).

| Request | Class | Lock waits ms | Total s |
|---|---|---|---|
| what song is this | voice | none (direct) | 0.07 |
| what is the capital of Spain | voice | 0 | 1.72 |
| what's playing in the office | voice | 1652, 4333 | 11.40 |
| what's the weather like | voice | 3256, 3188 | 14.05 |
| what is the capital of Italy | display | 11305 | 11.58 |
| what is the weather going to be like tomorrow | display | 16059 | 22.73 |
| search the web for how tall the statue of liberty is | display | 13984, 6782 | 25.94 |
| what's playing in the kitchen | display | 15048, 9799 | 27.26 |

Grant order (from the `model_lock` events, seconds from the first):
voice Spain 0.06; voice office 1.72; voice weather 3.33; voice office
(2nd round) 7.67; **display Italy 11.37**, ahead of the waiting voice
weather round, because it had been overtaken three times
(`max_overtakes: 3`); voice weather 11.58; then display search 14.05,
kitchen 15.12, weather 16.13, search 22.73, kitchen 25.94. Voice first,
first come first served within each class, the starvation guard as
designed.

## The deadline value (criterion 6)

`deadline_s: 25` in `config/voice.yaml`, through the M21 loader (schema
`Voice.deadline_s`, 5-120 or null, default 25 equal to the file, pinned by
`test_deadline_is_in_config_with_its_default`).

- Tempo, production voice requests, 2026-11-08 to 2026-11-17 (n = 54):
  p50 2.70 s, p95 11.68 s, max 17.36 s (music through the home agent).
  None over 20 s. Those spans exclude any lock wait; under the old lock a
  voice request behind another one waited outside its trace.
- The replay's slowest row before M9: 13.1 s (M10 baseline), 12.98 s (M8).
- What a person waits for at a puck: CLAUDE.md tenet 12, "~15-25 s
  worst-case Kronk tier — already at the edge of tolerable". 25 s is the
  top of that range and 7.6 s above the slowest legitimate production
  request.
- What it cuts: on dev, coordinator requests delegating to research two to
  four times (39-82 s in Tempo). On production none in nine days.
- What is said: nothing acting sent: "Sorry, that took too long, so I
  stopped. Please try again." A tool outside `tools.READ_ONLY_TOOLS` sent
  (music, rating, taste, shopping list, mirror, remote command, diagram, or
  any tool not on the list): "That's taking too long to confirm. I've
  already sent the request, so it may still go through." Pinned by
  `test_deadline_after_an_acting_tool_was_sent_does_not_deny_it` (no
  "didn't", "did not", "couldn't", "wasn't", "nothing was", "stopped" in
  it) and `test_deadline_sentence_by_what_was_sent` (every acting tool and
  an unknown one).

Drew may prefer a lower value for spoken requests; it is one line and a
reload.

## Criteria

1. **Met.** "Check first" above: slots and context per server; slot
   choice (LCP similarity > 0.10, else LRU; idle slots saved to the RAM
   prompt cache and cleared on each launch); `cache_prompt` default on;
   `id_slot` passes through LiteLLM; every piece of shared state with why
   it is or is not safe; disconnect behaviour per route, the buffered one
   shown live.
2. **Met.** The bench above; baseline committed (`36a1a2b`) before the
   first lock change (`f79b68a`); two baseline runs for the variance.
3. **Met.** Step 1 (`f79b68a`): routing (rules table, M14 gate, lead-in),
   weather and local context, direct dispatch before the lock.
   `test_routing_and_context_run_while_another_request_holds_the_lock`
   (classify and the weather context run while the lock is held; the
   model phase waits). Behaviour otherwise unchanged: criterion 12's pin.
4. **Met.** Step 2 (`9591cc6`): `test_two_servers_do_not_wait_for_each_other`,
   `test_one_server_runs_generations_one_at_a_time` (two model names on one
   server), `test_a_tool_wait_holds_no_lock` (request B's generation
   finishes while A's tool call is stuck), `test_servers_come_from_the_litellm_catalog`.
   Live: `model_lock` events name `127.0.0.1:11438`; two-client voice waits
   76-976 ms while research's web search runs.
5. **Met.** Step 3 (`5830770`): `test_three_waiters` (display, display,
   voice -> voice, display, display), `test_voice_waiters_go_first_fifo_within_a_class`,
   `test_queue_order_through_held_and_the_priority_class`,
   `test_no_preemption_the_holder_keeps_the_lock`,
   `test_a_display_request_cannot_starve` (served after exactly
   `max_overtakes` voice grants), cancelled and granted-then-cancelled
   waiters. Starvation shows in telemetry as `{ name = "lock.wait" &&
   span.kronk.priority = "display" && span.kronk.wait_ms > 10000 }`.
   Live: the load check's grant order.
6. **Met.** Step 4 (`11db421`): value and sentence above.
   `test_a_stuck_model_ends_with_the_sentence_and_frees_the_lock` (buffered
   and streamed, valid response, lock free, next request served),
   `test_deadline_after_a_read_only_tool_says_it_stopped`,
   `test_streamed_writer_keeps_what_was_sent_and_ends_with_the_sentence`,
   `test_display_requests_have_no_deadline`, `test_deadline_trace_is_findable`.
   Live on dev: `inc-1021-ninetys-no-verb` in the replay, `request_ended
   reason deadline duration_s 25.0 tools_sent [web_search x3]`, spoken
   "Sorry, that took too long, so I stopped. Please try again.", root
   `kronk.ended = deadline`, status ERROR (trace
   `d814a7e7531aff72b705d90f80332586`).
7. **Met.** `test_disconnect_cancels_the_pipeline_on_every_route` (seven
   route shapes: `/message`, `/api/chat` and `/v1` streaming and not,
   `/voice/api/chat` buffered and streamed): `request_ended reason
   client_gone`, no `request_complete`, lock free, `/message` stores
   nothing, a request right after is served.
   `test_disconnect_during_a_tool_call_is_not_a_retry_or_a_failure` (one
   POST, no `tool_error`, no failure sentence), `test_disconnect_trace_says_the_client_went_away`
   (`kronk.ended = client_gone`, not ERROR), `test_a_completed_response_is_not_a_disconnect`.
   M8's streamed writer: its 100 tests pass, plus the streamed deadline
   test. Live: trace `e13665cb27052742e3c419b3f0c30c65` (dropped at 1.0 s,
   ended at 1.0 s; llama.cpp "stop: cancel task"; next request 1.04 s).
8. **Met.** Step 5 (`3b79209`): `test_threads_appending_to_one_and_many_sessions`
   (8 threads, 400 exchanges, 4 sessions, real SQLite in `tmp_path`: every
   message once, pairs adjacent, cache and file agree),
   `test_window_reads_while_threads_write`,
   `test_pipelines_interleaving_on_shared_and_separate_sessions` (30
   pipelines, 3 sessions). A mutant with unlocked per-message writes fails
   3 of the 5 tests.
9. **Met.** Table above, each step kept; the two-client numbers; the
   plan's stopping rule considered and not triggered (research cache held).
10. **Met.** `test_many_mixed_requests_at_once_match_each_run_alone`: 54
    requests (voice and display, nine origins, `/message` on two shared
    sessions, failing tools in both styles, model errors, direct dispatch)
    at once and one by one: replies, tool_service payloads (origin device
    and area, `said`), trace roots (voice, origin, route, status) and stored
    history equal. A mutant making `origin.voice` and `origin.current`
    process-wide fails it.
11. **Met** for the orchestrator's guards:
    `test_a_request_in_its_tool_round_keeps_its_own_limits` (a devops
    request held in its tool round while a research request and the pinned
    mirror sentence run; its next round's `web_search` and
    `update_magicmirror` are both refused; one mirror POST, B's). The fetch
    address block (M5) and dry-run live in tool_service and are decided per
    request there; nothing in the orchestrator holds them.
12. **Met.** `test_every_route_sends_the_bytes_it_sent_before_m9`: 6
    scenarios x 8 route shapes (`/message` with stored history, `/api/chat`
    and `/v1` streaming and not, voice buffered, voice `stream: true` with
    `VOICE_STREAM` off and on), stubbed model and tool_service, against
    `tests/fixtures/m9/replies.json` written from `b762fe9` (`git archive`
    into scratch). 48 of 48 equal after normalising times, ids and the
    model name.
13. **Met.** `test_a_failing_holder_frees_the_lock_for_the_waiter` (model
    error; exception inside the lock after tokens), `test_a_tool_error_frees_everything`,
    `test_a_cancelled_waiter_and_a_cancelled_holder_free_everything`, the
    deadline and disconnect tests. On dev after each kind: after a
    disconnect (1.04 s), after the deadline (the replay's next rows
    passed), after a tool failure ("pause the music in the office" -> "The
    Office speaker is already paused.", next request lock wait 0 ms,
    0.72 s). A model error was not provoked live (it needs LiteLLM or a
    llama server broken).
14. **Met, with the caveat in the summary.** Voice waits for at most one
    generation on its server (max 2.2 s across steps 2-5, against 2-7.7 s
    with the step-1 lock); end-to-end voice latency in this two-client case
    is within the baseline's range.
15. **Met.** Load check above.
16. **Met.** `lock.wait` span with `kronk.lock`, `kronk.priority`,
    `kronk.wait_ms` (`test_the_wait_is_a_span_and_an_event`), separate from
    the generation span; `kronk.ended` on the root, one TraceQL clause each
    (`docs/features/model-locks.md`). Live in Tempo for the deadline and
    disconnect traces above.
17. **Met.** `4516 passed, 3 skipped, 53 xfailed` (output below). The new
    tests use `no_network`, open no sockets (ASGI calls in process,
    `httpx.ASGITransport`), and put SQLite files in `tmp_path` or a
    `tempfile` directory.
18. **Met.** Replay below: every M10 baseline row has its status except
    `inc-0930-search-deep-house-ytm` (known flapper, fail -> pass).
19. **Met.** Only `tool_service-dev` and `orchestrator-dev` were rebuilt
    (`docker compose --profile dev up -d --build tool_service-dev
    orchestrator-dev`, seven times). All live requests went to `/dev/` or
    through `kronk-dev` (the replay). Production containers, nginx, LiteLLM,
    HA (41 h), MA (13 days), the llama units and Piper were not restarted
    or changed. Dry-run was on at every check. nginx shows two sets of POSTs
    to production chat routes in the window: 15:58 UTC (curl, before this
    build's first command at 16:01) and 17:10:47 (HA's client, departure 7);
    none from this build. Nothing was sent to the Pi.
20. **Met.** `docs/features/model-locks.md`; `TECH_DEBT.md`; the lock's
    comment in `main.py:89-94`; `README.md` (design decision and
    last-updated line); expected latency in the feature doc.
21. **Met.** `MODEL_LOCK=pipeline` (env, read per request) restores the
    pipeline-wide lock around a request's model phases (step 1's form);
    `test_the_switch_restores_one_request_at_a_time`, `test_mode_switch`,
    `test_mode_defaults_to_server`, and step 1's tests run in that mode. It
    adds one branch in `llm.stream` and one in `_run_pipeline`. Rollback
    per step: below.

## Test output

```
$ ./scripts/run_tests.sh
…
=========== 4516 passed, 3 skipped, 53 xfailed, 6 warnings in 39.19s ===========
```

New test files alone: `81 passed, 3 warnings in 8.90s`.

## Replay output

```
$ python3 scripts/voice_replay.py --label m9-build
…
voice_replay: {'pass': 134, 'xfail': 22, 'skip': 3, 'xpass': 1}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_170353_m9-build.json
exit=0
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows,
`{'pass': 91, 'xfail': 20, 'skip': 2, 'fail': 1}`): 113 rows same status;
`inc-0930-search-deep-house-ytm` fail -> pass (known flapper, also pass in
the M8 runs). 46 rows are newer than the baseline: 42 pass, 2 xfail, 1 skip
(`m4b-fetch-and-run-on-the-mirror`, typed link), 1 xpass
(`m19-what-happens-if-i-ask-kronk-to-pause`, also xpass in M8's e2e run).
`inc-1021-ninetys-no-verb` kept its xfail and ran into the deadline (above).
The run did not stop or skip for a busy player; no rerun was needed.

## Departures from the criteria and why

1. **"No slower" judged on overhead and evaluated tokens, not latency
   medians.** At temperature 1.0 and with per-minute prompt lines, six runs
   per route do not separate a lock change from the model's own variance
   (coordinator/voice: same 2058-token prompt evaluated in 1.19-1.23 s in
   every run, answers of 8 vs 45-80 tokens). Latency and cache reuse are
   reported for every step; each flag is traced to its cause above.
   `scripts/pipeline_bench.sh` was not run (brief).
2. **Two-client voice end-to-end is not faster in this bench**; the wait
   for the lock is (criterion 14's stated goal). A research query with
   several tool calls would show the end-to-end gain; the bench's is short.
3. **A turn stopped part way is not stored** in the web UI session. Before
   M9 a `/message` disconnect stored whatever had streamed. A stopped turn
   is a failed turn, which the existing rule already leaves out.
4. **The disconnect middleware has no switch**; rollback is by tag.
5. **Slot pinning (`id_slot`) measured once, not built.** LiteLLM passes
   it through; the one pinned test request reported 0 cached tokens for a
   prompt another slot had just processed, i.e. pinning appears to skip
   the RAM prompt-cache lookup. Not measured further; no server setting
   changed.
6. **Test venv and container run different Starlette** (0.46.2 vs 0.38.6).
   The disconnect tests send `spec_version 2.3`, the shape the container's
   Starlette and uvicorn 0.30.6 use, so the streaming path under test is
   the container's. Not changed (tenet 3).
7. **Observation, not mine:** production received a voice request through
   HA at 17:10:36 UTC ("Play Anime Pop Radio. More words, extra sentences.
   Garbage junk. in the Kitchen", trace `7cd7b3a6874765aef14b6476cd28f90e`,
   played on the kitchen PE). The replay had finished at 17:10:08 and used
   `kronk-dev`; the sentence is in no repo file. Recorded so the
   coordinator can match it to whoever sent it.
8. **Findings outside M9, not acted on:** the two per-minute prompt lines
   cost a full prompt re-process on gemma-4 at each minute boundary; and
   llama.cpp b9611 clears idle slots into its RAM cache on every launch.
   Both are in `docs/features/model-locks.md` (gotchas). Whether either is
   worth a roadmap item is Drew's call.

## For the coordinator at promotion

- Code: orchestrator only (tool_service unchanged). Config: two new keys
  (`config/voice.yaml deadline_s`, `config/tuning.yaml model_lock`); the
  release's `config/` must come with the code (the pre-M9 loader rejects
  unknown keys, the M9 loader needs neither: both have defaults).
- `docker-compose.yml` sets `MODEL_LOCK=server` on production's
  orchestrator; `VOICE_STREAM` stays `off`.
- Deploy: the release flow for the orchestrator, then `docker compose
  restart nginx` per CLAUDE.md. No HA or MA restart. No LiteLLM or llama
  change.
- Verify through the shim with nothing playing: `/api/chat` "what is the
  capital of France" answers, and the orchestrator log shows a
  `model_lock` event with `lock: 127.0.0.1:11438`. Disconnect check
  (silent): `curl --max-time 1` a coordinator question to production's
  `/voice/api/chat`, then expect `request_ended reason client_gone` in the
  log. Do not use music, rating or mirror sentences as checks.
- Ask Drew for a real "Okay Nabu" on a plain question, and, if he wants to
  hear the deadline sentence, a long research question by voice.

## Rollback

| Step | How |
|---|---|
| 2 and 3 (per-server priority locks) | `MODEL_LOCK=pipeline` on the orchestrator service and recreate it (`up -d`, no rebuild): the pipeline-wide lock around each request's model phases; no per-server lock. |
| 4, deadline | `deadline_s: null` in the release's `config/voice.yaml`, then `scripts/config_reload.sh prod`. |
| 4, disconnects | By tag (no switch). |
| 1, 5 | By tag; both are safe on their own (plan) and neither changes a reply (criterion 12). |
| All | The previous release directory (`kronk-releases/sprint-M8`) and its tag. |
