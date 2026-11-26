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

Plumbing (coordinator, 2026-11-26, step B live):

1. `scripts/voice_runs_backfill.py`, standard library only, run by hand:
   `--db` (default the live record), `--journal FILE` (a saved
   `journalctl -o json` export; without it the script reads
   `journalctl --user -u wyoming-whisper.service -u
   wyoming-whisper-dev.service -o json` itself, read-only), `--apply`
   (without it: a report and no write).
2. It takes the record's lock the way `--remove` does, and writes in one
   `BEGIN IMMEDIATE` transaction. A row is filled only when its `heard`
   is empty, it is not removed, and exactly one journal transcript falls
   in the run's speech-to-text window (the stage times in the row, with
   the same two seconds before and five after the audio join uses) from
   the unit that served it (household or dev), and that transcript's
   length equals the row's `voice.transcript_chars` (a row with no
   stored count is filled only if the match is the only transcript
   within a minute). Anything else is left and listed with the reason.
3. A filled row gets `heard`, `heard_source = 'whisper_journal'`, a
   raised `seq`, and is published by the exporter's next run or at once
   by the script calling the exporter's publish. The exporter's rule 1
   then holds it: Tempo's empty text never replaces it.
4. Run twice it changes nothing the second time.
5. The same pass tries the audio join again, against the NAS layout
   read-only (`/mnt/nas-backup/recordings/…`, the month directory the
   archive adds), for rows stored "not tried" (46 on the first export),
   if the test of step B's fix round 2 limit shows the join's own
   function can take a second root without a copy of its logic. If it
   cannot, that is left over and said.

Criteria:

- D1. On a fixture record and a fixture journal: a certain match is
  filled; two transcripts in the window, a length that differs, a
  removed row, a row that already has text, a dev row against the
  household unit: each left, each listed with its reason.
- D2. Without `--apply` the record's bytes do not change.
- D3. A second `--apply` changes no row and no `seq`.
- D4. No transcript text in the script's output, its report or any
  committed fixture (invented sentences; the report prints run ids,
  counts and lengths).
- D5. An export after the backfill does not empty a filled row, and the
  hourly check reports it as neither differing nor missing.
- D6. The suite passes.
- D7. By the coordinator on the live record: the dry run's counts, then
  `--apply`, then the number of rows with `heard_source =
  'whisper_journal'` and one export after it.

