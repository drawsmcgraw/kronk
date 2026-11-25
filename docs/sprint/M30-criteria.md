# M30 pass criteria: what Whisper heard, kept and shown

Rewritten whole 2026-11-25 after the design review
(`docs/sprint/M30-review.md`): all eleven of its points are taken. This
file is one design; what it replaced is in git (`9baf934`, `c921fb2`) and
summarised under "History". Where this file is short, the review's point
of the same subject is the detail, and its Appendix A is the schema to
build to.

Source: Drew, 2026-11-24. "the /work endpoint should also include all the
stt transcriptions to help me troubleshoot. Make it the top of the page
but make it foldable so it doesn't pollute the view". On production
Whisper contacting huggingface.co at each start: "why does prod whisper
phone home and why do we want that?" And: "i want to build a dataset of
househould voice utterances because it will be useful for future work.
back up the transcript db."

Handoff line: every household voice run is kept for good in a database
of its own, with what Whisper heard and what answered; `/work` opens with
a folded "Heard" section showing the last 90 days of it; and production
Whisper starts without contacting anyone.

Plan doc: this file. Three steps, each with its own build and test, and
a small fourth:

| Step | What | Tier |
|---|---|---|
| A | Production Whisper offline | 3 (a production voice restart) |
| B | The record: exporter, database, published copy, status file, timer, backups | 3 (stored data, backups) |
| C | The route and the "Heard" section | 2 (a page on production) |
| D | Backfill the words of 42 early runs from the Whisper journal | 3 (stored data), small |

## What the investigation and the review found

- What Whisper heard is on the `voice.run` spans `voice_tracer` writes to
  Tempo (`voice.transcript`), only for runs traced with
  `TELEMETRY_RECORD_TEXT=true`. Tempo keeps 90 days; its oldest voice run
  is 2026-11-08; the first household run with text is 2026-11-18 07:06.
  Today: 123 household runs, 110 with a speech-to-text stage, 68 with
  text; 3 dev runs with audio ("Hey Jarvis") among 3,606 dev runs, the
  rest corpus replays with no speech-to-text stage.
- `scripts/voice_transcripts.py` (M12) lists runs from Tempo on loopback.
  Its 17.7 s for 90 days is spent enriching dev replays; a TraceQL filter
  on the stage (`span.voice.stages =~ "stt.*"`) returns the 113 wanted
  runs in 0.04 s.
- A run's span is exported once, with its wrong-action mark already on
  it; nothing older than 15 minutes changes in Tempo (review 2). A
  correction that arrives too late is written nowhere (`marks_late`, 0 so
  far).
- A satellite run reaches Tempo three to four minutes after it ends.
- Three production containers mount `data/` read-write as drew's uid; a
  `mode=ro` reader of a WAL database still creates `-wal` and `-shm`
  beside it (review 3).
