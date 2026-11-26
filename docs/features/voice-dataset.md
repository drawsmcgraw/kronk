# Feature: the voice-run record, a dataset of household speech (`scripts/voice_runs_export.py`)

**Shipped:** work item M30 step B (2026-11-25 build, fix round 1 the same
day, `docs/sprint/M30-fix1.md`; promoted by the coordinator, who runs the
first export with `--init` and then installs the timer). **Origin:**
Drew, 2026-11-24: "i want to build a dataset of househould voice utterances
because it will be useful for future work. back up the transcript db." and
"the /work endpoint should also include all the stt transcriptions to help
me troubleshoot". Criteria and evidence: `docs/sprint/M30-criteria.md`
(step B), `M30-review.md` (Appendix A, the schema; point 9, the ten rules),
`M30-build2.md`, `M30-e2e2.md`, `M30-fix1.md`.

## What it does

Tempo keeps a voice run for 90 days, then forgets it. This keeps every
household voice run with a speech-to-text stage for good: what Whisper
heard, what answered, how, whether it was marked wrong, the whole voice
trace, and which recording holds its audio. One SQLite file, one writer,
backed up monthly in the config tar and weekly beside the recordings on the
NAS. It also publishes the last 90 days for the "Heard" section of `/work`
(step C, `GET /api/voice_runs`; `work-page.md`, "Heard").

| File | What | Who writes it |
| :-- | :-- | :-- |
| `~/.local/state/kronk-voice/voice_runs.db` | **the record**: every run, for good. Directory 700, file 600, outside every container's mount. Rollback journal | the exporter only (`--db`, `KRONK_VOICE_DB`) |
| `data/voice_runs.db`, `data/dev/voice_runs.db` | the published copies: the last 90 days, the page's columns | the exporter, rebuilt when a published row changed |
| `data/voice_runs.json`, `data/dev/voice_runs.json` | the status file: last good read, last error, what the run did, the summary counts. No text | the exporter, every run |
| `/mnt/nas-backup/voice-runs/voice_runs-<date>.db` | the weekly copy, newest eight dates | `backups.py archive` (Monday 01:00) |
| `kronk-config-<date>.tar.gz` → `voice-state/voice_runs.db` | the monthly copy | `backups.py config` (the 10th) |

Which runs: every `voice.run` whose `voice.stages` holds `stt`, household
and dev alike (dev runs with audio are kept, `is_dev = 1`). Corpus replays
and typed runs have no STT stage and are left out.

## How

`kronk-voice-runs-export.timer` fires the service every 60 s. One run:

1. **Reads Tempo** on loopback, from the watermark minus one hour to now,
   one search a day, with the stage filter in the TraceQL query
   (`{ name = "voice.run" && span.voice.stages =~ ".*stt.*" }`, the one
   optional argument `voice_transcripts.fetch_runs` gained). The first run
   has no watermark and reads all of Tempo; it is the same code. A
   watermark later than now (a run made with a clock that was ahead) is
   not trusted: the read starts an hour before now, the status says
   `ok: false` with the reason for that run, and the watermark is set back.
2. **Reads the whole voice trace**, once, for each run that ended more
   than 15 minutes ago and has none stored (the child spans can lag one
   export batch; nothing older than 15 minutes changes in Tempo). It fills
   the columns the listing does not select and `raw_trace`. Runs never
   tried go first, newest first; a run whose read failed is stamped
   `trace_failed_at` and goes to the back, the longest-waiting first, so a
   trace that never reads holds up nothing. Any shape of answer that is
   not a trace is a failed read, never a crash.
3. **Joins the audio** when the row is final (15 minutes after the run
   ended), with `voice_label_sheet.join` imported, not copied, over every
   row in the record and every recording that could compete with it
   (widened through any chain of shared candidates). The row waits while
   any of those rows is younger than 15 minutes, since a run that could
   fit the same recording may not have reached Tempo yet. So the stored
   answer is the join's whatever order the runs arrived in: `certain`,
   `ambiguous` (stored, never guessed) or `none`, with the recording's
   relative name, size and mtime, and `audio_tried_at`. No age limit: a
   row older than seven days that finds no recording keeps `audio_join`
   NULL (its recording may be on the NAS; joining the NAS layout is left
   for step D), a younger one is `none`. A recordings directory that
   cannot be read leaves the row untried; the next run tries again.
   A row that reaches the record after a neighbour was joined (a read
   the budget cut at a day mark, the hourly check's take, a run Tempo
   showed late), final or not, may have neighbours joined without it:
   every insert clears `audio_tried_at` on every row of its set that is
   not removed, and the set is joined again whole, in the same run if
   every row of it is final, or once its youngest row is
   (`audio_reopened` in the status). The audio columns raise no `seq`
   and are not published, so nothing else moves.
