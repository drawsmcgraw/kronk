# M30 step C, fix round 1

Fix agent (Opus 5.5), 2026-11-26, 02:00 to 02:40 local (EST), on
`a172065`. The jobs are the coordinator's ruling on step C's test
(`M30-criteria.md`, "Rulings", the 2026-11-26 entry on `M30-e2e6.md`):
B1, S1, S2, S3, note 1, note 3. All six are done. Work commit `294339d`.

Guard rails: none broken. The live record was never written. The only
reads of it were the suite's own guard (`mode=ro`, run ids, `seq` and
counts) in the three full runs. No sentence from `data/voice_runs.db` or
dev's was read. Production was not touched. No unit was started, stopped
or installed, and the live timer ran through every suite run. The
exporter was run only against scratch paths. `scripts/work.py` and the
bench were not run. The dev pair was rebuilt twice with the exact
command (the second rebuild carried a wording fix to S1).

## B1, the guard (`tests/conftest.py`)

Mtime is no longer the test for the record. There are now two fences.

**1. In process, a write is refused.** Conftest replaces these at import:
`sqlite3.connect` (and `sqlite3.dbapi2.connect`), `builtins.open` /
`io.open` in any mode containing `w`, `a`, `x` or `+` (so
`Path.write_text`, `Path.write_bytes` and `Path.open("w")` too), `os.open`
with `O_WRONLY`, `O_RDWR`, `O_CREAT`, `O_TRUNC` or `O_APPEND` (so
`Path.touch` and `tempfile`), and `os.replace`, `os.rename`, `os.remove`,
`os.unlink` and `os.truncate` (either path of a rename or replace). Each
one raises `LiveVoiceFileBlocked` (an `AssertionError`) when the path,
resolved and with any `file:` URI's query stripped, is one of the live
files or lies under `~/.local/state/kronk-voice/`. The live files are the
record, its `-journal`, `-wal` and `-shm`, `data/voice_runs.db`,
`data/dev/voice_runs.db`, `data/voice_runs.json` and
`data/dev/voice_runs.json`. A `sqlite3.connect` with `mode=ro` is refused
too, as the ruling says. Every attempt is recorded. Wrapper hooks on
`pytest_runtest_setup`, `_call` and `_teardown` fail the test that made
one even when the code under test caught the error, and
`pytest_sessionfinish` fails the run if any attempt was made at all.

What it does not block:

- a read (`open(..., "r")`, `os.open(O_RDONLY)`), which nothing in the
  suite does;
- `os.mkdir`, `shutil.rmtree` (it unlinks through `dir_fd`, so the names
  are relative);
- C code that opens files directly;
- anything a subprocess does.

The exporter run as a subprocess against scratch is that last case, and
it never names a live path. No test in the suite reads the live
published copy: every route and schema test builds its copy in
`tmp_path`, and the suite passes with the fence on. The guard's own
reader keeps a reference to the real `connect`.

**2. At the end of the session, by content.**

- **The record.** It is read once at conftest import and once at session
  end, each time `mode=ro` (no `-journal` is made) inside one read
  transaction. The read takes `run_id` and `seq` of every row,
  `COUNT(*) WHERE removed IS NOT NULL`, `COUNT(labels)` and
  `COUNT(run_changes)`, and nothing else. The run fails when:
  - the record existed at the start and is gone (never excused);
  - it did not exist and now does;
  - it cannot be read at either end;
  - any run id at the start is gone;
  - any `seq` is lower;
  - the removed count moved;
  - labels or `run_changes` shrank;
  - a row added or raised carries a `seq` at or below the start's maximum;
  - the change does not balance against the journal (next point).

  Otherwise the change must balance. The rows added must equal the summed
  "new N", the rise in max `seq` must equal "new N" plus "changed N", and
  the number of rows whose `seq` rose must not exceed "changed N". The
  sums come from the unit's journal lines between the two reads. A run
  wholly between the reads counts. A run that overlaps a read (its commit
  may be on either side) is tried both ways. If the journal cannot be read
  (`journalctl` missing or exiting non-zero), any difference fails.
- **The copies** may change only inside a run whose line says "copies
  written K" with K > 0. A copy gone at the end fails.
- **The status files** keep the mtime rule: a change inside any run of the
  unit is the timer's. A file gone at the end now fails; before, the old
  mtime excused it.
- **The record's `-journal`, `-wal` and `-shm`** follow the status-file
  rule, but one that is gone is the timer's when its last mtime was inside
  a run (the `-journal` comes and goes inside every run).

Two details of the journal parsing. The exporter's line comes through its
stdout stream, which journald may read after systemd's "Finished". It is
counted to the last run started, and it extends that run's end. If the
first comparison fails, the journal (not the files) is read once more
after 2 s, so a run that ended just before the last read is not missed.
Failure messages name paths and counts, never a run id or any text.

