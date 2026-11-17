# M12 build report: capture what was said

Build agent, 2026-11-17, branch `refactor`. Criteria: `docs/sprint/M12-criteria.md`.
Commits: `03f5187` (step 2), `f6100cf` (step 3), `52f11b7` (step 4), `1ebf260`
(step 5), `0705485` (step 6), and the commit that adds this report. Step 1 was
read-only.

## Summary

- **Transcripts.** `TELEMETRY_RECORD_TEXT` now governs every span attribute and
  log line that carries words, in the orchestrator, tool_service and
  voice_tracer. Before, the `kronk.events` previews, tool_service's music log
  lines and URL query strings (search terms, fetched URLs) on spans and in
  httpx/uvicorn logs carried text with the switch off. One exception, kept on
  purpose: the failure detail behind a friendly spoken error (tenet 7).
  Production tool_service now takes the switch from `.env`; the dev pair has
  it on by compose. Production stays off until the coordinator adds the `.env`
  line.
- **Audio.** The repo's `ha/configuration.yaml` turns `debug_recording_dir` on
  at `/voice_recordings`; `docker-compose.ha.yml` bind-mounts
  `/home/drew/kronk-voice-recordings` there (fails closed if the directory is
  missing) and adds `stop_grace_period: 2m`. Not installed. HA's own
  `check_config` (2026.5.4, offline, scratch copy) accepts the file.
- **Wrong-action mark.** voice_tracer marks a satellite run that the same puck
  corrects within 60 s: `not_that`, `rate_down`, `stop` (routing's own forms,
  now in `orchestrator/command_forms.py`), or `near_repeat` (text on only).
  Runs are held back from export until the window closes, so the mark is on
  the earlier `voice.run` span. Verified offline only: voice_tracer is a
  production container.
- **Tools.** `scripts/voice_transcripts.py` (listing from Tempo),
  `scripts/voice_label_sheet.py` (the labelling sheet),
  `scripts/voice_capture_check.py` with reference units
  `systemd/kronk-voice-capture-check.{service,timer}` (the end-date reminder).
- **Docs.** New `docs/features/voice-capture.md`; telemetry, voice pipeline,
  TELEMETRY_GUIDE, README and ROADMAP point at it.
- **Tests:** 4646 passed, 3 skipped, 53 xfailed (baseline 4517 / 3 / 53).
  **Replay:** see "Replay output".
- **Production, HA, MA, pucks:** unchanged. No audio recorded, no sound, no
  notification sent.

## Check first (read-only)

### Every place text is stored, and for how long

Measured 2026-11-17. "Text" means words someone said or a model wrote.

| Store | Holds | Retention (as found) | Bounded at 90 days? | Proposal |
| --- | --- | --- | --- | --- |
| Tempo | Text attributes when the switch is on (list below); URL query strings on HTTP spans regardless of the switch, before this build | `block_retention: 2160h0m0s` and `compacted_block_retention: 1h0m0s`, from the running config (`curl localhost:3200/status/config`) | Yes | None |
| Container logs | `kronk.events` previews (`request.text_preview` 80, `route.text_preview` 60, retracted round, escalation note, tool-syntax leak, mirror refusal, voice-stream retract), tool_service music lines (query, said, normalized media, genre phrase, resolver, library/station lookup, music decision JSON), httpx `HTTP Request:` lines and uvicorn access lines with query strings. All regardless of the switch, before this build | Docker log driver `journald` for every container (`/etc/docker/daemon.json`; `docker inspect … LogConfig` = `{journald map[tag:{{.Name}}]}`). `/etc/systemd/journald.conf` is all defaults, no drop-ins. Journal = 4 GB (`journalctl --disk-usage`), oldest entry 2026-10-04 06:34 (44 days). Bounded by size (`SystemMaxUse` default: 10% of the filesystem, capped at 4 GB), not by time | No: today 44 days, but only because of volume; no time cap | For Drew (outside the repo, deletes data): `/etc/systemd/journald.conf.d/kronk.conf` with `[Journal]` `MaxRetentionSec=90day`, then `sudo systemctl restart systemd-journald`. Compose cannot bound it: the `journald` driver has no size options |
| HA recordings | WAV per satellite run (after install) | Nothing deletes them | No | For Drew: after the labelling sheet is built, keep / delete / keep only the labelled sample. Files are root-owned; deletion needs `sudo` or `docker exec homeassistant rm` |
| M11 shadow log `data/music_shadow.jsonl` | `sentence` of each music hand-off (production: 1 line on 2026-11-17; dev: `data/dev/music_shadow.jsonl`) | No rotation, no limit | No | For Drew: delete or truncate at the M11 cutover review, when the comparison is done. Not built (the brief: retention that deletes data is a proposal) |
| Chat session store `data/sessions.db` | Web chat messages only. Voice history is HA's, sent with each request (`orchestrator/sessions.py` header); one session `webui`, 6 messages, 2026-11-05 to 11-11 | Sessions idle 30 days are pruned at orchestrator start (`SESSION_IDLE_PRUNE_DAYS`); an active session keeps all its messages | No voice text; web chat text unbounded while active | None for M12 |
| HA pipeline debug (memory) | Transcript, reply, for the last 10 runs per pipeline | Until HA restarts | Yes | None |
| HA `.storage/trace.saved_traces` | The music blueprint's stored traces include the trigger sentence; HA keeps the last 5 runs per automation and saves them at a clean shutdown (85 bytes today: the M17 shutdown was a SIGKILL, so nothing was saved) | Count-bounded, not time-bounded | Count-bounded | None |
| HA recorder `home-assistant_v2.db` | No sentence text found: 0 `automation_triggered` events whose data mentions a sentence; HA 2026.5's conversation trigger has no description field | 10-day default purge (oldest event 2026-11-07) | Yes | None |
| Music taste DB `data/music_taste.db`, `music_taste.yaml` | `picks.phrase` (8 rows, the genre phrase asked for), `feedback.reason` (27 rows), stated taste | No limit | No; household taste is the feature | None |
| voice_tracer state `data/voice_tracer_state.json` | Run ids only | Last 2000 | n/a | None |

