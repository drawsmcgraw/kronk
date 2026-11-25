# M30 design review: what Whisper heard, on /work

Reviewer: `m30-review` (Fable 5.2), 2026-11-25. Read-only; this file is the
only change. Each point ends with the change to the criteria. The brief's
eight questions are marked (Q1 to Q8). How each claim was checked is in
Appendix B.

Short version:

- Rewrite the criteria file whole before a build (point 1).
- The premise behind the daily full read is false: a mark is never added
  to a run after it is exported, and nothing in Tempo older than 15 minutes
  changes (point 2).
- `data/voice_runs.db` as the lasting record can be rewritten or deleted
  by three production containers, and there are two of it (point 3).
- The fields plumbing 4 allows are not a dataset. A schema is in
  Appendix A (point 4).
- Monthly backup leaves up to a month of rows that nothing else holds
  (point 5).
- Found in passing: both Whisper units write every transcript to the user
  journal. It is a store the docs do not list, and it holds the words of
  the 42 household runs Tempo has without text (points 4 and 6).

## 1. Rewrite the criteria file whole (Q1)

The file is a JSON-file design with three amendments under it. A build
agent reads it top down and meets the reversals last. As it stands, the
agent would get these wrong:

1. **Build the delete.** Plumbing 2 says "deletes rows older than Tempo's
   retention". The strike is 100 lines lower, in Rulings.
2. **Build the daily full read for marks that never arrive** (point 2).
3. **Store twelve fields and call it the dataset.** Plumbing 4 ends
   "Nothing else from the span." The ruling adds four fields and asks the
   reviewer about a fifth. Criterion 2 then says "field for field" against
   `voice_transcripts.py`, which has 23 columns
   (`scripts/voice_transcripts.py:67`, `COLUMNS`) and writes `""` where
   plumbing 4 says `heard: null`.
4. **Treat a SQLite file as the JSON file it replaced.** Plumbing 8: "a
   file older than three minutes shows the section's own stale line".
   A database's mtime does not move when a run of the exporter finds
   nothing new (most runs: the household makes about seven voice runs a
   day), and in WAL mode it moves only at a checkpoint. Staleness by mtime
   would show STALE all day. Criterion 4, "keeps the old file, records the
   reason in it", has the same problem. `servers.read_source`
   (`orchestrator/servers.py:53`) reads a JSON `ts`; it cannot be reused
   on a database as plumbing 5 says ("the same fresh/stale/missing
   envelope").
5. **Test criterion 2 with rows that are filtered out.** "a run made while
   a new voice run is being written (a dev replay supplies new rows; no
   audio, no sound)". Plumbing 3 leaves corpus replays out of the store
   ("are not transcriptions and are left out"). A dev replay therefore
   supplies no row. The agent sees nothing arrive and either passes the
   criterion on nothing or loosens the filter. Agents may not run STT
   ("No STT or wake-stage run"), so no agent can make a real row either.
6. **Mark the member secret.** The flag does nothing (point 5).
7. **Write two databases with no rule for which is the record.** Plumbing
   1: "`data/voice_runs.db` and `data/dev/voice_runs.db`". The ruling
   names only the first as "the household's lasting record". What the dev
   one holds, whether it also keeps rows for good, and whether it is
   backed up, is not said (point 3).
8. **Prove the units with `--out`.** Plumbing 10: "running the exporter by
   hand with `--out` in scratch". The exporter has no `--out` since the
   SQLite ruling. And criterion 1 ("On dev, `/dev/work` opens with …") needs
   a database under `data/dev`, where the agent is told to write nothing
   by hand.

Other contradictions and things that cannot be checked:

- **Handoff line** says "listing every voice run Tempo still holds". The
  ruling says a run Tempo has dropped keeps its row. The handoff line
  becomes the build commit's subject; it should say what is built.
- **Criterion 1**, "equals a count made independently from Tempo for the
  same moment". A satellite run reaches Tempo three to four minutes after
  it ends (`docs/features/telemetry.md`, "Search lag") and the export is
  up to a minute old. There is no same moment. Checkable form: the summary
  carries `as_of`; a count from Tempo over `[as_of − 24 h, as_of]`, made
  at least 15 minutes after `as_of`, equals it (point 2 says why 15).
- **Criterion 3** rests on a false premise (point 2).
- **Criterion 6** asks for "200 rows then show older" and for the stale
  and missing states on dev in a browser. There are 113 rows in existence
  (Appendix B). The agent may not touch a timer or edit `./data` by hand.
  The criteria must say what the test agent may write into `data/dev`, and
  from what fixture.
- **Criterion 5**, "no transcript text is written anywhere else". As
  ruled, it is also in the tar on the NAS, and a WAL database has a `-wal`
  file beside it that holds rows (point 3). Say where it may be.
- **The ruling's backup bullet** carries an unfinished parenthesis in
  mid-sentence ("there is no nightly one … is asked of Drew") and promises
  "a new criterion 10" that is not written.
- **Counts.** "127 are the household's (`kronk-voice-tracer`)" counts by
  service. Seven of those are `kronk-dev` pipeline runs emitted before
  M17 under the production service name; `is_dev`
  (`scripts/voice_transcripts.py:146`) calls them dev. Today: 123
  household runs, 110 with a speech-to-text stage, 68 with text. "the
  first run with text is 2026-11-17 20:00": the first household run with
  text is 2026-11-18 07:06; the earlier ones are dev.
- **"Hey Jarvis" runs behind a toggle.** Three exist (dev runs with an STT
  stage), of 3,606 dev runs.
- **Plumbing 7**, the Grafana link "only if `/services` already builds
  such a link". It does not (no Grafana URL in
  `orchestrator/static/*.html`). So: text to copy. Say that.
- **Plumbing 9**, "about ten seconds without speech recognition". The last
  production start took 1.4 s from "Started" to "Ready" (point 8).
- **M32's plumbing 11** (`docs/sprint/M32-criteria.md:97`): "Report text,
  rulings and transcripts stay off, as before." M30 puts transcripts on
  the same page first. One of the two files has to say which transcripts
  it means.

Change to the criteria: rewrite the file as one design, with the rulings
folded into the plumbing and kept below only as history (date, who, one
line each). Take the corrections above. Points 2 to 10 give the content.