Tier 3 (stored data): one test, a fresh narrow test after any fix.

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
- 2026-11-25, coordinator, step A. Test `M30-e2e.md` reads FAILS on one
  count only: a suite failure that is not this step's
  (`test_m33_work_state.py::test_the_real_import_at_the_build_start`,
  broken by the coordinator's own change to the M33 seed at M33's
  promotion, after the suite had been run). A1 and A2 pass: with the two
  variables the production command line reached "Ready" in 1.5 s having
  connected to nothing. Graded pass for step A. The test fix is made
  inline by the coordinator (a test of a promoted item, not of this one)
  and left for Drew to commit; with it the suite reads `7968 passed, 3
  skipped, 54 xfailed`.
  Step A done by the coordinator at 09:43 EST: unit installed (the old
  one kept in `~/backups/m31-units-2026-11-24/wyoming-whisper.service.before-m30`),
  `daemon-reload`, restart after 15 minutes with no line in Whisper's
  journal. "Started" 09:43:40.38, "Ready" 09:43:41.56, no `httpx` line
  between or since; the process's environment holds both variables;
  `refs/main` still `0a363e91…` with its mtime unchanged (09:36:09, set
  by the test's one online run); 10300 listening; dev Whisper's process
  untouched; 25 synthetic clips against M26's run: 0 of 25 texts differ,
  p50 0.311 to 0.317 s. A4 (a spoken test) is Drew's.

- 2026-11-25, coordinator, on step B's test (`M30-e2e2.md`, FAILS on one
  blocker; B1 to B6, B8, B9 and B11 hold; suite passes). The grade
  stands. Fix round 1 of step B, all of these; each finding's `xfail`
  test in `tests/test_m30_voice_runs_e2e2.py` loses its mark with its
  fix:
  - F1 (blocker): the audio join is made when the row is final (the
    15-minute rule), against every run that can fit the recording, so
    the stored answer is `voice_label_sheet.join`'s whatever order the
    runs arrived in. A recordings directory that could not be read
    leaves the row to be tried again, not NULL for good.
  - Note 7, decided: the six-day limit goes. A final row is tried
    against the local recordings whatever its age. A row older than
    seven days at that moment that finds no recording is stored "not
    tried" (NULL), not `none`, since its recording may be on the NAS; a
    younger one is `none`. A join against the NAS layout is left over
    (step D decides).
  - F2: a trace-by-id answer of any shape is a failed trace read, never
    a raise.
  - F3: failing trace reads do not starve the rows behind them: the
    limit is a time budget for the run, and a row whose read failed
    goes to the back of the order.
  - F4: a watermark later than now is not trusted: the read starts from
    now minus the overlap, the status says so with `ok: false` until it
    is right. And a run the hourly check finds in Tempo and not in the
    record is taken (an insert is not a rewrite; "reports, does not
    rewrite" stays for a row that differs).
  - F5: the record is not made again when a status file or a published
    copy says one existed: exit 1, the status says the record is gone
    and names the restore step. `--init` makes the first one (the
    coordinator's first export) and a new one on purpose.
  - F6: removal is a command, not hand-typed SQL:
    `voice_runs_export.py --remove RUN_ID` marks the row, empties its
    text and its `raw_trace`, deletes its `run_changes` rows, with
    `secure_delete` on, and publishes at once. Publishing follows the
    record, not a good Tempo read. The feature doc says what the weekly
    copies and the monthly tars made before the removal still hold, and
    how long.
  - F7: the restore runbook stops the timer before the install and
    starts it after.
  - F8: the whole-window reads (the first read and the hourly check)
    have a time budget inside the unit's 50 s and carry on where they
    stopped on the next run.
  - F9: a SQLite error in the export reaches the status file with its
    reason; exit 1; no traceback.
  - F10: a failed enrichment is in `last_error` and the status.
  - From the notes: tests for `BEGIN IMMEDIATE`, the weekly copy's
    read-back and its use of the backup API under writes (the test
    agent's own check, brought into the suite); the published copy is
    checked against its `meta` row count every run and rebuilt when
    they differ.
  Left for step C, not this round: `last_error` shown with its time or
  only when `ok` is false; `ok: true` is not the only signal; the page
  notices a removal by `meta.rows` or `content_sha256`. Left as it is:
  status files 644 (no text in them); `left_out_dev_text_runs` NULL;
  the timer running the working tree's script, as `kronk-work-export`
  does. Tier 3: a fresh narrow test follows the fix.
- 2026-11-25, coordinator, on step B's fix round 1 (`M30-fix1.md`: every
  job done, schema 2, suite passes). Its four points:
  1. A removal leaves `labels.correct_text`. Stands for now: the table
     ships empty and nothing writes it. Left over, with the labelling
     tool whenever one is ordered: a removal must take the label's words
     too, and `--remove` already says when a run has one.
  2. and 3. Accepted as premises and to be checked by the narrow test:
     the stretch a mended watermark skips is the hourly check's to take;
     voice_tracer's 840 s export limit stays under the 15 minutes (a
     test should pin the two constants against each other).
  4. `ok` true after `--remove` with Tempo down a minute earlier: step
     C's.
  The limit under F8 (one day whose reads alone exceed the budget) is
  accepted: about a hundred times today's margin.
  A fresh narrow test follows (tier 3).
- 2026-11-25, coordinator, on step B's narrow test (`M30-e2e3.md`, FAILS
  on one blocker; every job of fix round 1 done; B1, B3, B5, B8, B9
  unbroken; suite passes). The grade stands: a stored `certain` the join
  calls ambiguous is the fault step B's first test failed on, reached by
  two paths the fix itself added. Fix round 2, small, all of these; the
  three `xfail` tests in `tests/test_m30_voice_runs_e2e3.py` lose their
  marks:
  - F1 again: when a row is inserted already final (a read cut by its
    budget, the hourly check's take, a run Tempo shows late),
    `audio_tried_at` is cleared for every row of its set and the set is
    joined again whole. The audio columns raise no `seq` and are not
    published, so nothing else moves.
  - `--remove` and `--init` wait for the record's lock (up to the
    longest a run can hold it) and exit 1 if they do not get it. Only
    the timer's ordinary run gives way with exit 0. `--check-now` or
    `--why` given where it does nothing is refused, exit 2.
  - A request to Tempo is bounded in wall time, not per socket wait, so
    the 43 s holds against a Tempo that dribbles.
  - The wait for the record (`BUSY_TIMEOUT_S`) is cut so that 43 s plus
    it is under the unit's 50, and "database is locked" reaches the
    status.
  Left as notes: a recording copied in after the row was tried; a
  neighbour with a nonsense end. A fresh narrow test of these four
  follows.
