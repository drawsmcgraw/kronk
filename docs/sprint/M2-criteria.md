# M2 pass criteria: friendly errors on voice, and one voice signal in the pipeline

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M2. Friendly
errors on voice", row C3 and the M2 line of "Verified on Kronk, 2026-11-15";
C3 approved by Drew 2026-11-15). The tester checks against this file, not
against the build report.

## What M2 is

Today a failure on the voice path is spoken in debug wording, because
`ERROR_STYLE_VOICE` is never set, and the pipeline cannot tell a spoken
request from any other shim request: `/voice/api/chat`, `/api/chat` and
`/v1` all run as `transport="shim"`. M2 threads one request-scoped "this
is the speech mount" value into the pipeline and keys the friendly error
style on it. M8, M14 and later items read the same value, so it is built
once, here.

## Criteria

### A. The voice signal

1. There is one request-scoped value that says whether the request came in
   on the speech mount (`/voice/api/chat`), set once where the request
   enters and readable anywhere in the pipeline without passing it through
   every call (the same mechanism as `origin.current` / `origin.said`).
   It is false for `/message`, `/api/chat` and `/v1/chat/completions`.
2. It is set from the mount the request arrived on, never from request
   content: no field, header or message text sent by a client can turn it
   on or off.
3. It does not leak between concurrent requests: a voice request and a
   non-voice request running at the same time each see their own value.
   A test shows this.
4. Existing uses of the speech profile inside `_ollama_chat_impl` (answer
   shaping for speech, retracts) behave as before. If they are moved onto
   the new value, behaviour is unchanged; if they are left, the report
   says so. There is one source of truth for "is this voice", not two
   that can disagree.
5. The value is on the trace (a span attribute on the request's root or
   pipeline span), so voice requests can be filtered in Tempo.

### B. Friendly errors

6. On the speech mount, every failure that reaches the user is a short
   plain sentence suitable for a speaker: no exception class names, URLs,
   ports, status codes, stack text, model names or JSON. This holds for at
   least: a routing failure, a pipeline crash, an unreachable model
   server, a tool_service outage, and a specialist agent failure.
7. Each friendly sentence still says what kind of thing went wrong where
   the code knows (tenet 7): "the music service isn't responding" is
   acceptable; "something went wrong" alone is not, unless the cause is
   truly unknown.
8. Music failure sentences are unchanged in both styles (they are already
   specific and spoken).
9. On `/message`, `/api/chat` and `/v1/chat/completions` errors keep
   today's debug wording, byte for byte. The benches that use those
   routes see no change.
10. Logs and traces keep the full detail for every failure in both styles:
    the friendly sentence never replaces the logged cause, and a failed
    voice request can be found in Tempo with its real error in one step.
11. The friendly style on voice is on by default in production and dev
    without an `.env` edit (a compose default or code default), and can
    be switched back to debug with one documented setting.
    `ERROR_STYLE` / `ERROR_STYLE_VOICE` keep working as documented, or
    the docs are changed to match what is built.

### C. Cleanup, tests, regression

12. The stale comments about a "voice smoke test" in `errors.py` and
    `tests/test_error_style.py` are removed.
13. Unit tests cover: the signal per mount, isolation between concurrent
    requests, each failure kind in criterion 6 on the speech mount
    (friendly) and on one other mount (debug, unchanged).
14. Live on dev, without touching production or any model server's unit:
    at least two real failures are produced on `/dev/voice/api/chat` and
    on `/dev/api/chat` by safe means inside the dev pair (for example a
    request while `tool_service-dev` is stopped, then started again) and
    the spoken text and the debug text are recorded.
15. `./scripts/run_tests.sh` passes: at least `2215 passed, 2 skipped,
    18 xfailed` plus the new tests.
16. The voice replay on the dev path matches the M10 baseline row by row,
    apart from the known flapping rows. Answers for successful requests
    are unchanged.
17. Production is untouched by the build; no HA or Music Assistant
    restart; no sound.
18. `docs/features/voice-pipeline.md` (or the doc that covers error
    styles) describes the signal and the setting. README mentions of
    error style are correct.

## Reports

Build: `docs/sprint/M2-build.md`. End to end: `docs/sprint/M2-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.

## Coordinator rulings after the retest (2026-11-17)

- **The speech backstop is withdrawn.** The coordinator asked for it in the
  fix round 1 brief; the retest showed it replacing correct spoken answers
  (`M2-e2e2.md` findings 1, 2 and 4). It is removed, not tuned. Criterion 6
  is met by code-written failure text leaving through one function, not by
  matching text after the fact.
- **Whether a tool result is a failure is decided by the code that produced
  it, never by matching its text.** A result that embeds user, model or web
  text must not be able to look like a failure.
- **Criterion 8 wins over criterion 6 for music.** Music failure sentences,
  including the detail tool_service puts in them (for example
  `(HTTP 502)` or `(ConnectError)`), are spoken as they are today. The plan
  says these stay specific. Finding 3 is an observation.
- The mirror preflight detail is not spoken on voice; protocol errors
  (400/422 for malformed requests) are out of scope; the M4b refusal
  sentence is spoken unchanged.