## 2. Nothing is added to a run after the fact; the read can be simpler (Q3)

Plumbing 2: "A full re-read once a day (marks are added to a run after the
fact; a mark later than 24 h is rare but real)." Criterion 3 tests it. The
code says otherwise:

    voice_tracer/marks.py:30   The mark goes on the earlier run's `voice.run` span: voice_tracer holds a
                               satellite's run back from export until its correction window has closed
                               (ready_to_emit), so the mark is set before the span leaves.

A run's span is exported once (`_emit_run`, then `_mark_seen`). A
correction that arrives after the earlier run has left is not written
anywhere in Tempo, on either run:

    voice_tracer/main.py:596   if target is None:
                                   HEALTH["marks_late"] += 1
                                   logger.warning("voice run %s corrects %s (%s after %.1fs), but %s was already exported; not marked",

It returns before `apply_mark`, so the later run gets no
`correction_of_run_id` either. Counted in `/health` (`marks_late`: 0 in
this process; no such line in the container's log).

Second fact. No span older than 14 minutes is ever exported
(`voice_tracer/main.py:121`, `EXPORT_AGE_MAX_S = 840`; Tempo refuses
anything past its 15 m ingestion slack, `telemetry/tempo.yaml:36`). So a
window that ended more than 15 minutes ago is final in Tempo: no run can
still appear in it and none in it can change. Two exceptions, both small:

- The listing's own enrichment (`scripts/voice_transcripts.py:196`): for a
  run with a Kronk trace and no `kronk.rule`, it reads the Kronk trace. A
  failed read leaves the rule empty for that pass (`_enrich_error`).
- voice_tracer killed hard between its export and its state save emits
  the run again after the restart, with the mark recomputed. Same ids;
  Tempo may hold two copies of the span with different attributes until
  compaction.

So the design is: each run reads from the last good watermark minus an
overlap (one hour is generous), to now. A first run, or a run after the
exporter was down for a week, is the same code with an older watermark.
No daily full read is needed for correctness.

Cost, measured today on the live Tempo (Appendix B):

| Read | Runs | Time |
|---|---|---|
| 90 days, dev included, with enrichment (the criteria's 17.7 s) | 3,729 | 17.7 s, of which 3,039 trace-by-id calls |
| 90 days, household only, with enrichment | 123 | 0.7 s (19 calls) |
| 90 days, TraceQL filtered to runs with an STT stage, any service | 113 | 0.04 s |
| 24 hours, household | 7 | 0.01 s |

The 17.7 s is enrichment of dev replays the store leaves out anyway.
Filtering in the query (`{ name = "voice.run" && span.voice.stages =~
"stt.*" }`) returns exactly the rows plumbing 3 wants (110 household, 3
dev with audio) in 40 ms, and the dev replays can never fill the 5,000
trace search limit (`SEARCH_LIMIT`, line 54). `fetch_runs` and
`search_query` need one optional argument for the filter; the CLI stays
as it is.

A full read is then cheap enough to run every hour as a **check**, not a
rewrite: every run Tempo lists is in the database and agrees; a
difference is recorded (point 9), not silently applied.

One rule the criteria must state: the watermark does not move past a
window `fetch_runs` reported in `truncated`, and does not move when Tempo
failed.

Change to the criteria: plumbing 2 becomes "each run reads from the last
good watermark minus one hour; the query filters to runs with an STT
stage; an hourly full read compares and reports". Criterion 3 goes.
Criterion 8's five seconds can be one second. New criterion: with the
exporter stopped for longer than the overlap (fixture clock), the next
run takes every run in the gap.

## 3. Where the database lives, and how many there are (Q4, Q8)

Facts:

    docker inspect (Mounts → /data)
    kronk-orchestrator-1      …/kronk-releases/item-M31/data -> /data  rw
    kronk-tool_service-1      …/kronk-releases/item-M31/data -> /data  rw
    kronk-voice_tracer-1      …/kronk-releases/item-M31/data -> /data  rw
    kronk-orchestrator-dev-1  …/kronk/data/dev -> /data                rw
    kronk-tool_service-dev-1  …/kronk/data/dev -> /data rw, …/kronk/data -> /shared ro

    docker exec kronk-orchestrator-1 id    uid=1000(kronk) gid=1000(kronk)

uid 1000 is drew's on the host. Mode 600 fences nothing: the three
production containers can read, rewrite and delete `data/voice_runs.db`,
and the orchestrator is the LAN-facing one. This is M33 review point 11,
unchanged, and it matters more here. `work.db` is "mostly rebuildable"
from git. This file, by Drew's ruling, cannot be rebuilt after 90 days.

"The orchestrator opens it read-only (`mode=ro`)" does not keep its hands
off. Scratch trial, SQLite 3.46.1 (the host's and the container's):

    writer closed:              ['t.db']
    ro reader open, no writer:  ['t.db', 't.db-shm', 't.db-wal']
    after reader close:         ['t.db', 't.db-shm', 't.db-wal']

A `mode=ro` reader of a WAL database creates `-shm` and `-wal` beside it
and leaves them. WAL also buys nothing here: the writer runs for well
under a second once a minute, and the readers are short page requests.

Two databases: the dev orchestrator sees only `data/dev`, so the page on
dev needs its own file. Plumbing 1 names both and says nothing about
which is the record.

Recommended: one record, outside every mount; what the page needs is
published from it.

- **The record:** `~/.local/state/kronk-voice/voice_runs.db`, directory
  700, file 600, beside `~/.local/state/kronk-work/`. One writer (the
  exporter). Default rollback journal, not WAL. Every write in one
  `BEGIN IMMEDIATE` transaction (M33 review point 3). This is the file
  that is backed up.
- **The page's copy:** `data/voice_runs.db` and `data/dev/voice_runs.db`,
  the same content in both, built from the record into a temporary file
  in the same directory and renamed into place, only when a row changed.
  It holds the page's columns for the page's window (point 6 proposes 90
  days). The orchestrator opens it per request with
  `mode=ro&immutable=1`: no lock, no `-shm`, and a rename under an open
  reader is safe (the reader keeps the old file). A container that
  damages it damages a copy the next export replaces.
- **The status file:** `data/voice_runs.json` (and dev), written every
  run: `ts`, schema, last good read, last error, and the summary counts
  (point 7). No text in it. `servers.read_source` reads it as it reads
  `work.json`, so the envelope in plumbing 5 is the existing one.

What this costs: one more function in the exporter and a HOME path in the
backup list (the list has those: `work-state/work.db`,
`scripts/backups.py:657`). What it removes: the exception to
`refuse_repo_path` for the record (the copy under `data/` still needs the
header note), the WAL files, the restore trap in the served file, the
question of what dev's database is, and the mtime problem of point 1.

If the coordinator keeps `data/voice_runs.db` as the only file: drop WAL
(or accept and name the two extra files), state "three containers can
write and delete it", and say what `data/dev/voice_runs.db` is (a copy,
not backed up).

Consistent with M33 where it should be: one writer, `BEGIN IMMEDIATE`,
600, a `sqlite` member, outside the mounts, a location override for tests
(`--db`), and no agent run against the live file. Different on purpose: a
container reads a published copy of this one; none reads `work.db`.

Change to the criteria: plumbing 1 as above. Criterion 5 names the three
places text may be (the record, the two copies) plus the backup. The
exporter takes `--db`, `--publish PATH` (repeatable) and `--status PATH`
so every test runs in scratch.

## 4. The dataset: what a row holds (Q2)

Drew: "i want to build a dataset of househould voice utterances because
it will be useful for future work."

**What Tempo has that the listing does not read.** One household voice
trace today (attribute names only):

    voice.run            ha.conversation_id ha.pipeline_id ha.pipeline_name ha.pipeline_run_id ha.satellite_id
                         kronk.direct_action kronk.join_matches kronk.origin.device_id kronk.rule kronk.tools
                         kronk.trace_id voice.handled_by voice.language voice.mark_basis voice.response_type
                         voice.speech voice.speech_chars voice.stages voice.transcript voice.transcript_chars
                         voice.wake_detection
    voice.stt.listen     voice.vad_start_ms voice.vad_end_ms              (1,963 ms)
    voice.stt            voice.engine …                                    (2,265 ms)
    voice.stt.transcribe voice.engine                                      (301 ms)
    voice.intent         voice.engine voice.prefer_local_intents voice.processed_locally …  (1,037 ms)
    voice.tts            voice.engine voice.voice voice.streamed

`ATTRS` (`scripts/voice_transcripts.py:58`) selects 22 attributes of the
root span. Not selected, and gone when Tempo's 90 days pass:

- `voice.window_cut_short` (one run in the last 30 days). It says the
  run left before its correction window closed, so "not marked" on that
  row means "not known". A dataset that counts wrong actions needs it.
- `ha.conversation_id` (which runs were one conversation),
  `voice.language`, `voice.response_type`, `kronk.join_matches`,
  `voice.corrected_by_trace_id`.
- Everything on the child spans: which STT engine heard it
  (`voice.engine`: `stt.faster_whisper` is production's Whisper,
  `stt.faster_whisper_2` the dev one with the vocabulary prompt,
  `docs/features/voice-pipeline.md:98`), how long the speech was, how
  long Whisper took, how long the answer took.
- The Kronk side (the joined trace: what the model was given, the tool
  arguments and results). The row keeps `kronk_trace_id`; after 90 days
  it points at nothing.

Recommended: for each new row, one trace-by-id read of the voice trace,
stored whole as JSON in a `raw_trace` column (a few KB; about 2,400 rows
a year at today's rate of 6.5 a day), with the useful parts also as columns. Nothing
has to be foreseen then. The Kronk trace is larger and is a different
dataset (what Kronk did, not what was said); the criteria should say
plainly that it is not kept, so the dangling id is a decision.

**Audio.** Recordings exist from 2026-11-19 (66 WAVs locally, 6 on the
NAS). The weekly archive moves those older than 7 days
(`scripts/backups.py:328`, `age_days: int = 7`; `voice-capture.md` says
14) to

    <dest>/recordings/<device>/<pipeline>/<YYYY-MM>/<run>/<file>      (backups.py:1771)

keeping each file's mtime (it fails the copy otherwise: "the destination
did not keep the recording's time"). The run directory is HA's monotonic
clock, not a run id; the join is by device, pipeline and the WAV's mtime
inside the run (`scripts/voice_label_sheet.py:120`, used only when
certain).

So the stable name of a recording is `<device>/<pipeline>/<run>/<file>`;
the month in the NAS path is the month of its mtime. Yes, the path should
be a column, in that relative form, with the mtime, the size (duration is
`(bytes − 44) / 32000`) and how the join came out. The join can be made
later from the row's device, pipeline, start and end, as long as those
are stored to the millisecond, so it is not "now or never". It is cheap
to make now (a directory listing and a `stat` for each new row; the
files are mode 644) and it reuses `voice_label_sheet.join`. A row keeps
`audio_join = NULL` until tried, so rows before 2026-11-19 are simply
"none".

**Which Whisper, which prompt.** Production's has had one model and no
prompt since May; its history can be rebuilt later from
`systemd/wyoming-whisper.service` in git. The dev prompt cannot:
`~/services/wyoming-whisper/dev-prompt.env` is rewritten by
`kronk-stt-prompt.timer` (daily, 04:02) and is in no repository. Today
that affects three rows. Smallest sound choice: store `stt_engine` per
row now (it says which unit), keep the `stt_config` table in the schema,
and fill it from the day production gets a prompt
(`voice-pipeline.md`, "Moving the prompt to production, when Drew
decides"): that item's criteria must say so. The model on disk today, for the record: repository
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, `refs/main`
`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.

**Satellite.** The value is HA's entity id
(`assist_satellite.home_assistant_voice_0ac919_assist_satellite`: 101 of
123 household runs; two other pucks, 7 and 3; none for 12 typed runs).
Store the entity id and the device id as they are. A friendly name for
the page is display, not data: a three-line map in the page or the
config, not a column.

**Labels.** The corpus workflow's columns are `correct_text` and `notes`
(`labels.csv`, `voice-capture.md`), and a corpus row has an `id`. They
belong in a second table keyed by run id that the exporter never writes.
Then no upsert can ever overwrite a label, and M30 ships the table empty
(labelling from the page stays out of scope).

**What must be captured now because it cannot be recovered later:**

1. The rows themselves, with the fields above: 90 days from each run.
2. **The words of the 42 household runs of 2026-11-08 to 11-17 that
   Tempo holds without text.** They are in the user journal:
   `wyoming-whisper.service` logs each transcript
   (`INFO:wyoming_faster_whisper.dispatch_handler:`) and each clip's
   length (`INFO:faster_whisper:Processing audio with duration`); 1,848
   such lines since 2026-11-08 16:12, the unit's oldest entry. The
   journal is bounded by size, not time (4 GB, 44 days on 2026-11-17).
   A one-time backfill, joined by time and checked against
   `voice.transcript_chars`, with `heard_source = 'whisper_journal'`.
   This is the only item on this list with a deadline measured in weeks.
   It is more than Drew's order; it is offered, and it is cheap.
3. `window_cut_short` and the child spans: 90 days.
4. Late corrections exist only in voice_tracer's container log (none so
   far).

Not in the database by design, and the page should not claim otherwise:
a run voice_tracer never exported (too old, lost in a hard kill, HA
unreachable for nine minutes: `voice-capture.md`, "What can still be
lost") has no row. Its audio is in the recordings and its words are in
the journal. "Every voice run" on the page is every run that reached
Tempo.

Change to the criteria: plumbing 4 is replaced by the schema of Appendix
A; "Nothing else from the span" goes. Criterion 2 compares the columns
that come from `to_row` against `voice_transcripts.py` for the same
window, and the rest against the fixture trace. New criteria: a label row
survives any number of exports; the audio join gives the same answer as
`voice_label_sheet.join` on a fixture, and "ambiguous" is stored, not
guessed. The journal backfill is a separate small step (point 10) or
dropped by a ruling that says so.

## 5. Backups (Q6)

**The flag.** "added … as a `sqlite` member, marked secret". The flag is
read by nothing:

    scripts/backups.py:915      elif kind == "sqlite":
    scripts/backups.py:916          read_sqlite(c, arc, p)

`read_sqlite` takes no `secret` and builds its `Member` without it. For
files and trees the flag is stored on the `Member` (lines 707, 898) and
no line reads it again: not the manifest, not the tar builder. The whole
tar is 600 and "holds every secret in the clear"
(`docs/features/backups.md:150`). Marking the member changes nothing.
What does the work is the sentence in CLAUDE.md and in `backups.md` that
the tar and the share now hold household speech. (The share already
holds the speech itself: the recordings.)

**Required.** `music_taste.db` and `solar.db` are in `required_sources`
(line 677) under "The only copies of household data in the tar". This
file is the same kind once it exists. Add it there, so a tar without it
is INCOMPLETE and the notice says so. Until its first export it is
absent; the member must not make every earlier run INCOMPLETE, so add it
to the list at the promotion, after the first export.

**Monthly is not enough for this file.** Tempo is on the same disk. After
a disk loss, every row newer than the last tar is gone for good: up to a
month, about 200 household runs. The audio fares better than its rows:
recordings older than a week are already on the NAS. So for up to three
weeks of speech the NAS would hold the WAVs and nothing that says what
was heard, what answered or what was corrected.

Smallest fix without a new timer: the weekly recordings archive (Monday
01:00, same share, same lock, already notifies) also writes a dated copy
of the database (`<dest>/voice-runs/voice_runs-<date>.db`, by the online
backup API, read back, the newest N kept). Rows then leave the box with
the audio they describe. It is new code in `backups.py`, which took four
test rounds in M16; if that is too much for this item, the honest wording
is "monthly; up to a month of rows is lost with the disk" and the
question already owed to Drew ("do you want the databases daily?") covers
it. He should be asked with that sentence, because "back up the
transcript db" reads as an expectation that it is safe.

A cheap guard either way: the daily stale check (`stale_warnings`,
line 2220) warns when the export's last good read is older than seven
days. An exporter that died quietly costs nothing for 90 days and then
costs rows; nothing else would say so.

**Restore.** The tar's copy is one whole file (online backup API, no
WAL). The trap is on the destination: a `-wal` or `-shm` left beside the
old file is replayed onto the restored one (`docs/runbooks/restore.md`,
steps 13 and 14, say so for `auth.db` and `work.db`). With the record in
rollback-journal mode (point 3) there is no `-wal`; the stale file to
remove is `-journal`. The runbook step removes all three names whatever
the mode, then `PRAGMA integrity_check`, then a row count against the
manifest. The published copies are not restored; the next export
rebuilds them.

Change to the criteria: the ruling's backup bullet becomes: a `sqlite`
member from the HOME path, in `required_sources` from the promotion on;
no "secret"; the docs sentence; either the weekly copy or the stated
loss, by the coordinator's choice and Drew's answer; the stale warning;
criterion 10 written out: from a tar made with `--dest <scratch>
--no-notify`, restore into scratch with stale `-wal`, `-shm` and
`-journal` files planted beside the target, `integrity_check` ok, every
run id and every label present.

## 6. Privacy and exposure, as facts (Q4)

Who can read what, once this is built as ruled:

| Place | Readers |
|---|---|
| `data/voice_runs.db` | drew on the host; the three production containers (read and write, point 3); `tool_service-dev` (read, through `/shared`) |
| `/api/voice_runs`, `/work` | anyone who can reach port 80 (`nginx/nginx.conf:9`, `listen 80`; no auth on `location /`); guest Wi-Fi reaches this host (`voice-capture.md`, "Guests") |
| The monthly tar | drew; the tar is 600 on a share mounted as drew (`drwx------`) |
| The NAS share | whoever holds the NAS backup account, and the NAS's own admins |

Already true before M30:

- Grafana on port 3000 shows transcripts and replies with no login, 90
  days back (Drew's decision; `telemetry.md`).
- **The user journal holds every Whisper transcript.** Both
  `wyoming-whisper` units log each one at INFO. It reaches back to
  2026-11-08 for production (1,848 lines) and is readable by drew and
  the `systemd-journal` group. `voice-capture.md`'s table of stores does
  not list it (its journald row is about container logs). So the
  criteria's "it does write them to a file for the first time" is not
  so, and `TELEMETRY_RECORD_TEXT=false` does not turn this store off.
  This is a finding about M12, not M30; it belongs in that table.
- `data/music_shadow.jsonl` holds music sentences with no limit.

What goes beyond Drew's order, in my reading:

1. **For good, on the LAN, without a login.** Drew ordered the
   transcriptions on `/work` and, separately, a dataset kept for good and
   backed up. Neither sentence says the whole dataset is served to the
   LAN. The coordinator's ruling joins them: "The page still … pages back
   through all of it", and the route takes `q`. Grafana's reach is 90
   days; this would be every utterance ever, searchable by anyone on
   guest Wi-Fi. Recommended: the page and the route serve the last 90
   days (what Grafana already serves, and enough to troubleshoot); the
   rest is read on the host with `sqlite3`. With point 3 the published
   copy simply holds 90 days, so the route cannot serve more whatever it
   is asked. If the coordinator keeps "all of it", Drew should be told in
   one sentence at promotion.
2. **Replies.** Drew asked for "all the stt transcriptions". The handoff
   line adds "what answered and what was said back". A reply can speak
   what the household asked Kronk to look up. Grafana shows it already,
   so nothing new is opened, and for troubleshooting the reply is half
   the story. Recommended: keep it as plumbing 6 has it (first line, the
   rest on opening the row), and store it in the record. Not a question
   for Drew.
3. **The wall display** (M32: "this page … will eventually be put onto a
   display"). Folded, the section shows counts only, and nobody can
   touch that display. Plumbing 6 keeps the open state in localStorage:
   if the section is ever opened on that browser it stays open. Keep the
   filter and the switch across reloads; let open or closed last only
   until a reload.
4. **Guests' speech** is in the audio and the traces already; the dataset
   keeps their words for good. "No row is ever deleted by the exporter"
   is right for the exporter. The criteria should say that a person can
   remove a row by hand, and how the exporter then leaves it removed
   (Appendix A, `removed`), or the first request to forget an utterance
   will be answered by a row that comes back a minute later.

Change to the criteria: plumbing 3 or 5 states the page's window; the
docs line for `voice-capture.md`'s table gains the Whisper journal and
this database; the open state is not persisted; a removed row stays
removed.

## 7. The route and the page (Q5)

**Query shape.** Newest first, keyset, never OFFSET:

    GET /api/voice_runs?limit=200&before=<start_s>,<run_id>&q=…&flagged=1&include_dev=1

    WHERE (start_s, run_id) < (:start_s, :run_id)
      AND (:dev OR is_dev = 0)
      AND (:flagged = 0 OR corrected_by IS NOT NULL OR error_code IS NOT NULL)
      AND (:q IS NULL OR heard LIKE :pat ESCAPE '\' OR reply LIKE :pat ESCAPE '\')
    ORDER BY start_s DESC, run_id DESC LIMIT :limit

`before` on time alone would drop a run that shares a millisecond with
the page's last row; the pair is the cursor. `LIKE` over a few thousand
short rows needs no full-text index. `limit` is clamped (500). `q` is
bound, never formatted in, and its `%` and `_` are escaped. The answer
carries `next_before` (null at the end) and `max_seq`.

**Updates without a re-render.** Each row has `seq`, a counter the
exporter raises whenever the row is inserted or changed. The page keeps
the highest `seq` it has seen and asks `?changed_after=<seq>`; it gets
only new and changed rows and patches them in by `data-run`. This is
what M32's plumbing 8 will demand ("only what changed is replaced"), and
it costs one column now.

**The summary line** needs no database read. The exporter computes it and
writes it into the status file (point 3): `as_of`, runs in the last 24
hours, the newest run's time, how many of those are marked or carry an
error. A folded section then costs the orchestrator one small JSON read,
and M32's stream can `stat` that file exactly as it will `stat`
`work.json`. The line should say "as of"; the newest run is three to
four minutes behind the room by design (the correction window).

**Twenty open pages.** Folded: twenty JSON reads every 30 s. Open: twenty
indexed queries returning zero or a few rows. The first open is 200
rows (my estimate: about 60 KB). Nothing to measure; criterion 7's "no model
completion and no Tempo request" is checked by reading the route (it
imports no HTTP client) and by the dev orchestrator's trace for the
request having one span.

**So M34 and M32 do not redo it.**

- M34 moves `work.json`'s source to `work.db`. M30 must not touch
  `work_export.py`, `work.json` or `/api/work` (criterion 7 already holds
  `/api/work` byte for byte). Its exporter, units, route and status file
  are its own. Then M34 has nothing of M30's to move.
- M32 replaces `loadAll`'s rebuild. Today every section is cleared and
  built again every 30 s (`work.html:386`, `clear(box)`, and the same at
  lines 394, 408, 429, 467 and 474).
  If "Heard" is rendered inside `loadAll` it loses its open rows and its
  scroll twice a minute, and plumbing 6's "survive the 30 s refresh" has
  to be bought with state-saving code that M32 then deletes. Build it
  outside: its own element, its own fetch and timer, rows keyed by run
  id, patched in place, all text through the page's `el()` (which sets
  `textContent`, `work.html:209`). M32 then only swaps the timer for the
  stream.
- The route is added in a new module (`orchestrator/voice_runs_api.py`),
  not in `work_api.py`, which M34 and M32 both edit.

Change to the criteria: plumbing 5 and 6 as above (cursor, `changed_after`,
`limit` bound, the summary from the status file, "as of", the section
outside `loadAll`, the new module). Criterion 6 gains: an open row and
the scroll position survive three refreshes with a new run arriving
(fixture database served to dev).

## 8. Production Whisper offline (Q7)

**Is `HF_HUB_OFFLINE=1` enough?** Yes, for what is installed
(`wyoming-faster-whisper` 3.1.0, `faster-whisper` 1.2.1,
`huggingface_hub` 1.16.1, `ctranslate2` 4.7.2):

- `--local-files-only` does not reach faster-whisper. `ModelLoader`
  passes it to the ONNX and Transformers loaders only:

      wyoming_faster_whisper/models.py:154   transcriber = FasterWhisperTranscriber(
                                                 model, cache_dir=self.download_dir, device=…,
                                                 compute_type=…, cpu_threads=…, vad_parameters=…)

  So the flag on the dev unit's command line is inert, and the dev unit's
  comment is right.
- `WhisperModel` calls `download_model`, which calls
  `huggingface_hub.snapshot_download` (`faster_whisper/utils.py:116`).
- `huggingface_hub/constants.py:185` reads the variable once at import;
  the request hook refuses before any socket opens:

      huggingface_hub/utils/_http.py:223   if constants.is_offline_mode():
                                               raise OfflineModeIsEnabled(

  and `snapshot_download` catches that (`_snapshot_download.py:248`) and
  resolves `refs/main` from the cache (line 286).
- The model loads once, at start (`__main__.py:217`, "Pre-loading
  transcriber"), so start is the only time the Hub is asked.
- Observed: the dev unit's journal has no `huggingface.co` line since its
  first entry (2026-11-17), across its daily prompt restarts. Production's
  journal holds one start (2026-11-24) and the two GETs with it.

`HF_HUB_DISABLE_TELEMETRY=1` adds nothing in offline mode. Keep it, so
the two units read the same.

**The two units.** The repo copies equal the installed ones (`diff`,
both). Dev has, and production lacks: the two `Environment=` lines;
`StartLimitIntervalSec=300` and `StartLimitBurst=3`; the prompt
`EnvironmentFile` and `--initial-prompt`; `--local-files-only`; and it
listens on `127.0.0.1:10301` against production's `0.0.0.0:10300`. Add
the two `Environment=` lines and their comment, nothing else (tenet 9).
Not the start limit: production should keep trying.

**What a restart looks like.** The last one, 2026-11-24: "Started"
15:38:30.27, the two Hub requests at .67 and .69, "Ready" 15:38:31.71.
1.4 seconds with the model file in the page cache; longer after a boot.
An utterance that reaches STT in that window fails at the STT stage. HA
opens a Wyoming connection per request, so HA needs nothing.

**What would make an offline start fail.**

1. `models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/refs/main`
   missing, or a file of the snapshot it names missing: then
   `LocalEntryNotFoundError`, exit, and `Restart=on-failure` every 5 s
   for ever, with speech recognition down. Present today: the ref, and
   `config.json`, `model.bin`, `preprocessor_config.json`,
   `tokenizer.json`, `vocabulary.json`.
2. A faster-whisper upgrade that changes the name the cache directory is
   derived from. `faster_whisper/utils.py:29` maps `large-v3-turbo` to
   `mobiuslabsgmbh/faster-whisper-large-v3-turbo`; upstream has since
   moved the repository (the 307 to `dropbox-dash/…` in the journal). A
   release that updates the map looks for a directory that is not there.
   The venv is not in `scripts/upgrade_rules.toml`, so only an upgrade by
   hand can do it; the unit's comment should say so.
3. Nothing about the network itself. Today, online, a dead network is
   also survived (`httpx.ConnectError` is caught at the same line), after
   a wait. Offline removes the wait.

**The pin.** "offline mode makes the cached files the only source, which
is the pin" holds only while nothing online writes that cache. Until
now production did, at each start: `refs/main` has mtime 2026-11-24
15:38, its last restart. So production's start could have changed what
the dev unit loads next. After this change no unit writes it. Record the
revision in the unit's comment:
`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.

**Proving no request left the box.** Three things, in rising strength:

1. The journal: from "Started" to "Ready", no `INFO:httpx:HTTP Request`
   line (the one earlier start in the journal has two). Absence of a log
   line, so weak alone.
2. The environment the process really has:
   `tr '\0' '\n' < /proc/<MainPID>/environ | grep HF_HUB`, and
   `refs/main` with its mtime unchanged after the restart (an online
   start rewrites it; that is checked above).
3. The same command line started by hand before the promotion, under
   `strace -f -e trace=connect` (installed, `/usr/bin/strace`), on a
   spare loopback port, stopped at "Ready": the only `connect` calls
   are local. Or in an empty network namespace (`unshare -rn`), which
   proves the stronger thing, that it starts with no network at all. I
   did not run either (it loads the model: about 1.9 GB of GTT for a few
   seconds; and whether the GPU opens inside a user namespace is
   untested). It is the coordinator's step, with the memory checked
   first. Not an STT run and no sound.

Change to the criteria: plumbing 9 gets the measured time, the revision
in the comment, and the three checks; "the build agent edits the repo
copy only" stays. Out of scope's line on pinning gains "while nothing
online writes the cache".

## 9. Where a row can be wrong for ever (Q3)

With "no row is ever deleted" and a source that forgets, each of these
needs a rule in the criteria:

1. **An upsert that overwrites.** After 90 days Tempo returns nothing
   for a run; before that it can return less (an enrichment read that
   failed). Rule: a stored value is never replaced by an empty one. A
   stored value that differs from a new non-empty one is replaced, and
   the old row goes to `run_changes` (Appendix A). The hourly check of
   point 2 reports through the same path.
2. **A run seen half-written.** The fields of a row all sit on the root
   span, which is complete when it is exported, so the row itself cannot
   be half a row. The child spans can lag by one export batch. Rule:
   `raw_trace` is read only for runs that ended more than 15 minutes
   ago.
3. **A window cut short** (voice_tracer stopped by a deploy, HA's
   websocket dropped): the run is exported unmarked whatever followed.
   Store `window_cut_short`; "not marked" on such a row is unknown.
4. **A late correction**: in neither run (point 2). The exporter could
   recompute marks from its own rows with `marks.find_mark`, which is
   pure. Not proposed for this item; the criteria should name the gap.
5. **Blueprint hand-offs have no rule or tools.** 30 of 123 household
   runs are `blueprint_then_kronk`, and none joins to a Kronk trace (the
   hand-off carries no device id; `voice-capture.md`, Gotchas). Their
   `rule` and `tools` are empty and will stay so. The page should show a
   dash, not "no rule matched".
6. **Clock and zone.** `to_row` writes `time` as local ISO with the
   offset, to the second (line 213), and `start_s` to the millisecond.
   Store the epoch as the truth and the local string as written. Store
   the local day too: the page groups by day, the wall display's browser
   may sit in another zone, and "day" should be the house's. One hour a
   year has two 01:30s; the offset in the string tells them apart.
7. **Dev or not.** `is_dev` is true by service or by pipeline name
   (line 146); the criteria count by service alone. Store the service
   and pipeline as they came, and `is_dev` as decided on the day. If a
   pipeline is renamed, old rows keep the answer of their time.
8. **A corpus replay taken for speech.** The test is "an STT stage is in
   `voice.stages`". Replays start at the intent stage and have none
   (3,603 of the 3,606 dev runs today; the other three are spoken). Twelve household runs are typed text with no
   satellite; the same test leaves them out, rightly. Two household runs
   have only an STT stage (nothing recognised or a duplicate wake); they
   are in, with `heard_chars` 0 or the error.
9. **A truncated search** (5,000 traces in a 60 s window) advancing the
   watermark: point 2's rule.
10. **A run that never reached Tempo**: no row, ever (point 4).

Change to the criteria: the ten rules, each with one test at the function
boundary with a fake Tempo. They replace criteria 2 to 4.

## 10. Smaller, and in steps (Q8)

More than one household and one operator need:

- WAL, and a second database for dev (point 3).
- The daily full re-read (point 2).
- An "export state" table with history. One row: watermark, last good
  read, last error.
- Paging the page back through everything (point 6).
- The Grafana-link condition (it resolves to "text").
- `kronk-context.md` "only if the file is touched anyway" (plumbing 11):
  either a job or not in the file.
- A 60 s timer for data that is three minutes old on arrival. It is not
  wrong, and it matches the other exporters; M32 will want it at 15 s
  anyway. Leave it.

A simpler design than mine, considered: no database on the page side at
all; the exporter writes the last N rows into a JSON file as
`work.json` does. It fails on "all the stt transcriptions" with a
filter, and on M32's stream, which would then push every transcript to
every open page on each change. The published SQLite copy is the
smallest thing that pages and filters.

**Split it in three.** The parts share nothing but the name, and they are
of different tiers:

| Step | What | Tier | Why separate |
|---|---|---|---|
| A | Production Whisper offline: two lines in a unit file, installed and restarted by the coordinator | 3 (a production voice restart) | Ready now. Stands alone. Drew's question was direct. One variable. Can be promoted today |
| B | The exporter, the record, the published copy, the status file, the timer, the backup member, the restore step | 3 (stored data, backups) | The dataset's clock is running: 2026-11-08's rows leave Tempo on 2027-02-06, and the journal's words for the 42 textless runs sooner. No page needed to start keeping rows |
| C | The route and the "Heard" section | 2 (a page on production) | Needs B's copy to exist. Tested in a browser against a fixture database |

The journal backfill (point 4) is a fourth, small, optional step after B.
WORKFLOW.md step 4: "An item with separable parts is split into steps,
each with its own criteria, build and test."

Change to the criteria: three steps, A first; the rewritten file has a
section per step with its own criteria.

## 11. Criteria for the rewrite, in checkable form (Q1)

What each step can be held to. Numbers are for the coordinator to renumber.

Step A:

1. The repo's unit differs from the installed one by the two
   `Environment=` lines and the comment; `systemd-analyze --user verify`
   is clean.
2. After the coordinator's restart: the three checks of point 8;
   `refs/main` unchanged; one spoken test by Drew.

Step B (all tests at the function boundary; a fake Tempo; `--db`,
`--publish` and `--status` in scratch):

3. First run on an empty record takes every run with an STT stage the
   fixture holds; a second run changes nothing (`seq` unchanged, the
   published copy not rewritten).
4. The ten rules of point 9.
5. Tempo down, slow (10 s) or answering nonsense: the record and the
   copies are untouched, the status file carries the reason and a fresh
   `ts`, exit 0, the watermark has not moved.
6. A label row survives 100 exports. A row marked removed stays removed.
7. The record is 600 in a 700 directory outside the repo; the copies are
   under `data/` only; no transcript text in the journal, the status
   file or a leftover temporary file (searched for a marker string
   planted in the fixture). `git status` clean.
8. The backup and restore test of point 5.
9. On the live system, by the coordinator at promotion: the first export;
   then a count from Tempo over a window that ended 15 minutes earlier
   equals the record's for the same window.

Step C:

10. The route: cursor paging returns every fixture row once across pages,
    with two rows sharing a timestamp; `q` with `%`, `_`, a quote and
    markup; `limit` out of range; the published copy missing, of an
    unknown schema, or replaced mid-request.
11. `/api/work` byte for byte for the same `work.json`. The route's
    module imports no HTTP client.
12. In a browser on dev, against a fixture database the test agent
    publishes to `data/dev` with the exporter's `--publish` (named in the
    brief as allowed; no household text in the fixture): folded on first
    load; the summary with "as of"; markup stays text; the filter and the
    switch; an open row and the scroll survive three refreshes with a
    new row arriving; "not recorded"; stale (status file older than three
    minutes) and missing; 200 rows then older.
13. Docs: `work-page.md`; `voice-capture.md`'s table (this database, the
    Whisper journal); CLAUDE.md's Secrets note; `backups.md`;
    `restore.md`; a feature doc for the dataset
    (`docs/features/voice-dataset.md`) with the schema and how to query
    it, since "useful for future work" starts with finding it.
14. The suite passes; last lines quoted.

## Appendix A: a schema to build to (Q2)

```sql
PRAGMA user_version = 1;

-- One row per voice run with a speech-to-text stage. Written only by the exporter.
CREATE TABLE runs (
  run_id              TEXT PRIMARY KEY,   -- ha.pipeline_run_id
  trace_id            TEXT NOT NULL,      -- the voice trace in Tempo (gone after 90 days)
  start_s             REAL NOT NULL,      -- epoch seconds, UTC, to the millisecond
  end_s               REAL NOT NULL,
  local_time          TEXT NOT NULL,      -- ISO 8601 with offset, the house's clock, as to_row writes it
  local_day           TEXT NOT NULL,      -- YYYY-MM-DD, the house's day
  service             TEXT NOT NULL,      -- resource.service.name as it came
  pipeline            TEXT,               -- ha.pipeline_name as it came
  pipeline_id         TEXT,
  is_dev              INTEGER NOT NULL,   -- decided on the day of export, never recomputed
  satellite_id        TEXT,               -- HA entity id
  device_id           TEXT,               -- kronk.origin.device_id; the recordings' <device>
  conversation_id     TEXT,
  language            TEXT,
  stages              TEXT NOT NULL,
  wake_detection      TEXT,
  heard               TEXT,               -- NULL when the run was traced without text
  heard_chars         INTEGER,
  heard_source        TEXT,               -- 'tempo' | 'whisper_journal'
  reply               TEXT,
  reply_chars         INTEGER,
  response_type       TEXT,
  tier                TEXT,               -- voice.handled_by
  rule                TEXT,
  direct_action       TEXT,
  tools               TEXT,               -- JSON array
  kronk_trace_id      TEXT,               -- not kept beyond Tempo's 90 days (a decision)
  kronk_join_matches  INTEGER,
  blueprint_run_id    TEXT,
  error_code          TEXT,
  corrected_by        TEXT,               -- not_that | rate_down | stop | near_repeat
  corrected_after_s   REAL,
  corrected_by_run_id TEXT,
  correction_kind     TEXT,               -- on the later run
  correction_of_run_id TEXT,
  mark_basis          TEXT,               -- rule_only | rule_and_text
  window_cut_short    TEXT,               -- shutdown | ha_disconnected: "not marked" is then unknown
  stt_engine          TEXT,               -- voice.engine of voice.stt: which Whisper unit
  listen_ms           INTEGER,            -- voice.stt.listen
  transcribe_ms       INTEGER,            -- voice.stt.transcribe
  intent_ms           INTEGER,            -- voice.intent
  stt_config_id       INTEGER REFERENCES stt_config(id),   -- NULL: not recorded
  audio_join          TEXT,               -- NULL not tried | 'certain' | 'ambiguous' | 'none'
  audio_rel           TEXT,               -- <device>/<pipeline>/<run>/<file>; on the NAS a <YYYY-MM> of the mtime follows <pipeline>
  audio_bytes         INTEGER,            -- duration_s = (audio_bytes - 44) / 32000
  audio_mtime_ns      INTEGER,
  raw_trace           TEXT,               -- the whole voice trace as JSON, read once the run is 15 minutes old
  removed             TEXT,               -- set by a person: when and why; text columns are then NULL and stay NULL
  first_seen_at       TEXT NOT NULL,      -- UTC
  last_seen_at        TEXT NOT NULL,      -- the last export Tempo still listed it in
  last_changed_at     TEXT NOT NULL,
  seq                 INTEGER NOT NULL    -- raised on insert and on every change; the page's change cursor
);
CREATE INDEX runs_time ON runs (start_s DESC, run_id DESC);
CREATE UNIQUE INDEX runs_seq ON runs (seq);

-- What a person adds. The exporter never writes here.
CREATE TABLE labels (
  run_id       TEXT PRIMARY KEY REFERENCES runs(run_id),
  correct_text TEXT,                -- what was said (labels.csv: correct_text)
  speaker      TEXT,                -- free: household, guest, tv, test
  corpus_id    TEXT,                -- the row's id in tests/voice_corpus.jsonl, if it became one
  notes        TEXT,
  labelled_at  TEXT NOT NULL,
  labelled_by  TEXT NOT NULL
);

-- The Whisper in force. Filled from the item that gives production a prompt.
CREATE TABLE stt_config (
  id             INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL UNIQUE,   -- of the fields below
  unit           TEXT NOT NULL,
  model          TEXT NOT NULL,
  model_repo     TEXT,
  model_revision TEXT,
  compute_type   TEXT,
  language       TEXT,
  initial_prompt TEXT,
  packages       TEXT,                   -- JSON: wyoming-faster-whisper, faster-whisper, ctranslate2
  first_seen_at  TEXT NOT NULL
);

-- A stored value that a later read contradicted. The old row, whole.
CREATE TABLE run_changes (
  id         INTEGER PRIMARY KEY,
  run_id     TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  why        TEXT NOT NULL,              -- 'export' | 'hourly check' | 'backfill'
  old_row    TEXT NOT NULL               -- JSON
);

-- One row.
CREATE TABLE export_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  watermark_s   REAL,                    -- every window ending before this is taken
  last_ok_at    TEXT,
  last_error    TEXT,
  last_error_at TEXT,
  left_out_dev_text_runs INTEGER         -- plumbing 3: "the file says how many were left out"
);
```

The published copy holds `runs` for its window without `raw_trace`,
`audio_*` and `removed` rows, plus a one-row `meta` (schema, built at).

## Appendix B: what was checked, and how

All read-only. No unit, timer, container or file outside this report was
changed.

- **Read:** the files the brief names; the installed packages under
  `~/services/wyoming-whisper/.venv/lib/python3.12/site-packages`
  (`wyoming_faster_whisper`, `faster_whisper`, `huggingface_hub`).
- **Tempo on loopback** through `scripts/voice_transcripts.py`'s own
  functions, counts and attribute names only: 3,729 `voice.run` in 90
  days; 123 household (pipeline `kronk`), of which 110 with an STT stage,
  68 with text, 42 with an STT stage and no text, 12 typed; tiers kronk
  45, blueprint_then_kronk 30, ha_local 26, blueprint 19, unknown 3; 3
  marked, 1 error; 3,606 dev, 3 with an STT stage, 46 marked; no window
  truncated; no duplicate run or trace id. Household runs span
  2026-11-08 17:08 to 2026-11-25 08:55. Timings in point 2.
- **Units:** `diff` of both repo unit files against
  `~/.config/systemd/user/`: equal. `systemctl --user show` for start
  times; `list-timers`.
- **Journal:** the two Whisper units' lines, counted by pattern.
- **Docker:** `docker inspect` mounts; `docker exec … id` in the three
  production containers; `docker ps` ports (nginx `0.0.0.0:80`, Grafana
  `0.0.0.0:3000`, Tempo `127.0.0.1:3200`).
- **voice_tracer** `/health` through nginx's `/probe/voice_tracer` on
  loopback: `marks_late` 0, `held_lost` 0, `too_old` 0 since its last
  start.
- **Recordings and the NAS:** directory names and counts only.
- **SQLite:** the WAL reader trial, in the session scratchpad, removed
  afterwards.

Three things to say plainly:

- One journal read printed transcript lines into this session's pane (a
  filter that did not match). None is quoted here. The later reads
  counted lines without printing them.
- I ran `python3 scripts/work.py show M30` once, a read, before I had the
  rule in mind that no agent runs that tool against the live file. It
  wrote nothing.
- Not verified: that `unshare -rn` or `strace` can start the Whisper
  command line with the GPU (point 8, check 3); the size of a Kronk trace
  as JSON; whether the labelling script's join can be imported unchanged
  by the exporter (read, not run).
