# Feature: Capture what was said (sprint M12)

**Built:** 2026-11-17 on the `refactor` branch · **Plan:** `Kronk evaluation findings and implementation plan.md`, "M12. Capture what was said" · **Criteria:** `../sprint/M12-criteria.md` · **Build report:** `../sprint/M12-build.md` · **Fix 1:** `../sprint/M12-fix1.md` · Production is switched on by the coordinator's runbook in `../sprint/M12-fix1.md` (it supersedes the build report's).

## What it does

Makes speech-recognition accuracy measurable, by Drew's decision of
2026-11-11:

1. **Transcripts in traces.** With `TELEMETRY_RECORD_TEXT=true`, every voice
   run's trace carries what Whisper heard (`voice.transcript` on
   `voice.run`, `kronk.input` on the orchestrator's `pipeline.*` span) and
   what Kronk answered (`voice.speech`, `kronk.output`). Kept 90 days
   (Tempo's retention).
2. **Audio from every puck, with no end date** (Drew, 2026-11-19; first
   planned as two weeks). Home Assistant's
   `assist_pipeline` `debug_recording_dir` writes each satellite run's speech
   audio to `/home/drew/kronk-voice-recordings` on the host. Recordings
   older than 14 days move to the NAS every Monday
   (`docs/features/backups.md`). The end-date reminder timer is disabled.
3. **A wrong-action signal.** `voice_tracer` marks a voice run when the same
   puck, within 60 seconds after it, says "not that", a thumbs down, a stop
   or pause, or the same request again. A wrong-action rate without
   labelling.
4. **A labelling sheet.** About 200 recordings, spread over days, pucks and
   tiers, with what Whisper heard and an empty column for what was said: the
   bench set for M13 and the roadmap's Whisper alternatives.

**Guests:** every puck records audio (no end date since 2026-11-19), household and guests
alike. Grafana is viewable without a login on the LAN (Drew's decision), so
transcripts in traces are readable by anyone on the LAN, guest Wi-Fi
included (M6: guest Wi-Fi reaches this host).

## Where text and audio are kept, and for how long

| Store | What | How long | Who can read it |
| --- | --- | --- | --- |
| Tempo (`kronk-telemetry`) | `voice.transcript`, `voice.speech` (voice_tracer); `kronk.input` / `kronk.output` on pipeline, routing, agent, model and tool spans (orchestrator); URL query strings (search terms, fetched URLs) and the shopping-list item in `DELETE /shopping_list/{item}` on HTTP spans; `music.media_id` on a dry-run span and `music.failure` on `music.play_verified` (tool_service) | 90 days (`block_retention: 2160h`, confirmed in the running config) | Grafana on LAN port 3000, no login; Tempo API on loopback |
| Container logs (journald; Docker's log driver is `journald`, set in `/etc/docker/daemon.json`) | `kronk.events` previews (request, route, retracted round, escalation note, tool-syntax leak, mirror refusal), tool_service music lines (query, said, genre phrase, resolver), httpx and uvicorn URL lines | Bounded by size only: journald's default cap (`SystemMaxUse`, 10% of the disk, at most 4 GB). On 2026-11-17 the journal was 4 GB and reached back to 2026-10-04 (44 days). No time limit | The operator (`adm` group), root |
| HA recordings, `/home/drew/kronk-voice-recordings` | One WAV per satellite run | Until deleted; nothing deletes them | The operator (files are root-owned, mode 644); not in HA's backups |
| Labelled set, `/home/drew/kronk-voice-labels/` | `labels.csv` and copies of the sampled WAVs | Kept; goes into the M16 backup | The operator |
| M11 shadow log, `data/music_shadow.jsonl` | The sentence of each music hand-off (`sentence`) | No rotation, no limit | The operator |
| HA pipeline debug (in memory) | The last 10 runs per pipeline, with transcript and reply | Until the next HA restart | HA admins, and voice_tracer over the API |
| HA automation traces, `.storage/trace.saved_traces` | The music blueprint's last 5 runs, including the trigger sentence | Until replaced by newer runs; written at a clean HA shutdown | HA admins |
| Music taste DB, `data/music_taste.db` | `picks.phrase` (the genre phrase asked for), stated taste in `music_taste.yaml` | No limit (household taste is the feature) | The operator |

Not stores of voice text: `data/sessions.db` (the web chat's history; voice
history is HA's, sent with each request), HA's recorder database (no
sentence text in `automation_triggered` events, checked; 10-day purge).

### With the switch off: what is covered, and what is not

With `TELEMETRY_RECORD_TEXT` off (as of M12 fix 1):

- **Covered, no words:** the text span attributes (`kronk.input`,
  `kronk.output`, `voice.transcript`, `voice.speech`, `bp.media_name`,
  `bp.area_or_player`, `music.media_id`, `music.failure`) are absent;
  lengths stay (`voice.transcript_chars`, `kronk.input_chars`). Event
  previews and every log line that quotes a person or a model go through
  `telemetry.text()` and read `null` / `None`. URL query strings read
  `?<query>` on server and client spans (both services; the client hook is
  `async_request_hook`, since every client is an `AsyncClient`) and in the
  `httpx` and `uvicorn.access` log lines. The two routes with words in the
  path read `/shopping_list/<item>` (`DELETE /shopping_list/{item}`, in both
  services) and `/files/<name>` (the web chat's `DELETE /files/{filename}`,
  orchestrator) in the same places. A play failure carries
  `music.failure_kind` (`resolve_miss`, `ha_unreachable`, `ha_websocket`,
  `ha_auth`, `ha_error`) and HA's error code instead of HA's message.
- **Not covered, on purpose (rulings):**
  1. The failure detail behind a friendly spoken error
     (`failure_spoken_friendly.debug`, `kronk.failure.debug_text`): the
     spoken sentence says "the details are in the logs" (sprint M2, tenet 7).
     Code-written failure text; it can quote a media name from the request.
  2. Exception text, wherever it goes: `agent_llm_error.error`, the
     "Agent '<x>' stream failed" log line, span status descriptions,
     `exception.message` span events, and the `...: %s` exception argument of
     log lines. Kept so the cause stays findable (tenet 7); it carries words
     only when an exception or a LiteLLM error quotes the request.
- **Not covered, known:**
  3. A fetched page's own URL, host and path (the query is stripped), on
     tool_service's client spans and `httpx` lines for `/fetch` and
     `/http_check` hops: the URL a person said or a model picked from search
     results. It is not one of our routes, so no path rule can name it.
  4. nginx's access log (journald) has the request line of everything that
     passes through it, `/files/<name>` from the web chat included.
  5. The log filter leaves a query with no `=` (`/path?flag`) or a bracketed
     key; no Kronk URL has either. Spans strip everything after `?`.
  6. Catalog names (`music.candidates`, the picks in the genre log lines),
     entity ids, rule names and fixed-vocabulary values are not text by this
     definition and stay.
  7. The stores in the table that are not Tempo or container logs (the
     shadow log, the dev session store, the taste DB) take text whatever the
     switch says.

## How it works

### Transcripts

One switch, `TELEMETRY_RECORD_TEXT`, read at start by the orchestrator,
tool_service and voice_tracer (compose passes `${TELEMETRY_RECORD_TEXT:-false}`
from `.env`). The dev pair has it on by a compose setting
(`docker-compose.yml`, `orchestrator-dev` and `tool_service-dev`), because
the M10 replay finds each row's request by its logged text; the replay
refuses to run when the dev pair's logs carry none.

- Spans: `telemetry.py` in each service only adds text attributes with the
  switch on (unchanged since Telemetry v2), and since M12 a request hook on
  the FastAPI and httpx instrumentations replaces URL query strings with
  `?<query>`, and a text-carrying path segment with a placeholder
  (`_PATH_TEXT`), when it is off. httpx takes the hook for `AsyncClient` as
  `async_request_hook` (a coroutine function); `request_hook` is for the
  sync `Client` only, and passing only that was M12's first leak.
- Logs: `telemetry.text(value, limit)` returns the text or `None`; every
  log line or event field that quotes a person or a model goes through it.
  A filter on the `httpx` and `uvicorn.access` loggers rewrites query
  strings and the same path segments out of their arguments.

### Audio

HA 2026.5.4, `assist_pipeline/pipeline.py`: when `debug_recording_dir` is
set, every run that starts at the wake-word or STT stage writes

```
<dir>/<device_id>/<pipeline name>/<time.monotonic_ns()>/01_stt-<stt engine>.wav
```

16 kHz, 16-bit, mono PCM (32,000 bytes a second). The pucks detect the wake
word on the device, so HA gets no wake-word audio and writes no
`00_wake-*.wav`. Text runs (the M10 replay, scripts) start at the intent
stage and record nothing. The run directory is named by the host's
monotonic clock, not by HA's run id: the labelling script joins a recording
to its voice run by device, pipeline and time (the WAV's last write falls
inside the run).

The directory is a bind mount (`docker-compose.ha.yml`), not a path in the
`kronk_ha-config` volume: the volume is root-only on the host, so the
labelling script could not read it without `docker exec`, and HA's own
backups would carry two weeks of guests' speech. `create_host_path: false`
makes a missing directory stop HA's start instead of letting Docker create
it root-owned.

### The wrong-action signal

`voice_tracer/marks.py` (pure) and `voice_tracer/main.py` (wiring).

- **Corrections** are routing's own forms (`orchestrator/command_forms.py`,
  shared by `routing.py` and copied into the voice_tracer image):
  `not_that` (the `music_another` form: "not that", "something else",
  "a different station"), `rate_down` (thumbs down, "I don't like this
  song", "never play this again"), `stop` (the playback form with action
  pause: "stop", "pause", "shut up").
- **Near-repeat**, one definition: both transcripts have words, and the
  word-level `difflib.SequenceMatcher` ratio of the lowercased,
  punctuation-free words is at least 0.75. Skip, louder, resume and thumbs
  up are never near-repeats.
- **Window:** the later run starts at most 60 s (inclusive) after the
  earlier one ended, on the same satellite, and the earlier one is the run
  just before it. Dev runs only pair with dev runs.
- **Without text:** a correction Kronk answered is known from its routing
  decision (`kronk.rule` and, since M12, `kronk.direct_action` on the
  `routing.decide` span). A stop that HA's own intent handled is not: HA's
  `intent-end` names the media player, not the intent, so "pause" and
  "resume" look alike. Near-repeat is skipped. `voice.mark_basis` says which
  (`rule_only` or `rule_and_text`).
- **Late marking:** voice_tracer holds a satellite's run back from export
  until its window plus 90 s has passed and no later run from that puck is
  still being joined, so the mark is on the span when it leaves. Satellite
  voice traces reach Grafana three to four minutes after the utterance. A
  wait for a later run is capped at 8 minutes from the fetch, and while
  voice_tracer is up and connected no run is held past 12 minutes after it
  ended.
- **A stop or an HA disconnect cuts the window short** (M12 fix 1). On
  SIGTERM (`docker stop`, a deploy) and whenever HA's websocket drops,
  every held run is exported at once with whatever mark it has, carries
  `voice.window_cut_short` (`shutdown` or `ha_disconnected`), and is
  recorded as emitted: a restart never fetches it again. Runs still waiting
  for the Kronk join keep joining (Tempo is all the join needs) and leave
  the moment it resolves; at a stop they get one last attempt (2 s). The
  cut runs' facts, without the transcript, stay in
  `data/voice_tracer_state.json` for the rest of the window, so a correction
  that arrives later, after a restart too, is logged and counted as late
  (`/health` `marks_late`). `/health` `cut_short` counts these exports.
- **What can still be lost:** a held run is lost on a hard kill (SIGKILL,
  OOM, `docker kill`) or a host crash inside its window, and on a stop
  while Tempo cannot be reached (a host shutdown, or the telemetry stack
  stopped first): the export at the stop fails, the stop takes about 15 s,
  and no counter shows it; the only sign is the OpenTelemetry library's
  "Failed to export spans batch" line. A run that ends while HA's
  websocket is unreachable for about nine minutes or more is never fetched
  (the same before M12). For the hard-kill case: Its id is
  then in the state file; the next start fetches it again from HA if HA
  still lists it (HA keeps the last 10 runs per pipeline, in memory) and it
  is under 11 minutes old, and recomputes its mark; otherwise it is logged
  with its id and counted in `/health` `held_lost`.
- **Never exported late:** no run whose first span is older than 14 minutes
  (`EXPORT_AGE_MAX_S`, 840 s; Tempo's ingestion slack is 15) is exported.
  Such a run is recorded as seen, logged with its id and age, and counted in
  `/health` `too_old`. A span past the slack broke every TraceQL metrics
  query on 2026-11-08 (`telemetry.md`, Gotchas).

Attributes on the earlier `voice.run`: `voice.corrected_by` (kind),
`voice.corrected_after_s`, `voice.corrected_by_run_id`,
`voice.corrected_by_trace_id`. On the later one: `voice.correction_kind`,
`voice.correction_of_run_id`, `voice.correction_of_trace_id`. voice_tracer
also copies Kronk's `kronk.rule`, `kronk.direct_action` and `kronk.tools`
onto `voice.run`.

The wrong-action rate, in Grafana Explore (Tempo), production pucks only:

```
{ name = "voice.run" && resource.service.name = "kronk-voice-tracer"
  && span.ha.pipeline_name != "kronk-dev" && span.voice.corrected_by != nil }
```

over

```
{ name = "voice.run" && resource.service.name = "kronk-voice-tracer"
  && span.ha.pipeline_name != "kronk-dev" && span.voice.stages =~ "stt.*" }
```

### Reading the transcripts

`scripts/voice_transcripts.py --since 7d [--out FILE]` lists voice runs from
Tempo on loopback: time, puck, pipeline, what was heard, tier, rule, tools,
reply, the wrong-action mark. Dev runs only with `--include-dev`;
`--wrong-only` for the marked ones. It writes to stdout or `--out`, never
inside the repo. Tempo returns at most 5,000 traces per search: a window
that comes back full is halved and searched again, down to a minute; if a
minute is still full the script says so on stderr and exits 3 after writing
what it read (dev replays can put thousands of runs in a day).

### The labelling sheet

```
scripts/voice_label_sheet.py --recordings ~/kronk-voice-recordings \
    --out ~/kronk-voice-labels/<date> [--sample 200] [--seed 12]
```

Writes `labels.csv`, `audio/<sample_id>.wav` (copies; `--no-copy` points at
the recordings instead) and `manifest.json`. Opens recordings read-only;
refuses to overwrite an existing `labels.csv`, and refuses `--out` inside the
repo or inside the recordings directory (copies there would read as new
recordings to the end-date check). Recordings with no trace are kept
(`notes` = `no trace`); voice runs with no audio are counted in the
manifest. Exit 3 when Tempo's search limit was hit (as for the listing).

**The join is used only when it is certain** (M12 fix 1). Recordings and
the runs that could be theirs (same pipeline, same device when both know
it, an STT stage, the WAV's last write inside the run's start − 2 s to end
+ 5 s) are paired one to one, and a pairing is kept only when every pairing
that matches the most recordings agrees on it. Otherwise (a recording whose
own run is missing from Tempo, with another run in range, say) the row
keeps its audio and device, `heard` and the run's fields stay empty, and
`notes` reads `ambiguous join: candidate runs <ids>; ...; heard left empty`.
The manifest counts them (`recordings_ambiguous`).

**File format, for the M13 bench.** `labels.csv`: UTF-8, comma-separated,
RFC 4180 quoting, one header row. Columns:

| Column | Meaning |
| --- | --- |
| `sample_id` | `YYYYMMDD-<puck>-NNN`, unique in the sheet |
| `audio_file` | the WAV, relative to the sheet's directory (absolute with `--no-copy`) |
| `correct_text` | empty; the labeller types what was said. A row is labelled when this is non-empty |
| `heard` | what Whisper heard (empty for runs traced with text off) |
| `time` | local ISO time of the recording |
| `satellite`, `device_id`, `pipeline` | which puck, which HA pipeline |
| `tier` | `ha_local`, `blueprint`, `kronk`, `blueprint_then_kronk`, `unknown`, or empty |
| `rule` | Kronk's routing rule, when Kronk answered |
| `wrong_action` | the mark, when the run was corrected |
| `duration_s`, `sample_rate` | of the WAV (16000) |
| `run_id`, `trace_id` | HA's run id and the voice trace id in Tempo |
| `source_audio` | the recording it was copied from |
| `notes` | `no trace`; `ambiguous join: ...` (heard and the run's fields left empty); `joined by time and pipeline only (no device id on one side)` (a certain join, said so); or the labeller's own |

**The labelled set lives at `/home/drew/kronk-voice-labels/`** (one
directory per sheet). The monthly config tar includes it when it exists
(`docs/features/backups.md`).

### The end date

Not in force: capture continues without an end date and
`kronk-voice-capture-check.timer` was disabled on 2026-11-19 (the unit
files remain). What follows describes the reminder as built.

`scripts/voice_capture_check.py`, run daily at 09:00 by
`kronk-voice-capture-check.timer` (reference copies in `systemd/`). The end
date is set once, in the installed unit (`VOICE_CAPTURE_END`, capture start
plus 14 days). From that date, while HA still records (the live
`configuration.yaml` has `debug_recording_dir`, or a recording is newer than
HA's last start), it pushes one reminder a day through
`scripts/lib/notify.sh`. Before the date, and once capture is off, nothing.
It never changes HA or deletes recordings.

## Turning each part off

- **Transcripts:** remove `TELEMETRY_RECORD_TEXT=true` from `.env` (or set
  it to `false`), then `scripts/deploy_tag.sh deploy <the running tag>`
  (`scripts/deploy_tag.sh status` names it): the deploy recreates the three
  containers from the release, which reads the same `.env` through its
  link, and restarts nginx. Text already in Tempo ages out over 90 days.
  **Rule: no `docker compose` command is typed in the working tree against
  the production Kronk stack** (project `kronk`). Production runs from a
  release directory; a `docker compose up` in the repo recreates the named
  services (and `searxng` with them) from the working tree, moves production
  off the tag, and mounts the working tree's `config/`, `kronk-context.md`,
  `litellm/config.yaml` and `magicmirror/`. The `.env` value reaches
  production only through `deploy_tag.sh` (M12-e2e, must-know 1). Home
  Assistant's own stack (`docker-compose.ha.yml`, project `kronk-ha`) is not
  deployed from tags; its commands are typed in the repo as before.
- **Turning audio capture off:** comment the two `assist_pipeline` lines in
  `ha/configuration.yaml` again; back up the live file, install, run
  `check_config`, restart HA once (with `stop_grace_period` in place a plain
  `docker compose -f docker-compose.ha.yml restart homeassistant` is a clean
  restart). Check: no new directory under `~/kronk-voice-recordings` after
  the next spoken request. Then remove the reminder timer (its Operations
  block). The recordings stay until Drew decides what to delete.
- **The wrong-action mark:** no switch; it costs nothing without
  corrections. Rolling back voice_tracer to the previous tag removes it.

## Gotchas

- The run directory name is a monotonic clock, not a run id: join by
  device, pipeline and time.
- Satellite voice traces are held 2.5 to 4 minutes for the correction
  window. Text runs (no satellite) are not held. A stop or an HA
  disconnect exports held runs at once (`voice.window_cut_short`), so an
  HA restart costs those runs their late corrections, not the runs.
- After the end date the HA compose file still declares the recordings bind
  mount with `create_host_path: false`: removing
  `~/kronk-voice-recordings` makes the next HA recreate fail closed (HA does
  not start) until the directory exists again or the mount is removed.
- Blueprint hand-offs (`blueprint_then_kronk`) have not joined to a Kronk
  trace in any run since 2026-11-08 (12 of 12): the hand-off reaches Kronk
  without the satellite's device id. So a "not that" that went through the
  catch-all is only recognised from its transcript, with text on.
- HA writes recordings as root: the operator can read them, not delete
  them, without `sudo` or `docker exec`.
- Until 2026-11-18 the HA container ran from the `:stable` tag (2026.5.4)
  and the pinned `:2026.5.4` tag was not on the box (it runs the pinned tag
  since the M12 recreate); the build report's
  runbook tags the local image before the recreate, so nothing is pulled.

## Blog hooks

- Recording your own voice assistant for two weeks: what Home Assistant's
  `debug_recording_dir` actually writes, why the directory name is a
  monotonic clock, and joining audio to traces by time.
- A wrong-action rate with no labelling: "not that" within a minute, read
  from the router's own decision, and holding a span back so the mark is on
  it when it leaves.
- The privacy switch that leaked: an "off" that still put search terms in
  URL attributes and access logs, and the hooks that closed it. Then the
  hook that never ran: OpenTelemetry's httpx instrumentation takes
  `request_hook` for the sync client only, every client here was async, and
  the test called the hook by hand, so it passed. The fix's test sends a
  real request through the instrumented client.
- Holding a span back costs you the span when the holder dies: export on
  SIGTERM and on losing the source, and refuse anything older than the
  store's ingestion slack.
