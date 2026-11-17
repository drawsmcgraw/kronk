# M8 pass criteria: sentence-level streaming on the voice mount

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M8.
Sentence-level streaming on the voice mount", the M8 line of "Verified on
Kronk, 2026-11-15", Drew's decision of 2026-11-14) and the items promoted
since (M2's `origin.voice` signal and friendly failures, M7 direct dispatch,
M14 gate, M19 lead-in, M21 `config/`). The tester checks against this file,
not against the build report.

## Scope

`/voice/api/chat` today collects the whole reply, runs `render.to_speech`
and sends one chunk. M8 sends completed sentences as they arrive, so Home
Assistant can start speech before the reply is finished, and tells the
coordinator the reply will be spoken so it answers short. Not in this item:
any change to Home Assistant, the pucks, `wyoming-piper` or its flags, the
display profile (`/api/chat`, `/v1`, `/message`), or the lock (M9).

## Context the plan did not have

- The voice signal exists: `origin.voice`, set only by `/voice/api/chat`
  (M2). M8 reads it; it does not add a second signal.
- Failures on voice are decided by a flag, not by matching text (M2 fix 2:
  `tools.execute` returns `ToolResult(text, failed)`, `errors.failure_text`).
  Streaming must not bring text matching back.
- Direct-dispatch replies (M7) and terminal-tool results are one sentence
  and are not model text.
- Prompts and tuning live in `config/` through the M21 loader. A new
  spoken-reply line and any new threshold go there, with schema, comments,
  defaults equal to the file and pins, as M21 step 1 requires.
- HA `2026.5.4` starts streamed TTS only once a reply passes 60 characters
  (`STREAM_RESPONSE_CHARS`); `wyoming-piper` 2.2.2 streams on sentence
  boundaries by default.

## Criteria

### A. Check first, reported before the design is fixed

1. The build report states, from this system and read-only: whether a
   `kronk-dev` pipeline run shows `tts_start_streaming` (or HA's
   equivalent) for a reply over 60 characters; HA's Ollama client timeout
   and that it sends `stream: true`; how many `retract` events fired in the
   orchestrator's logs and Tempo over the period available and the largest
   number of characters one removed; and what the puck does with a reply
   that arrives in several chunks (from HA's pipeline events, not by
   making sound).

### B. The sentence buffer

2. With streaming on and the speech profile, a reply of several sentences
   leaves `/voice/api/chat` as several NDJSON content chunks, each made of
   whole sentences or whole lines, each already passed through
   `render.to_speech`. The first chunk is sent before the model has
   finished generating (shown with a slow stubbed model in a unit test, and
   with timestamps on dev).
3. The text spoken is the same as the buffered path's for the same model
   output: for a set of recorded or stubbed replies (plain prose, markdown
   emphasis, a list, a table, a code fence, a heading, a URL, numbers and
   units, abbreviations such as "Dr." and "e.g.", decimals, an ellipsis, a
   reply with no final punctuation, a one-sentence reply, an empty reply),
   the chunks joined equal `to_speech` of the whole reply, apart from
   whitespace between chunks. Any difference is listed and justified.
4. One sentence is held back: sentence N is released only when sentence
   N+1 completes or the stream ends. A `retract` that falls inside the held
   text removes it before anything is sent. A test covers a tool-round
   preamble followed by a tool call: nothing of the preamble is spoken.
5. A retract larger than the held text is handled in a defined way and
   never corrupts later chunks: the report says what is spoken in that
   case, a test pins it, and the event is logged and counted (span
   attribute or metric) so the plan's "widen the hold" decision can be made
   from data.
6. Markdown blocks are units: nothing inside a table, a list or a code
   fence is released until the block ends. A block that never closes is
   released at the end of the stream, rendered as the buffered path would.
7. Sentence boundaries do not split inside abbreviations, decimals,
   initials, URLs or times in the cases of criterion 3. The splitter is one
   pure function with its own tests. If `transcript_gate`'s sentence
   counter can be shared, it is; if not, the report says why.
