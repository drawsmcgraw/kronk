# Feature: model locks, the voice deadline, and disconnects

**Shipped:** 2026-11-17 (sprint M9, branch `refactor`) · **Plan:**
`Kronk evaluation findings and implementation plan.md`, section "M9. Narrow
the global lock" · **Criteria:** `docs/sprint/M9-criteria.md` · **Build
report:** `docs/sprint/M9-build.md`.

## What it does

Before M9 one asyncio lock (`main._llm_lock`) wrapped every request's whole
pipeline: routing, context fetches, every generation and every tool wait
(the eight-second playback verify, web searches). One person's research
question blocked everyone else's voice command until it was completely
finished.

Now:

- **Model-free work runs before any lock.** Routing (the rules table, the
  M14 gate, the lead-in), the weather and local context, and the M7 direct
  dispatch never wait for a model.
- **One lock per model server, held for one generation.** `llm.stream`
  takes the lock of the llama.cpp server its model runs on for the
  duration of one streaming completion and releases it when the stream
  ends or is closed. Tool calls hold nothing. A request on devstral never
  waits for one on gemma.
- **The lock is a priority queue.** Waiting voice requests (the speech
  mount, `/voice/api/chat`) are served before waiting display requests
  (`/message`, `/api/chat`, `/v1/chat/completions`), first come first
  served within each class. A running generation is never interrupted. A
  display request is overtaken by at most `max_overtakes` voice requests
  that arrived after it, then served next.
- **Voice requests have a total deadline** (25 s). When it passes, the work
  stops and one sentence is spoken.
- **A client that disconnects cancels its request** on every chat route.

## How it works

### The locks (`orchestrator/model_lock.py`, `orchestrator/llm.py`)

- Servers are told apart by host and port of each model's `api_base` in
  `litellm/config.yaml` (read once through `servers.load_catalog`):
  `gemma-4-e4b` → `127.0.0.1:11438`, `devstral-2512-q4` → `127.0.0.1:11440`.
  A model not in the catalog gets a lock of its own name.
- `llm.stream` wraps `llm._stream_body` (the HTTP call) in
  `model_lock.held(lock, server)`. Each round of an agent loop takes and
  releases the lock, so between rounds (while a tool runs) another
  request's generation can go.
- `PriorityLock` holds one owner and a list of waiters. On release it picks
  the earliest voice waiter, unless a display waiter has been overtaken
  `max_overtakes` times, in which case that one goes. A cancelled waiter
  leaves the queue; a waiter granted the lock and cancelled before it ran
  passes it straight on. Futures are made on the running loop, so the lock
  is not bound to one event loop.
- The class comes from `origin.voice` (the speech mount), never from
  request content.
- Each llama.cpp server has 4 slots with the full context each, and
  production and dev are separate processes with separate locks, so two
  generations already ran at once on one server whenever both were busy.
  The lock is per process, not per GPU. It is a scheduling choice: one
  generation per server per process keeps each generation at full speed
  and keeps the prompt cache warm for the request that holds it.

### The voice deadline (`main._with_deadline`, `config/voice.yaml`)

- `deadline_s: 25`, read per request; null switches it off. Set from Tempo
  on 2026-11-17: the slowest production voice request in nine days took
  17.4 s (n = 54, p95 11.7 s); tenet 12 puts the worst tolerable case at
  15 to 25 s; HA's Ollama client has no timeout, so nothing else ends a
  stuck request.
- The wrapper passes the pipeline's events through. At the deadline it
  cancels the pipeline at the await it is in (a generation, the lock queue,
  a tool call) or closes it if it is between events. The pipeline's own
  `finally` blocks close the trace and release the lock. Nothing is
  retried or undone.
- The sentence (`errors.deadline_text`, friendly style on the speech mount):
  - no tool that changes anything was sent: "Sorry, that took too long, so
    I stopped. Please try again."
  - a tool outside `tools.READ_ONLY_TOOLS` was sent (music, a rating, the
    shopping list, the mirror, a remote command, or any tool not listed):
    "That's taking too long to confirm. I've already sent the request, so
    it may still go through." It never says the action did not happen.
  `tools.execute` records every tool in the request's `run_state.Run`
  before it calls tool_service, so a call in flight counts as sent.
- Buffered path (production): the reply is the sentence alone. Streamed
  path (`VOICE_STREAM=on`): what was already sent stays, held text is
  dropped, the sentence is the last chunk, then one `done` record.

### Disconnects (`orchestrator/client_watch.py`)

- A pure ASGI middleware on the four chat paths reads the request body,
  hands it to the app, then keeps listening on the connection. A
  disconnect before the response is complete marks the request's `Run`
  (`client_gone`) and cancels the app's task. A connection closing after
  the response is complete is ignored.
- Before M9 only streaming responses noticed a disconnect (Starlette's
  `StreamingResponse` listens for one); a buffered request, which is every
  production voice request, ran to its end with the lock held and nobody
  listening. llama.cpp also stops the generation when its HTTP stream is
  closed (`stop: cancel task` in its journal).
- A tool call already sent is not retried and is not reported as failed.

### How a stopped request ends