4. **Writes the record** in one `BEGIN IMMEDIATE` transaction, under an
   flock on the record's directory (a run by hand never overlaps the
   timer's). The timer's ordinary run gives way to a run that holds the
   lock (exit 0; the next minute reads). `--remove` and `--init` wait for
   it up to 55 s (the unit kills a run at 50) and exit 1 when they still
   do not get it. Every connection has `secure_delete` on.
5. **Publishes** when a copy differs from the record: the set of
   `(run_id, seq)` in the 90-day window changed, or the copy's row count
   is not its `meta.rows` (a copy damaged behind its meta is mended the
   next run, not the next change). Built in memory, written to a temporary
   file beside the copy, renamed into place. The orchestrator opens it
   `mode=ro&immutable=1`. The copies follow the record, not Tempo: they
   are published while Tempo is down too.
6. **Writes the status files**, every run.

Once an hour it reads Tempo's 90 days and compares: a run Tempo has that
the record should hold and does not is taken (an insert, counted
`missing` and `taken`, its id in the status file); a stored value Tempo
now gives differently goes to `run_changes` with `why = 'hourly check'`
and Tempo's values in `detail`, once per distinct difference, and the row
is not changed. A check stamped later than now runs anyway. `--check-now`
runs it at once.

**The time budget.** The unit stops a run at 50 s. No request of the main
read starts later than 20 s into the run, of the trace reads 30 s, of the
hourly check 38 s; each request has 5 s of wall time, the whole request
(connect, headers, body: a one-shot `SIGALRM` timer, since urllib's own
timeout bounds each socket wait only and a Tempo that sends a byte a
second would outlast it), so the run is done with Tempo by 43 s. The one
wait for the record (a person's open transaction, the 5 s busy timeout)
makes 48. A read the budget cuts carries on where it stopped on the next run:
the main read through the watermark (it never moves past the last whole
day read), the check through `export_state.check_progress`. A day cut in
the middle is dropped whole and read again.

Failures, each in the status file with its reason: a failed trace read or
Kronk-trace enrichment (`last_error`, counted in `this_run`); a read cut by
the budget; a recordings directory that cannot be read; a SQLite error on
the record (locked past the 5 s busy timeout: "database is locked"; a full
disk), which exits 1 with no traceback.

### The rules a row lives by

From the review's point 9; each has a test in `tests/test_m30_voice_runs.py`.

1. A stored value is never replaced by an empty one. A differing non-empty
   value replaces it, and the old row, whole, goes to `run_changes`
   (`why = 'export'`).
2. `raw_trace` and the trace's columns are read only once the run ended
   15 minutes ago.
3. `window_cut_short` is stored: on such a row "not marked" means unknown.
4. A late correction is not recomputed (see "What it does not hold").
5. A blueprint hand-off has no rule and no tools: NULL, not "no rule".
6. `start_s` / `end_s` (epoch, UTC, to the millisecond) are the truth;
   `local_time` is the string as `voice_transcripts.to_row` writes it, with
   its offset (the two 01:30s of the autumn change are told apart), and
   `local_day` the house's day. Both are written once and never recomputed.
7. `is_dev` is decided on the day of the first export, never recomputed;
   `service` and `pipeline` are kept as they came.
8. Replays and typed runs are out; a run with only an STT stage is in.
9. The watermark never moves past a window Tempo's 5,000-trace search
   limit cut short, nor past a failed read.
10. A run that never reached Tempo has no row (its audio is in the
    recordings, its words in the Whisper journal).

Tempo down, slow or answering nonsense (a shape the search answer never
has): the record is not touched, the watermark stays, the status files
carry `ok: false`, the reason and a fresh `ts`, and the exit is 0. A
record that cannot be opened, or that others can read (mode or
directory), exits 1.

**No record, no `--init`: exit 1, nothing made.** The record is made only
by `--init`: the coordinator's first export, and a new one on purpose. A
run that finds no record and a published copy or a status file that says
one existed writes `ok: false`, `record_lost: true`, the last good read it
knew, and "the record … is gone … Restore it (docs/runbooks/restore.md,
step 14)", every run until it is restored. With no such evidence it says
to run `--init`. `--init` on an existing record is an ordinary run. Like
`--remove`, `--init` waits for the lock up to 55 s and exits 1 without it.

## The schema

The record (`PRAGMA user_version = 2`), as in the review's Appendix A, with
these additions: `run_changes.detail` (the hourly check's Tempo values,
which the row does not take), `export_state.last_check_at` / `last_check`,
and, from fix round 1 (schema 2), `runs.audio_tried_at` (when the audio
join was made; NULL: not yet), `runs.trace_failed_at` (the last failed
whole-trace read, which orders the retries) and
`export_state.check_progress` (an hourly check under way: where it
stopped and its counts so far). The three are bookkeeping: setting them
does not raise `seq`, and none is published. A schema-1 record (only the
unfixed build made one, in scratch) is refused with its reason; there is
no migration.