Every span attribute and log field that carries text, by service, with the
switch off and on, after this build:

| Service | Field | Off | On |
| --- | --- | --- | --- |
| orchestrator | `kronk.input` on `pipeline.*`, `routing.decide`, `agent.*`, `llm.*`, `tool.*` | absent | the input (≤ 500 chars) |
| orchestrator | `kronk.output` on the same | absent | the output (≤ 500 chars) |
| orchestrator | `http.url`, `http.target`, `url.full`, `url.query` (auto-instrumented server and client spans) | query string → `?<query>` | unchanged |
| orchestrator | `kronk.failure.debug_text` | **kept** (exception, below) | kept |
| orchestrator | events `request.text_preview`, `route.text_preview`, `round_content_retracted.preview`, `voice_stream_retract_unhonoured.preview`, `escalate.note`, `tool_syntax_leak.fragment`, `mm_update_refused.said` | `null` | the text |
| orchestrator | event `failure_spoken_friendly.debug` | **kept** (exception) | kept |
| orchestrator | log "terminal tool result had no speech mapping" | length, text `None` | the text |
| orchestrator | `httpx` "HTTP Request:" and `uvicorn.access` lines | query string → `?<query>` | unchanged |
| tool_service | `music.media_id` on `music.dry_run` | absent | the media id |
| tool_service | `http.url`, `http.target`, `url.*` | `?<query>` | unchanged |
| tool_service | logs: music request query, station-name said, normalized query and media id, voice hand-off query and artist, genre request text, genre pick phrase, music search text, resolver text, library lookup name, station lookup query, shadow failure said, stated taste value and meaning | `None` in place of the text | the text |
| tool_service | log "music decision {…}" | outcome shape only (action, reading, decided_by, kind, media_type, provider, via, counts, ms) | the full JSON |
| tool_service | `httpx` and `uvicorn.access` lines | `?<query>` | unchanged |
| voice_tracer | `voice.transcript`, `voice.speech` on `voice.run`; `voice.speech` on `voice.intent` | absent (`voice.transcript_chars`, `voice.speech_chars` kept) | the text |
| voice_tracer | `bp.media_name`, `bp.area_or_player` on `blueprint.run` | absent | the text |

Not text, so on in both: `music.candidates` (catalog titles MA returned),
`music.uri`, entity ids, rule names, `kronk.direct_action` (a fixed
vocabulary: pause, next, up, down, …).

