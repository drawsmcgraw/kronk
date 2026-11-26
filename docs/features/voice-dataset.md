# Feature: the voice-run record, a dataset of household speech (`scripts/voice_runs_export.py`)

**Shipped:** work item M30 step B (2026-11-25 build; promoted by the
coordinator, who installs the timer and runs the first export). **Origin:**
Drew, 2026-11-24: "i want to build a dataset of househould voice utterances
because it will be useful for future work. back up the transcript db." and
"the /work endpoint should also include all the stt transcriptions to help
me troubleshoot". Criteria and evidence: `docs/sprint/M30-criteria.md`
(step B), `M30-review.md` (Appendix A, the schema; point 9, the ten rules),
`M30-build2.md`.

## What it does

Tempo keeps a voice run for 90 days, then forgets it. This keeps every
household voice run with a speech-to-text stage for good: what Whisper
heard, what answered, how, whether it was marked wrong, the whole voice
trace, and which recording holds its audio. One SQLite file, one writer,
backed up monthly in the config tar and weekly beside the recordings on the
NAS. It also publishes the last 90 days for the "Heard" section of `/work`
(step C).

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
   with the stage filter in the TraceQL query
   (`{ name = "voice.run" && span.voice.stages =~ ".*stt.*" }`, the one
   optional argument `voice_transcripts.fetch_runs` gained). The first run
   has no watermark and reads all of Tempo; it is the same code. Each
   request gets 5 s.
2. **Reads the whole voice trace**, once, for each run that ended more
   than 15 minutes ago and has none stored (the child spans can lag one
   export batch; nothing older than 15 minutes changes in Tempo). It fills
   the columns the listing does not select and `raw_trace`. At most three
   failed reads or 15 s a run; the rest wait for the next run.
3. **Joins the audio** for each new run that ended less than six days ago
   (older recordings may already be on the NAS), with
   `voice_label_sheet.join` imported, not copied: `certain`, `ambiguous`
   (stored, never guessed) or `none`, with the recording's relative name,
   size and mtime. Older rows keep `audio_join` NULL: not tried.
4. **Writes the record** in one `BEGIN IMMEDIATE` transaction, under an
   flock on the record's directory (a run by hand never overlaps the
   timer's).
5. **Publishes** when the set of `(run_id, seq)` in the 90-day window
   changed: built in memory, written to a temporary file beside the copy,
   renamed into place. The orchestrator opens it `mode=ro&immutable=1`.
6. **Writes the status files**, every run.

Once an hour it reads Tempo's 90 days whole and compares: a run Tempo has
that the record should hold and does not is counted as missing (its id in
the status file); a stored value Tempo now gives differently goes to
`run_changes` with `why = 'hourly check'` and Tempo's values in `detail`,
once per distinct difference. The row is not changed. `--check-now` runs
it at once.

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
has): the record and the copies are not touched, the watermark stays, the
status files carry `ok: false`, the reason and a fresh `ts`, and the exit
is 0. A record that cannot be opened, or that others can read (mode or
directory), exits 1.

## The schema

The record (`PRAGMA user_version = 1`), as in the review's Appendix A, with
two additions: `run_changes.detail` (the hourly check's Tempo values, which
the row does not take) and `export_state.last_check_at` / `last_check`.

| Table | What |
| :-- | :-- |
| `runs` | one row per run, keyed by `run_id` (HA's `pipeline_run_id`). The listing's columns; `pipeline_id`, `conversation_id`, `language`, `response_type`, `reply_chars`, `kronk_join_matches`, `window_cut_short` from the whole trace; `stt_engine` (which Whisper unit: `stt.faster_whisper` production, `stt.faster_whisper_2` dev), `listen_ms`, `transcribe_ms`, `intent_ms`; `raw_trace` (the voice trace as JSON); the audio columns; `removed`; `first_seen_at`, `last_seen_at` (the last read Tempo still listed it in), `last_changed_at`, `seq` (raised on every insert and change) |
| `labels` | what a person adds: `correct_text`, `speaker`, `corpus_id`, `notes`, `labelled_at`, `labelled_by`. The exporter never writes it. Ships empty |
| `stt_config` | the Whisper in force; empty until the item that gives production a prompt fills it |
| `run_changes` | a stored row a later read contradicted: the old row whole, `why` (`export`, `hourly check`, later `backfill`), `detail` |
| `export_state` | one row: `watermark_s` (every run that started before it is taken), `last_ok_at`, `last_error`, `last_error_at`, `last_check_at`, `last_check` |

The published copy: `runs` without `raw_trace`, the audio columns,
`removed` and `last_seen_at`, only rows not removed and started in the last
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

Removing a run (a guest, the TV, something that should not be kept):
set `removed` and empty the text in the same statement. The exporter never
writes that row again and never publishes it; nothing deletes it.

```
UPDATE runs SET removed = '<date> <who>: <why>', heard = NULL, reply = NULL, raw_trace = NULL WHERE run_id = '<run_id>';
```

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
- **The audio of rows first exported more than six days after the run**
  (the first export's older rows): `audio_join` NULL, not tried.

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
- **Required in the tar from the promotion on.** The coordinator adds the
  record to `required_sources` after the first export, so no earlier run
  reads INCOMPLETE.
- **The run is three to four minutes old when its row lands**: a satellite
  run reaches Tempo that late.

## Operations

The `# Operations:` block in `systemd/kronk-voice-runs-export.service`
(install, check, one-shot, compare, remove). The journal line per run:
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
