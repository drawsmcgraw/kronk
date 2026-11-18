# M13 pass criteria: bias Whisper toward household vocabulary (dev path only)

Written by the sprint coordinator on 2026-11-18 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M13. Bias
Whisper toward household vocabulary", the M13 line of "Verified on Kronk,
2026-11-15", Drew's decision of 2026-11-11) and the items promoted since
(M12 capture, on since 2026-11-18 00:14 UTC; M6's audit). The tester checks
against this file, not against the build report.

## Drew's decision this item carries out

Build it and use it on the dev path, with a second Whisper instance on its
own port. **Keep the prompt off for production until the M12 clips show it
helps.** The mishearing table is not approved and is not built.

## Scope

Built: a script that writes the vocabulary prompt, a second Whisper
instance for the dev path that uses it, the `kronk-dev` pipeline pointed
at that instance, a bench that compares transcription with and without the
prompt, and the wiring to regenerate the prompt. Not in this item: any
change to `wyoming-whisper.service` (production STT), to the `kronk`
pipeline, to the pucks; the mishearing table; any routing change; a
measurement on real household clips (M12 has just started; the labelled set
does not exist yet).

## Context

- Production STT is the host unit `wyoming-whisper.service`,
  `large-v3-turbo` on the GPU, port 10300 on all interfaces (M6 audit: its
  only client is HA on `localhost`; rebinding it is an open decision of
  Drew's, not this item's).
- Both pipelines, `kronk` and `kronk-dev`, use that one instance today.
- A second instance loads the model a second time on the GPU, which the
  llama.cpp servers share (plan, M13 line): memory is measured before it
  is left running.
- Audio capture is on: a spoken "Hey Jarvis" request on a puck is recorded
  under `<device>/kronk-dev/`.

## Criteria

### A. Check first

1. The build report states, read-only, before any change: the flags of
   the installed `wyoming_faster_whisper` (`--initial-prompt`,
   `--beam-size`, the VAD options) from its `--help`; how HA is connected
   to Whisper today (the Wyoming config entry, which STT entity each
   pipeline uses); GPU and system memory in use and free, and what the
   running Whisper process holds; and whether one instance can serve two
   prompts (per request or per connection) so that a second model load is
   not needed. If it can, that is the design, and the second instance is
   not built.

### B. The prompt

2. `scripts/build_stt_prompt.py` writes one short line, under about 100
   tokens (the report gives the count and how it was counted), from: HA
   area names, Music Assistant player names, the Pandora station names in
   the MA library, and the top artists from the music taste profile.
   Names only: no command verbs (a test fails if the output contains a
   word from the routing command forms: play, pause, stop, skip, next,
   resume, shuffle, volume, thumbs, and the rest of that vocabulary,
   unless it is part of a name, in which case the report lists each).
3. It reads its sources read-only (HA and `tool_service` on loopback, the
   MA reads `tool_service` already makes), writes only the output file
   given on the command line (default outside the repo, beside the
   Whisper service's files), is deterministic for the same inputs, is
   stable in order so that a change in the text means a change in the
   names, and exits non-zero without touching the old file when a source
   is unreachable or returns nothing. Tested with stubbed sources.
4. When the token budget is too small for every name, the report states
   the rule for what is kept (for example stations and rooms first, then
   artists by play count) and a test pins it.

### C. The dev instance

5. Dev transcription uses the prompt and production's does not. Either a
   second unit (reference copy in `systemd/`, bound to loopback, its own
   port, the same model and compute type, reading the prompt from an
   `EnvironmentFile`) or, if criterion 1 found a way, one instance
   serving both. `wyoming-whisper.service` is byte-identical before and
   after, was not restarted, and its transcriptions are unchanged (shown
   by the bench: the same audio through port 10300 before and after gives
   the same text).
6. Memory: GPU and system memory before and after the second load, the
   llama.cpp servers still healthy (a dev chat request on each model
   answers), no out-of-memory line in the journal. The report states
   the cost and recommends leaving the dev instance up or stopping it
   when not in use. If memory after the load leaves less headroom than
   the report can justify, the unit is stopped and disabled and the item
   reports that; that is a valid outcome.
7. HA: the `kronk-dev` pipeline's STT is the dev instance; the `kronk`
   pipeline's STT entity, language and every other field are unchanged
   (read before and after). The change is made without an HA restart, is
   listed with its exact undo, and the Wyoming entry for the dev instance
   points at loopback.
8. Regeneration: the prompt is rebuilt on the nightly taste rebuild (or a
   timer beside it), and the dev instance restarts only when the text
   changed. Never production's unit. Tested with a stubbed restart.

### D. Evidence

9. A bench (`scripts/stt_prompt_bench.py` or similar) that sends audio
   files to a Wyoming STT port and records the text and the time, with
   and without the prompt, and scores word error rate and "command-form
   errors" (the transcript no longer routes to the same rule and call as
   the reference text does). It makes no sound, reads audio from a
   directory given on the command line, and can read the M12 labelling
   sheet's format, so the same tool runs on the real clips later.
10. Since no labelled household clips exist yet, the build runs it on
    synthetic speech: the corpus sentences and the station, artist and
    room names, rendered to files by the existing Piper service (no
    speaker), with noise-free and at least one degraded variant. The
    report gives WER and command-form errors with and without the prompt,
    p50 and p95 latency for both, and says plainly that synthetic speech
    is not evidence for production.
11. The hazard in the plan is measured: silence, room noise and
    non-speech audio (generated, or from a public-domain source already
    on the box; nothing downloaded without saying so) through both
    instances: how often each returns text, and whether the prompted one
    returns names from the prompt. The report gives the counts.
12. The verification that decides production is written down as a
    procedure for after the M12 labelling: the command, the inputs, the
    pass rule from the roadmap (WER and command-form errors better at the
    same p95 latency), and that the decision is Drew's.

### E. Regression and safety

13. `./scripts/run_tests.sh` passes: at least `4743 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network.
14. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows (it starts at the intent stage, so
    it does not exercise STT; it shows the pipeline change broke nothing).
15. Production untouched: `wyoming-whisper.service`, the `kronk`
    pipeline, production containers, Music Assistant, the pucks. No HA
    restart. No sound. No new listener on a LAN address (the dev
    instance binds loopback; shown with `ss`).
16. Docs: `docs/features/voice-pipeline.md` (the dev instance, the
    prompt, how to regenerate it, how to stop it, how to move it to
    production when Drew decides), `README.md` service map and the
    systemd unit list, `ROADMAP.md` voice-accuracy item updated.

## Reports

Build: `docs/sprint/M13-build.md`. End to end: `docs/sprint/M13-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
