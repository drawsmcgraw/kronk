# M30 step B, fix round 1: report

Fix agent, 2026-11-25, on `a83d852` (the ruling of 2026-11-25 on step B's
test). Commits: `fdcf442` (docs), `ecbe171` (code and tests), this report.

Every job in the ruling is done, each with a test that fails without it
(below, "Mutations"). The eight `xfail(strict=True)` marks in
`tests/test_m30_voice_runs_e2e2.py` are gone, and their tests pass.

Guard rails: none broken. The exporter never ran against the live record
or the real `data/`. Neither exists on this host (checked at the end: no
`~/.local/state/kronk-voice/`, no `data/voice_runs*`). Every run used
`--db`, `--publish` and `--status` under pytest's `tmp_path` or the session
scratch directory, with Tempo faked. No unit installed, started or
stopped; `systemd-analyze --user verify` ran on a scratch copy of the two
unit files only (exit 0, no output). No backup outside scratch, no notice
sent. `scripts/work.py` not run. CLAUDE.md not edited. Every sentence in
the tests is invented.

## The schema: 2

Three fixes needed state the record did not have. All three are
bookkeeping: setting them does not raise `seq`, and none is published.

| Column | For | What it holds |
| :-- | :-- | :-- |
| `runs.audio_tried_at` | F1, note 7 | when the audio join was made; NULL means not yet. Needed because NULL `audio_join` now also means "tried, older than seven days, no recording" |
| `runs.trace_failed_at` | F3 | the last failed whole-trace read; orders the retries |
| `export_state.check_progress` | F8 | an hourly check under way: where it stopped, and its counts so far |

`PRAGMA user_version` goes from 1 to 2. There is no migration: only the
unfixed build made a schema-1 record, and only in scratch. A schema-1
record is refused with that reason and `--init` named
(`test_a_schema_1_record_is_refused_with_its_reason`). The columns are in
`docs/features/voice-dataset.md`, "The schema".

## Each job

### F1 (blocker): the audio join when the row is final

`scripts/voice_runs_export.py`, `audio_joins()` and `_closure()`, called
inside the export's transaction after every insert, so a row competes
with every run that has arrived, those of this run included.

- Targets are rows that are not removed, are final (ended more than
  `FINAL_S` ago) and have `audio_tried_at` NULL.
- For each target, `_closure()` collects every record row and every
  recording that `voice_label_sheet.join` could link to it, through any
  chain of shared candidates. A run's span is `[start - 2 s, end + 5 s]`
  and a recording is its mtime; the span is widened until no row outside
  it overlaps it. That set contains the target's whole group in `join`,
  so `join` over it gives the target the same answer as `join` over
  everything. Removed rows are in the set: they still compete for a
  recording.
- **One thing beyond the ruling's words.** A final row waits while any
  row in its set is younger than `FINAL_S`. "When the row is final" is
  not enough on its own: a neighbour three seconds later can still be
  short of 15 minutes, and a run that fits the same recording may not
  have reached Tempo yet (`audio_waiting` in `this_run`).
- A target with no recording in its own span skips the closure. `join`
  cannot pair it with anything, so the answer is fixed.
- A recordings directory that cannot be read (or is not a directory)
  stamps nothing. Every target stays untried, the reason goes to
  `this_run.audio_not_tried` and `last_error`, and the next run tries again.

Tests:

- `test_f1_…_arrive_together_or_an_export_apart`, in both arrival orders.
- `test_f1_the_stored_join_is_the_joins_answer_in_any_order_of_arrival`:
  eight seeds, six runs and four recordings, random arrival, one or two
  runs per export. The fixture holds both `certain` and `ambiguous`.
- `test_f1_a_final_row_waits_while_a_run_that_could_share_its_recording_is_not`.
- `test_f1_a_removed_row_is_never_joined_but_still_competes`.
- `test_b7_an_unreadable_recordings_directory_leaves_the_row_to_be_tried_again`.

The build's B7 test passes unchanged.

**The premise this rests on.** The arrival-order test first failed with
my own fixture: it let runs reach Tempo after they were already 15
minutes old, and then the join guesses again. The design depends on what
the 15-minute rule already depends on: voice_tracer exports a run within
`EXPORT_AGE_MAX_S` (840 s) of its end. A run exported later than
`FINAL_S` after it ended can still turn an ambiguous join into a stored
`certain`. The fixture now keeps to the premise and asserts it.

### Note 7: no age limit

