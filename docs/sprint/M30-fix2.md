# M30 step B, fix round 2: report

Fix agent, 2026-11-26, on `6ce05b0` (after `80801e7`, the ruling on the narrow test,
`M30-e2e3.md`). All four jobs are done. Each has a test that fails without
it, and each was broken by hand in a scratch copy (19 mutations, all
caught; table below). The three `xfail(strict=True)` tests in
`tests/test_m30_voice_runs_e2e3.py` have lost their marks and pass. No
schema change (the record stays at schema 2, the copy at 1).

Commits: `a484f60` (code and tests), `01a6b56` (feature doc), then this
report.

Guard rails: none broken. Every exporter run was through pytest's
`tmp_path` or the scratch copy, with Tempo faked at the function boundary.
No live record, no real `data/`, no socket, no unit, container, backup or
`scripts/work.py`. Every sentence in the tests is invented ("invented").

## Job 1: a row inserted already final re-opens its set

**Change.** `scripts/voice_runs_export.py`, new `reopen_joins()`, called in
`export()` after every insert and before `audio_joins()`. `export()` now
collects every row it inserts, from the main read and from the hourly
check's take, in `new_rows`. For each one already final (`end_s < now -
FINAL_S`), `_closure()` (rows only, no recordings needed) gives its set.
`audio_tried_at` is set NULL on every row of those sets that is not
removed and was tried. `audio_joins()` then joins them again whole, in the
same transaction. The audio columns raise no `seq` and are not published.
The status gets `this_run.audio_reopened` (rows re-opened) when it is
non-zero.

The ruling names three ways in, and all three go through this one point:
the budget's cut (the main read's insert), the hourly check's take (the
check's insert) and a run Tempo shows late (the main read's insert).

**Tests.**
- `test_m30_voice_runs_e2e3.py`:
  `test_f1_a_first_read_cut_by_its_budget_between_two_runs_on_one_recording`
  and `test_f1_a_run_the_hourly_check_takes_beside_a_row_already_joined`:
  marks removed, assertions unchanged, both pass.
- The note test for the third path changes to the ruling, as the brief
  said it would. It was
  `test_f1_note_a_run_that_reaches_tempo_after_final_s_is_ambiguous_beside_a_stored_certain`,
  which asserted the old result (A `certain`, B `ambiguous`). It is now
  `test_f1_a_run_that_reaches_tempo_after_final_s_reopens_its_neighbours_join`
  and asserts stored = the join = both `ambiguous`.
- `tests/test_m30_voice_runs_fix2.py` (new):
  - `test_job1_a_run_tempo_shows_after_final_s_leaves_every_join_as_the_join_says`:
    the third path in four shapes (two runs on one recording; a chain
    whose change lands two hops from the late run; a chain with a third
    recording only the late run fits; a late run that fits no recording).
    After it, the stored joins equal `voice_label_sheet.join` over the
    finished record, and no existing row's `seq` or `last_changed_at`
    moves.
  - `test_job1_the_two_hops_shape_changes_the_far_row`: in that shape A's
    answer moves although A's span and A's recording are both outside the
    late run's span. Re-opening only direct neighbours would leave A wrong.
  - `test_job1_a_run_only_the_hourly_check_can_reach_reopens_its_neighbours_join`:
    see "Found in the ruling", point 1.
  - `test_job1_a_removed_row_in_the_set_is_not_reopened`: B removed after
    its join, a late C beside it. B's row does not move (`audio_tried_at`
    included), A and C get the join's answer, `audio_reopened` is 1.
  - `test_job1_a_reopen_raises_no_seq_and_rewrites_no_copy`: two runs 91
    days old (outside the copy's 90 days) on one recording, with the first
    read's budget cut between them. The second run re-opens A, and A's
    answer moves from `certain` to `ambiguous`. A's `seq` is unchanged,
    `published` is 0, and the copy's bytes and mtime are unchanged.

## Job 2: `--remove` and `--init` wait for the lock; misuse refused

**Change.** `lock_dir(db, wait=0.0)` now polls the flock every
`LOCK_POLL_S` (0.1 s) for up to `wait` seconds of wall time.
`main()` passes `LOCK_WAIT_S` (55 s) when `--remove` or `--init` is given
and 0 otherwise. A run by hand that still has no lock logs `FAILED:
another export held the record's lock for 55 s; nothing removed (<id>)`
(or `nothing made or read`) and exits 1. The timer's ordinary run gives
way with exit 0 as before. Why 55: the unit kills a run at 50
(`TimeoutStartSec`), and a holder that started before us has less than
that left; the 5 s is margin.