The exception: `kronk.failure.debug_text` and `failure_spoken_friendly.debug`
stay with the switch off. The friendly spoken error says "The details are in
the logs" (sprint M2); gating it broke `test_spoken_failures.py::
test_mirror_preflight_failure_on_voice_is_not_spoken`, which holds that
promise. It is code-written failure text and can quote a media name from the
request. See Departures.

### HA's recording format and layout (HA 2026.5.4 source, read in the container)

`/usr/src/homeassistant/homeassistant/components/assist_pipeline/pipeline.py`:

- `PipelineRun.start()` (line 643): the recording thread starts only when the
  run's start stage is wake word or STT, so text runs (the M10 replay,
  scripts) record nothing.
- `_start_debug_recording_thread` (1493–1524): directory
  `<debug_recording_dir>/<device_id>/<pipeline.name>/<time.monotonic_ns()>`,
  or `<dir>/<pipeline.name>/<monotonic ns>` with no device. The run
  directory is the host's monotonic clock, **not HA's run id**.
- Files: `00_wake-<wake word entity>.wav` (line 750, only when HA runs the
  wake word) and `01_stt-<stt engine>.wav` (line 943). The pucks detect the
  wake word on the device (`voice.wake_detection = satellite` on every run),
  so only `01_stt-*.wav` is written.
- `_pipeline_debug_recording_thread_proc` (1597–1636) and `const.py`:
  `SAMPLE_RATE = 16000`, `SAMPLE_WIDTH = 2`, `SAMPLE_CHANNELS = 1`; 32,000
  bytes a second plus a 44-byte header. Nothing in HA deletes them.
- `__init__.py:75`: `vol.Optional(CONF_DEBUG_RECORDING_DIR): str`.

### Container user, mount, free disk

- `docker exec homeassistant id`: `uid=0(root)`, umask `0022`. Rootful Docker
  (`/var/lib/docker`), no userns remap. Files HA writes into a bind mount are
  root-owned, mode 644, directories 755: the operator can read them, not
  delete them.
- `/var/lib/docker/volumes/kronk_ha-config/_data`: permission denied for the
  operator (no passwordless sudo). A recordings directory inside the config
  volume could not be read by the labelling script without `docker exec`, and
  HA's own backups would carry the audio. Hence a bind mount:
  host `/home/drew/kronk-voice-recordings`, container `/voice_recordings`.
  `/share` does not exist in this container (M17 finding).
- Free disk: `/` (and `/home`) 1.8 TB, 888 GB used, **851 GB free**.
- The running HA container was created from `ghcr.io/home-assistant/home-assistant:stable`
  (`sha256:ceb12021…`, labels `io.hass.version=2026.5.4`; `homeassistant.const.__version__`
  = 2026.5.4). The compose pin `:2026.5.4` is **not on the box**:
  `docker compose -f docker-compose.ha.yml up -d --dry-run homeassistant`
  plans `Recreate` and an image pull, ending `No such image:
  ghcr.io/home-assistant/home-assistant:2026.5.4`. A recreate would pull from
  ghcr. The runbook tags the local image first (step H3), so nothing is
  pulled and the version does not change.

### The household's voice run rate (Tempo, `scripts/voice_transcripts.py --since 10d --voice-only`)

- 37 runs with audio (an STT stage) from 2026-11-08 17:39 to 2026-11-17 12:24
  (8.8 days; Tempo holds nothing before 2026-11-08): **4.2 a day**.
- All on the `kronk` pipeline. Kitchen puck 36, basement 1, office 0.
- Tiers: blueprint_then_kronk 12, kronk 10, ha_local 9, blueprint 5, unknown 1.
- Speech length (`voice.stt.listen`, 36 spans): mean 3.56 s, p50 3.75 s,
  p95 5.33 s, max 7.83 s.
- Dev: 1,637 `kronk-dev` runs in the last 24 h, every one a text run with no
  satellite (the replays). No spoken Hey Jarvis run with audio in Tempo.
- Wake-word false triggers: 1 of 37 runs had an STT stage and no transcript
  (2026-11-14 19:22, tier unknown): about 3%.

## The disk estimate (criterion 6)

- Per run: 32,000 B/s × 3.56 s mean = **114 KB** (+44 B header, + one 4 KB
  directory). p95 171 KB, max 251 KB.
- Two weeks at 4.2 runs a day: 59 runs × ~118 KB = **about 7 MB**.
- Worst case, ten times the rate and every run at the longest seen: 590 × 255
  KB = **about 150 MB**.
- Free: 851 GB. Not a constraint.
- Dev runs: spoken Hey Jarvis runs are recorded too, under
  `<device>/kronk-dev/`. Their share to date is 0% (none spoken); the replays
  are text runs and record nothing. The labelling sheet and the listing skip
  `kronk-dev` unless `--include-dev`.
- False triggers: recorded too (the puck streams until HA's VAD ends the
  turn); about 3% of runs on today's data. They stay in the sheet as rows
  with an empty `heard`; labelling them as noise is useful data.

## What changed

| File | Change |
| --- | --- |
| `orchestrator/command_forms.py` (new) | `PLAYBACK_RE`, `PLAYBACK_ACTIONS`, `RATE_RE`, `RATE_THUMBS`, `ANOTHER_RE` moved here unchanged from `routing.py`; `command_of(text)` |
| `orchestrator/routing.py:142-145`, `:179-182` | aliases to `command_forms`; `:427-434` `kronk.direct_action` on `routing.decide`; `:453` route preview via `telemetry.text` |
| `orchestrator/telemetry.py:43`, `:122-124`, `:167-238` | `text()`, `strip_query()`, URL request hooks on FastAPI and httpx instrumentation, `_NoQueryText` filter on `httpx` and `uvicorn.access` |
| `orchestrator/main.py:327`, `:1220`; `agents.py:186`, `:711`, `:726`, `:895`; `tools.py:957` | text fields through `telemetry.text` |
| `orchestrator/errors.py:124-127` | comment: failure detail stays with the switch off |
| `tool_service/telemetry.py:32`, `:86-157`, `:170-172` | `RECORD_TEXT`, the same helpers, hooks and filter |
| `tool_service/main.py:1231-1233`, `:1502`, `:1527`, `:1569-1570`, `:1698`, `:1869`, `:1948`, `:2050`, `:2126`, `:2207`, `:2506`; `music_shadow.py:154`; `music_decide.py:507-516`; `taste.py:41-52`, `:246` | text in logs and the dry-run span through `telemetry.text` |
| `voice_tracer/marks.py` (new) | `RunFacts`, `correction_kind` (:116), `near_repeat` (:139), `find_mark` (:152), `apply_mark`, `ready_to_emit` (:201), `routing_from_trace` (:240); constants :46-53 |
| `voice_tracer/main.py` | header; `HELD_RECOVERY_S` (:97); `held`, `facts`, `prev_held`; state file carries held ids (`_save_state` :329); `fetch_voice_runs(also=…)`; `kronk_routing` (:472); `_apply_routing` (:513); `_mark_corrections` (:527); `flush_held` (:550); `account_for_prev_held` (:566); `/health` keys `held`, `marked`, `marks_late`, `held_lost` |
| `voice_tracer/Dockerfile` | copies `marks.py` and, from the `orchestrator` build context, `command_forms.py` |
| `docker-compose.yml:121` | production tool_service `TELEMETRY_RECORD_TEXT=${TELEMETRY_RECORD_TEXT:-false}`; `:167-172` voice_tracer build `additional_contexts`; `:258-263` comment; `:366`, `:405` dev pair `TELEMETRY_RECORD_TEXT=true` |
| `ha/configuration.yaml:1-15`, `:29-42` | install notes; `assist_pipeline: debug_recording_dir: /voice_recordings` |
| `docker-compose.ha.yml:13-27`, `:48-61` | notes and Operations; bind mount with `create_host_path: false`; `stop_grace_period: 2m` |
| `scripts/voice_transcripts.py`, `scripts/voice_label_sheet.py`, `scripts/voice_capture_check.py` (new) | listing, labelling sheet, end-date check |
| `scripts/voice_replay.py:708-718`, `:749` | `require_text_logs`: refuse when the dev pair logs no text |
| `systemd/kronk-voice-capture-check.{service,timer}` (new) | reference units, not installed |
| tests | new: `test_voice_marks.py` (80), `test_m12_routing_choice.py` (6), `test_m12_text_capture.py` (14), `test_m12_scripts.py` (14), `test_m12_capture_check.py` (12), `test_m12_ha_files.py` (3); fixtures `tests/fixtures/m12/` (two real HA run payloads, words replaced by corpus sentences); `test_lead_in.py`, `test_music_decide.py`, `test_music_dry_run.py`, `test_routing_pin.py`, `test_transcript_gate.py` set the switch on where they read text |
| docs | `docs/features/voice-capture.md` (new), `telemetry.md`, `voice-pipeline.md`, `TELEMETRY_GUIDE.md`, `README.md`, `ROADMAP.md` |

## Criteria

1. **Text in traces, per service, off and on.** Field table above.
   Orchestrator: `test_m12_text_capture.py::test_orchestrator_switch_off_no_words_anywhere`
   sends a sentence through `/voice/api/chat` and finds it in no span
   attribute and no log record; `…switch_on_pipeline_span_has_heard_and_reply`
   finds `kronk.input` = what was heard, `kronk.output` = the reply on the
   `pipeline.shim` span. tool_service: URL hook, log filter, `text()` tests
   parametrized over both services. voice_tracer:
   `test_voice_tracer_spans_switch_off_and_on`. Live on dev: see criterion 14.
   voice_tracer's text on a live run: unverified until promotion (production
   container, `.env` off).
2. **Stores and retention.** Table above. Tempo 90 days from the running
   config. Over 90 days or unlimited: container logs (size-bound, no time
   cap), recordings, the shadow log, the taste DB, web chat sessions while
   active. Proposals listed; none built (Departures, 2).
3. **Listing without TraceQL.** `scripts/voice_transcripts.py`: time,
   satellite, pipeline, heard, tier, rule, tools, reply, wrong-action kind and
   seconds, run and trace ids; dev excluded unless `--include-dev`; Tempo on
   loopback only (a non-loopback `--tempo` is refused before any request);
   writes only `--out` or stdout; `--out` inside the repo refused. Live run
   against Tempo: 37 production runs in 10 days, rules and tools filled for
   the 10 Kronk-joined runs (from the joined Kronk trace for runs traced
   before M12). Tests: `test_m12_scripts.py` (6 for the listing).
4. **Repo HA config and compose.** `ha/configuration.yaml:41-42`;
   `docker-compose.ha.yml:48-61`. Host `/home/drew/kronk-voice-recordings`,
   container `/voice_recordings`; why: check-first above. Offline
   `check_config` on a scratch copy (HA 2026.5.4 image, `--network none`,
   live `automations.yaml`, `scripts.yaml`, `scenes.yaml`,
   `rest_command.yaml` and blueprints copied out read-only):
   `Successful config (partial)`, `assist_pipeline: debug_recording_dir:
   /voice_recordings [source /check/configuration.yaml:42]`, rc 0; the
   previous file gives the same with an empty `assist_pipeline`. Scratch copy
   deleted after. Nothing installed. `test_m12_ha_files.py`.
5. **Runbooks.** Below: production and end date, each step with check and
   undo.
6. **Disk.** Above.
7. **End date.** `scripts/voice_capture_check.py`, user timer daily 09:00
   (Persistent). Decision logic: before the end date nothing; from it, one
   push a day while capture is on (live config has the key, or a recording
   newer than HA's start); nothing once off; a push that says it could not
   tell when it cannot read HA. Reads only. End date set once in the
   installed unit by the install command (placeholder refused: exit 2).
   `test_m12_capture_check.py` (12, injected date, stubbed notifier and HA
   reads). Live, read-only: `capture_state()` on today's HA = `(False, 'not
   in the live configuration.yaml')`; HA started at 1794788901 (the M17
   restart); a before-end run printed `before the end date 2026-12-01;
   nothing to do`; `systemd-analyze --user verify` on the units with a date
   filled in: rc 0. No push sent.