`JOIN_LOCAL_MAX_AGE_S` (six days) is gone. Every final row is tried
against the local recordings, whatever its age. A row older than
`JOIN_NAS_AGE_S` (seven days, the archive's age) that finds no recording
is stored NULL with `audio_tried_at` set: not tried against the NAS,
never tried again. A younger one is `none`.

Tests:

- `test_b7_an_old_run_is_joined_when_its_recording_is_local_and_not_tried_when_it_is_not`
  (replaces the build's
  `test_b7_an_old_run_and_an_unreadable_recordings_directory_are_not_tried`,
  whose first half asserted the six-day limit).
- `test_note7_an_old_row_the_join_gives_no_recording_is_null_and_a_young_one_none`:
  the path through the join, with a recording from another puck in the span.

The NAS join is left over (step D).

### F2: a trace answer of any shape is a failed read

`Tempo.trace()`: any exception from `trace_fields()` becomes
`TempoError("Tempo's trace answer could not be read: <type>")`. Test:
`test_f2_…` (three shapes), mark removed.

### F3: failed trace reads do not starve the rest

- The three-failure cap (`TRACE_FAILS_MAX`) is gone. The limit is the
  time budget: no trace read starts after `TRACE_BUDGET_S` (30 s into the
  run).
- Order: rows never tried go first, newest first (the new rows of this
  read included). Then rows whose read failed, by `trace_failed_at`
  ascending. A failed row gets `trace_failed_at` in the transaction.
- The status line reads "… trace read(s) failed (…); tried again after
  the others waiting".

Tests:

- `test_f3_three_traces_that_never_read_do_not_starve_the_older_ones`:
  mark removed, passes unchanged.
- `test_f3_a_trace_that_failed_is_tried_after_those_never_tried`.
- `test_rule2_trace_reads_that_keep_failing_wait_for_the_next_run` is
  **changed to the ruling**. It asserted `TRACE_FAILS_MAX`. It now uses a
  fake budget clock (`EX.clock`) where every read costs its whole 5 s
  timeout, and asserts 7 failures, 13 deferred, and the run over within
  `TRACE_BUDGET_S + TEMPO_TIMEOUT_S`. The intent is the same: the unit's
  50 s is never reached.

### F4: a watermark ahead of now, and missing runs taken

- `export()`: a watermark later than `now` is not trusted. The read
  starts at `now - OVERLAP_S`, the new watermark is set from this read
  (not `max` with the bad one), and the status carries `ok: false` with
  "the watermark was N s later than now …" for that run. The next run is
  right again.
- The hourly check (`do_check()`) returns the runs it finds missing.
  `export()` inserts them in the same transaction (`new_row`, `seq`
  raised). The summary gains `taken`. `last_error` says "… missing from
  the record, taken". A differing row is still only reported.
- **One thing beyond the ruling's words, same bug class.** The clock that
  was ahead also stamped `last_check_at` a day ahead, and that stopped the
  hourly check, the very thing F4 relies on, for a day. `check_due()`
  treats a stamp later than now as due.

Tests:

- `test_f4_a_clock_a_day_ahead_for_one_run_loses_no_run`: mark removed,
  passes unchanged.
- `test_f4_a_watermark_ahead_is_reported_and_mended_in_one_run`.
- `test_f4_a_check_stamped_in_the_future_does_not_stop_the_check`.
- `test_the_hourly_check_reports_a_difference_rewrites_nothing_and_takes_a_missing_run`
  is **changed to the ruling**. It was
  `…_reports_a_difference_and_rewrites_nothing` and asserted that
  `R-lost` was not taken. It now asserts that R-lost is taken with the
  next `seq`, that the other rows' `seq` is unchanged, that the
  difference is still not applied, and that the next check counts 0
  missing.

### F5: no record is made but by `--init`

- `main()`: no record and no `--init` exits 1 and makes nothing, not
  even the directory.
  - If a published copy exists, or a status file shows an earlier record
    (`record`, `record_lost` or a `last_ok_at`), the status says "the
    record … is gone … Restore it (docs/runbooks/restore.md, step 14), or
    run once with --init …", with `record_lost: true` and the last good
    read it knew. It keeps saying so every run, so `backups.py`'s
    "record is missing" warning can now fire.
  - Otherwise the status says to run `--init`, and that status file does
    not count as evidence of a record.
- `--init` makes the record. On an existing record it is an ordinary run,
  logged as such.
- `open_record(create=…)` makes the file only when asked.
- The test helper `Scratch.run` passes `--init` on a fresh scratch (no
  record and no status file), as the coordinator's first export does.
  `init=False` leaves it out. `real_record` (B8) passes `--init`.

Tests:

- `test_f5_a_lost_record_is_said_not_silently_started_again`: mark
  removed, passes unchanged.
- `test_f5_a_first_run_without_init_makes_nothing_and_says_to_run_init`.
- `test_f5_a_lost_record_keeps_saying_so_until_restored_or_made_on_purpose`.
- `test_f5_init_on_an_existing_record_is_an_ordinary_run`.

Install text: `docs/features/voice-dataset.md`, "Operations", and the
unit file's `# Operations:` block (`first:` line): the coordinator runs
`python3 scripts/voice_runs_export.py --init` before enabling the timer.

### F6: removal is a command; publishing follows the record

- `voice_runs_export.py --remove RUN_ID [--why "<date> <who>: <why>"]`
  (`remove_run()`). In one `BEGIN IMMEDIATE` transaction under the
  export's flock it:
  - sets `removed` to `<time> <why>` (an earlier mark is kept);
  - empties `heard`, `reply` and `raw_trace`;
  - deletes the run's `run_changes` rows.

  It then publishes the copies and writes the status. It reads no Tempo.
  An unknown run id exits 1. `--db`/`--publish`/`--status` are still all
  or none.
- `secure_delete` is turned on in `open_record()` for every connection
  the exporter opens, not only for the removal, so the old values earlier
  changes freed are zeroed too. This host's SQLite (3.46.1) is built with
  `SECURE_DELETE`, so the pragma is the guard for a build that is not.
- `main()` publishes whenever the record opened and the export raised no
  SQLite error. A failed Tempo read no longer stops it, and the copy is
  written only when it differs.
- The feature doc's hand-typed `UPDATE` is replaced by the command, with
  what still holds the words afterwards and for how long:
  - the weekly copies: up to eight weeks;
  - the monthly tars (`KRONK_BACKUP_KEEP` 12): up to a year;
  - Tempo and Grafana: 90 days;
  - the recording;
  - the rollback journal's freed disk blocks.

  `backups.md` says the same in a sentence.

Tests:

- `test_f6_the_removal_takes_the_words_out_of_the_record` is **changed to
  the ruling**: it removes with the command, and it checks that the doc
  names `voice_runs_export.py --remove`. Its assertion, the words absent
  from the record file's bytes, is unchanged. Without the
  `run_changes` delete it fails.
- `test_f6_remove_reads_no_tempo_publishes_at_once_and_is_never_undone`:
  Tempo down, a label present, the next exports and a `--check-now` that
  do not refill the row, an unknown id.
- `test_f6_the_copies_follow_the_record_while_tempo_is_down`.
- `test_f6_the_record_has_secure_delete_on_even_where_sqlite_defaults_it_off`:
  a connect wrapper turns it off first; `open_record` must turn it on.

### F7: the restore step stops the timer

`docs/runbooks/restore.md`, step 14:

- `systemctl --user stop kronk-voice-runs-export.timer kronk-voice-runs-export.service`
  before the `rm`/`install` block, so a run in progress is stopped too;
- `systemctl --user start kronk-voice-runs-export.timer` after the count;
- a line saying not to run `--init` there.

The stop and start sit in their own fenced blocks, so the B8 test that
runs the restore block in a shell never runs `systemctl`. Test:
`test_f7_the_restore_step_stops_the_exporter_before_the_install_and_starts_it_after`
(stop < install < start within step 14).

### F8: the whole-window reads have a budget and carry on

- `read_window()` reads a day per search, oldest first, under a deadline.
  It serves the main read (the first read included) and the hourly check.
- `Tempo.get()` refuses to start a request once the phase's deadline has
  passed (`BudgetSpent`, a `TempoError`). A day whose search or
  enrichment the budget cut is dropped whole and read again next time.
  Days are `[t, t2)`, so a run on a boundary is counted once.
- The deadlines, in seconds from the start of the export:
  `READ_BUDGET_S` 20, `TRACE_BUDGET_S` 30, `CHECK_BUDGET_S` 38. With 5 s
  a request, the run is done with Tempo by 43 s, inside the unit's 50.
- The main read carries on through the watermark, which never moves past
  the last whole day read.
- The check carries on through `export_state.check_progress`:
  `last_check_at` and `last_check` are written when a pass completes, and
  the counts add up across passes. `--check-now` starts a fresh pass.
- A read cut by the budget is in `last_error` ("the read stopped at …").

Test: `test_f8_a_slow_tempo_never_runs_past_the_unit_and_the_reads_carry_on`.
Every request costs 0.6 s on a fake clock, so the first read (92
searches) and the check (91) cannot fit one run. It asserts:

- every run stays within 43 s, exits 0 and writes a fresh status;
- the first run says it stopped;
- after eight runs all 30 rows and all 30 traces are in, the watermark is
  right, and a completed check listed 30.

**Limit:** a single day whose reads alone exceed the budget (about 33
requests at 0.6 s, i.e. a day with that many Kronk-trace enrichments on a
Tempo that slow) never completes. I judged that out of reach. The
coordinator may disagree.

### F9: a SQLite error reaches the status

`main()` catches `sqlite3.Error` from the export or removal:

- the status gets "the record could not be written: OperationalError:
  database is locked" with a fresh `ts` and `ok: false`;
- the exit is 1, with no traceback;
- the publish is skipped.

If the status body cannot be read either, a minimal status carries the
reason. The busy timeout is now the constant `BUSY_TIMEOUT_S` (10 s) so
the test can shorten it. Test:
`test_f9_a_locked_record_is_in_the_status_with_its_reason_and_exit_1`.

### F10: a failed enrichment is said

`export()` counts listed runs with `_enrich_error` (`this_run.enrich_failures`)
and puts "N Kronk trace read(s) for the rule failed (<cause>); tried again
while the run is in the read window" in `last_error`.

**One line outside the exporter:** `voice_transcripts.fetch_runs` now
stores the exception's message with its type (`HTTPError: …` rather than
only `TempoError`). Under the exporter's getter that message is its own
TempoError text, which names the failure and never a value. Nothing else
reads `_enrich_error`. Test:
`test_f10_a_failed_enrichment_is_in_last_error_and_the_status`.