Refused with exit 2, before anything is opened: `--check-now` with
`--remove` (a removal reads no Tempo), and `--why` without `--remove`.
`--why`'s default moved from argparse into `remove_run`'s call ("removed
by hand", unchanged) so that "given" can be told from "not given".
`--check-now` with `--init` is not refused: `--init` runs an export, and
`--check-now` restarts a check under way.

**Tests.**
- `test_m30_voice_runs_e2e3.py::test_remove_while_an_export_holds_the_lock_does_not_exit_0_with_nothing_removed`:
  mark removed, assertion unchanged. Its setup gains one line,
  `monkeypatch.setattr(EX, "LOCK_WAIT_S", 0.3)`. It holds the lock for the
  whole run, so with the real 55 s it would spend 55 s of the suite. It
  passes on exit 1.
- `test_m30_voice_runs_fix2.py`:
  - `test_job2_a_run_by_hand_waits_for_the_lock_and_then_does_its_work`:
    `--remove`, `--init` on an existing record, `--init` with no record.
    The lock is released after 0.4 s; exit 0, the work done, at least
    0.35 s waited.
  - `test_job2_a_run_by_hand_that_never_gets_the_lock_exits_1_and_does_nothing`:
    the same three, never released: exit 1, `FAILED` and "lock" in the
    log, no traceback, the record's bytes unchanged (no record made in the
    third case).
  - `test_job2_the_timers_ordinary_run_still_gives_way_at_once_with_exit_0`:
    with `LOCK_WAIT_S` at 5 s, the ordinary run returns 0 in under 1 s
    with no Tempo call.
  - `test_job2_the_wait_outlasts_the_longest_a_run_can_hold_the_lock`:
    `LOCK_WAIT_S` > the unit's `TimeoutStartSec`, read from the unit file.
  - `test_job2_check_now_or_why_where_they_do_nothing_is_refused`:
    `--remove --check-now`, `--why`, `--init --why`, `--check-now --why`:
    exit 2, REFUSED, record and status file bytes unchanged, no Tempo call.
  - `test_job2_remove_without_why_still_marks_the_run`: the default text.

## Job 3: a request to Tempo is bounded in wall time

**Change.** `http_get_json()` arms a one-shot `ITIMER_REAL` for `timeout`
seconds around `urlopen()` and `read()`. Its `SIGALRM` handler raises
`TimeoutError("the request took longer than 5 s")` inside whatever call
is blocked: connect, status line, headers or body. `Tempo.get` already
turns any exception into a `TempoError`, so the read fails, the record is
not touched, and the status says why. On every way out, the timer is
cancelled and the previous handler restored (nested `finally`, so an alarm
landing between the two still leaves the old handler in place). urlopen's
own per-socket `timeout` stays, so the existing
`test_m30_voice_runs.py` test that checks it passes unchanged.

Signals go to the main thread only. Called from any other thread, or while
another `ITIMER_REAL` is armed, the function raises `RuntimeError` rather
than run unbounded. The exporter is a single-threaded CLI. The docstring
says this.

**Tests** (`test_m30_voice_runs_fix2.py`; no socket, `urlopen` replaced by
a fake that dribbles a byte every 20 ms, 2.3 s in all):
- `test_job3_a_tempo_that_dribbles_is_cut_at_the_whole_requests_timeout`:
  dribbling in the headers phase and in the body. With 0.3 s it raises
  `TimeoutError` between 0.3 and 0.8 s. urlopen still sees `timeout=0.3`.
  Afterwards the handler is the old one and no timer is left.
- `test_job3_through_tempo_the_cut_is_a_failed_read_that_names_the_bound`:
  `TempoError: TimeoutError: the request took longer than 0.3 s`.
- `test_job3_a_prompt_answer_leaves_no_timer_and_the_old_handler`.
- `test_job3_off_the_main_thread_the_request_is_refused_not_unbounded`.
- `test_job3_the_exporter_uses_the_bounded_request_by_default`: `main` and
  `Tempo` default to `http_get_json`.

## Job 4: the wait for the record fits, and is in the status

**Change.** `BUSY_TIMEOUT_S` 10 → 5: 38 + 5 (Tempo) + 5 (the record) = 48
< 50. The constants' comment and the header say so.

**Tests.**
- `test_m30_voice_runs_e2e3.py::test_f8_the_budgets_and_the_unit_by_the_constants`
  (the test that states the arithmetic): one line added, `CHECK_BUDGET_S +
  TEMPO_TIMEOUT_S + BUSY_TIMEOUT_S == 48 < limit`.
- `test_m30_voice_runs_fix2.py::test_job4_tempo_at_its_worst_and_a_person_holding_the_record_ends_inside_the_unit`:
  every request takes its whole 5 s on the budget clock, an hourly check
  runs, and a person's `BEGIN IMMEDIATE` holds the record with the real
  `BUSY_TIMEOUT_S`. Exit 1, no traceback, and the status file has a fresh
  `ts`, `ok: false` and "database is locked". The budget clock's span is
  at most 43, and 43 plus the wait really taken is under 50. It costs 5 s
  of real time in the suite. With 10 s it fails (53).