- 2026-11-26, coordinator, on step B's fix round 2 (`M30-fix2.md`: the
  four jobs done, no schema change, suite passes). Its points:
  2. A re-open can turn a stored `certain` into "not tried" when the
     row is older than seven days and its recording has gone to the NAS
     since. It needs a run inserted more than a week late beside it (a
     week's outage of the exporter, or a restore). Accepted as a limit
     and written in the feature doc by step D, whose NAS-layout join is
     the cure; the old answer is not lost to the house, the recording
     and the run are both still there to be joined.
  3. A person holding `BEGIN EXCLUSIVE` on the record in a run's last
     seconds can take it past 50 s. Accepted: the unit kills the run,
     nothing is written, the next run a minute later is whole.
  4. No status write when `--remove` or `--init` does not get the lock:
     right.
  A last narrow test of the four jobs follows.
- 2026-11-26, coordinator, on the narrow test of fix round 2
  (`M30-e2e4.md`, FAILS on one blocker; the four jobs done; lock, alarm
  and arithmetic hold against real processes; 300 random seeds pass).
  The grade stands, and the cure is the test agent's: fix round 3 is one
  change. `reopen_joins()` re-opens the set of every inserted row, final
  or not (the condition `end_s < now - FINAL_S` goes); a set with a
  young row then waits whole. The two `xfail` tests in
  `tests/test_m30_voice_runs_e2e4.py` lose their marks. Nothing else.
  Notes left: the re-open's cost on a first read of thousands of rows
  (Tempo holds hundreds); a removed row keeps the join it had. A last
  check follows: the diff is that change, the randomized test at 300
  seeds, the suite.
- 2026-11-26, coordinator: step B passes (`M30-e2e5.md`, PASSES, the
  last check of fix round 3; five tests, three fix rounds). **Step B is
  live** (B10): `voice_runs_export.py --init` at 01:04 EST took 113 runs
  (110 household, 3 dev; 71 with text; 66 recordings joined, 46 not
  local, 1 none) in 1.2 s; record 600 in a 700 directory, both copies
  600, `git status` clean. Units installed and the timer enabled; its
  first run at 01:05:30 listed 0 new and wrote no copy. `--check-now`
  at 01:05:45: Tempo lists 113, the record holds 113, 0 differing, 0
  missing. `integrity_check` ok, schema 2, no label. CLAUDE.md's Secrets
  paragraph says the tar and the share hold household speech. Left to
  step C's build agent, now that a first export exists: the record in
  `required_sources` and its test turned over. Step D's plumbing and
  criteria are written above.
- 2026-11-26, coordinator, on step C's test (`M30-e2e6.md`, FAILS on
  one blocker; C1 to C5 hold, the page ran in a browser with no script
  error and no data rendered as markup). The grade stands: the blocker
  is in the suite's guard, not the feature, but the guard is what keeps
  a test from writing the one copy of the dataset. Fix round 1 of step
  C, all of these; the five `xfail` tests in
  `tests/test_m30_heard_e2e6.py` lose their marks:
  - B1, the guard (`tests/conftest.py`). Mtime is not the test. At the
    end of a session: the record must exist if it did at the start
    (never excused); its run ids at the start are all still there with
    the same `seq`, unless the unit's journal lines for the runs in
    between account for the difference ("new N, changed N" summed
    equals the rows added and the `seq` raised; a removal is never the
    timer's); labels and `run_changes` likewise never shrink. The two
    copies may differ only when a journal line in between says a copy
    was written; the status files keep the mtime rule (they heal
    themselves). The record is read `mode=ro`. If the journal cannot be
    read, any difference fails. And the write is made hard as well as
    detected: an in-process `sqlite3.connect` or a write-mode `open` of
    a live path fails the test that makes it.
  - S1: the three out-of-range numbers answer 400 with a reason.
  - S2: a resync loads as many rows as the page had loaded (in pages of
    500 at most) and keeps the open rows and the place by run id; a
    reader who paged back stays where they were.
  - S3: a required `sqlite` member that holds no table (a zero-byte
    file) is INCOMPLETE, not OK.
  - Note 1: a reader at the top of the page (the section's head in
    view) is not anchored; a new run does not scroll the page.
  - Note 3: the satellite map is looked up as own keys only.
  Left as notes: the folded line with the copy gone and the status
  fresh; the open list's growth; the "last export failed" banner not
  seen in a browser; the small parameter oddities of note 7. Tier 2 and
  a blocker was found: one narrow retest follows.
- 2026-11-26, coordinator, on step C's narrow retest (`M30-e2e7.md`,
  FAILS by the brief's wording; all six jobs of the ruling done and
  holding in a browser, on dev and on the fixture house; suite passes).
  **Graded pass; step C is done.** The brief said any write of the
  record the fix report did not name is a blocker, and the test agent
  found five and said the call was the coordinator's. The call: the
  guard is there to stop a careless test, not one written to get round
  it. Four of the five take a deliberate route no test or code under
  test takes (a hard link to the record, `sqlite3.Connection` called
  directly, `io.FileIO`, a bare name against a `dir_fd` of the live
  directory); the fifth needs an outside process to insert rows inside
  the half second around one of the guard's two reads while the timer
  lists a new run. The ordinary routes are all refused or detected
  (`connect`, `open`, `pathlib`, `shutil`, URIs, symlinks, threads,
  deletion, a row inserted from outside). The four `xfail` tests in
  `tests/test_m30_heard_e2e7.py` stay as the record of what the fence
  does not cover. Left over, not ordered: compare by device and inode,
  wrap the two classes, and read the start snapshot again when a run is
  within the margin. No further test of step C. Step D is next.
