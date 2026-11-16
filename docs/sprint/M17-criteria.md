# M17 pass criteria: dev path on the Hey Jarvis wake word

Written by the sprint coordinator on 2026-11-15, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M17. A dev
pipeline on the second wake word", rows C2 and C6 of "Verified on Kronk,
2026-11-15", and "Drew's review"). The tester checks against this file, not
against the build report.

## Scope

In: `orchestrator-dev` and `tool_service-dev`; the nginx `/dev/` prefix; a
minimal music dry-run switch in `tool_service`; the `kronk-dev` HA pipeline;
slot 2 and Hey Jarvis on all three satellites; the one `configuration.yaml`
edit from C6; a way to deploy production from a tag.

Out: the regression corpus and replay script (M10); any change to production
behaviour; puck firmware; Music Assistant settings; the MagicMirror Pi.

## Criteria

### A. Dev services

1. `docker-compose.yml` defines `orchestrator-dev` and `tool_service-dev`
   under a compose profile. A plain `docker compose up -d` (no profile)
   neither starts nor stops them, and starting them does not recreate any
   production container. Check container start times before and after.
2. Each dev service builds from the same Dockerfile as its production twin,
   listens on its own port, and reads its own env file. The repo carries an
   `.example` for that env file; the real one is gitignored if it holds
   secrets.
3. `orchestrator-dev` calls `tool_service-dev`, never the production
   `tool_service`. Prove it with a request whose trace or logs show the dev
   `tool_service` handling the tool call and the production one idle.
4. Dev state is separate from production state: the dev orchestrator's
   sessions DB and the dev ops audit log are different files from
   production's. A dev request leaves production chat history and
   `/data/ops_audit.log` byte-identical.
5. Shared household data that dev needs (taste file and DB, `solar.db`) is
   either mounted read-only or copied. Where dev can write to a shared file,
   the build report names the file and the reason.
6. Both dev services set their own `OTEL_SERVICE_NAME`. In Tempo, a dev
   request is distinguishable from a production request by service name
   alone. The build report gives the TraceQL filter that excludes dev
   traffic.
7. The compose additions carry an `# Operations:` block: up, rebuild, logs,
   stop, for the dev pair.

### B. nginx

8. `http://localhost/dev/api/chat` and `http://localhost/dev/voice/api/chat`
   reach `orchestrator-dev` and return a real model answer. Streaming is not
   buffered (same proxy settings as the production locations).
9. Every production route answers as before: `/`, `/api/status`, `/message`,
   `/api/chat`, `/voice/api/chat`, `/v1/chat/completions`.
10. With the dev pair stopped, nginx still starts and production routes
    still work. `/dev/` returns an error; it does not take nginx down.

### C. Music dry-run

11. `tool_service` honours `MUSIC_DRY_RUN`. When on, a play request resolves
    as usual and returns what it would have played, and no `play_media` (or
    any other state-changing call) goes to Music Assistant or HA. Transport
    commands (pause, resume, stop, next, volume) and ratings are also
    suppressed and say so.
12. The dry-run result is marked as dry-run in the tool result and on the
    trace span, so nobody reads it as a real play.
13. `MUSIC_DRY_RUN` is on by default for `tool_service-dev` and absent (off)
    for production. Production code paths are unchanged when it is off;
    a unit test covers both states.
14. Live check on dev with dry-run on: "play some jazz in the office"
    through `/dev/api/chat` returns a dry-run answer, and the state of every
    `media_player` entity is the same before and after.
15. With dry-run switched off on dev for one test, a play request aimed at
    the office speaker (`media_player.satellite1_d8d1bc`) plays there and
    nowhere else. Check player state first; do not replace or pause anything
    already playing. Stop the test track afterwards and turn dry-run back
    on.

### D. Home Assistant

16. A second Ollama config entry points at `http://localhost/dev/voice`. The
    existing entry is unchanged.
17. A pipeline named `kronk-dev` exists with the same STT (`stt.faster_whisper`),
    TTS (`tts.piper`, voice `en_US-hfc_male-medium`) and language as `kronk`,
    and the dev conversation agent. `kronk` stays the preferred pipeline and
    its settings are byte-for-byte what they were.
18. `assist_pipeline/run` over the websocket with `start_stage: intent`
    against `kronk-dev` gets its answer from `orchestrator-dev` (visible in
    the dev service's traces). The same call against `kronk` gets its answer
    from production.
19. The `[kronk-origin]` stamp (device and area) reaches `orchestrator-dev`
    on the dev pipeline as it does in production.
20. On all three satellites (kitchen PE `0ac919`, Satellite1 `d8d1bc`,
    basement PE `0ab117`): `select.<sat>_assistant_2` is `kronk-dev`,
    `select.<sat>_wake_word_2` is Hey Jarvis, and slot 1 (`_assistant`,
    `_wake_word`) is unchanged at `kronk` / Okay Nabu. Done through HA
    entities only; firmware versions are the same before and after.
21. `configuration.yaml` loads `rest_command` from the repo's
    `ha/rest_command.yaml` content and carries the audio-capture setting
    (`debug_recording_dir`) present but switched off (commented out or
    otherwise inert), with a comment saying M12 turns it on. HA was
    restarted once for this, came back healthy, and `rest_command` services
    are listed. No debug audio is written anywhere after the restart.
22. After the restart: all three satellites are available, the existing
    automation `automation.kronk_music_assistant_voice_device_first` is on,
    and the Music Assistant integration is loaded.

### E. Deploy from a tag

23. A documented, scripted way to build and start production from a named
    git tag rather than the working tree, and to say which tag is running.
    It must not touch `homeassistant` or `music-assistant-server`, and must
    not run during this item except in a dry or no-op form: production
    promotion is the coordinator's step.

### F. Regression

24. `./scripts/run_tests.sh` passes with at least the baseline count
    (`841 passed, 2 skipped`) plus the new tests. Any change in skips is
    explained.
25. Production voice still works end to end after everything above:
    `assist_pipeline/run` with `start_stage: intent` on `kronk`, one
    question ("what time is it") and one music request checked without
    disturbing anything already playing.

### G. For Drew, in person (not a gate for the agents)

26. Spoken "Hey Jarvis" on each puck reaches dev; "Okay Nabu" reaches
    production.
27. What the tap-to-talk button does on each device now that slot 2 has
    changed. The agents record anything they can observe from HA and say
    what is left for Drew to press.

## Reports

Build: `docs/sprint/M17-build.md`. End to end: `docs/sprint/M17-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence
(command, output, trace ID).