| Table | What |
| :-- | :-- |
| `runs` | one row per run, keyed by `run_id` (HA's `pipeline_run_id`). The listing's columns; `pipeline_id`, `conversation_id`, `language`, `response_type`, `reply_chars`, `kronk_join_matches`, `window_cut_short` from the whole trace; `stt_engine` (which Whisper unit: `stt.faster_whisper` production, `stt.faster_whisper_2` dev), `listen_ms`, `transcribe_ms`, `intent_ms`; `raw_trace` (the voice trace as JSON); the audio columns; `removed`; `first_seen_at`, `last_seen_at` (the last read Tempo still listed it in), `last_changed_at`, `seq` (raised on every insert and change) |
| `labels` | what a person adds: `correct_text`, `speaker`, `corpus_id`, `notes`, `labelled_at`, `labelled_by`. The exporter never writes it. Ships empty |
| `stt_config` | the Whisper in force; empty until the item that gives production a prompt fills it |
| `run_changes` | a stored row a later read contradicted: the old row whole, `why` (`export`, `hourly check`, later `backfill`), `detail` |
| `export_state` | one row: `watermark_s` (every run that started before it is taken), `last_ok_at`, `last_error`, `last_error_at`, `last_check_at`, `last_check`, `check_progress` |

The published copy: `runs` without `raw_trace`, `trace_failed_at`, the
audio columns (with `audio_tried_at`), `removed` and `last_seen_at`, only rows not removed and started in the last
90 days, plus a one-row `meta` (`schema`, `built_at`, `built_s`,
`window_start_s`, `rows`, `max_seq`, `content_sha256`). User version 1.

## Querying it

On the host, read-only (the CLI `sqlite3` is not installed; Python's is):

```
python3 - <<'EOF'
import os, sqlite3
c = sqlite3.connect("file:" + os.path.expanduser("~/.local/state/kronk-voice/voice_runs.db") + "?mode=ro", uri=True)
for q in (
    "SELECT COUNT(*), MIN(local_day), MAX(local_day) FROM runs WHERE is_dev = 0",
    "SELECT tier, COUNT(*) FROM runs WHERE is_dev = 0 GROUP BY tier",
    "SELECT local_time, heard FROM runs WHERE corrected_by IS NOT NULL ORDER BY start_s DESC LIMIT 20",
    "SELECT audio_join, COUNT(*) FROM runs GROUP BY audio_join",
    "SELECT r.heard, l.correct_text FROM runs r JOIN labels l USING (run_id)",
):
    print(q, "\n ", c.execute(q).fetchall())
EOF
```

A recording's path from `audio_rel` (`<device>/<pipeline>/<run>/<file>`):
under `~/kronk-voice-recordings/` while it is younger than a week, then on
the NAS at `recordings/<device>/<pipeline>/<YYYY-MM>/<run>/<file>`, the
month being that of `audio_mtime_ns`. Its length is
`(audio_bytes - 44) / 32000` seconds.

A label, by hand (one transaction; the exporter never touches the table):

```
python3 - <run_id> '<what was said>' household <<'EOF'
import os, sqlite3, sys
c = sqlite3.connect(os.path.expanduser("~/.local/state/kronk-voice/voice_runs.db"), isolation_level=None)
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT OR REPLACE INTO labels (run_id, correct_text, speaker, labelled_at, labelled_by) "
          "VALUES (?, ?, ?, datetime('now'), 'drew')", sys.argv[1:4])
c.execute("COMMIT")
EOF
```

Removing a run (a guest, the TV, something that should not be kept), by
the command, never by hand-typed SQL:

```
python3 scripts/voice_runs_export.py --remove <run_id> --why "<date> <who>: <why>"
```

It waits for the export's lock (up to 55 s) and exits 1, nothing removed,
when it does not get it; exit 0 means the run is marked. `--why` goes
with `--remove` only (default "removed by hand"), and `--check-now` with
`--remove` is refused (exit 2): a removal reads no Tempo.

In one transaction under the export's lock, with `secure_delete` on: it
sets `removed` (`<time> <why>`), empties `heard`, `reply` and `raw_trace`,
and deletes the run's `run_changes` rows (they hold old rows whole, words
included). Freed space is zeroed, so the words leave the file and its free
pages. Then it publishes the copies at once; it reads no Tempo. The
exporter never writes that row again and never publishes it; nothing
deletes the row. `seq` is not raised: the page notices a removal by the
copy's `meta.rows` (`/api/voice_runs` answers `resync`; `work-page.md`,
"Heard"). A label on the run
(`labels.correct_text`) is not touched (the exporter never writes
`labels`); the command says when there is one, and it is deleted by hand
if its words should go too.