- Transcripts are already readable on the LAN without a login in Grafana
  for 90 days (Drew's decision), and **both Whisper units log every
  transcript to the user journal** (1,848 lines for production since
  2026-11-08), a store `voice-capture.md` does not list. The journal
  holds the words of the 42 household runs Tempo has without text; it is
  bounded by size, not time.
- Recordings exist from 2026-11-19; the weekly archive moves them to the
  NAS. The config backup is monthly (the 10th); there is no nightly one.
  `backups.py`'s `secret` flag on a member is read by nothing.
- Production Whisper: `wyoming-faster-whisper` 3.1.0 resolves `--model
  large-v3-turbo` through `huggingface_hub`, which asks huggingface.co
  for `refs/main` at each model load and rewrites the cached ref. Nobody
  chose that. `HF_HUB_OFFLINE=1` is enough for the installed versions
  (review 8); the dev unit has run that way since M13. The last
  production start took 1.4 s from "Started" to "Ready". The cached
  revision is `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.

## Step A: production Whisper offline

Plumbing:

1. `systemd/wyoming-whisper.service` gains `Environment=HF_HUB_OFFLINE=1`
   and `Environment=HF_HUB_DISABLE_TELEMETRY=1` with a comment: why, the
   cached revision, and that an upgrade of faster-whisper by hand can
   change the cache directory's name (the venv is in no upgrade rule).
   Nothing else in the unit changes (tenet 9): not the start limit, not
   the port.
2. The build agent edits the repo copy only. The coordinator installs it
   and restarts production Whisper after checking no puck is mid-run.

Criteria:

- A1. The repo's unit differs from the installed one by the two lines
  and the comment; `systemd-analyze --user verify` on a scratch copy is
  clean.
- A2. Before the restart, by the test agent: the unit's command line
  with the two variables, started by hand on a spare loopback port under
  `strace -f -e trace=connect`, stopped at "Ready": every `connect` is
  local. If it can be made to work, also in an empty network namespace
  (`unshare -rn`); if the GPU does not open there, say so. Memory is
  checked first (about 2 GB of GPU memory for a few seconds). No audio
  is sent to it.
- A3. After the coordinator's restart: no `INFO:httpx:HTTP Request` line
  between "Started" and "Ready"; the running process's environment
  holds both variables; `refs/main` keeps its mtime; dev and production
  transcribe the same 25 clips to the same texts as before (the
  coordinator's check from M26).
- A4. One spoken test by Drew.

## Step B: the record

Plumbing:

1. **The record** is `~/.local/state/kronk-voice/voice_runs.db`
   (directory 700, file 600, override `--db` / `KRONK_VOICE_DB`), outside
   every container's mount, beside the work-state database. One writer,
   `scripts/voice_runs_export.py`. Rollback journal, not WAL. Every write
   in one `BEGIN IMMEDIATE` transaction. This is the file that is backed
   up.
2. **The published copy** is `data/voice_runs.db` and
   `data/dev/voice_runs.db`, the same content, built from the record into
   a temporary file in the same directory and renamed into place, only
   when a row changed. It holds the page's columns for the last 90 days
   (what Grafana already serves the LAN). A container that damages it
   damages a copy the next export replaces. The script's header says why
   it writes household speech under the repo's gitignored `data/` where
   `voice_transcripts.py` refuses to: Drew's order, and the same words
   Grafana shows.
3. **The status file** is `data/voice_runs.json` (and dev's), written
   every run: `ts`, schema, last good read, last error, and the summary
   (`as_of`, runs in the last 24 hours, the newest run's time, how many
   of those are marked wrong or carry an error). No text in it.
4. **What is read.** Each run reads from the last good watermark minus
   one hour to now, with the stage filter in the query (`fetch_runs` and
   `search_query` in `voice_transcripts.py` gain one optional argument;
   its CLI and its refusal of `--out` in the repo stay). The first run is
   the same code with no watermark. The watermark does not move past a
   truncated window or a failed read. Once an hour a full read compares
   with the record and reports a difference; it does not rewrite.
5. **What a row holds**: the review's Appendix A. The listing's columns;
   the fields the listing does not read (`window_cut_short`, the
   conversation id, language, response type, the engine that heard it,
   the stage times); the voice trace whole as JSON (`raw_trace`), read
   only for runs that ended more than 15 minutes ago; epoch times as the
   truth, the local string as written, and the house's local day;
   service, pipeline and `is_dev` as decided on the day; `seq`, raised
   whenever a row is inserted or changed; first and last written. The
   Kronk-side trace is not kept: its id will point at nothing after 90
   days, and the feature doc says so.
6. **Audio.** For each new row the join to its recording is tried with
   `voice_label_sheet.join` (imported, not copied) and stored: the
   recording's stable relative name, mtime, size, and how the join came
   out, "ambiguous" stored and never guessed.
7. **Labels** (`correct_text`, `notes`, a corpus id) are a second table
   keyed by run id that the exporter never writes. It ships empty.
8. **Nothing is deleted, and nothing known is lost** (review 9, the ten
   rules): a stored value is never replaced by an empty one; a stored
   value that differs from a new non-empty one is replaced and the old
   one kept in `run_changes`; a row a person marks `removed` stays
   removed and is not published.
9. **Which runs.** Every run with a speech-to-text stage, both services.
   Corpus replays have none and are out. Dev runs with audio are in,
   marked.
10. **Timer.** `kronk-voice-runs-export.timer`, every 60 s, never
    overlapping itself. The build agent proves the unit files with
    `systemd-analyze --user verify`; the coordinator installs them.
11. **Backups.** A `sqlite` member from the HOME path in the monthly
    config tar; in `required_sources` from the promotion on (added by
    the coordinator after the first export, so no earlier run reads
    INCOMPLETE); no `secret` flag. **And a weekly copy with the audio it
    describes**: the recordings archive (Monday 01:00, same share, same
    lock) also writes `<dest>/voice-runs/voice_runs-<date>.db` by the
    online backup API, read back, the newest eight kept. The daily stale
    check warns when the export's last good read is older than seven
    days. The restore runbook gains a step (remove `-wal`, `-shm` and
    `-journal` beside the target whatever the mode; `integrity_check`;
    row count).
12. Every test runs in scratch: `--db`, `--publish PATH` (repeatable),
    `--status PATH`, and a fake Tempo at the function boundary. No agent
    runs the exporter against the live record or the real `data/`.
13. Docs: `docs/features/voice-dataset.md` (the schema, how to query it,
    what it does not hold: a run that never reached Tempo, late
    corrections, the Kronk trace); `voice-capture.md`'s table of stores
    gains this database and the Whisper journal; CLAUDE.md's Secrets
    note and `backups.md` say the tar and the share hold household
    speech (the coordinator writes CLAUDE.md's line).

Criteria (review 11):

- B1. A first run on an empty record takes every run with a
  speech-to-text stage the fixture holds; a second run changes nothing
  (`seq` unchanged, the published copy not rewritten).
- B2. The ten rules of the review's point 9, each with a test.
- B3. Tempo down, slow (10 s) or answering nonsense: the record and the
  copies are untouched, the status file carries the reason and a fresh
  `ts`, exit 0, the watermark has not moved.
- B4. With the exporter stopped for longer than the overlap (fixture
  clock), the next run takes every run in the gap.
- B5. A label row survives 100 exports. A removed row stays removed and
  is in no published copy.
- B6. The record is 600 in a 700 directory outside the repo; the copies
  are under `data/` only; no transcript text in the journal, the status
  file or a leftover temporary file (a marker string planted in the
  fixture is searched for). `git status` clean.
- B7. The audio join gives the same answer as `voice_label_sheet.join`
  on a fixture.
- B8. Backup and restore: from a config tar and from a weekly copy, both
  made with `--dest <scratch> --no-notify` and `KRONK_BACKUP_STATE` in
  scratch, restored into scratch with stale `-wal`, `-shm` and
  `-journal` files planted beside the target: `integrity_check` ok,
  every run id and every label present. The stale warning fires on a
  fixture.
- B9. An incremental run takes under 1 s, twenty times.
- B10. By the coordinator at promotion, on the live system: the first
  export; then a count from Tempo over a window that ended 15 minutes
  earlier equals the record's for the same window.
- B11. The suite passes; last lines quoted.

## Step C: the route and the "Heard" section

Plumbing:

1. `GET /api/voice_runs` in a new module, `orchestrator/voice_runs_api.py`
   (not `work_api.py`, which M34 and M32 edit): newest first, keyset
   paging on `(start_s, run_id)`, `limit` clamped to 500, `q` bound and
   its `%` and `_` escaped, `flagged`, `include_dev`, `changed_after=<seq>`
   for updates; the answer carries `next_before` and `max_seq`. It opens
   the published copy per request with `mode=ro&immutable=1`. The
   envelope (fresh, stale, missing, with the reason) and the summary come
   from the status file through `servers.read_source`. No call to Tempo,
   HA or a model; the module imports no HTTP client. `/api/work`,
   `work.json` and `work_export.py` are not touched.
2. The section is a `<details>` named "Heard", first on `/work`, closed
   on every load (open or closed is not remembered: the page will run on
   a wall display nobody can touch). Its summary line shows without
   opening: runs in the last 24 hours, the newest, how many of those are
   marked wrong or carry an error, and "as of" with the time.
3. Open: newest first, grouped by the house's day, one row per run:
   time, satellite (a friendly name from a three-line map in the page;
   the entity id is the data), what was heard, whole; tier and rule (a
   dash where a blueprint hand-off has none); the reply's first line,
   the rest on opening the row; a mark for wrong-action and for error;
   "not recorded" for a run traced without text. A text filter and a
   "wrong or error only" switch, both kept across reloads. The newest
   200 rows; "show older" pages back through the 90 days.
4. It is built outside `loadAll`: its own element, fetch and timer, rows
   keyed by run id and patched in place from `changed_after`, all text
   through the page's `el()`. M32 then only swaps the timer for the
   stream.
5. The trace id is text to copy (no page here builds a Grafana link).
6. Docs: `docs/features/work-page.md` gains the section; `telemetry.md`
   and `voice-pipeline.md` point at it. M32's criteria line "transcripts
   stay off" is corrected by the coordinator to mean report text.

Criteria:

- C1. The route: paging returns every fixture row once, with two rows
  sharing a timestamp; `q` with `%`, `_`, a quote and markup; `limit`
  out of range; the published copy missing, of an unknown schema, or
  replaced mid-request.
- C2. `/api/work` is byte for byte what it was for the same `work.json`.
- C3. In a browser on dev, against a fixture database the test agent
  publishes to `data/dev` with the exporter's `--publish` and `--status`
  (allowed by this line; no household text in the fixture; dev's real
  files put back after): folded on load; the summary with "as of";
  markup in heard, reply, satellite and rule stays text; the filter and
  the switch; an open row and the scroll survive three refreshes with a
  new run arriving; "not recorded"; stale (status older than three
  minutes) and missing; 200 rows, then older.
- C4. Twenty loads of `/dev/work` cause no model completion and no Tempo
  request from the orchestrator.
- C5. The suite passes; last lines quoted.

## Step D: the words of the early runs

The 42 household runs of 2026-11-08 to 11-17 that Tempo holds without
text have their words in the user journal of `wyoming-whisper.service`.
A one-time, repeatable command joins them by time, checks each against
the run's `voice.transcript_chars`, and writes the text with
`heard_source = 'whisper_journal'`; a run with no certain match is left
as it is. More than Drew ordered; cheap; and the journal is bounded by
size, so it is done soon after step B or not at all. Criteria are
written when step B is promoted.

## Out of scope

- Audio playback; editing or labelling a run from the page.
- Any change to what `voice_tracer` records; recomputing late
  corrections.
- Keeping the Kronk-side trace.
- Serving more than 90 days to the LAN. The rest of the record is read on
  the host (`sqlite3`, the feature doc's queries).
- Pinning the Whisper model beyond offline mode, which is the pin while
  nothing online writes the cache.
- A daily copy of the databases (asked of Drew; the weekly copy is what
  this item builds).

## History

- 2026-11-24: first written as a JSON file under `data/`, last 90 days,
  rows deleted with Tempo's retention, not backed up.
- 2026-11-24, coordinator: SQLite, after Drew's "we need to move our
  information to a database".
- 2026-11-24, Drew: M29 (page-script tests in a browser) dropped; the
  page is checked by the test agent in a browser by hand.
- 2026-11-24, Drew: "i want to build a dataset … back up the transcript
  db." Nothing is deleted; it is backed up.
- 2026-11-25, design review: the record moved outside the mounts with a
  published copy; the daily full read dropped (a mark is never added
  after export); the schema widened to a dataset; a weekly copy with the
  recordings; the page's window set to 90 days; three steps and a
  fourth.

## Rulings

- 2026-11-25, coordinator: the review's eleven points are all taken, as
  written above. Three choices of the coordinator's inside them, each
  reported to Drew: the page and the route serve 90 days, not all of the
  record; the weekly copy rides with the recordings archive; step D is
  in.
