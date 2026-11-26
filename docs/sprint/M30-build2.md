# M30 step B build: the voice-run record

Suite: every test passes; no failure that is not this step's (last lines
at the end). Guard rails: none broken. The exporter never ran against the
live record or the real `data/`; no unit installed, enabled or started;
no backup run outside scratch; `scripts/work.py` not run. Two read-only
GETs of the live Tempo (one search answer and one trace, printed as
structure with every value replaced by its type; then a count of matching
traces for two stage patterns, numbers only). No household text was
printed or written.

## What was built

| File | What |
|---|---|
| `scripts/voice_runs_export.py` | New, standard library only. The record (`--db`, `KRONK_VOICE_DB`, default `~/.local/state/kronk-voice/voice_runs.db`; directory 700, file 600, rollback journal; refused when either can be read by others or the record path is inside the repo), the schema of the review's Appendix A, one `BEGIN IMMEDIATE` per run under an flock on the record's directory. Reads Tempo from the watermark minus one hour with the stage filter; whole traces once, for runs 15 minutes old (at most 3 failures or 15 s a run); the audio join through `voice_label_sheet.join`, imported; the hourly comparison (`--check-now` forces it). Publishes to every `--publish` (default `data/` and `data/dev/`) when the `(run_id, seq)` set in the 90-day window changed: built in memory, temporary file, rename, mode 600. Writes every `--status` (default `data/` and `data/dev/`) every run, no text. The header says why it writes household speech under `data/` |
| `scripts/voice_transcripts.py` | `search_query(stages=None)` and `fetch_runs(..., stages=None)` (and the private `_search`) gain the filter. Without it the query string is byte for byte what it was; the command line and the `--out` refusal are untouched |
| `systemd/kronk-voice-runs-export.{service,timer}` | Oneshot, `TimeoutStartSec=50`, `UMask=0077`, `Nice=10`; timer every 60 s, `OnBootSec=90`. `# Operations:` block. `systemd-analyze --user verify` on a scratch copy: no output, exit 0. Not installed |
| `scripts/backups.py` | The `sqlite` member `voice-state/voice_runs.db` (from `Settings.voice_db`: `KRONK_VOICE_DB`, else the HOME path), no `secret` flag, not in `required_sources`. `voice_runs_copy`: in the recordings archive, after the move, same lock; written to `.voice_runs-<date>.db.tmp` `O_EXCL`, fsynced, read back (SHA-256, then `integrity_check` and the row counts of runs and labels), renamed to a free name (`.2` on a second run the same day), 0600 in a 0700 `voice-runs/`; newest 8 dates kept (`KRONK_VOICE_COPY_KEEP`). The archive notice gains one line. `voice_export_warning` in `stale_warnings`: last good read over 7 days (`KRONK_VOICE_STALE_DAYS`), never a good read, unreadable record, or record gone while `data/voice_runs.json` exists; nothing before the first export |
| `tests/conftest.py` | `KRONK_VOICE_DB` set to a scratch path that does not exist; the session fails if the live record (and its `-journal`, `-wal`, `-shm`) or the four real copy and status paths changed |
| `tests/test_m16_backups.py` | The house fixture looks for the record at its HOME path and has none (the live state before the first export); `voice_record()` makes one; the two tests that count `NOT READ (1)` make one first |
| `tests/test_m30_voice_runs.py` | New, 58 tests (below) |
| `docs/features/voice-dataset.md` | New: what, the rules, the schema, queries, labels and removal by hand, what it does not hold, gotchas, blog hooks |
| `docs/features/voice-capture.md` | The stores table gains the record, its copies and the Whisper journal; two stale facts corrected (below) |
| `docs/features/backups.md` | The tar's household data lists the record; the tar and the share hold household speech; the weekly copy; the stale warning |
| `docs/runbooks/restore.md` | Step 14: choose the newer copy; remove `-wal`, `-shm`, `-journal` whatever the mode; install 600; `integrity_check` and the two counts. The B8 tests run this block as written |