8. **The mark.** `voice_tracer/marks.py`; attributes on the earlier
   `voice.run`: `voice.corrected_by`, `voice.corrected_after_s`,
   `voice.corrected_by_run_id`, `voice.corrected_by_trace_id`; on the later:
   `voice.correction_kind`, `voice.correction_of_run_id`,
   `voice.correction_of_trace_id`. Near-repeat: word-level
   `SequenceMatcher` ratio ≥ 0.75 over lowercased, punctuation-free words;
   controls (skip, louder, resume, thumbs up) never count. Tests in
   `test_voice_marks.py`: same satellite only, window 0 / 59.9 / 60.0
   marked and 60.1 / 240 not, interleaved satellites, only the run just
   before, dev and production never pair (4 cases plus a dev run between),
   corpus playback and thumbs rows (17), music_another forms, near-repeat
   cases.
9. **Without text.** Kronk-answered corrections from `kronk.rule` +
   `kronk.direct_action` (`test_text_off_kronk_not_that_marks_from_the_rule`,
   `test_kind_from_kronks_routing_decision_needs_no_text`). HA-answered
   stops are not identifiable: HA's `intent-end` gives `processed_locally`,
   `response_type` and the target media players, not the intent (real
   payload in `tests/fixtures/m12/ha_run_ha_local_stop.json`);
   `test_text_off_ha_local_stop_is_not_guessed`. Near-repeat skipped with
   text off; the transcript is not even read into the facts.
   `voice.mark_basis` says `rule_only` or `rule_and_text`.