What still holds the words after a removal, and for how long:

- **The weekly copies** made before it (`/mnt/nas-backup/voice-runs/`):
  the newest eight dates are kept, so up to eight weeks.
- **The monthly config tars** made before it (`voice-state/voice_runs.db`
  in `kronk-config-<date>.tar.gz`): the newest twelve dates
  (`KRONK_BACKUP_KEEP`), so up to a year.
- **Tempo** (and Grafana), for 90 days from the run, and **the
  recording** under `~/kronk-voice-recordings/` (a week), then on the NAS.
- **The rollback journal's old disk blocks**: SQLite deletes the
  `-journal` after the commit but cannot zero blocks the filesystem has
  freed. Nothing at the file level promises that on an SSD.

To take the words out of a backup sooner, remove that copy or tar by hand;
nothing here does it.

## What it does not hold

- **A run that never reached Tempo** (too old when voice_tracer saw it, a
  hard kill, HA unreachable for nine minutes: `voice-capture.md`, "What
  can still be lost"). Its audio is in the recordings, its words in the
  Whisper journal.
- **Late corrections.** voice_tracer exports a run once, with its mark;
  a correction that arrives after that is written nowhere in Tempo
  (`marks_late` in `/health`, 0 so far). Recomputing marks from the
  record (`marks.find_mark` is pure) is not part of M30.
- **The Kronk-side trace** (what the model was given, the tool arguments
  and results). `kronk_trace_id` points at nothing after 90 days; that is
  a decision, not a gap.
- **The words of the 42 household runs of 2026-11-08 to 11-17** that Tempo
  holds without text: they are in the Whisper journal, and step D
  backfills them (`heard_source = 'whisper_journal'`).
- **The audio of rows older than seven days when they were joined that
  found no local recording** (the first export's older rows whose
  recordings the archive has moved): `audio_join` NULL. Their recordings
  are on the NAS; a join against the NAS layout is step D's to decide.

## Gotchas

- **Household speech under the repo's `data/`.** `voice_transcripts.py`
  refuses `--out` inside the repo; the exporter publishes there on Drew's
  order, and the copies hold what Grafana already shows the LAN for 90
  days. `data/` is gitignored (a test asks git). A `--publish` or
  `--status` inside the repo but outside `data/` is refused, and so is a
  `--db` anywhere inside the repo.
- **`--db`, `--publish` and `--status` come together or not at all.** A
  scratch record with the default status path would write the real
  `data/`.
- **Three production containers can write `data/`** (uid 1000 is drew's).
  The copies are copies: the next change rebuilds one a container damaged.
  The record is outside every mount for that reason.
- **No WAL.** A `mode=ro` reader of a WAL database leaves `-wal` and `-shm`
  beside it; the record is in rollback-journal mode, and the copies are
  opened `immutable=1`.
- **The stale check.** `backups.py check` (daily) warns when the export's
  last good read is over seven days old, and when the record is gone but
  its status file is not. Before the first export it says nothing.
- **Required in the tar since the first export.** Step C added the
  record to `required_sources` (2026-11-26), after the first export, so no
  run before it read INCOMPLETE; a config tar without it is INCOMPLETE now.
- **The run is three to four minutes old when its row lands**: a satellite
  run reaches Tempo that late. Its audio join waits until it is 15
  minutes old.
- **`--init` makes the record; nothing else does.** After a restore, do
  not run it: install the copy (`docs/runbooks/restore.md`, step 14).

## Operations

The `# Operations:` block in `systemd/kronk-voice-runs-export.service`
(first export, install, check, one-shot, compare, remove a run, remove the
unit). **Install**: the first export on a machine with no record needs
`--init`, run by hand before the timer is enabled:

```
python3 scripts/voice_runs_export.py --init
```

Without it every run exits 1 and makes nothing. The journal line per run:
`listed N, new N, changed N (replaced N), traces read N (failed N), copies
written N of 2`. Restore: `docs/runbooks/restore.md`, step 14.

## Blog hooks

- **A 90-day memory, made permanent.** Telemetry became a dataset the day
  someone said "this will be useful later": what had to be captured now
  (the rows, the child spans, `window_cut_short`) versus what could wait
  (the audio join).
- **Nothing is deleted and nothing known is lost**, as ten testable rules,
  for a source that forgets: an empty value never overwrites, a changed one
  is kept, the watermark never passes a truncated search.
- **The `mode=ro` reader that writes files.** A read-only SQLite reader of
  a WAL database leaves `-wal` and `-shm` behind; the fix was a published
  copy opened `immutable=1` and a record nobody else mounts.
- **A filter in the query beats enrichment.** 17.7 s to 0.04 s by asking
  Tempo for the runs with a speech-to-text stage instead of reading 3,000
  corpus replays to throw them away.
