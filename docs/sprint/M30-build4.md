# M30 step D build: the words of the early runs

Suite: every test passes, with the export timer running (last lines at
the end). Guard rails: none broken. The live record was not opened and the
backfill was not run against it, not even as a dry run. The real `data/`
was not written; its status file (`data/voice_runs.json`, counts and times
only) was read once. No unit, timer or container started, stopped or
rebuilt; `scripts/work.py` and `scripts/pipeline_bench.sh` not run; the
NAS path not touched. The Whisper journal was read only through scripts
that print field names, timestamps, logger names and lengths, with every
transcript replaced by its length. One read-only `docker exec homeassistant
… grep` read HA's source to learn whether HA strips Whisper's text. No
household word is in a file, a fixture, a log line or this report.

## What was built

| File | What |
|---|---|
| `scripts/voice_runs_backfill.py` | New. Standard library only. Dry run by default, `--apply` to write, `--db`, `--journal FILE`; also `--recordings`, `--nas-recordings` (default `/mnt/nas-backup/recordings`) and `--no-audio`. Imports `voice_runs_export` for the lock, the record's checks and opening, `trace_spans`, `DDL`, `_closure` and `audio_joins`; copies none of them. Output: run ids, counts, lengths, reasons |
| `scripts/voice_label_sheet.py` | `scan` reads the archive's layout too: a five-part path whose third part is `YYYY-MM` is `<device>/<pipeline>/<month>/<run>/<file>`. HA's run directories are monotonic nanoseconds and never match, so the exporter's local scan is unchanged |
| `tests/test_m30_backfill.py` | New, 15 tests (below) |
| `docs/features/voice-dataset.md` | "The backfill" section; `heard_source` values; `run_changes` gets no `backfill` rows; "What it does not hold" brought up to date; fix round 2's accepted limit and its cure; a blog hook |

`voice_runs_export.py`, `tests/conftest.py` and CLAUDE.md are not touched.

## What the journal and HA showed

- Each transcript is one line from the unit's own process,
  `INFO:wyoming_faster_whisper.dispatch_handler:<text>`
  (`logging.BASIC_FORMAT`). The text is Whisper's segments joined with a
  space, so it starts with a space. 1,873 such lines from production and
  3,898 from dev since the journal begins; all 1,873 production ones start
  with a space, none ends with one, none holds a newline, none is blank.
  No `MESSAGE` is a byte array.
- The journal of the two units begins 2026-11-08 16:12:07 local. A run
  earlier that day has no words anywhere and is left ("the journal does not
  cover the run").
- `_SOURCE_REALTIME_TIMESTAMP` is absent (a stdout stream), so a line's
  time is `__REALTIME_TIMESTAMP`. The line before each transcript
  (`INFO:faster_whisper:Processing audio …`) is 0.30 s earlier at p50,
  3.07 s at most: the line is logged when the transcription ends.
- HA 2026.5.4's Wyoming STT passes `transcript.text` through unstripped
  (`stt.py` line 125; `pipeline.py` lines 982 to 998). voice_tracer
  counts `voice.transcript_chars` on that text and strips only the words it
  stores. So the row's count is the journal text's length, leading space
  included, and the backfill stores the text stripped, as Tempo's are.
- Production Whisper's last transcript line is 2026-11-25 09:44:13, the
  end of step A's synthetic clips. The status file's newest household run is
  2026-11-25 08:55:45; nobody has spoken to a puck since, so the silence is
  not a logging fault.

## The rule, as built

A row is filled only when all of these hold; otherwise it is left and
listed with one of the reasons in brackets:

1. not removed [removed]; `heard` empty (a row with words is checked by the
   same rule and reported, never written: the calibration below);
2. `heard_chars` is not 0 [Whisper heard nothing];
3. the journal of its unit covers the window [the journal does not cover
   the run]. The unit is `wyoming-whisper.service` for `is_dev = 0`,
   `wyoming-whisper-dev.service` for `is_dev = 1`;
4. exactly one transcript from that unit in the window [no transcript in
   the window, with the other unit's count when it has one; several
   transcripts in the window]. The window is the `voice.stt` span in the
   row's `raw_trace`, 2 s before to 5 s after (`ls.JOIN_BEFORE_S`,
   `ls.JOIN_AFTER_S`); with no span, the whole run, the audio join's own
   window. The row's columns hold the stage durations only, not when STT
   started, so the span is where the stage times are;