### From the notes

- **`BEGIN IMMEDIATE`:**
  `test_every_write_to_the_record_is_inside_begin_immediate`. It traces
  every statement on the record's connections over an insert run, a
  changing run with `--check-now`, and a `--remove`. Every `BEGIN` is
  `BEGIN IMMEDIATE`, and every write is inside one.
- **The weekly copy's read-back:**
  `test_the_weekly_copy_is_read_back_and_a_copy_that_differs_fails`. The
  disk "gives back" one flipped byte; the run fails and no copy is left.
- **The backup API under writes:**
  `test_the_weekly_copy_is_by_the_backup_api_while_a_writer_holds_the_record`.
  A writer with `cache_size = 1` spills 300 uncommitted labels into the
  file and rolls back after a second. The copy waits for the lock, holds
  none of the uncommitted text, and has the committed counts. A plain
  read of the file fails this test.
- **The copy against its `meta`:** `copy_sha()` also compares
  `COUNT(*)` with `meta.rows`, every run, and returns None (rebuilt) when
  they differ.
  `test_a_copy_whose_rows_do_not_match_its_meta_is_rebuilt_on_the_next_run`.

## Mutations

Each fix broken by hand, one at a time, and the three M30 step-B test
files run against it (scratch script; the files were restored and
compared byte for byte). 23 of 23 caught:

- **F1:** no wait; no widening; an unreadable directory stamps the row
  tried.
- **Note 7:** NULL turned into `none`.
- **F2:** the catch.
- **F3:** failed rows first; the three-failure cap back.
- **F4:** the ahead check; the take; the future stamp.
- **F5:** always create; `record_lost` forgotten.
- **F6:** `secure_delete`; the `run_changes` delete; publish only after a
  good read.
- **F8:** the read budget; the check budget.
- **F9:** the catch.
- **F10:** the error line.
- **The notes:** the meta row check; plain `BEGIN`; the read-back
  removed; a plain file read in place of the backup API.

Two were missed on the first pass, and each now has the test that
catches it:

- `secure_delete`: this host's SQLite defaults it on, so removing the
  pragma changed nothing.
- Note 7's NULL: the old fixture only reached the no-recording shortcut.

## Found in the ruling, for the coordinator

1. **Removal and labels.** "Empties its text" and "a second table … that
   the exporter never writes" (plumbing 7) meet at `labels.correct_text`,
   which is the run's text too. I left labels alone; the command says
   when the run has one ("labels.correct_text is not touched"). If a
   removal should take the label's words, that is a decision against
   plumbing 7.
2. **"The read starts from now minus the overlap" (F4)** leaves the
   stretch between the last trustworthy watermark and `now - 1 h` to the
   hourly check alone. That holds only because the check now takes
   missing runs and is no longer blocked by the future stamp (above).
3. **The join's premise**, under F1 above: voice_tracer's 840 s export
   limit must stay under `FINAL_S`.
4. **`ok` after a `--remove` run** is true even if Tempo failed a minute
   earlier. The next timer run sets it right. This sits under step C's
   "`ok: true` is not the only signal".

## Not done, and why

- The NAS-layout join (note 7, "left over (step D decides)").
- Step C's items as the ruling lists them: `last_error` shown with its
  time; other signals than `ok`; the page noticing a removal by
  `meta.rows` or `content_sha256`.
- `left_out_dev_text_runs`, status files at 644, and the timer running
  the working tree's script: left as they are, per the ruling.
- B10 (the first export and the count against Tempo): the coordinator's,
  now with `--init`.

## Suite

`./scripts/run_tests.sh`, exit 0, on `ecbe171`. It was `8026 passed, 3
skipped, 62 xfailed` before this round. The eight findings' tests now pass
(nine with F1's two orders), the build's split B7 test adds one, and
`tests/test_m30_voice_runs_fix1.py` adds 29:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
===== 8065 passed, 3 skipped, 54 xfailed, 6 warnings in 161.46s (0:02:41) ======
```
