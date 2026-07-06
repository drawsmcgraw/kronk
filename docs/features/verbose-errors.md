# Feature: Verbose error reporting

**Shipped:** 2026-07-05 (branch `p1-verbose-errors`; P0 groundwork same day) · **Plan:** `../plans/VERBOSE_ERRORS_PLAN.md` · **Audit:** `../REVIEW_2026-07-05_codebase.md` (P0/P1 sections)

## What it does

Every user-facing failure carries the most specific cause available at the
layer that failed, and every failed turn is findable in Langfuse in one step
(traces are marked ERROR; unexpected crashes speak their rid). "An
unexpected error occurred" is now, by tenet 7, a bug.

Live example: "what's the weather in Zzyzzxqq Falls?" → "I could not find
any weather information for Zzyzzxqq Falls" — the geocoder's 404 survives
tool_service → tool result → agent → speech, instead of dying en route.

## How it works — the error path, layer by layer

- **tool_service / health_service routes** return specific `detail` strings
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
  the error a "specialist result — use this to answer."
- **Trace marking**: `_run_pipeline` tracks `pipeline_error` on every path
  (router failure, specialist failure — even when the coordinator recovers —
  coordinator error, unexpected crash) and passes
  `level="ERROR", status_message=…` to `telemetry.end_pipeline`. Filter
  Langfuse traces by level to see every failure.
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

## Verbosity policy

Chat UI and voice speak the same text: one clear sentence of specific cause,
rid included on pipeline-level failures. Model-facing tool results carry
enough detail to relay or adapt. Full policy in the plan doc.

## Gotchas

- A *tool* failure the agent handles gracefully (e.g. unknown location) is
  NOT a pipeline error — the trace stays clean because the user got a
  truthful answer. ERROR marking means "something failed that the user
  should be able to find," not "a tool returned non-200."
- The specialist-failure path still runs the coordinator (a transient LLM
  5xx deserves a recovery attempt); honesty comes from the FAILED label +
  ERROR trace, not from skipping recovery.
- 25 tests pin these behaviors (`test_hooks.py`, `test_tool_service_search.py`,
  and additions across the suite) — the failure *strings* are contracts now.

## Blog hooks

- "'An unexpected error occurred' is a bug": auditing every layer an error
  crosses in a voice assistant, and the one helper (`_fail`) that fixed six
  swallowing points.
- Marking traces ERROR is the difference between "filter Langfuse" and
  "correlate timestamps by hand."
- When the model is part of your error path: FAILED labels, invented-answer
  bans, and never letting a model paraphrase a stack trace.
