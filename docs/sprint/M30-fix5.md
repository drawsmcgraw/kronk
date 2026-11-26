# M30 step D, fix round 1

Fix agent (Opus 5.5), 2026-11-26, on `53d4e9f`. Work in `b5e7bb2`. The five
jobs of the coordinator's ruling on step D's test (`M30-criteria.md`,
"Rulings", last entry), which replace step D's plumbing 2 where they differ.
Findings: `M30-e2e8.md` B1 and should-fix 1 to 3.

Nothing was run against the live record, not even a dry run; nothing opened
`~/.local/state/kronk-voice/` or `/mnt/nas-backup`. No unit, timer or
container touched; `scripts/work.py` not run. Scratch records and fixture
journals of invented sentences only.

## The jobs

### 1. Each journal line is one run's

`one_run_per_line()` in `scripts/voice_runs_backfill.py`, run by `plan()`
after `decide()` has judged every row. It collects, for each unit, every
row whose trace has a `voice.stt` span and the transcripts inside its
window. A row `decide()` would fill whose line is inside the window of any
other row of the same unit is left: "the transcript is in another run's
window too: also inside the window of R-… (unit)".

Plumbing decided here, for the coordinator to see:

- **The test is the window, not "the one match".** The ruling says a line
  that is the one match of more than one row fills none. Here a line inside
  the window of more than one row fills none. That contains every case the
  ruling names, and also refuses a fill whose line sits in a neighbour's
  window when the neighbour's window holds two lines (a neighbour with no
  single match). It is the reason text the ruling wrote, taken literally.
  With a one-second window it costs nothing on the live data (calibration:
  the nearest other line to any of the 40 is 16.1 s away).
- **Who holds a line.** Every row of the unit with a span: empty rows, rows
  with Tempo's words, rows with no count, rows with a count of 0. The
  ruling names the first two; the other two are real runs whose line it may
  be, and holding costs them nothing (they are never filled).
- **A removed row holds nothing.** `--remove` empties `raw_trace`, so a
  removed row has no span and no window. Giving it one would need a
  whole-run window, which the ruling removes. Stated in the doc and the
  script; tested as it is.

Tests (`tests/test_m30_backfill_fix5.py`):
`test_a_line_inside_two_runs_windows_fills_neither[empty|words|no count|nothing heard]`
(two pucks half a second apart, one line in both windows: neither filled,
B's reason as above; with A holding Tempo's words, A reports "the journal
agrees" and B is left); `test_a_removed_row_has_no_window_and_holds_no_line`;
`test_runs_three_seconds_apart_each_take_their_own_line` (the rule does
not reach past the window); `test_the_other_unit_does_not_hold_a_line`.

Mutation: `plan()` without `one_run_per_line` fails the four parametrized
cases and the e2e8 test `test_two_empty_rows_that_match_one_line_are_neither_certain`.

### 2. No fill without a stored count or a span

`decide()`: a row with `heard_chars` NULL is left, "no stored count:
voice.transcript_chars was not recorded". A row whose `raw_trace` has no
`voice.stt` span (or is not a trace) is left, "no speech-to-text span: no
voice.stt span in the trace". `stt_window()` returns None for it; the
whole-run fallback and `ALONE_S` are gone. A row with Tempo's words and no
span is reported "not compared".

Tests: `test_a_row_with_no_stored_count_is_never_filled_even_alone`,
`test_a_row_with_no_stt_span_is_never_filled`; D1's house (R-nocount,
R-nospan).

Mutations: the count check off fails the first and D1; the whole-run
window back fails 15, among them the second, D1, D1's window test and the
eight `stt_window` cases.

### 3. The window: the span's end, one second either side

`STT_END_S = 1.0`, one named constant. `stt_window()` reads
`endTimeUnixNano` of the `voice.stt` span and returns `(end - 1, end + 1)`.
The fill line now says where the line sat: "(+0.05 s from the stt span's
end, wyoming-whisper.service)". The coverage check uses the same window
(no widening).

Tests: `test_d1_the_window_is_the_end_of_the_stt_span_one_second_either_side`
(lines 0.01 s inside and outside each edge), `test_the_window_is_the_one_constant`
(a line 1.5 s after: left at 1 s, filled when only the constant is raised
to 2), `test_the_window_is_measured_from_the_spans_end_not_its_start`.

Mutations: `STT_END_S = 2.0` fails four; the window from the span's start
fails 31.

### 4. The NAS scan before the lock and the transaction

`scan_audio()` scans both recordings trees (the directory checks and
`voice_label_sheet.scan`, local and NAS) in `main()`, before the dry run
opens the record and before `--apply` takes the lock. `nas_audio()` gets
the scans and reads only the record. To keep one copy of the join,
`voice_runs_export.audio_joins` takes an optional `recs` (the scan already
made); without it, it scans as before, so the exporter is unchanged. Left
inside the transaction: one `os.stat` per `certain` answer, in
`audio_joins` (at most the rows stored not tried, 46).

