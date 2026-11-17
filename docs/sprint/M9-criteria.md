# M9 pass criteria: narrow the global lock

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M9. Narrow
the global lock", the M9 line of "Verified on Kronk, 2026-11-15", Drew's
hard constraint of 2026-11-09) and the items promoted since (M7 direct
dispatch outside the lock, M2 friendly failures and `origin.voice`, M8's
streamed writer and its `aclose()`, M11's direct LiteLLM call from
`tool_service`, M21 `config/`). The tester checks against this file, not
against the build report.

## Scope

`_llm_lock` (`orchestrator/main.py`) wraps the whole pipeline, tool waits
included, so one request blocks every other. M9 narrows it in the plan's
five steps, each its own commit, each measured, each reverted if it breaks
the hard constraint. Not in this item: any change to the llama.cpp units,
LiteLLM, slot counts or context sizes; any change to Home Assistant;
streaming on the voice mount (stays off in production).

## The hard constraint (Drew, 2026-11-09)

**No performance penalty.** A single request with nobody else using Kronk
must be no slower after M9 than before, and prompt-cache reuse must not
drop. This is measured before the first change and after every step. A step
that slows the single-request case or lowers cache reuse beyond run-to-run
variance is reverted and reported; steps 1 and 4 are safe to keep on their
own.

## Context the plan did not have

- The M7 branch is already outside the lock.
- Production and dev are separate processes with separate locks sharing the
  same llama.cpp servers, so two generations already run at once on one
  server whenever dev and production are both busy. The lock is per
  process, not per GPU.
- HA's Ollama client has no request timeout (M8 check-first), so "shorter
  than HA's client timeout" has no number to sit under. The voice deadline
  needs its own justified value.
- `tool_service` asks LiteLLM directly for the news brief, genre tagging
  and (new, not live) the M11 model question; none takes the orchestrator's
  lock.
- `scripts/pipeline_bench.sh` clears chat history and defaults to
  production. It is not to be run against production in this sprint.

## Criteria

### A. Check first and the baseline

1. The build report states, read-only: slots and context per llama.cpp
   server (`/props`, `/slots`); how a slot is chosen for a request and
   whether LiteLLM or the orchestrator can pin one (`id_slot`,
   `cache_prompt`, slot similarity); everything that relies on the lock
   for safety, found by reading every piece of module-level or shared
   mutable state the pipeline touches (`sessions._cache` and its SQLite
   writes, telemetry, metrics, config reload, the routing and lead-in
   state, `agents`, `tools`), with a line for each saying why it is or is
   not safe without the lock; and what happens today when a client
   disconnects on each route.
2. A bench that measures the constraint, on the dev path only, that does
   not clear production history and makes no sound: single-request latency
   per route (direct dispatch, a routed specialist, coordinator with no
   tool, coordinator with a delegation, a web-search request) on the voice
   mount and one display route, with prompt-cache reuse per generation
   read from llama.cpp's own timings (prompt tokens evaluated against
   cached). And a two-client version: a research query on a display route
   and a voice weather query started one second later, recording the voice
   request's latency. Enough repetitions to state run-to-run variance. The
   baseline is taken and committed before any lock change.

### B. The steps, one commit each, measured after each

3. Step 1: work that needs no model is outside the lock: routing
   (`routing.classify`, the M14 gate, the lead-in), the `weather_context`
   fetch, and anything else the check-first step shows is model-free and
   done before the first generation. Behaviour is otherwise unchanged.
4. Step 2: the pipeline-wide lock is replaced by one lock per model
   server, held for one generation inside `llm.stream`. Tool waits
   (playback verify, web fetches, `tool_service` calls) hold no lock. A
   request on one model server does not wait for a request on another.
   Two requests on the same server run their generations one at a time.
5. Step 3: the per-model lock is a priority queue: a waiting voice request
   is served before a waiting display request, first come first served
   within a class. No preemption: a running generation finishes. A test
   shows the order with three waiters; another shows a display request
   cannot starve for ever behind voice requests, or the report says why
   that cannot happen at household scale and how it would show in
   telemetry.