## Mutations

Each broken by hand in a scratch copy of the tree (`git ls-files`
extracted under the session's scratch directory) against the five M30
step-B test files. `test_m30_voice_runs.py::test_b6_the_copies_and_status_files_are_gitignored`
fails in every scratch run because the copy has no `.git`. It is not
counted.

| Job | Mutation | Caught by |
| :-- | :-- | :-- |
| 1 | no re-open | both former xfails, the third-path note test, every `test_job1_*` |
| 1 | the check's take does not re-open | `test_job1_a_run_only_the_hourly_check_can_reach_…` (only it; see below) |
| 1 | the main read's inserts do not re-open | both former xfails (e2e3), the third-path tests |
| 1 | direct neighbours only, not the closure | `test_job1_the_two_hops_…`, the third-path test's two-hops shape, the removed-row test |
| 1 | removed rows re-opened too | `test_job1_a_removed_row_in_the_set_is_not_reopened` |
| 1 | the re-open raises `seq` | `test_job1_a_reopen_raises_no_seq_and_rewrites_no_copy`, the third-path test |
| 2 | no wait for a run by hand | both "by hand" tests |
| 2 | exit 0 without the lock | the former xfail, the "never gets the lock" test |
| 2 | the ordinary run waits too | `test_job2_the_timers_ordinary_run_still_gives_way_…` |
| 2 | `--init` does not wait | both "by hand" tests (the two `--init` cases) |
| 2 | `--remove --check-now` allowed | the refusal test (`argv0`) |
| 2 | `--why` without `--remove` allowed | the refusal test |
| 2 | `LOCK_WAIT_S` = 50 | `test_job2_the_wait_outlasts_…` |
| 3 | no timer | the dribble test (both phases), the Tempo test |
| 3 | the handler not restored | the prompt-answer and dribble tests |
| 3 | the timer not cancelled | the prompt-answer test (the whole run then dies on the stray `SIGALRM`) |
| 3 | no main-thread check | `test_job3_off_the_main_thread_…` |
| 4 | `BUSY_TIMEOUT_S` = 10 | the job-4 test, `test_f8_the_budgets_…` |
| 4 | `sqlite3.connect(timeout=10)` hard-coded | the job-4 test |

## Found in the ruling and the tests

1. **The narrow test's hourly-check case does not reach the check's
   take.** In `test_f1_a_run_the_hourly_check_takes_beside_a_row_already_joined`,
   B starts at `NOW - 7197`, and the main read of the `--check-now` run
   starts at the watermark (`NOW - 3900`) less an hour, `NOW - 7500`. The
   main read takes B and the check never does. With the check's take
   left out of the re-open, every existing test passed. The new
   `test_job1_a_run_only_the_hourly_check_can_reach_…` moves the
   watermark first, so only the check can take B (`check.taken == 1`).
   The e2e3 test is left as it is. It still shows the main-read path.
2. **A re-open can lose a known `certain` when the recording has been
   archived.** A re-joined row is joined against the local recordings.
   If the weekly archive (recordings older than seven days) moved its
   recording to the NAS after the first join, the re-join finds it gone,
   and a row older than seven days is stored NULL ("not tried"), losing
   `audio_rel`. The neighbours' answers are computed without that
   recording too. For this to happen, a row must be inserted already
   final beside a row older than seven days whose recording was local
   when it was joined and has been archived since. The budget's cut
   comes back a minute later and the hourly check within the hour, so in
   practice this means the archive running in that window, or a
   restore. Not fixed (outside the four jobs). The guard would be to
   keep a stored `certain` whose recording is no longer local, or to
   join against the NAS layout (step D). For the coordinator.
3. **"One wait for the record" is the common case, not a bound.** 48 s
   holds for the wait the finding names: a person's write transaction
   (RESERVED) at the run's `BEGIN IMMEDIATE`. A person holding `BEGIN
   EXCLUSIVE` at that moment also stops the status read after it, which
   is a second 5 s wait before `failed_status` (53). The weekly backup's
   read during our `COMMIT` is a wait of its own as well. Both need a
   person or the backup to act inside a run's last seconds. Not fixed;
   the cure would be to drop the busy timeout to 0 for the reads after a
   failed transaction.
4. **No status write when `--remove` or `--init` does not get the lock.**
   The holder writes its own status when it ends, and a write of ours
   without the lock would race it. The person at the terminal gets the
   log line and exit 1. This is my choice; the ruling says "exit 1" only.

Nothing in the ruling looked wrong.

## Suite

`./scripts/run_tests.sh`, exit 0. Before: `8149 passed, 3 skipped, 57
xfailed`. After: three xfails pass, and 28 new tests in
`tests/test_m30_voice_runs_fix2.py`.

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
8180 passed, 3 skipped, 54 xfailed, 6 warnings in 177.18s (0:02:57)
```