5. its length equals `heard_chars` [length differs: both lengths]. With no
   stored count: no other transcript from the unit within 60 s either side
   of the window [no stored count, not alone];
6. the text is not all space [blank transcript].

A filled row gets `heard` (stripped), `heard_source = 'whisper_journal'`,
`seq` raised from the record's maximum, `last_changed_at`. `heard_chars`
stays Tempo's. Nothing is replaced, so no `run_changes` row. Each `UPDATE`
is guarded by `heard` still empty and `removed IS NULL`.

**Calibration.** For every row that already has Tempo's words, the report
says whether the journal agrees: one transcript in the window, the same
length as `heard_chars`, the same words once stripped. On the live record
that is the rule run against the 71 rows Tempo gave words, and its count is
how far to trust the fills. Counts only; no words compared aloud.

**Writing.** `--apply` checks the directory's mode, takes the export's
lock with `lock_dir(db, LOCK_WAIT_S)` (55 s, exit 1 "nothing written"
without it, as `--remove`), opens with `open_record` (no `create`), and
does the whole pass, the NAS scan included, in one `BEGIN IMMEDIATE`. When
nothing is to be written it rolls back, so a second run leaves the file's
bytes as they were. The dry run checks mode and owner the same way and
opens the record `mode=ro` with no lock (a rollback-journal reader leaves
no file), so it never makes the timer give way.

**Publishing.** Not done by the backfill. Plumbing 3 allowed either; the
exporter's next run (a minute) publishes any raised `seq`, and a copy
written outside the export unit would also be a difference the suite's
guard cannot account for. The feature doc says to run it with no suite
running.

## Plumbing 5: built

`voice_label_sheet.scan` needed the one change above to read the archive;
`join` needed none, it takes recordings and runs from any root. The answer
for a row is the exporter's own, by its own function: the backfill makes an
in-memory copy of the record's runs (`ve.DDL`, every column), marks every
row tried except the targets (rows with `audio_join` NULL, `audio_tried_at`
set, not removed: "not tried"), and calls `ve.audio_joins(mem, nas_root,
now)`. That gives each target the answer `voice_label_sheet.join` gives
over every competing run and recording (`_closure`), the 15-minute wait and
the seven-day rule included. `audio_joins` only reads; the in-memory copy is
there so no row of the record is marked untried, not even inside the
transaction.

Two rules of the backfill's own:

- A target with a recording still under `~/kronk-voice-recordings/` in its
  window (by `ve._closure` over the local scan) is left: a set the archive's
  Monday cut split would be joined against half its recordings, and could
  read `certain` where the whole set is `ambiguous`.
- Only `certain` and `ambiguous` are written. "No recording on the NAS
  either" leaves the row as it is, so a second run changes nothing.

`audio_rel` drops the month (`<device>/<pipeline>/<run>/<file>`, HA's
layout, as every other row has; the feature doc gives the NAS path from the
month of `audio_mtime_ns`). The audio columns raise no `seq` and are not
published, as in the exporter. NAS unreadable or not a directory: nothing
joined, the reason in the report, the words still written.

This is also the cure for fix round 2's point 2 (a re-open turning a stored
`certain` into "not tried" after a week's outage or a restore): run the
backfill again. The feature doc says so.

I did not look at the NAS. The coordinator's dry run is its first read;
`scan` walks the whole archive (`rglob` and a WAV header read per file)
over SMB, inside the transaction when applied. The timer's runs give way
(exit 0) while it holds the lock.