6. Step 4: voice requests have a total deadline, set in `config/` through
   the M21 loader with a value justified from Tempo (the slowest
   legitimate voice requests seen) and from what a person will wait for a
   puck. When it passes, the run ends with one spoken sentence (the
   friendly style, M2) and a valid response; the pipeline task is
   cancelled and holds no lock afterwards. The sentence never claims an
   action did not happen when a tool call for it was already sent: the
   report defines what is said when the deadline falls during or after a
   tool call that changes something (music started, a rating sent), and a
   test pins it.
7. Step 4, second half: when the client disconnects, the pipeline task is
   cancelled on every route, the lock is released, the trace closes with
   an attribute saying the client went away, and a request sent right
   after is served. A tool call already sent is not retried or reported as
   failed to anyone. M8's streamed writer keeps working.
8. Step 5: `sessions.append` and every other shared write found in
   criterion 1 are safe with requests interleaving: two requests on the
   same session at once, and on different sessions at once, store every
   message once, in an order that is a valid interleaving, with no lost
   write and no exception (a test with many concurrent appends against
   the real SQLite store in a temporary directory).
9. For each step the report gives the single-request table and the
   two-client number against the baseline, and says kept or reverted. If
   two conversations alternating on one server evict each other's prompt
   cache and the cost exceeds the queueing saved, the plan's rule applies:
   stop after steps 1 and 4, and say so with the numbers.

### C. Correctness with requests interleaving

10. Nothing leaks between concurrent requests: origin (device, area,
    `said`, `voice`), error style, trace root, session history, routing
    result, direct-dispatch arguments, the M4b mirror pin's "user's words",
    and the M2 failure flag each belong to their own request. Shown by a
    test that runs many mixed requests at once (voice and display,
    different origins, some failing) through the app with a stubbed model
    and `tool_service`, and checks every reply, stored history and trace
    attribute against the same request run alone.
11. The mirror update pin (M4b), the devops agent's lack of web tools
    (M4), the fetch address block (M5) and dry-run cannot be bypassed by
    timing: a second request arriving during a first one's tool round does
    not change what the first is allowed to do.
12. Model output is unchanged for a request run alone: for the same
    stubbed model and tools, every route sends the same bytes before and
    after (timestamps normalised), streaming and not, voice and display.
13. Failure paths release everything: a model error, a tool error, a
    timeout, a cancelled request and an exception inside the lock each
    leave no lock held and no waiter stuck (a test for each, and on dev a
    request after each kind of failure is answered).

### D. Evidence on dev

14. Two-client result on dev: the voice weather request's latency while a
    research query runs, before M9 and after each kept step. The plan's
    goal is that the voice request no longer waits for the research
    request's tool waits, and waits at most for one generation on its own
    model server.
15. A load check on dev within reason for this machine (for example four
    voice and four display requests started together, mixed routes): all
    answer, none errors, the queue order is as criterion 5 says, and the
    report gives each request's wait and total time. llama.cpp and LiteLLM
    logs show no slot or context error.
16. Telemetry: a request's trace shows time spent waiting for the model
    lock separately from generation time, which lock, and its priority
    class; deadline and disconnect endings are findable in one query.

### E. Regression and safety

17. `./scripts/run_tests.sh` passes: at least `4435 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network.
18. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows.
19. Dev only; no sound; production untouched; no change to the llama.cpp
    units, LiteLLM, Home Assistant, Music Assistant or the pucks; no
    restart of any of them; no command to the MagicMirror Pi.
20. Docs: `docs/features/` describes the locks, the queue, the deadline,
    disconnect handling and how to read the wait in a trace; `TECH_DEBT.md`
    and the lock's own comment no longer say "one request at a time";
    `README.md` where it describes the lock. Tenet 12: the expected
    latency effect is stated.
21. Rollback: stated per step. If a single switch can restore the
    pipeline-wide lock, it exists and is tested; if not, the report says
    rollback is by tag.

## Reports

Build: `docs/sprint/M9-build.md`. End to end: `docs/sprint/M9-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