CLAUDE.md is not edited (its Secrets line is the coordinator's).

## The criteria, and the test that shows each

All in `tests/test_m30_voice_runs.py`, run with a fake Tempo
(`FakeTempo`: honours the TraceQL stage filter, `start`, `end`, `limit`,
serves `/api/v2/traces/<id>` with the captured shape) and `--db`,
`--publish` (twice) and `--status` (twice) under `tmp_path`. A marker
string is planted in every invented transcript and reply.

- **B1.** `test_b1_first_run_takes_every_stt_run_and_a_second_run_changes_nothing`: of six runs (two household, one STT-only, one dev with audio, one corpus replay, one typed) the four with an STT stage; the first read starts at `now - 92 d`; every search carries the filter. The second run: every `seq` and `last_changed_at` the same, both copies' inode, mtime and size the same, the status `ts` fresh. Also `test_the_record_table_matches_the_columns_and_appendix_a` (the columns, user_version, `journal_mode = delete`, labels empty; the listing's columns equal `voice_transcripts.to_row` for the same run, the rest equal the fixture trace).
- **B2, the ten rules.** 1 `test_rule1_…` (an empty value never replaces; a filled one raises `seq` with nothing kept; a changed one is replaced and the old row whole is in `run_changes`). 2 `test_rule2_the_whole_trace_is_read_only_once_the_run_is_15_minutes_old`, `test_rule2_a_failed_trace_read_is_tried_again_…`, `test_rule2_trace_reads_that_keep_failing_wait_for_the_next_run`. 3 `test_rule3_window_cut_short_is_stored`. 4 `test_rule4_a_late_correction_is_not_recomputed`. 5 `test_rule5_a_blueprint_hand_off_keeps_no_rule_and_no_tools`. 6 `test_rule6_…` (America/New_York: the two 01:30s of 2026-11-01 as `-04:00` and `-05:00`, epoch to the ms, 23:30 EST on the house's day; the zone changed to UTC and the row written again: `local_time` and `local_day` unchanged). 7 `test_rule7_is_dev_is_decided_on_the_day_and_never_recomputed`. 8 `test_rule8_…`, twice: Tempo honouring the filter and Tempo ignoring it. 9 `test_rule9_a_truncated_window_holds_the_watermark` (`SEARCH_LIMIT` 2, three runs in a minute). 10 `test_rule10_a_run_that_never_reached_tempo_has_no_row_even_with_a_recording`.
- **B3.** `test_b3_tempo_failing_touches_nothing_and_says_why`, 11 cases: down (connection refused), slow (the fake sleeps the timeout it is given, 0.2 s here, and times out; 10 s otherwise), not JSON, and eight nonsense shapes. Each: the record and both copies byte for byte the same, the watermark the same, exit 0, both status files `ok: false` with `ts` the run's time, `Tempo: <reason>`, the summary counts still there, no marker. `test_b3_the_real_getter_gives_urlopen_a_timeout_under_ten_seconds` (5 s). `test_b3_a_record_that_cannot_be_opened_exits_1_and_says_so`.
- **B4.** `test_b4_after_a_long_stop_the_next_run_takes_every_run_in_the_gap`: five hours stopped, three runs at 1, 2.5 and 4.5 h, all taken.
- **B5.** `test_b5_a_label_survives_100_exports_and_a_removed_row_stays_removed`: 100 exports with rows changing between them; the label row identical; the removed row keeps `removed` and NULL text, is in neither copy, and is out of the summary.
- **B6.** `test_b6_the_record_is_600_in_a_700_directory_…` (and the defaults: the record under `~/.local/state/kronk-voice/`, the copies and status files under the repo's `data/`); `test_b6_an_existing_record_others_can_read_is_refused` (file 644, directory 755: exit 1); `test_b6_paths_in_the_repo_outside_data_and_partial_arguments_are_refused` (4 cases, exit 2, nothing written); `test_b6_no_transcript_text_in_the_log_the_status_or_a_leftover_temporary_file` (the marker only in the record and the two copies; an old temporary file of ours removed, a young one kept; no `.tmp` or `-journal` left); `test_b6_a_publish_that_fails_midway_leaves_no_temporary_file`; `test_b6_the_copies_and_status_files_are_gitignored` (`git check-ignore --no-index`). `git status` clean after the work: see the end.
- **B7.** `test_b7_the_audio_join_gives_voice_label_sheets_answer`: certain (relative name, size, mtime), ambiguous for both of two runs one recording fits, none; equal to what `voice_label_sheet.join` itself says on the same runs and recordings, read from its own results and notes. `test_b7_an_old_run_and_an_unreadable_recordings_directory_are_not_tried`.
- **B8.** `test_b8_from_the_config_tar` (the m16 house and `FakeOps`, dest and state in scratch, notices captured, none sent: the tar holds `voice-state/voice_runs.db` 0600, the MANIFEST names it as a `sqlite backup`; restored by the runbook's block with stale `-wal`, `-shm`, `-journal` planted: `ok 5 1`, every run id and the label present). `test_b8_from_the_weekly_copy_with_the_recordings_archive` (the same from `voice-runs/voice_runs-2026-12-10.db`). `test_b8_weekly_copies_keep_the_newest_eight_dates_and_never_overwrite`. `test_b8_the_weekly_copy_with_no_record_yet_is_noted_not_a_failure`. `test_b8_a_record_that_will_not_read_fails_the_archive_run`. `test_b8_the_weekly_copy_is_made_when_only_the_recordings_directory_is_missing`. The stale warning: `test_b8_the_stale_check_warns_when_the_last_good_read_is_over_seven_days_old` (6 days quiet, 8 days and never warn), `test_b8_the_stale_check_before_the_first_export_is_quiet_and_a_lost_record_is_not`. `test_b8_the_record_is_not_yet_required`.
  By hand, the real command line, all in scratch: a record of four invented runs made by the exporter; then `KRONK_VOICE_DB=<scratch> KRONK_BACKUP_STATE=<scratch> KRONK_ARCHIVE_DELETE=direct python3 scripts/backups.py archive --dest <scratch> --no-notify --src <scratch>`: exit 0, `Voice-run record: voice_runs-2026-11-25.db (4 runs, 0 labels, 65.5 KB) written and read back.`, the copy 600 in a 700 directory; once before the record existed: `… does not exist (the exporter has not run); no copy.`, exit 0. `backups.py check --dest <scratch> --no-notify`: no voice-run warning (the read was 6.6 days old); its one warning is the scratch share having no config tar. No config tar was made by hand: a real `config` run reads the real secrets, and its tests are above.
- **B9.** `test_b9_an_incremental_run_takes_under_a_second_twenty_times`: a record of 2,500 runs over 89 days, the trace backlog drained, then 20 runs each taking one new run, the eleventh with `--check-now` (90 days read whole and compared): the slowest under 1 s. The whole file runs in about 2 s.
- **B10** is the coordinator's on the live system.
- **B11.** The suite passes; the last lines below.

Also tested: the hourly check (`test_the_hourly_check_reports_a_difference_and_rewrites_nothing`: a changed mark and a missing run reported, the row and every `seq` unchanged, the same difference recorded once); the published copy (`test_the_published_copy_…`: exactly the published columns, `meta`, both copies identical, opened `immutable=1`, the window drops a row after 95 days while the record keeps it); the lock; the listing's query; the units' text.

## Choices made, and what I found wrong or loose in the criteria

1. **The stage filter is `.*stt.*`, not `stt.*`.** `voice_tracer/convert.py:161` writes stages as `wake_word,stt,intent,tts` when HA runs the wake word. If TraceQL's `=~` is anchored, `stt.*` misses those runs. Both patterns count 113 on the live Tempo today (counts only), so nothing is lost now; `.*stt.*` is right either way. The exporter also drops, client side, any run whose stages lack `stt`, so a Tempo that ignored the filter would not let a replay in (rule 8's second case).
2. **Two additions to Appendix A's schema.** `run_changes.detail` (the hourly check reports a value it does not apply: the stored row is `old_row`, and Tempo's value would otherwise be written nowhere) and `export_state.last_check_at` / `last_check`. Recorded in the feature doc.
3. **"The record untouched" on a Tempo failure, and `export_state.last_error`.** B3 says the record is not touched, so a failed read writes nothing to it; the reason goes to the status files only. `last_error` in the record holds the problems of runs that did write (a truncated window, failed trace reads, a missing run in the check). The status file's `last_error` is the current failure, else the record's.
4. **`--status` is repeatable**, like `--publish`: there are two status files.
5. **`--db`, `--publish` and `--status` are all or none.** Not in the criteria. A scratch `--db` with the default status path would write the real `data/`; refused with exit 2.
6. **`left_out_dev_text_runs` is obsolete.** It came from an earlier plumbing ("the file says how many were left out"). Dev runs with audio are kept now and replays are filtered in the query, so there is nothing to count. The column is kept (Appendix A) and stays NULL.
7. **The audio join is tried only for rows that ended less than six days before their first export.** The archive moves recordings older than seven days to the NAS, in a layout with the month added, which `voice_label_sheet.scan` does not read. A row the first export takes that is older than that (on the live system, the rows before about 2026-11-20) keeps `audio_join` NULL, "not tried", rather than a wrong `none`. A join against the NAS layout is a later pass (step D could take it, or a by-hand run).
8. **The overlap is one hour, so a change to a run older than that is only reported.** By the review's point 2 nothing older than 15 minutes changes in Tempo; the one exception it names (voice_tracer re-emitting after a hard kill, with a recomputed mark) would reach `run_changes` as an hourly-check difference, not the row. That is the criteria's "reports, does not rewrite"; said so here so the first such report is not a surprise.
9. **For step C.** A person's removal does not raise `seq` (the exporter never writes that row again), so a page following `changed_after=<seq>` will not see a row disappear; the copy's `meta.rows` and `content_sha256` change when it does. The route or the page needs to notice that.
10. **`voice-capture.md` had two stale facts**, corrected in passing: recordings move to the NAS after 7 days (it said 14; `backups.py` `age_days = 7`, the review's point 4 found it), and the stores table said of the recordings "nothing deletes them" (they leave the host weekly since M16).
11. **The Tempo search shape.** A real answer carries both `spanSet` and `spanSets` (and `matched`, `serviceStats`); the fake carries the same. `check_search` refuses any answer without a `traces` list, a trace without a string `traceID`, a span set that is not an object with spans, a span with no numeric start, or attributes that are not key/value objects. An answer that passes and still makes `rows_from_search` raise is a failure too.

## What I could not do, or did not

- B10: the first export and the count against Tempo. The coordinator's.
- Installing the units, `required_sources`, CLAUDE.md's Secrets line: the coordinator's, by the criteria.
- `systemd-analyze --user verify` is not in the suite (it talks to the user manager); run by hand on a scratch copy of both files: no output, exit 0.
- No config tar made by hand with the real command line (it would read the real secrets into scratch); the tar and its restore are tested with the m16 house.

## Suite

`./scripts/run_tests.sh -q` at the start: `7968 passed, 3 skipped, 54 xfailed`. After this step, on the committed tree:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
8026 passed, 3 skipped, 54 xfailed, 6 warnings in 158.61s (0:02:38)
```

`git status --short` after the last commit: empty.