## The criteria, and the test that shows each

| | Test (`tests/test_m30_backfill.py`) |
|---|---|
| D1 | `test_d1_a_certain_match_is_filled_and_each_other_row_is_left_with_its_reason`: three fills (household, dev against its own unit, no stored count and alone) and every column of each other row unchanged: two in the window, a length that differs, removed (by `--remove`), already with text (and "the journal agrees"), a dev row whose only transcript is the household unit's, no stored count with another 30 s later, a count of 0, nothing in the window; each with its reason line. `test_d1_the_window_is_the_stt_span_and_the_slack_of_the_audio_join` (10 ms inside and outside the 5 s; no span, whole run), `test_d1_a_journal_that_does_not_cover_the_window_leaves_the_row`, `test_systemds_own_lines_and_other_units_are_not_transcripts`, `test_without_journal_the_two_units_are_read_by_journalctl_read_only` |
| D2 | `test_d2_a_dry_run_does_not_change_the_records_bytes` (SHA-256, mtime, inode, the directory listing), `test_d2_a_dry_run_works_while_an_export_holds_the_lock_and_apply_waits_for_it` |
| D3 | `test_d3_a_second_apply_changes_nothing` (every row equal, the file's SHA-256 equal, audio pass included) |
| D4 | `test_d4_no_transcript_text_in_the_output` (the marker, each invented sentence, dry run and `--apply`; the fixture journal holds invented sentences only) |
| D5 | `test_d5_an_export_and_the_hourly_check_after_the_backfill_keep_the_words`: an export with `--check-now` after the backfill keeps each filled row's words, source and `seq`; the check reports missing 0, differing 0, no `hourly check` row; the copies carry the words; a further export leaves the row alone |
| D6 | the suite, below |
| D7 | the coordinator's, below |
| Plumbing 5 | `test_voice_label_sheet_scan_reads_the_archives_month_layout`, `test_plumbing5_the_nas_join_fills_the_rows_stored_not_tried` (certain with the month dropped, an ambiguous pair, none on the NAS, one with a local recording near it; the same answer as `voice_label_sheet.join` over the archive; repeatable), `test_plumbing5_an_unreadable_nas_joins_nothing_and_says_so`, `test_plumbing5_reads_the_nas_only` |
| Refusals | `test_the_backfill_refuses_a_record_inside_the_repo_and_a_missing_record` (a `--db` in the repo exit 2; no record exit 1; an unreadable `--journal` exit 1) |

## D7: the coordinator's two commands

With no test suite running, from the repo:

```
python3 scripts/voice_runs_backfill.py
python3 scripts/voice_runs_backfill.py --apply
```

The first is the report: a `fill`, `left` or `text` line per run, the
`words:` and `audio:` totals, the record untouched. The second writes and
prints the same, "filled N", "joined N". Then the count, read-only:

```
python3 - <<'EOF'
import os, sqlite3
c = sqlite3.connect("file:" + os.path.expanduser("~/.local/state/kronk-voice/voice_runs.db") + "?mode=ro", uri=True)
print(c.execute("SELECT COUNT(*) FROM runs WHERE heard_source = 'whisper_journal'").fetchone()[0])
EOF
```

and the export after it: the timer's next run says `copies written 2 of 2`
(`journalctl --user -u kronk-voice-runs-export.service -n 3`).

What to expect, from the journal's shape (not from the record): the 42
runs, less those before 2026-11-08 16:12, less any the rule refuses. Read
the `text` lines first. If "the journal agrees" is far below the rows with
words, the rule is wrong for the live data, and the apply waits.

To give both commands the same journal, save it once into scratch and pass
`--journal FILE` to each; that file holds household speech and is removed
by hand after. Without it the two runs read the journal as it is at each
moment; the early lines do not change between them.

## Suite

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
8347 passed, 3 skipped, 58 xfailed, 6 warnings in 205.55s (0:03:25)
```

(8332 before, plus the 15 new tests; the export timer active throughout.)
