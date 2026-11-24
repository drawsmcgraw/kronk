# Feature: Verbose error reporting

**Shipped:** 2026-07-05 (branch `p1-verbose-errors`; P0 groundwork same day) · **Plan:** `../plans/VERBOSE_ERRORS_PLAN.md` · **Audit:** `../REVIEW_2026-07-05_codebase.md` (P0/P1 sections)

## What it does

Every user-facing failure carries the most specific cause available at the
layer that failed, and every failed turn is findable in Grafana (Tempo) in one step
(traces are marked ERROR; unexpected crashes speak their rid). "An
unexpected error occurred" is now, by tenet 7, a bug.

Live example: "what's the weather in Zzyzzxqq Falls?" → "I could not find
any weather information for Zzyzzxqq Falls" — the geocoder's 404 survives
tool_service → tool result → agent → speech, instead of dying en route.

## How it works — the error path, layer by layer

- **tool_service routes** (health_service was removed in sprint M1) return specific `detail` strings
  and status-check their own upstreams (`_check_upstream` for NWS/Open-Meteo;
  `/search` names SearXNG's status + body; `/music` was already the gold
  standard). tool_service finally calls `logging.basicConfig` so its INFO
  logs actually exist.
- **orchestrator/tools.py `_fail(action, resp)`** — every HTTP-failure
  branch surfaces `detail` (FastAPI convention) or `resp.text[:200]` plus
  the status. Kills the `[Web search failed]` class. `shopping_list_clear`
  now checks its response instead of asserting success (tenet 6).
- **Specialist failure → coordinator** (`main.py`): the error context block
  says `FAILED`, instructs "report the failure and its cause plainly — do
  NOT invent an answer," and sets `pipeline_error`. The old block labeled
  the error a "specialist result — use this to answer." Exception (M24): an
  agent with `coordinator_fallback=False` (only `talkie`) is asked for by
  name and no other model can answer as it, so its failure is the reply,
  rendered like an unreachable or refusing model server (table below, with
  `talkie` in place of `coordinator` in the debug text). Before M24 the
  coordinator, told Talkie had failed, answered "ask talkie …" itself 2
  times in 10 on dev and named the failure in 4 (`docs/sprint/M24-build.md`).
- **Trace marking**: `_run_pipeline` tracks `pipeline_error` on every path
  (router failure, specialist failure — even when the coordinator recovers —
  coordinator error, unexpected crash) and passes
  `level="ERROR", status_message=…` to `telemetry.end_pipeline`. Filter
  with TraceQL `{ status = error }` to see every failure.
- **Last-resort guard**: an unexpected raise mid-pipeline yields
  `Error: the pipeline failed unexpectedly (Type: msg) [rid …]` and still
  terminates the SSE/NDJSON stream properly (P0.5).
- **Terminal-tool speech**: unmapped result shapes (e.g. transport
  timeouts) are no longer spoken raw; internals go to the log, speech gets
  "That didn't work — the tool failed with <short cause>."
- **Router failure** says `Error: routing failed: <cause> [rid …]` instead
  of guessing "is the server still loading?".
- **Health imports admit partial failure**: row-level exceptions surface as
  `sample_errors` (first 3, with row context, also logged); vector-store
  upsert failures surface as `vector_store_error` instead of silently
  drifting chroma from SQLite; bloodwork reports honest chunk counts.
- **Shopping list page**: tool_service down → 502 → the page's "offline"
  state, no longer indistinguishable from an empty list.
- **`/api/query` guards its params** (days ≤ 3650, end_date must parse) →
  422 with a correctable message, because a 4B model will eventually send a
  bad date (tenet 5).

## Verbosity policy — the ERROR_STYLE toggle

Error rendering has two styles, resolved per request in
`orchestrator/errors.py` (the single seam for all user-facing failure
text):

- **`debug`**: full detail — error types, causes, HTTP statuses, rid. The
  default on `/message`, `/api/chat` and `/v1/chat/completions`.