10. **Late marking.** Delayed export: a satellite run is held until its
    window + 90 s has passed and no later run from that puck that started in
    the window is still being joined; a wait is capped at 8 min from when
    this process fetched the run, and nothing is held past 12 min after it
    ended (Tempo ingestion slack: 15 min). The listing reads the mark from
    `voice.run`. Restart: held ids are in the state file; after a restart
    they are fetched again up to 11 min old, marks recomputed, each run
    emitted once (`test_restart_refetches_held_runs_and_emits_each_once`);
    ids HA no longer lists are logged with their ids and counted
    (`test_restart_reports_held_runs_ha_no_longer_lists`); a correction after
    export is logged and counted (`test_a_late_correction_is_counted_and_logged`).
    Trace ids are deterministic, so a re-emit would also be idempotent in
    Tempo.
11. **Labelling sheet.** `scripts/voice_label_sheet.py`; format documented
    in `docs/features/voice-capture.md`. Tests (`test_m12_scripts.py`, 6)
    build HA's layout in a temp dir: rows joined by device, pipeline and
    time; recordings fingerprinted (sha256, mtime, mode) before and after,
    unchanged; copies byte-identical; audio with no trace kept (`no trace`);
    traces with no audio counted; 12 of 36 sampled across 3 days × 2 pucks ×
    2 tiers, one each, same result for the same seed; existing `labels.csv`
    refused; output inside the repo refused.
12. **Labelled set location.** `/home/drew/kronk-voice-labels/`, named in
    `docs/features/voice-capture.md` for M16's backup list. No backup job
    touched.
13. **Tests.** `4646 passed, 3 skipped, 53 xfailed`. New tests use
    `no_network`, temp dirs, no sockets.
14. **Dev.** See "Dev verification" and "Replay output".
15. **Nothing changed in production.** Container image ids and start times
    before and after the dev rebuild: only `kronk-orchestrator-dev-1` and
    `kronk-tool_service-dev-1` changed. HA: read only (API, `docker exec cat
    / ls`, `docker cp` out of the container). No audio recorded (capture
    stays off live), no sound, no push. A throwaway HA container ran
    `check_config` offline (`--network none`, `--rm`) on a scratch copy; a
    scratch voice_tracer image was built to check the Dockerfile, then
    removed. Neither was a production container.
16. **Docs.** `docs/features/voice-capture.md` (captured, stores and
    retention, who can read, end date, turning each part off, the
    wrong-action query, the guest line), `telemetry.md`, `voice-pipeline.md`,
    `TELEMETRY_GUIDE.md`, `README.md` (Telemetry: traces hold text only with
    the switch on; Grafana without a login; every puck records for two
    weeks).

## Dev verification

- Rebuilt only the pair: `docker compose --profile dev up -d --build
  tool_service-dev orchestrator-dev`. Image ids and start times of every
  running container, before and after: only `kronk-orchestrator-dev-1`
  (`sha256:1b11059b…` → `sha256:a0cdb86b…`) and `kronk-tool_service-dev-1`
  (`sha256:520814a2…` → `sha256:648db072…`) changed. Both healthy, both
  `TELEMETRY_RECORD_TEXT=true` (`docker inspect … .Config.Env`), from the
  compose setting; `.env.dev` still says `false`.