**Tests.** The two B1 `xfail` tests lost their marks. Each was rewritten
to the ruling, because the ruling changes what the guard reads: they fed
stat tuples to `_voice_changes_not_the_timers`, which is gone.

- `test_e2e6_the_guard_reports_the_record_deleted_during_the_run` is now
  a content snapshot before and `None` after, reported with the journal
  or without it.
- `test_e2e6_the_guard_reports_a_write_the_timer_then_wrote_over` is now
  a row added while the journal says "new 0". It is reported, and passes
  when the journal says "new 1".

`test_the_guard_reads_the_timers_runs_from_its_journal` in
`test_m30_heard.py` keeps its cases under the new shape: runs carry
counts, and its gone file is now a side file, since a gone status file
fails.

The new tests in `tests/test_m30_heard_e2e6.py`:

- `_record_problems`: the timer alone balances; a deleted row is caught
  even when a new one makes up the count; a `seq` raised or lowered by
  hand; a removal; labels and `run_changes` shrinking (growth passes);
  the journal unread; a record that appears; a run overlapping a read
  counts either way, and one before the start read does not.
- Journal parsing: counts, the late stdout line, a "not read" line, a run
  still going, and a non-zero exit or `OSError` giving `None`.
- `_file_problems`: copies, status files and side files, each with a run,
  between runs, deleted, and with the journal unread.
- `_record_snapshot` on a record made by the exporter in `tmp_path`: ids,
  `seq` and counts only; the file's sha256 and mtime unchanged; no
  `-journal` left; a missing file gives `None`, a non-database an error.
- The fence: installed and covering every live path (`file:` URIs and
  `Path` objects included); each of twelve writes of a fenced `tmp_path`
  refused with nothing written; reads and other paths let through; a
  caught attempt still fails its test; the exporter in process against a
  fenced record raises and writes nothing.

Eleven mutants of the guard were run against these tests. Ten died at
once. The eleventh, dropping the "added == new" term, survived. One case
was added ("changed 1" does not account for a new row), and it dies now.

### B1 on scratch

A copy of the working tree (`git ls-files`, 36 MB, no `data/`, no
symlinks) in the session scratchpad. `HOME` was a scratch directory, so
the record's path was scratch, and the tree's `data/` was scratch.

