# M20 pass criteria: TTS trial with a go/no-go (the part that needs no clips)

Written by the sprint coordinator on 2026-11-18 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M20. TTS
trial with a go/no-go", check C9, the M20 line of "Verified on Kronk,
2026-11-15", Drew's decision of 2026-11-14) and the items promoted since.
The tester checks against this file, not against the build report.

## Drew's decision this item carries out

A trial only. Its output is a go/no-go recommendation on replacing Piper,
with numbers. Voice customization counts as a benefit in its own right.
Drew supplies the clips for cloning later; until then, everything that does
not need them: stand up each candidate, measure speed, stability, idle GPU
and model slowdown with stock or preset voices, and write it up. The
hard limits are the plan's table, confirmed by Drew.

## Scope

Built and run: `scripts/tts_bench.py`, each candidate as a Wyoming service
on a spare loopback port, the measurements, `docs/bench/tts_bench_<date>.md`
with a provisional verdict per hard limit, and the material for the
listening test. Not in this item: cloning anyone's voice, the household
listening test, attaching anything to an HA pipeline, the puck timing
(method step 7), any change to the production Piper or to HA.

## Context the plan did not have

- The production voice is `en_US-hfc_male-medium` (C9), served by the
  `wyoming-piper` container (`rhasspy/wyoming-piper:2.2.2`) on port 10200.
  HA uses it for every spoken reply, and replies of more than one sentence
  are streamed by sentence (M8 finding; streaming of the model's reply is
  off in production, Piper's own sentence streaming is not).
- Two Whisper instances are on the GPU now (production and dev, M13), with
  the three llama.cpp servers. GPU memory is measured before any candidate
  loads. The production Whisper unit already describes itself as running on
  AMD HIP, so a ROCm process exists today; the idle baseline is taken with
  everything as it is now.
- Audio capture is on. The bench never plays through a speaker and never
  runs an HA pipeline, so it records nothing.
- Tenet 1: this item downloads software and model weights. That is the
  trial Drew asked for. Every download is listed (what, from where, size,
  where it landed), and no candidate is left able to reach the network at
  run time when it can be run offline.

## Coordinator rulings

1. Candidates live outside the repo under `~/kronk-tts-trial/` (venvs,
   weights, scratch compose files, samples). Nothing is added to the Kronk
   compose files. Containers, if used, run under their own compose project
   name (`kronk-tts-trial`), never the `kronk` project.
2. The baseline is a second Piper, same image and same voice, on a spare
   loopback port. The production Piper on 10200 is not benched and not
   restarted.
3. No systemd unit is installed, enabled or started. Candidates run as
   scratch containers or foreground processes for the length of the trial
   and are stopped at the end. What stays on disk is listed with sizes and
   the one command that removes it (not run).
4. The model-slowdown measurement sends requests straight to the
   `gemma-4-e4b` llama.cpp server on loopback (not through any Kronk chat
   route) with a fixed prompt, in short bursts. The household shares that
   server: check read-only that nothing is playing and no voice request is
   recent, and keep each burst under two minutes.
5. "Garbled or extra audio" cannot be judged by ear here. The automatic
   stand-in is stated as a stand-in: each output is transcribed by the dev
   Whisper (loopback 10301) and compared with the sentence, and checked for
   length against the expected speaking time, clipping, and trailing or
   leading noise. The hard limit's real verdict waits for the listening
   test.
6. A candidate that cannot be stood up in bounded effort (the ROCm PyTorch
   for this GPU, a server that will not build) is a result, not a failure
   of the item: the report gives what was tried, the exact error, and what
   would have to change. The item does not install system packages, change
   GPU drivers, kernel parameters or anything under `/etc`.
7. A candidate whose load would leave less GPU or system memory free than
   the report can justify is not loaded beside the others; candidates are
   measured one at a time, each unloaded before the next.

## Criteria

### A. Check first

1. The build report gives, before anything is installed: the current
   version of each candidate (Pocket TTS and its Wyoming server, Chatterbox
   and Chatterbox-Turbo with the OpenAI-compatible server and
   `wyoming-openai`, the RVC layer), its licence, where it is fetched from,
   and whether its Wyoming path supports streamed synthesis (and how that
   was established: the code or a run, not a README claim alone).
2. Read-only state before the trial: GPU memory and utilisation, system
   memory, disk free, GPU power if the host exposes it, what each existing
   GPU process holds; `docs/incidents/INVESTIGATION_2026-06-09_hangs.md`
   read and its idle-GPU symptom restated as the thing criterion 8 looks
   for.

### B. The bench tool

3. `scripts/tts_bench.py` speaks Wyoming directly to a host and port given
   on the command line, with a fixed sentence set in a data file of about
   20 sentences covering: short confirmations, music confirmations with
   artist and station names, a two-sentence weather answer, a
   three-sentence answer, a long spoken answer, and numbers, dates and
   times. It measures per sentence and per engine: time to first audio,
   total synthesis time, audio duration, real-time factor, failures; and
   separately cold start (first request after the service starts) and
   memory of the service process. It repeats each sentence a stated number
   of times and reports p50 and p95. It writes each output to a WAV file
   under a directory given on the command line and plays nothing.
4. It has tests: timing arithmetic, the result file's schema, failure
   handling (refused connection, a stream that stops, an empty reply), with
   the Wyoming exchange faked in memory. Tests open no socket.
   `./scripts/run_tests.sh` passes with at least the count on `refactor`
   before the build plus the new tests.

### C. The measurements

5. Baseline first: the scratch Piper with `en_US-hfc_male-medium`, full
   sentence set, with streaming as production has it.
6. Each candidate that could be stood up: the same run, bound to loopback
   only (shown with `ss`), with a stock or preset voice. No cloning: no
   reference audio of any person is used, and the report says which preset
   was used.
7. Model slowdown: `gemma-4-e4b` tokens per second alone, then while each
   engine synthesises, by ruling 4. The same for the baseline, so the
   comparison is with what the household has today.
8. Idle: with each candidate loaded and idle for one hour, GPU busy
   percentage, GPU memory and power at a fixed interval, beside the same
   figures for the Piper-only state taken the same way for at least fifteen
   minutes. If two candidates share a runtime the report may argue for one
   hour covering both; it says so.
9. Stability: the count of crashes, hangs, timeouts and refused requests
   across everything run, per engine, with the journal or log lines.
10. The stand-in check of ruling 5 over at least 100 synthesised sentences
    per engine (the set, repeated), with the mismatches listed.

### D. The write-up

11. `docs/bench/tts_bench_<date>.md`: per engine the numbers; the plan's
    hard-limit table with pass, fail or "waits for the listening test" for
    each limit and each engine; the latency of each candidate beside
    Piper's, as the plan's tradeoff note asks; whether each could carry M8's
    streaming; what was not stood up and why; and a provisional
    recommendation that says plainly it is provisional. A no-go names the
    limit that failed.
12. Listening-test material, ready for when the clips arrive: the WAVs for
    the sentence set from each engine under `~/kronk-tts-trial/samples/`
    with names that do not reveal the engine, the key in a separate file,
    a one-page sheet for rating naturalness and counting mispronunciations
    and garbled sounds, and the steps for the cloning run (what Drew
    supplies: length, format, consent; the command; where the output goes).
    Nothing is played.

### E. Safety and cleanup

13. Nothing in production changed: the `wyoming-piper` container (same
    container ID and start time before and after), HA and its pipelines,
    Music Assistant, the pucks, both Whisper units, the llama units,
    LiteLLM. No new listener on a LAN address. No sound.
14. At the end every trial process and container is stopped, GPU and
    system memory are back to the starting figures (shown), and the report
    lists what remains on disk with sizes.
15. Downloads are listed as ruling context requires, and no candidate
    sends telemetry or contacts a remote service during synthesis (shown
    by running with the network denied to the process or container, or by
    the offline switches each project documents, and stating which).
16. Docs: `ROADMAP.md` (the TTS item's state), `docs/features/voice-pipeline.md`
    only if a fact about the current TTS path was found wrong, the plan's
    open item on the clips.

## Reports

Build: `docs/sprint/M20-build.md`. End to end: `docs/sprint/M20-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