`_run_pipeline` catches `CancelledError` and `GeneratorExit`, sets
`kronk.ended` on the pipeline root span (`deadline`, `client_gone`, or
`cancelled`), marks a deadline ERROR, emits a `request_ended` event (reason,
route, tools sent, duration), stores no partial exchange in the session,
and re-raises. Generation and tool spans end with status message "stopped:
the request ended". Nested generators are closed with `contextlib.aclosing`,
so the server lock is released immediately, not when the generator is
garbage-collected.

### Sessions (`orchestrator/sessions.py`)

`append_exchange` writes a turn's user and assistant messages as one
transaction and one cache update under a `threading.RLock`; `window` and
`clear` take the same lock. Two requests on the same session cannot split
each other's pair.

## Reading the wait in a trace

- `lock.wait` span (child of the generation span, or of the pipeline root
  with `MODEL_LOCK=pipeline`): `kronk.lock` (the server, e.g.
  `127.0.0.1:11438`, or `pipeline`), `kronk.priority` (`voice` |
  `display`), `kronk.wait_ms`. The generation's own time is its `llm.*`
  span minus the `lock.wait` child. `kronk.ttft_ms` on the generation span
  still counts from the span start, so it includes the wait.
- `model_lock` event in the orchestrator log, same fields (plus
  `acquired: false` when the waiter was cancelled).
- `kronk.cached_tokens` on each generation span and `cached_tokens`,
  `prompt_tokens`, `completion_tokens` on each `agent_round` event:
  llama.cpp's own prompt-cache split, passed through by LiteLLM.

TraceQL:

```
{ span.kronk.ended = "deadline" }            # voice requests stopped at the deadline
{ span.kronk.ended = "client_gone" }         # clients that went away
{ span.kronk.ended != nil }                  # every early end
{ name = "lock.wait" && span.kronk.wait_ms > 2000 }   # long waits
{ name = "lock.wait" && span.kronk.priority = "display" && span.kronk.wait_ms > 10000 }
```

## Expected latency (tenet 12)

- A request alone: no change. The bench (`scripts/m9_lock_bench.py`, dev)
  showed the orchestrator's own overhead at 0.00 to 0.06 s per route before
  and after every step, and the same prompt tokens evaluated per route;
  latency differences between runs come from how much the model writes
  (temperature 1.0) and from minute boundaries in the prompt.
- A voice request behind someone else's research question: it waits for
  at most the generation in progress on its server, not for the other
  request's tool calls. Measured on dev, voice weather started one second
  after a web-search research query: lock wait 2.0 to 7.7 s with the step-1
  lock, 0.1 to 2.2 s after (steps 2 to 5).
- Under load (4 voice + 4 display at once on dev): voice waits 0 to 4.3 s,
  display waits up to 16 s, all answered.
- A voice request never runs past 25 s.

## Rollback

`MODEL_LOCK=pipeline` (env on the orchestrator service, read per request)
restores the pipeline-wide lock around a request's model phases, as after
step 1, with no per-server lock. Unknown values mean `pipeline` and are
logged once. The deadline: `deadline_s: null` in `config/voice.yaml` and
`POST /config/reload`. The disconnect middleware has no switch; roll back
by tag.

## Gotchas

- **The interleaving cache cost is real on this build.** When llama.cpp
  (b9611) starts a task on one slot it saves the other idle slots to its
  host-memory prompt cache and clears them. A conversation that comes back
  is restored from RAM only when the cached prompt is a full prefix of the
  new one; a partial match on gemma-4 (SWA) re-processes the whole prompt
  ("forcing full prompt re-processing due to lack of cache data"). The
  lock keeps one generation per server per process, so a request alone
  keeps its slot; two requests alternating rounds may each pay a prompt
  re-process. Measured in the two-client case, research's cache reuse did
  not drop.
- **Two lines in the prompt change every minute**: the clock in
  `agents.kronk_facts` and "fetched N minutes ago" in the weather block.
  A request in a new minute re-processes the prompt from that line on
  (often the whole prompt, see above). This predates M9 and dominates the
  run-to-run cache variance.
- **Pinning a slot (`id_slot`) is possible but not done**: LiteLLM passes
  it through, but in one test a pinned request skipped the prompt-cache
  lookup and reported 0 cached tokens.
- **Tests that patch `agents.llm.stream` bypass the lock.** To test the
  lock, patch `llm._stream_body`.
- **`tools.httpx` is the httpx module.** Patching `tools.httpx.AsyncClient`
  swaps the class for everyone in the process, the test's own client
  included.
- **tool_service's own model calls** (news brief, genre tagging, the M11
  question) go straight to LiteLLM and take no orchestrator lock.

## Blog hooks

- One lock was the scheduler for a year. Taking it apart one step at a
  time, with a bench that says "no slower" in numbers, not adjectives.
- "No slower" needs a definition: why latency medians lie at temperature
  1.0, and what to measure instead (overhead and prompt tokens evaluated).
- The voice deadline that will not lie: once the music request is sent,
  "I stopped" is false.
- A dropped voice request used to hold the GPU for the rest of its answer.
- llama.cpp's host prompt cache, SWA, and why a minute changing in the
  system prompt costs a full prompt re-process.