A stand-in timer, `harness/sim.py`, stood in for the live one. Every 5 s
it ran the real exporter (the scratch tree's `main()`) against the
scratch record, copies and status files, with the suite's `FakeTempo`
holding invented runs: a new run every other time, a changed one every
third. It wrote "Starting …", the exporter's own lines and "Finished …"
(or "Failed with result …") to a scratch journal file. A stand-in
`journalctl` on `PATH` served that file from `--since` on, and exited 1
while a `journal.broken` file existed.

Each scenario was one pytest session with one test. Before each session
the house was put back from a saved copy and the timer was left 6 s to
settle. Writes "by another process" were made in a subprocess, which
fence 1 does not see, so only the end-of-session guard could catch them.
"Record" is `COUNT(*)` and `MAX(seq)` before and after the session. "Runs"
counts the stand-in timer's runs started during the session.

| What the test did | Record | Runs (copy written) | Guard | pytest exit |
|---|---|---|---|---|
| nothing; waited for two timer runs | 69/74 → 70/75 | 2 (1) | silent | 0 |
| inserted a row (seq max+1), then two timer runs wrote | 81/86 → 83/88 | 2 (2) | `…/voice_runs.db: the record changed (2 row(s) added, 0 seq raised, max seq 86 to 88) and the timer's journal in between accounts for new 0, changed 0 (and 1 run(s) overlapping the reads)` | 1 |
| deleted a row, then two timer runs wrote | 82/87 → 83/89 | 2 (1) | `the record changed (1 row(s) added, 1 seq raised, max seq 87 to 89) and the timer's journal in between accounts for new 2, changed 0 (and 1 run(s) overlapping the reads)` | 1 |
| raised a row's seq to max+1, then two timer runs wrote | 84/89 → 85/91 | 2 (2) | `the record changed (1 row(s) added, 1 seq raised, max seq 89 to 91) and the timer's journal in between accounts for new 0, changed 0 (and 1 run(s) overlapping the reads)` | 1 |
| `voice_runs_export.py --remove` on a row (a subprocess, scratch paths) | 85/90 → 86/91 | 1 (1) | `the removed rows went from 0 to 1 (a removal is never the timer's); …: run_changes went from 5 to 4` | 1 |
| deleted the one label | 86/91 → 87/92 | 1 (1) | `labels went from 1 to 0` | 1 |
| deleted the record; the timer ran after | 71/76 → absent | 1 (0) | `…/voice_runs.db: the record was deleted` | 1 |
| `sqlite3.connect(record)` in the test, error swallowed | 72/77 → 72/77 | 0 | test FAILED `LiveVoiceFileBlocked`; `FAILED: 1 attempt(s) to write a live voice-run file were refused: a test tried to open with sqlite3.connect …/voice_runs.db` | 1 |
| `open(data/voice_runs.db, "ab")` in the test, error swallowed | 72/77 → 72/77 | 0 | test FAILED `LiveVoiceFileBlocked`; `… open (mode 'ab') …/tree/data/voice_runs.db` | 1 |
| appended to a copy; the timer's next run rewrote it ("copies written") | 74/79 → 75/80 | 2 (1) | silent (the copy heals, as ruled) | 0 |
| appended to a copy; timer paused, no run after | 76/81 → 76/81 | 0 | `FAILED: … …/tree/data/voice_runs.db` | 1 |
| appended to a status file; timer paused, no run after | 76/81 → 77/82 | 1 (1) | `FAILED: … …/tree/data/voice_runs.json` | 1 |
| deleted a status file; timer paused, no run after | 77/82 → 77/82 | 0 | `FAILED: … …/tree/data/dev/voice_runs.json (deleted)` | 1 |
| journal unreadable; only the timer wrote | 78/83 → 79/84 | 2 (1) | `the record changed (1 row(s) added, 0 seq raised, max seq 83 to 84) and the timer's journal cannot be read`, and each copy and status file `(the timer's journal cannot be read)` | 1 |
| journal unreadable; timer paused, nothing changed | 79/84 → 79/84 | 0 | silent | 0 |
| the real tests: `test_m30_voice_runs.py`, `_e2e3`, `test_m30_heard.py`, `_e2e6`, `test_m16_backups.py`, with the stand-in timer running | 91/96 → 92/98 | 3 | silent; `437 passed, 3 deselected` | 0 |

Notes on the table:

- Every message is quoted from the second sweep, which was run to add
  the counts. The first sweep gave the same verdicts.
- In "deleted a row", the stand-in timer put the row back: its Tempo
  still listed it, so the exporter inserted it again under a new `seq` and
  counted it "new". Every run id at the end was there, so the guard caught
  it on the balance, not as a gone row. The row's `seq` rose with no
  "changed" to account for it, and the rows added (1) did not equal "new"
  (2). A deletion the timer does not undo is caught as a gone row; the
  unit test `test_b1_a_row_deleted_is_reported_even_when_the_count_is_made_up_by_a_new_one`
  shows that case.
- In "status file, no run after", one timer run started before the pause
  took effect. The append came after the pause, outside any run.
- The three deselected tests need a git repository, which the scratch
  tree is not (`check-ignore` and two `git show 56e9368` lookups). They
  pass in the real tree.
- The stand-in timer stopped on its stop file, exit 0.

## S1: three out-of-range numbers answered 500

`orchestrator/voice_runs_api.py`. `changed_after` and `known_rows` are
bounded at 2^63−1 (what SQLite binds). `known_built_s` must be from 0 to
253402300799 (the span `datetime.fromtimestamp` turns into a date). Each
value outside its bound is a 400 with the reason. `limit` still clamps.

- `test_e2e6_a_number_out_of_range_is_a_400_not_a_500`: three cases, mark
  removed, assertions unchanged.
- `test_s1_the_400_names_the_parameter_and_the_edges_still_answer`, new:
  the reason names the parameter, and the edge values (2^63−1, 0,
  253402300799) still answer 200.

On dev after the rebuild:

```
?changed_after=99999999999999999999999&known_rows=1&known_built_s=1  {"error":"changed_after must be 9223372036854775807 or less"} 400
?changed_after=0&known_rows=1&known_built_s=1e18                     {"error":"known_built_s must be from 0 to 253402300799"} 400
?changed_after=0&known_rows=1&known_built_s=-1e18                    {"error":"known_built_s must be from 0 to 253402300799"} 400
```

## S2: a resync lost a reader who had paged back

`orchestrator/static/work.html`. On `resync`, `heardPatch` now calls
`heardLoad(heard.rows.size)`. `heardLoad(want)` asks for
`max(200, want)` rows in pages of `HEARD.page_max` (500, the route's
`LIMIT_MAX`), following `next_before` until it has them or the copy runs
out. The status, copy and `max_seq` come from the first page; "show
older" continues from the last page's cursor. If the filter changes
mid-load, the load stops and the filter's own load follows.

The place is kept by run id. `anchor()` now records the first row in view
and up to 19 below it (`HEARD.anchor_rows`, 20). `keepAnchor` uses the
first of them still there, so a removal of the row the reader was looking
at keeps them at the next one. Open rows are restored by run id, as
before. The first opening and a filter change still load 200.

Test: `test_s2_a_resync_loads_as_many_rows_as_were_loaded_in_pages_of_500_at_most`
and `test_s2_the_place_is_kept_by_run_id_falling_back_to_the_next_row_still_there`
(script text: the call, the loop and its bound, `page_max == LIMIT_MAX`,
the cursor, the order anchor → clear → keepAnchor). Both fail on the old
page.

## S3: a zero-byte record backed up as OK

`scripts/backups.py`. `collect` passes `need_table=True` to `read_sqlite`
for every SQLite member in `required_sources`: the voice record,
`solar.db` and `music_taste.db`. Such a database with no table in
`sqlite_master` is a gap, `sqlite: holds no table (<n> bytes): not the
database it should be`, so the run is INCOMPLETE. A database that is not
required (`work.db`) goes in as before. The weekly voice copy already
failed on such a record (`_db_counts` reads `runs`).

Tests:

- `test_s3_a_zero_byte_record_backs_up_as_incomplete`: exit 4, "config
  backup INCOMPLETE", the reason in the notice. It fails on the old
  `backups.py`.
- `test_s3_an_optional_database_with_no_table_is_still_backed_up`.

## Note 1: at the top of the page a new run scrolled it

`anchor()` returns nothing while the section's head (`#heard > summary`)
is in view (its top at or below the top of the window), so a reader at
the top is not held and a new run does not scroll the page. It now walks
the rows in page order (`heard.list.querySelectorAll`), not the `Map`'s
insertion order, in which rows patched in later came last.

Test: `test_note1_no_anchor_while_the_sections_head_is_in_view_and_rows_in_page_order`.

## Note 3: satellite ids that are properties of Object

`satelliteName(id)` looks the map up with `Object.hasOwn`. Any other id,
`constructor` included, is shown as the id; none is shown as `?`.

Test: `test_note3_the_satellite_map_is_looked_up_as_own_keys_only`. The
one `SATELLITE_NAME[…]` in the script is inside `satelliteName`.

## What a browser must confirm (the retest)

There is no JavaScript engine in the suite. The tests above read the
script's text. In a browser on `/dev/work`, with a fixture as in C3:

1. **S2.** With all rows loaded ("show older" until hidden, about 460),
   one row open and a row near the bottom in view, remove a row on the
   first page (`--remove`). After the next tick (`resync` true), check:
   - the same number of rows less the removed one, and "show older" still
     hidden;
   - the open row still open;
   - the row in view at the same place on screen (within a pixel or two);
   - the requests: `limit=460`, one page (or `limit=500` then the rest
     for a list over 500).

   Then remove the row that is in view. The next row below it takes its
   place on screen.
2. **S2, a list over 500** (if the fixture allows): after paging to more
   than 500 rows, a resync makes two requests, the second with `before=`,
   and the place holds.
3. **Note 1.** At scrollY 0, a new run published and one tick later: the
   new row on top, scrollY still 0, the filter field where it was.
   Scrolled down into the rows: a new run keeps the row in view in place,
   as C3 showed.
4. **Note 3.** A fixture row with satellite id `constructor` shows
   `constructor`, not `function Object() …`. The three real ids still
   show kitchen, basement, office.
5. No script error in any of the above, and the C3 lines still hold
   (folded on load, text only, filter and switch kept).

## Docs

- `docs/features/work-page.md`: the resync loads what was loaded and
  keeps the place; a reader at the top is not held.
- `docs/features/voice-dataset.md`, "Gotchas": a table-less required
  database is INCOMPLETE; how the suite keeps off the live record.

## Not done

- Nothing in the ruling. The ruling's notes 2, 4, 5 and 7 are left as
  notes, as ruled.
- `HEARD.heard_text[...]` and `HEARD_BANNER[...]` are looked up the same
  way the satellite map was, but their keys come from the route's own
  values, not from data. They were not changed; the ruling named only the
  map.
- Fence 1 does not cover `os.mkdir`, `shutil.rmtree`, C-level opens or
  subprocesses (above). Fence 2 cannot see a text change made from
  outside the process that raises no `seq` and moves no count. That would
  be a raw `UPDATE` from another process, say the `sqlite3` CLI; in
  process it is refused. Catching it would mean reading more than ids,
  `seq` and counts.

## The suite

With `kronk-voice-runs-export.timer` active (four runs of its unit in the
five minutes the run took), exit 0, no guard line:

```
8332 passed, 3 skipped, 54 xfailed in 205.09s (0:03:25)
```

It was `8304 passed, 3 skipped, 59 xfailed`. The five strict `xfail` tests
of `M30-e2e6` now pass, and 23 tests are new.

## State at the end

Dev runs `294339d`'s `work.html` and `voice_runs_api.py` (sha256 matched
in the container after the first rebuild; the second carried only the
reason's wording). `http://localhost/dev/work` answers 200. The scratch
tree and harness are in the session scratchpad and hold invented runs
only. The tree is clean after this commit.