- Text in dev traces (Tempo, last hour, after the replay through HA's
  `kronk-dev` pipeline): 72 `pipeline.*` traces from `kronk-orchestrator-dev`
  with `kronk.input` and `kronk.output`, transport `voice`, e.g. trace
  `e97ce37ec7c0d53d95b7a98856887cb1`: `kronk.input` "Is 98.6 a fever?",
  `kronk.output` "No, 98.6 degrees Fahrenheit is generally considered the
  average or normal human body temperature. …". Production
  `kronk-orchestrator` in the same hour: 0 traces with `kronk.input`.
- `kronk.direct_action` on dev `routing.decide` spans: 10 traces, e.g.
  `c9e6aa689fa4bb3cc42fa93d6d04acf6` (`playback`, `pause`),
  `9695462f01e46a97c9cc2085b3fbe0c7` (`playback`, `volume_down`),
  `ea5d05d3ed6e3f270cb29a88e179d2db` (`playback`, `resume`).
- Dev logs: 72 `request` events with `text_preview` filled in
  `kronk-orchestrator-dev-1`, none `null`; the replay matched every executed
  row by that text.
- The listing leaves dev out: `scripts/voice_transcripts.py --since 1h` → 0
  runs; with `--include-dev` → 46, all `kronk-dev` /
  `kronk-voice-tracer-dev`, `heard` empty (production voice_tracer, text off,
  pre-M12 code). The 72 executed rows produced 46 dev voice traces: HA keeps
  the last 10 runs per pipeline, and the replay's text runs (no satellite,
  so no idle event) outrun voice_tracer's 60 s backstop poll. Pre-existing,
  dev replays only; the household's rate is about 4 runs a day.
- voice_tracer's half of criterion 14 (text on `voice.run`) waits for
  promotion and the `.env` line.

## Production runbook (coordinator, at promotion)

Order: deploy the M12 tag as for other items (`scripts/deploy_tag.sh deploy
<tag>`; it rebuilds orchestrator, tool_service and voice_tracer and restarts
nginx), then T (transcripts), H (HA capture), R (reminder). `$HA_TOKEN` from
`.env`. `$D` = `~/backups/ha/m12-$(date +%F)`.

**T. Transcripts on**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| T1 | `echo 'TELEMETRY_RECORD_TEXT=true' >> .env` | `grep -c '^TELEMETRY_RECORD_TEXT=true$' .env` → `1` | delete the line |
| T2 | `docker compose up -d orchestrator tool_service voice_tracer && docker compose restart nginx` | `for c in kronk-orchestrator-1 kronk-tool_service-1 kronk-voice_tracer-1; do docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $c \| grep TELEMETRY_RECORD_TEXT; done` → `=true` ×3; `/api/status` and the `/api/chat` shim answer | after removing the line, the same command |
| T3 | (next spoken request) | TraceQL `{ name = "voice.run" && span.voice.transcript != nil }` returns it; `scripts/voice_transcripts.py --since 1h --voice-only` shows `heard` filled; voice_tracer `/health` (`docker exec kronk-voice_tracer-1 python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8010/health').read())"`) has `held`, `marked` | — |