8. Short replies are unchanged in shape: a one-sentence reply, a
   direct-dispatch reply, a terminal-tool result and a friendly failure
   sentence each leave as a single content chunk with the same text as
   today, followed by the same `done` record.
9. The NDJSON protocol stays valid for HA's Ollama client: every line a
   complete JSON object, `done: false` chunks then exactly one
   `done: true` record with the fields sent today; a non-streaming request
   (`stream: false`) on the voice mount still gets one JSON object with
   the whole spoken text.
10. Failure in mid-stream: if the model or a tool fails after some
    sentences were sent, the stream ends with the friendly failure
    sentence (M2) as its last content and a valid `done` record; it does
    not raise, hang, or repeat sentences. Failure is still decided by the
    flag, not by matching reply text.
11. What is stored is what was said: chat history and the trace hold the
    final reply (retracted text excluded), the same as the buffered path
    stores today.

### C. The switch

12. `VOICE_STREAM` (env) selects the path. `off` gives today's buffered
    behaviour byte for byte (the existing tests for it keep passing
    unchanged in meaning; `tests/test_render.py` and `tests/test_retract.py`
    pins that change are listed with the reason). The default in code and
    what dev and production compose set are stated; production's value is
    the coordinator's to set at promotion.
13. The display profile and the other routes are unchanged: `/api/chat`,
    `/v1/chat/completions` and `/message`, streaming and not, send the same
    bytes for the same model output before and after, with the switch on
    and off.

### D. The spoken-reply instruction

14. On voice requests the coordinator's system text ends with a line
    saying the reply will be spoken: one to three plain sentences, no lists
    or markdown. It is at the end, so the system text before it is
    byte-identical to today's for the same request (a test compares), and
    it is absent on non-voice requests. The wording lives in `config/`
    through the M21 loader and applies with either value of the switch.
15. It reaches the coordinator only (not delegated specialists' prompts,
    unless the report shows why that is needed), and does not change which
    tool the coordinator calls: the voice replay's routing and tool
    expectations hold.
16. Measured on dev, at least ten questions that draw long answers today,
    five runs each, before and after the line: reply length in characters
    and sentences, and generation time. The report gives the table.

### E. Evidence

17. Time to first content chunk on dev, switch off against on, for at
    least five long-answer questions, five runs each, measured at
    `/dev/voice/api/chat` (send time, first content chunk, `done`), with
    the instruction line on in both. And the same through HA's `kronk-dev`
    pipeline with `start_stage: intent`, read from HA's pipeline events
    (`intent-end`, `tts-start` or the streaming event): whether HA starts
    TTS before the reply ends. No audio is played for this: the replay's
    silent method, or a run with TTS output not sent to a speaker; if
    timing first audio needs a speaker, it is left for Drew and stated.
18. If HA does not start speech earlier on this system, the report says
    so plainly with the events it saw. The plan's rule then applies: keep
    the instruction line and the switch, and the coordinator decides
    whether streaming is promoted off.
19. Latency is stated up front (tenet 12): expected time to first audio
    and total time, before and after, for a short answer and a long one.

### F. Regression and safety

20. `./scripts/run_tests.sh` passes: at least `4235 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network.
21. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows, with the switch on.
22. Dev only; no sound; no change to Home Assistant, the pucks,
    `wyoming-piper`, Music Assistant or production; no HA or MA restart;
    no command to the MagicMirror Pi.
23. Telemetry: a voice request's trace shows whether it was streamed, the
    number of chunks, time to first chunk, and any retract that could not
    be honoured.
24. Docs: `docs/features/voice-pipeline.md` (and `render` docs where they
    describe the single chunk) describe the streamed path, the hold, the
    block rule, the switch and how to roll back; `docs/features/config-directory.md`
    lists the new entry.

## Reports

Build: `docs/sprint/M8-build.md`. End to end: `docs/sprint/M8-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
