# M30 step B, fix round 3: report

Fix agent, 2026-11-26, on `9abaad0` (the ruling on the narrow test of fix
round 2). The change is commit `ad70bf5`. The one job is done.

## The diff in words

- `scripts/voice_runs_export.py`, `reopen_joins()`: the condition
  `r["end_s"] < now - FINAL_S` is gone. Every inserted row's set
  (`_closure`) has `audio_tried_at` cleared, final or not, so
  `audio_joins()` waits for the set whole while it holds a young row. The
  docstring and the comment at the call site now say "final or not". The
  `now` parameter stays (unused now) so the signature and the call site do
  not move.
- `tests/test_m30_voice_runs_e2e4.py`: the two `xfail(strict=True)` marks
  are removed. The assertions are unchanged.
- `docs/features/voice-dataset.md`: the sentence about a row that "reaches
  the record already final" now says a row inserted after a neighbour was
  joined, final or not, re-opens its set, which is joined again in the
  same run if every row of it is final, or once its youngest row is.
- No existing test asserted the old behaviour: the fix-round-2 test that
  counts `audio_reopened == 1` (a removed row in the set) still passes,
  because its inserted row is final anyway. The test-file docstrings that
  say "inserted already final" (`test_m30_voice_runs_fix2.py`,
  `test_m30_voice_runs_e2e3.py`) describe the cases those tests build,
  which are still true; they are left as written.

## Checks

The six step-B files (`tests/test_m30_voice_runs*.py`), default seeds:

    223 passed in 35.50s

The randomized test at 300 seeds
(`KRONK_M30_E2E4_SEEDS=300`, `-k "random or seed"` on
`tests/test_m30_voice_runs_e2e4.py`):

    300 passed, 17 deselected in 37.60s

The condition put back in a scratch copy (`git archive HEAD` plus the
changed files, in the session's scratch directory), the two finding tests:

    FAILED tests/test_m30_voice_runs_e2e4.py::test_job1_a_catch_up_read_cut_at_a_day_mark_before_a_long_run_that_is_not_final_yet
    FAILED tests/test_m30_voice_runs_e2e4.py::test_job1_a_long_run_tempo_shows_late_but_before_it_is_final
    2 failed, 40 deselected in 0.61s

Both fail on A: stored `certain`, the join says `ambiguous`.

## Suite

`./scripts/run_tests.sh -q`, last line (it was `8220 passed, 3 skipped,
56 xfailed`; the two xfails are now passes):

    8222 passed, 3 skipped, 54 xfailed, 6 warnings in 195.09s (0:03:15)

## Guard rails

None broken. The exporter ran only in pytest's `tmp_path`, Tempo faked. The
live record, the real `data/`, every unit and container, and
`scripts/work.py` were not touched. No transcript text. The scratch copy is
removed.