**H. Audio capture on (one HA restart)**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| H1 | `mkdir -p $D && docker cp homeassistant:/config/configuration.yaml $D/configuration.yaml` | `git show b50834c:ha/configuration.yaml \| diff - $D/configuration.yaml` → empty (the live file is M17's) | none needed |
| H2 | `mkdir -p ~/kronk-voice-recordings && chmod 755 ~/kronk-voice-recordings` | `ls -ld ~/kronk-voice-recordings` → `drwxr-xr-x drew drew`, empty | `rmdir ~/kronk-voice-recordings` (only while empty) |
| H3 | `docker tag ghcr.io/home-assistant/home-assistant:stable ghcr.io/home-assistant/home-assistant:2026.5.4` | `docker image inspect ghcr.io/home-assistant/home-assistant:2026.5.4 -f '{{.Id}}'` equals `docker inspect homeassistant -f '{{.Image}}'` (`sha256:ceb12021…`) | `docker rmi ghcr.io/home-assistant/home-assistant:2026.5.4` (removes the tag only; `:stable` keeps the image) |
| H4 | `docker cp ha/configuration.yaml homeassistant:/config/configuration.yaml && docker exec homeassistant chown root:root /config/configuration.yaml` | `docker exec homeassistant cat /config/configuration.yaml \| diff - ha/configuration.yaml` → empty | `docker cp $D/configuration.yaml homeassistant:/config/configuration.yaml` and chown |
| H5 | `curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/config/core/check_config` | `{"result":"valid",...}`. Anything else: undo H4, stop | — |
| H6 | `curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states \| jq -r '.[] \| select(.entity_id \| startswith("assist_satellite.") or startswith("media_player.") or (startswith("select.") and (contains("_assistant") or contains("_wake_word")))) \| "\(.entity_id) \(.state)"' > $D/states-before.txt` | every `assist_satellite.*` is `idle` (nobody mid-request); note any `media_player` that is `playing` and pick the moment with Drew if one is; restore-state selects not changed in the last 15 min | — |
| H7 | `docker stop -t 120 homeassistant && docker compose -f docker-compose.ha.yml up -d homeassistant` (the stop uses a 120 s timeout because the old container still has Docker's 10 s default; this is the one restart) | `docker inspect homeassistant -f '{{.Config.Image}} {{.Image}} {{.Config.StopTimeout}}'` → `…:2026.5.4 sha256:ceb12021… 120`; `docker inspect homeassistant -f '{{json .Mounts}}'` lists `/home/drew/kronk-voice-recordings` → `/voice_recordings` RW; `docker events --since 10m --filter container=homeassistant --filter event=kill` shows signal 15 only; HA log has no "could not validate that the sqlite3 database … was shutdown cleanly" | restore H4, then `docker stop -t 120 homeassistant && docker compose -f docker-compose.ha.yml up -d homeassistant`; to also drop the compose change, `git revert` it first |
| H8 | (wait for HA) | `curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/` → `200`; rerun H6's command into `$D/states-after.txt` and `diff` with before: the three satellites (`home_assistant_voice_0ac919`, `satellite1_d8d1bc`, `home_assistant_voice_0ab117`) not `unavailable`, the six slot-2 selects still `kronk-dev` / `Hey Jarvis`, players as before; `python3 scripts/voice_replay.py --check-only` passes (pipelines `kronk` and `kronk-dev`, devices, areas); `curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services \| jq '.[] \| select(.domain=="rest_command") \| .services \| keys'` lists the commands as before; `docker ps` shows `music-assistant-server` with its old start time | as H7 |
| H9 | (next spoken "Okay Nabu" request) | `find ~/kronk-voice-recordings -name '01_stt-*.wav' -newermt "<H7 time>"` → one file under `<device_id>/kronk/<n>/`; `python3 -c "import wave,sys; w=wave.open(sys.argv[1]); print(w.getframerate(), w.getsampwidth(), w.getnchannels())" <file>` → `16000 2 1`; log the capture start date | — |

**R. Reminder timer (same day as H9)**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| R1 | `END=$(date -d '+14 days' +%F); sed "s/@END_DATE@/$END/" systemd/kronk-voice-capture-check.service > ~/.config/systemd/user/kronk-voice-capture-check.service; cp systemd/kronk-voice-capture-check.timer ~/.config/systemd/user/; systemctl --user daemon-reload; systemctl --user enable --now kronk-voice-capture-check.timer` | `systemctl --user cat kronk-voice-capture-check.service \| grep VOICE_CAPTURE_END` → the date 14 days from the start; `systemctl --user list-timers kronk-voice-capture-check.timer` shows the next 09:00 | `systemctl --user disable --now kronk-voice-capture-check.timer; rm ~/.config/systemd/user/kronk-voice-capture-check.{service,timer}; systemctl --user daemon-reload` |
| R2 | `systemctl --user start kronk-voice-capture-check.service` | `journalctl --user -u kronk-voice-capture-check.service -n 5` → `before the end date <END>; nothing to do`; no push | — |
| R3 | `VOICE_CAPTURE_END=$END python3 scripts/voice_capture_check.py --test-push` (the one real test push) | Drew's phone shows "Kronk: TEST voice capture reminder"; exit 0 | — |

## End-date runbook (coordinator, from the reminder)

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| E1 | In the repo, comment the two `assist_pipeline` lines of `ha/configuration.yaml` again; commit | `python3 -c "import sys; sys.path.insert(0,'scripts'); import voice_capture_check as c; print(c.capture_configured(open('ha/configuration.yaml').read()))"` → `False` | revert the commit |
| E2 | `mkdir -p ~/backups/ha/m12-end-$(date +%F) && docker cp homeassistant:/config/configuration.yaml ~/backups/ha/m12-end-$(date +%F)/` | file present, has the uncommented key | — |
| E3 | `docker cp ha/configuration.yaml homeassistant:/config/configuration.yaml && docker exec homeassistant chown root:root /config/configuration.yaml`; then check_config as H5 | `valid` | copy E2's backup back |
| E4 | H6 (nobody mid-request, what is playing) | as H6 | — |
| E5 | `docker compose -f docker-compose.ha.yml restart homeassistant` (clean since H7: `StopTimeout` 120) | as H8; `docker events` shows no signal 9 | E2's file back, restart |
| E6 | (next spoken request) | `find ~/kronk-voice-recordings -name '*.wav' -newermt "<E5 time>"` → nothing | — |
| E7 | `systemctl --user start kronk-voice-capture-check.service` | journal: `capture off (...): off`, no push | — |
| E8 | R1's undo (remove the timer) | `systemctl --user list-timers` no longer lists it | R1 |
| E9 | `scripts/voice_label_sheet.py --recordings ~/kronk-voice-recordings --out ~/kronk-voice-labels/$(date +%F)` | stderr `N of M recordings -> …/labels.csv`; `manifest.json` counts; `ls ~/kronk-voice-labels/<date>/audio \| wc -l` = sampled | the output directory can be removed; recordings untouched |
| E10 | Recordings: nothing deleted. Drew decides (below) | — | — |

Transcripts stay on after the end date (Drew: kept 90 days, matching Tempo).

## What stays unverified until promotion

- voice_tracer's new code against live HA and Tempo: holding, marks on real
  corrections, `kronk_routing` against a live trace (the parser was checked
  against two real Tempo payloads, v2 and v1, fetched read-only:
  `{'rule': 'playback', 'direct_action': None, 'tools': ['control_music']}`),
  the state file's `held` list, `/health`'s new keys, and the 3–4 minute
  arrival of satellite voice traces. Its image build was checked in a
  scratch image (`docker buildx build --build-context orchestrator=./orchestrator`;
  `import marks, command_forms, main` inside it worked) and removed.
- voice_tracer's `voice.transcript` / `voice.speech` on live runs (needs
  `.env`), and production orchestrator and tool_service text (needs `.env`).
- `kronk.direct_action` on production traces (needs the deploy; on dev,
  see Dev verification).
- HA writing into the bind mount, the WAV layout as found in the source, the
  `stop_grace_period` clean stop, and the image tag step: all at H3–H9.
- The reminder's live run after the end date, and the test push (R3).
- Real-world mark rate. On today's data (37 runs) no correction pairs exist
  to compare against.

## Test output

`./scripts/run_tests.sh -q`, final run on the last code commit (`0705485`):

```
4646 passed, 3 skipped, 53 xfailed, 6 warnings in 39.42s
```

Baseline `4517 passed, 3 skipped, 53 xfailed`; this build adds 129 (`test_voice_marks.py` 80,
`test_m12_routing_choice.py` 6, `test_m12_text_capture.py` 14,
`test_m12_scripts.py` 14, `test_m12_capture_check.py` 12,
`test_m12_ha_files.py` 3). Skips and xfails unchanged. The 6 warnings are
the existing Pydantic and pytest-asyncio deprecation warnings.

## Replay output

`python3 scripts/voice_replay.py --label m12-build`, dev path, dry-run on,
one run, nothing skipped for player state:

```
voice_replay: {'pass': 134, 'xfail': 22, 'skip': 3, 'xpass': 1}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_192154_m12-build.json
```

Row by row against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`:
114 baseline rows, all present, 46 rows added to the corpus since. One
status differs: `inc-0930-search-deep-house-ytm` fail → pass (a known
YouTube Music flapper). No failures. The one xpass,
`m19-what-happens-if-i-ask-kronk-to-pause`, is a row added after the
baseline; it also xpassed in `voice_replay_2026-11-17_181219_m9-e2e.json`,
before this build.

## Departures from the criteria and why

1. **Failure detail kept with the switch off** (criterion 1 says none of the
   listed fields carries text when off). `kronk.failure.debug_text` and
   `failure_spoken_friendly.debug` stay: the spoken error says "the details
   are in the logs" (M2), and tenet 7 wants the cause findable in one step.
   Gating them broke the test that holds that promise. Production will run
   with the switch on, where nothing differs. Drew's call if he wants them
   gated too.
2. **No in-repo retention change built** (criterion 2 says changes inside the
   repo are built). The brief makes every retention change that deletes data
   a proposal for Drew. The only in-repo candidate was the M11 shadow log;
   it is listed as a proposal. Docker log rotation in compose is not
   possible with the `journald` driver.
3. **voice_tracer verified offline only** (criterion 10, 14's voice_tracer
   half): by the brief, it is not rebuilt or restarted here.
4. **HA restart is `docker stop -t 120` then `up -d`**, not `up -d` alone
   (criterion 5: "recreate HA with the new compose file (one restart)"). Still
   one restart. The old container has Docker's 10 s stop timeout; this makes
   the one stop clean.
5. **Image tag step added to the runbook** (H3), not in the criteria: without
   it the recreate pulls `:2026.5.4` from ghcr, which tenet 3 forbids as a
   side effect.
6. **Kronk's rule is read by trace id** (`/api/v2/traces/<id>`), one extra
   loopback GET per Kronk-joined voice run, rather than a second search.
7. **routing.py's three patterns moved** to `orchestrator/command_forms.py`
   so voice_tracer reads the same forms without a copy (the stopping rule:
   no new patterns). Routing behaviour is unchanged: `test_routing_pin.py`
   (frozen copies of the patterns) and the shortcut tests pass unchanged.
8. **Satellite voice traces now arrive 3–4 minutes after the utterance**
   (was about 2), the price of the mark being on the span when it leaves.

## For Drew

1. **Journald retention:** add `MaxRetentionSec=90day` (outside the repo,
   deletes logs older than 90 days), or accept the size bound (44 days
   today). With text on, logs carry transcripts.
2. **Recordings after the two weeks:** keep, delete, or keep only the
   labelled sample. They are root-owned.
3. **M11 shadow log:** delete or truncate at the cutover review.
4. **Failure detail with the switch off:** keep (as built) or gate.
5. **Labelled set** `/home/drew/kronk-voice-labels/` into the M16 backup list.
6. **Finding: blueprint hand-offs never join a Kronk trace** (12 of 12
   `blueprint_then_kronk` runs since 2026-11-08 have no `kronk.trace_id`).
   The hand-off reaches Kronk without the satellite's device id. Without
   text, a "not that" that went through the catch-all is not marked. Fix
   (stamp the device on the hand-off), track, or drop.
7. **Finding: the HA image tag.** The container runs from `:stable` while
   compose pins `:2026.5.4`. The runbook tags locally (H3). Whether to keep
   `:stable` around is the next upgrade's question (M18).