- 2026-11-26, coordinator, on step D's test (`M30-e2e8.md`, FAILS on
  one rule; D1 to D5, the second writer, the kill and the NAS join hold;
  on the live data 71 of 71 rows with text match and 40 of 42 empty rows
  would fill, none by the weak paths). The grade stands and the fix
  comes before the live run: the write is for ever and the command will
  be run again. Fix round 1 of step D, all of these; the four `xfail`
  tests in `tests/test_m30_backfill_e2e8.py` lose their marks. This
  changes step D's plumbing 2 where they differ:
  - Each journal line is one run's. After every row is judged, a line
    that is the one match of more than one row (rows to fill and rows
    that already have words alike, same unit) fills none of them:
    "the transcript is in another run's window too".
  - A row with no stored count is never filled. A row with no
    speech-to-text span in its trace is never filled (no whole-run
    window). Each is left with its reason.
  - The window is the end of the speech-to-text span, one second either
    side (the line sits within 10 ms of it on all 111 live rows), not
    the audio join's two before and five after.
  - The NAS scan runs before `BEGIN IMMEDIATE` and before the lock; the
    transaction holds only the reads of the record and the writes.
  - A dry run that meets an interrupted write says so ("a write was
    interrupted; the next export or `--apply` repairs it").
  A narrow test follows (tier 3), then D7.
- 2026-11-26, coordinator, on step D's narrow test (`M30-e2e9.md`, FAILS
  by the brief's wording only; the five jobs hold, the exporter is
  unchanged, D2 and D3 hold, suite passes). **Graded pass.** The one
  route left: a run whose own line is missing from the journal, beside a
  line of the same length within a second of its speech-to-text end that
  belongs to a run with no window (a removed row, a row with no span, a
  run the record never held). 0 of 7,210 wrong fills in 400,000 random
  houses had an owner with a span. The third kind no rule on the record
  can close. On the live data every one of the 40 fills is a line within
  1 ms of the run's own span end with nothing else within 16 s. D7 runs
  as the script stands, on the test agent's dry-run checks (every fill's
  offset within 0.01 s). Left over, before the command is run again: a
  row with no span holds the lines between its start and end (covers the
  removed row and the row with no span); the three `xfail` tests stay.