- **`friendly`**: one short spoken sentence, no technical identifiers, that
  still says what kind of thing failed where the code knows. The default on
  the speech mount, `/voice/api/chat` (sprint M2).

Which style a request gets is keyed on `origin.voice`, the request-scoped
"this came in on the speech mount" value (see `docs/features/voice-pipeline.md`,
"The voice signal"), not on the transport: `/api/chat` and `/v1` are shims
too, but nothing speaks them, and the benches read their debug text.

Config, read at request time (`orchestrator/errors.py`):

| Setting | Covers | Default |
| --- | --- | --- |
| `ERROR_STYLE` | `/message`, `/api/chat`, `/v1/chat/completions` | `debug` (compose passes `${ERROR_STYLE:-debug}`) |
| `ERROR_STYLE_VOICE` | `/voice/api/chat` only | `friendly` (code default) |

To hear debug wording on the speakers again, set `ERROR_STYLE_VOICE=debug`
(uncomment the line in `docker-compose.yml` for production, or add it to
`.env.dev` for dev) and `up -d --build` the orchestrator. An unknown value
falls back to `debug`.

The friendly sentences (debug twin in brackets):

| Failure | Friendly | Debug |
| --- | --- | --- |
| Routing raised | "Sorry — I couldn't work out where to send that request. I've noted the details." | `Error: routing failed: <cause> [rid <rid>]` |
| Pipeline crash (cause unknown) | "Sorry — something went wrong on my end. I've noted the details." | `Error: the pipeline failed unexpectedly (<Type>: <cause>) [rid <rid>]` |
| Model server unreachable (connect error, timeout, 5xx) | "Sorry — I couldn't reach my language model. Try again in a moment." | `[coordinator agent error: <cause>]` |
| Model server refused (LiteLLM 4xx) | "Sorry — my language model couldn't handle that request." | `[coordinator agent error: LiteLLM returned 4xx: <detail>]` |
| Terminal tool raised (e.g. tool_service down) | "That didn't work — I couldn't get through to the music service." (or "the magic mirror updater", "the news service", or "the service that handles that") | `That didn't work — the tool failed with <cause>.`; for `news_brief`, `[Tool news_brief error: <cause>]` |
| Terminal tool refused its arguments (`[play_music error: query is required]`) | "That didn't work — I didn't get enough detail to ask the music service. Try saying it again." | `play_music error: query is required` |
| Mirror update did not start (preflight failed, tool_service answered an error) | "I couldn't start the magic mirror update. The details are in the logs." | `I couldn't update the magic mirror. <tool_service detail>` |
| News brief unavailable (tool_service answered an error) | "The news brief isn't available right now. Ask me to research the news live if you need it sooner." | `The news brief isn't available right now — <detail>. Ask me to research …` |
| News refresh failed (cached brief follows) | "I couldn't fetch fresh news, so here's the latest brief I have." + the brief | `I couldn't fetch fresh news (<cause>) — here's the latest brief I have.` + the brief |
| Model returned no token and no tool call | "Sorry — my language model didn't come up with an answer. Try asking again." | `[<agent> agent returned no response]` |
| Music failures with their own sentence | unchanged in both styles ("I couldn't play that. The speaker may be powered off.") | same |
| Mirror update refused (not the pinned sentence) | a refusal, not a failure: `To update the magic mirror, say "update the magic mirror".` in both styles | same |

Every one of these leaves through `errors.failure_text(debug, style,
friendly)`, the single exit for failure text the code writes for the user:
friendly style gets the sentence, debug style gets the debug text unchanged.
When it substitutes, the debug text is logged (`failure_spoken_friendly`
event) and set on the current span (`kronk.failure.debug_text`). Two tests
keep new paths honest: no literal or f-string may be yielded as a user token
in `main.py` / `agents.py` without going through `errors.*`, and every
terminal tool is driven through each way it can fail (service down, HTTP
error with a poisoned detail, missing arguments) and its friendly speech
checked for technical text (`tests/test_spoken_failures.py`).

