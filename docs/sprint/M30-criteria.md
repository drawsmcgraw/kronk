# M30 pass criteria: what Whisper heard, on /work

Source: Drew, 2026-11-24: "the /work endpoint should also include all the
stt transcriptions to help me troubleshoot. Make it the top of the page
but make it foldable so it doesn't pollute the view". And, on the
coordinator's note that production Whisper contacts huggingface.co at
each restart: "why does prod whisper phone home and why do we want that?"

Handoff line: `/work` opens with a folded "Heard" section listing every
voice run Tempo still holds, newest first, with what Whisper heard, what
answered and what was said back; and production Whisper starts without
contacting anyone.

Plan doc: this file. Design review before build.

## What the investigation found (coordinator, 2026-11-24)

- What Whisper heard is not stored by Kronk. It is on the `voice.run`
  spans `voice_tracer` writes to Tempo, as `voice.transcript`, only for
  runs traced with `TELEMETRY_RECORD_TEXT=true`. That is set in `.env`
  now; the first run with text is 2026-11-17 20:00. Tempo keeps 90 days
  (`telemetry/tempo.yaml`); its oldest voice run today is 2026-11-08.
- `scripts/voice_transcripts.py` (M12) already lists the runs from Tempo
  on loopback (`127.0.0.1:3200`): time, satellite, pipeline, heard, tier
  (HA's local intents, the music blueprint, Kronk), routing rule, tools,
  reply, the wrong-action mark and what set it, error, trace ids. Measured
  today: `--since 90d --include-dev` returns 3,726 runs in 17.7 s, of
  which 127 are the household's (`kronk-voice-tracer`) and 3,599 are dev
  (replays of the corpus and "Hey Jarvis" runs); 615 rows carry text; 49
  are marked wrong-action; 2 carry an error. `--since 24h`: 7 runs in
  0.05 s. Its `--out` refuses a path inside the repo ("Household speech").
- The production orchestrator runs from a release directory and reaches
  host facts through files a host timer writes under `data/` (gitignored;
  `gpu_mem.json`, `llama_props.json`, `work.json`). `/work` reads
  `/data/work.json` through `orchestrator/work_api.py` and makes no other
  call.
- Transcripts are already readable on the LAN without a login, in Grafana
  (`docs/features/telemetry.md`: "Grafana is viewable without a login
  (Drew's decision), so transcripts are …"). Putting them on `/work` opens
  nothing new, but it does write them to a file for the first time.
- `data/` is covered by the backups (`scripts/backup.sh`); what the
  nightly and the NAS runs take from it is for the reviewer to state.
- Production Whisper: `wyoming-faster-whisper` 3.1.0 is given `--model
  large-v3-turbo`, a name, and resolves it through `huggingface_hub`,
  which asks `huggingface.co/api/models/…/revision/main` at every model
  load (journal, 2026-11-24 15:38: two GETs, one a 307 to another owner's
  repository, then 200). Nobody chose that; it is the library's default.
  It asks for `main`, so a changed upstream repository would be
  downloaded at a restart: unpinned (tenet 3) and off the box (tenet 1).
  The dev unit has carried `HF_HUB_OFFLINE=1` and
  `HF_HUB_DISABLE_TELEMETRY=1` since M13 and starts from the same files
  in `~/services/wyoming-whisper/models`.

## Plumbing (coordinator's decisions)

1. A host exporter, `scripts/voice_runs_export.py`, on a new user timer
   `kronk-voice-runs-export.timer` (every 60 s), keeps a SQLite database,
   `data/voice_runs.db` and `data/dev/voice_runs.db`, mode 600: one table
   of runs keyed by run id, one row of export state (last run, last full
   read, the last error). A database, not a JSON file (revised 2026-11-24
   after Drew's "we need to move our information to a database"): the
   export is an upsert by key, the route pages and filters by query, and
   `metrics.db` is the precedent for a SQLite file the orchestrator reads.
   The orchestrator opens it read-only (`mode=ro`); the exporter is the
   only writer, in WAL mode so a read never blocks it. The exporter
   imports the listing from `scripts/voice_transcripts.py` (one source for
   how a run is read; that script's CLI and its refusal of `--out` inside
   the repo stay as they are, and the exporter's header says why it is
   the one exception: Drew's order, a gitignored directory, the same
   words Grafana already shows).
2. Cost: the first run reads everything Tempo holds; each later run reads
   the last 24 hours and upserts, and deletes rows older than Tempo's
   retention. A full re-read once a day (marks are added to a run after
   the fact; a mark later than 24 h is rare but real). A run must finish
   in under 5 s except the full one; the timer never overlaps itself.
3. "All the stt transcriptions" is read as: every run with a
   speech-to-text stage that Tempo holds, from both pucks' pipelines.
   The household's runs show by default. Dev runs with audio ("Hey
   Jarvis") are in the file, marked, behind a toggle on the page. Dev
   runs with no audio (corpus replays: about 3,600 rows, text typed by a
   script) are not transcriptions and are left out; the file says how
   many were left out.
4. Fields exported per run: time, satellite (the name
   `voice_transcripts.py` gives), pipeline, heard, tier, rule, tools,
   reply, error, the wrong-action mark with what set it and after how
   many seconds, `is_correction_of`, and the trace id. A run traced
   without text has `heard: null` and its length, shown as "not
   recorded". Nothing else from the span.
5. The route: `/api/work` is not changed (its keys are read by deployed
   pages). A new `/api/voice_runs` in `orchestrator/work_api.py` answers from
   the database (newest first, `limit`, `before`, `q`, `wrong_only`,
   `include_dev`; the summary counts in the same answer) with the same
   fresh/stale/missing envelope `/api/work` uses.
   No call to Tempo, HA or a model from the orchestrator.
6. The page: a `<details>` section "Heard", first on `/work`, closed on
   first load. Its summary line shows without opening: runs in the last
   24 hours, the time of the newest, and how many of the last 24 hours'
   runs are marked wrong or carry an error. Open: newest first, grouped
   by day, one row per run: time, satellite, what was heard (whole),
   tier and rule, the reply (first line; the rest on opening the row),
   a mark for wrong-action and for error. A text filter and a "wrong or
   error only" switch. The newest 200 rows render; "show older" adds
   more. Open or closed, the filter and the switch survive the 30 s
   refresh and a reload (localStorage, guarded).
7. All text is set as text. The trace id is a link to Grafana's trace
   view only if `/services` already builds such a link the same way;
   otherwise it is shown as text to copy.
8. Staleness: a file older than three minutes shows the section's own
   stale line with the reason, and the rows it has. A missing file shows
   "no export yet", not an empty list.
9. Production Whisper: `systemd/wyoming-whisper.service` gains
   `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1` with the dev
   unit's comment. The build agent edits the repo copy only. The
   coordinator installs it and restarts production Whisper (about ten
   seconds without speech recognition) at promotion, after checking no
   puck is mid-run, and checks the journal shows the model loaded and no
   request to huggingface.co.
10. The coordinator installs the new timer at promotion. The build agent
    proves the unit files by `systemd-analyze --user verify` and by
    running the exporter by hand with `--out` in scratch.
11. `docs/features/work-page.md` gains the section; `telemetry.md` and
    `voice-pipeline.md` point at it where they tell Drew how to see what
    was heard. `kronk-context.md`'s false line (that `/api/servers`
    probes LiteLLM, M25 leftover) is corrected in passing only if the
    file is touched anyway.

## Criteria

1. On dev, `/dev/work` opens with "Heard" folded; its summary equals a
   count made independently from Tempo for the same moment.
2. Every household run with a speech-to-text stage that
   `voice_transcripts.py --since 90d --voice-only` lists is in the file,
   field for field (compared by run id); none is missing, none doubled,
   across a full run, twenty incremental runs, and a run made while a new
   voice run is being written (a dev replay supplies new rows; no audio,
   no sound).
3. A mark added to an old run after the fact reaches the file by the next
   daily full read; shown with a fixture Tempo answer.
4. Tempo down, slow (a 10 s answer) or answering nonsense: the exporter
   keeps the old file, records the reason in it, exits 0, and the page
   shows the stale line. By test, with a fake Tempo on the function
   boundary (tests open no sockets).
5. The file is mode 600 and under `data/` only; no transcript text is
   written anywhere else (not the journal, not a temp file left behind,
   not a log line). `git status` stays clean.
6. (M29 dropped, see Rulings: checked by the test agent in a browser by
   hand.) The page shows: folded on first load; the summary;
   markup in `heard`, `reply`, satellite and rule stays text; the filter
   and the switch; state kept across `loadAll()`; "not recorded"; the
   stale and missing states; 200 rows then "show older".
7. `/api/work` is byte-for-byte what it was for the same `work.json`.
   Loading `/work` twenty times causes no model completion and no Tempo
   request from the orchestrator.
8. The exporter's incremental run finishes in under 5 s, measured twenty
   times.
9. The suite passes; last lines quoted.

## Out of scope

- Audio playback of the captured recordings.
- Editing or labelling a run from the page (the corpus workflow keeps
  that: `scripts/voice_label_sheet.py`).
- Any change to what `voice_tracer` records.
- Pinning the Whisper model to a revision; offline mode makes the cached
  files the only source, which is the pin.

## Rulings

- 2026-11-24, coordinator: the store is SQLite, not a JSON file (point
  1). "the file" in the criteria below reads as "the database". M29
  (page-script tests in a browser) was dropped by Drew on 2026-11-24;
  criterion 6 is checked by the test agent in a browser by hand.
- 2026-11-24, Drew: "i want to build a dataset of househould voice
  utterances because it will be useful for future work. back up the
  transcript db." This reverses two of the coordinator's decisions:
  - `data/voice_runs.db` is the household's lasting record, not a copy
    of Tempo. Plumbing point 2's "deletes rows older than Tempo's
    retention" is struck: no row is ever deleted by the exporter; a run
    Tempo has dropped keeps its row. The page still renders the newest
    200 and pages back through all of it.
  - It is added to `scripts/backups.py`'s list as a `sqlite` member,
    marked secret, in the nightly and the NAS sets, with a restore shown
    from a backup tar (a new criterion 10). CLAUDE.md's "Secrets" note
    on the NAS share gains household speech. The build agent says what
    else in the backup path now holds transcript text (a status file, a
    log line naming rows) and keeps it to none.
  - For the dataset: each row keeps the run id and trace id (the join to
    the captured audio of `docs/features/voice-capture.md`, where a
    recording exists; the reviewer says whether the recording's path
    should be a column), the wrong-action mark, and the time the row was
    first and last written. The first full read takes everything Tempo
    still holds (from 2026-11-08; text from 2026-11-17).