If there are no rows stored not tried, the scan still runs (outside the
lock, before the record is read). A second `--apply` therefore scans the
NAS again; it holds nothing up.

Test: `test_the_nas_scan_runs_before_the_lock_and_before_the_record_is_opened[apply|dry-run]`:
the events are exactly scan (lock free), scan (lock free), open, join with
`recs` given; same answers as plumbing 5's test.

Mutation: a scan again inside `plan()` fails both.

### 5. A dry run that meets an interrupted write

A read-only opener that meets a hot rollback journal gets
`SQLITE_READONLY_ROLLBACK`. `interrupted()` checks that extended code;
the dry run then says "FAILED: a write was interrupted; the next export or
--apply repairs it. Nothing read, nothing written" (exit 1), at the open or
at the read. Any other error keeps its old message.

Test: `test_a_dry_run_after_a_killed_apply_says_a_write_was_interrupted`
(the e2e8 kill: 400 rows, SIGKILL at the 250th update, hot journal on disk;
the dry run's last line is the message, no "readonly"; then `--apply`
fills all 400).

Mutation: `interrupted()` returning False fails it.

## Tests changed to the ruling

`tests/test_m30_backfill_e2e8.py`:

- The four B1 tests lose their `xfail(strict=True)` marks; `WRONG` removed.
  Three pass with assertions and fixtures unchanged.
  `test_two_empty_rows_that_match_one_line_are_neither_certain` is changed:
  B starts 0.5 s after A, not 3 s. At 3 s apart the line is outside A's
  one-second window and B is filled with its own line, which is right under
  the ruling; at 0.5 s the line is in both windows, the case the test is
  about. Assertion unchanged.
- `test_a_raw_trace_that_is_not_a_trace_falls_back_to_the_whole_run` →
  `test_a_raw_trace_that_is_not_a_trace_gives_no_window`: the same eight
  inputs, `stt_window()` is None.
- `test_lines_that_look_like_transcripts_but_are_not`: R-1 gets a stored
  count, so it reaches the window and the assertion "no transcript in the
  window" still tests what it tested. With no count it now stops earlier.
- `test_a_kill_between_rows_leaves_the_record_whole_and_the_next_apply_finishes`:
  the export reads all 400 traces (`EX.TRACE_READS_MAX` raised to 400 for
  the test). The exporter reads 300 traces a run, so 100 rows had no span
  and filled by the whole-run window; now they would be left. Assertions
  unchanged.

`tests/test_m30_backfill.py`:

- The D1 house: R-two's second line 0.5 s after the first (was 3 s, outside
  the new window). R-nocount (alone, no count) is now left, "no stored
  count". R-nocount-crowd is replaced by R-nospan (a count and its line, no
  span), left. Counts: filled 3 → 2, left 7 → 8; R-dev's reason now reads
  "within 1 s of the stt span's end" for "(stt span)".
- `test_d1_the_window_is_the_stt_span_and_the_slack_of_the_audio_join` →
  `test_d1_the_window_is_the_end_of_the_stt_span_one_second_either_side`:
  both edges, inside and outside, and a row with no span left (it was
  filled by the whole run).
- `test_d1_a_journal_that_does_not_cover_the_window_leaves_the_row`: the
  journal starts at the span's end (was 1 s after the run's start, which
  is now before the window opens).
- D2 "would fill 3" → 2 (twice); D3 "filled 3" → 2 and "already with text
  4" → 3; D5's filled rows `R-dev-own`, `R-fill`; D4's word list loses the
  dropped sentence 99.

## The rule as it now stands

A row's `heard` is filled from the Whisper journal only when all hold:

1. `heard` is empty and the row is not removed.
2. `heard_chars` is stored and above 0.
3. Its `raw_trace` has a `voice.stt` span.
4. The journal of the unit that served it (household or dev, by `is_dev`)
   covers the window: the span's end, `STT_END_S` = 1 s either side.
5. Exactly one transcript line of that unit is in the window.
6. Its length equals `heard_chars`, and it is not blank.
7. That line is in the window of no other row of the same unit (any row
   with a span: empty, with Tempo's words, with no count, with a count of
   0). A removed row has no span and holds no line.

Anything else is left and listed with its reason. Both recordings trees are
scanned before the lock and any transaction; the transaction holds the
reads of the record and the writes. A dry run that meets an interrupted
write says so.

Prediction for D7 unchanged: on e2e8's calibration the same 40 fill (each
had its line at the span's end to 10 ms, no line within 16.1 s of another),
and the two with no count are left, now as "no stored count".

## Suite

With `kronk-voice-runs-export.timer` active throughout (8380 before; plus
the four unmarked and 14 new):

```
===== 8398 passed, 3 skipped, 58 xfailed, 6 warnings in 205.53s (0:03:25) ======
```