A delegated specialist's empty reply is model-facing (the coordinator reads
it), so it stays `[<agent> agent returned no response]` on every mount.

Model-written text is never matched or replaced after the fact. (Fix 1 of
sprint M2 added a "speech backstop" that replaced a failed spoken turn's
answer when it carried a debug shape; it replaced correct answers and was
removed in fix 2. See `docs/sprint/M2-fix2.md`.)

Failures the model phrases — a non-terminal tool that failed, a specialist
that failed and fell back to the coordinator — keep full detail model-side
in both styles. In friendly style the phrasing rule
(`errors.FRIENDLY_TOOL_PHRASING`) is appended to the failed tool result
itself, and the specialist-failure block carries the friendly instruction.
Nothing is added to the system prompt or to a successful result, so a
successful request's model input is byte-identical in both styles. These
sentences are model-written, so they are instructed, not guaranteed.

**Whether a tool result failed is decided by the code that wrote it, never
by reading its text.** A handler in `orchestrator/tools.py` returns its text
for a success and `tools.failure(text)` for a failure; `tools.execute`
returns `ToolResult(text, failed)` (an exception or an unknown tool is a
failure). For a delegated specialist, `agents.run_delegated` returns
`(text, terminal, failed)`: failed when it errored before any text or
returned nothing. The agent loop appends the phrasing rule only when
`failed` is set. A successful result embeds search queries, page text and
speaker names that can look like anything — a search for "pip build failed:
ModuleNotFoundError" once got the failure rule and its answer replaced
(`docs/sprint/M2-e2e2.md` finding 1). `tests/test_tool_failure_flag.py`
drives every handler through success with failure-looking text and through
each failure branch. Not failures, by design: a shopping-list item that was
not on the list, a hot tub that answered "offline", the mirror refusal, and
a news refresh that failed but delivered the cached brief.

The toggle governs **rendering only, never capture**: logs and traces
always get full detail, and traces are marked ERROR in both styles — in
friendly mode you look in the trace instead of the transcript. Failed
spoken requests, in one TraceQL query:
`{ span.kronk.voice = true } && { status = error }` (the error may sit on
the pipeline root or, for a tool the agent reported, on the failing httpx
span inside the trace). The invented-answer ban never relaxes.

## Gotchas

- A *tool* failure the agent handles gracefully (e.g. unknown location) is
  NOT a pipeline error — the trace stays clean because the user got a
  truthful answer. ERROR marking means "something failed that the user
  should be able to find," not "a tool returned non-200."
- The specialist-failure path still runs the coordinator (a transient LLM
  5xx deserves a recovery attempt); honesty comes from the FAILED label +
  ERROR trace, not from skipping recovery. Not for `talkie` (M24, above):
  a recovery there is an answer in Talkie's name that Talkie did not give.
- 25 tests pin these behaviors (`test_hooks.py`, `test_tool_service_search.py`,
  and additions across the suite) — the failure *strings* are contracts now.

## Blog hooks

- "'An unexpected error occurred' is a bug": auditing every layer an error
  crosses in a voice assistant, and the one helper (`_fail`) that fixed six
  swallowing points.
- Marking traces ERROR is the difference between "filter traces by status" and
  "correlate timestamps by hand."
- When the model is part of your error path: FAILED labels, invented-answer
  bans, and never letting a model paraphrase a stack trace.
- One exit for failure text: three spoken debug leaks (news, mirror, empty
  reply) were three paths around the error style, not three bugs; the fix
  was one function and a test that refuses new paths.
- Don't decide failure by reading text: a regex over tool results and a
  "backstop" over the spoken answer both took a web search for "pip build
  failed: ModuleNotFoundError" for a failure and replaced a correct answer.
  The fix was a flag set by the code that wrote the result.
