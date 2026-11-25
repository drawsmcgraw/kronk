# M33 fix round 1: the ten should-fix of step 1's test, and N1, N4, N7

Fixer: `m33-fix` (Opus 5.5), 2026-11-25, from `963f7ec`. The job list is
the ruling of 2026-11-25 in `M33-criteria.md` ("Rulings"); the findings
and their reproductions are `M33-e2e.md`.

**Guard rails: none broken.** The live database
(`~/.local/state/kronk-work/`) was never created, opened or named: every
`work.py` run named `--db` in the session scratchpad with `KRONK_WORK_DB`
pointed there too, and the suite's own guard (conftest) held. No by-hand
backup was run. No herdr agent was launched; the tests use the M28 fake.
No request to any service. `scripts/work_export.py` is byte-for-byte
unchanged (`git diff 963f7ec -- scripts/work_export.py` is empty). The seed
was not edited.

Everything is in `scripts/work.py`, its tests (`tests/test_m33_work_state.py`,
`tests/test_m28_agent_tool.py`, one line of `tests/test_m16_backups.py`) and
`docs/features/work-state.md`. Each job has a test that fails with the fix
undone: the new and changed tests were run against `963f7ec`'s
`scripts/work.py` (the file swapped in, then restored and compared):
every one of them failed there, 79 of 79 counting parameter cases. The
only tests of the selection that passed on the old file were M28's own
`test_s1_…` to `test_s4_…` (matched by the name filter, not part of this
round) and `test_n4_a_refused_import_into_an_existing_empty_database_keeps_it`,
which pins that the N4 fix does not delete a file it did not make.

## The jobs

### S1. The stage reads a step's grading before its verdict

`item_stage()` (work.py) calls `work_export.stage()` unchanged and, when
the stage came from the steps (`by == "files"`) and the last counted step
is a graded test, replaces stage and detail: graded pass is "to promote",
graded fail "waiting for the coordinator", and the detail carries both
words: `to promote (test 1: FAILS, graded pass)`, `waiting for the
coordinator (test 2: PASSES, graded fail)`. Agents, a tag and a
promotion still decide before the grading, as before.

Test: `test_resume_shows_the_stage_computed_from_rows_and_the_grading`
(it pinned the old reading; now both directions, M28's case first).

### S2. An active item with no step says so

Same place: no counted step reads `planning (no finished step recorded)`,
not "files written, not committed".

Test: `test_s2_an_active_item_with_no_step_says_so`.

### S3. Stored text on one line; the writing commands refuse it

- `resume` is built line by line; every value from a row, git, herdr or a
  state file goes through `one_line()`, which escapes C0, DEL, C1, U+2028
  and U+2029 (`\n`, `\r`, `\t`, `\x1b`, ` `). The e2e's forged
  decision (a question, a blank line, five lines of fake headings and a
  `fix:` line) now prints as one escaped line under its own `- #5 …`.
- `show`, `queue`, `list`, `log`, `check` and every other JSON line go
  through `emit()`, which now also escapes the controls `json.dumps`
  leaves raw with `ensure_ascii=False` (DEL, C1, U+2028, U+2029). C0 was
  already escaped by `json.dumps`.
- `add` (`--title`, `--criteria`), `note` (`--next`, `--waits-note`,
  `--title`, `--criteria`), `hold --why`, `drop --why` and `matter add`
  (`--text`, `--source`) refuse any of those characters, a tab included:
  `--title holds a newline or a control byte (\n); give it on one line`,
  exit 1, no log row. `matter void --why` (new, S9) refuses them too.

Tests: `test_s3_writing_commands_refuse_a_newline_or_a_control_byte` (8
commands × 6 kinds of byte), `test_s3_resume_prints_stored_text_on_one_line_escaped`
(rows forged by hand in a title, a next step, a hold reason, a decision, a
leftover and `KRONK_WORK_WHO`: the section headings are exactly the
tool's ten, no raw escape byte, the forged text five times escaped),
`test_s3_json_lines_escape_what_json_leaves_raw`.

### S4. The seed is checked as the build report said

`load_seed()` refuses: an unknown top-level key (known: `note`, `items`,
`matters`), an unknown item key (as before), an unknown matter key (known:
`kind`, `item`, `owner`, `state`, `how`, `text`, `blocks`), an item id
given twice in any case (`M6` and `m6`), an item that is not an object
with an id. A matter naming an item nothing knows was refused already, and
still is. A seed state on an item PROMOTIONS.md promotes is now a note:
`M5: seed state dropped, but PROMOTIONS.md promotes it (line 13); imported
promoted`. The committed seed (11 items, 44 matters, a `note`) loads.

Tests: `test_s4_the_seed_is_refused_for_an_unknown_key_a_twice_given_id_or_an_unknown_item`
(7 cases, each also checking N4), `test_s4_a_seed_state_promotions_md_overrides_is_noted`,
`test_the_coordinators_seed_loads` (11 and 44).

### S5. Fix lines run as printed; every kind has one; `report-gone` clears

Every fix line is now one of:

- one command that runs as printed from the repo root: `python3
  scripts/work.py …`, carrying `--db` when the run's database is not the
  one the default (`KRONK_WORK_DB`, else the home path) would find, and
  `--repo` when the repo is not the script's own; or `git …` (with `-C
  <repo>` the same way) for `tag-gone`, which re-creates the tag on the
  commit the promotion row recorded;
- or a line starting `not a command: ` that says what to look up, then
  what to run.

Per kind:

| Kind | Fix line |
| :-- | :-- |
| `started-not-working`, `started-on-ended` | `step stop … --why '<the finding itself>'`, quoted, runnable |
| `started-report-in` | `step finish ITEM KIND --round N` (one command; `agent.py status` dropped, it fails once the state file is gone) |
| `commit-not-in-branch` | `step refresh …` when the report is in HEAD; else not a command: find where the report went |
| `verdict-changed` | `step refresh …` |
| `report-gone` | `step note-gone ITEM KIND --round N` (new) |
| `agent-no-step` | `step start … --at <agent.py's launch time>` when the state file gives it; else not a command: find the launch time, then the `step start` line to give it to. The round comes from the state file, else from the name (`m6-test2`: round 2) |
| `state-file-no-step` | `step start … --at … --model … --report …`, runnable |
| `tag-not-promoted` | not a command: see whether it was deployed (`scripts/deploy_tag.sh status`), commit the promote file, then `promote ITEM --tag item-ITEM` with `--deployed yes` or `--deployed no` |
| `tag-gone` | `git tag <tag> <the row's commit>`; not a command when the row has none |
| `promoted-no-row` | not a command (only a hand edit or an import fault makes it) |
| `criteria-not-committed` | not a command: write and commit the file, then `step add ITEM criteria` |
| `file-no-item` | `add --id ITEM --title '<the file's title>'`; not a command when the id is below the next number (`add --id` refuses it) or the file has no title |
| `file-no-step` | `step add …`, runnable |
| `no-shipped-line` | not a command: add the ROADMAP Shipped line, then `check` |
| `queue-numbering` | `move <first item free to move> <its place>`, which renumbers the queue 1 to n; not a command when every item is blocked |
| `after-dropped` | `after ITEM --clear` |
| `dump-missing`, `dump-stale` | `dump` |
| `git`, `herdr`, `repo` | not a command: what to look at, then `check` |

`step note-gone ITEM KIND [--round N] [--name X]` refuses a report that is
in HEAD, finds the commit in HEAD's history that deleted the file (`git log
-1 --diff-filter=D HEAD -- <report>`), refuses when there is none, and
appends `report deleted in <sha>` to the step's note. The row keeps its
commit and verdict. `check` skips `report-gone` for a step so noted when
that commit is in HEAD's history. A second run writes nothing.

Tests: `test_s5_every_check_kind_has_a_fix_line` (fifteen kinds made in
one scratch database; every fix line is a command or marked; no `<`, `>`,
`|`, `...` or `  (` in a command; then ten of the runnable ones are run as
printed, in a subprocess from the repo root, and each clears its own
finding), `test_s5_a_deleted_report_is_noted_with_the_commit_that_deleted_it`,
`test_s5_fix_lines_name_no_db_or_repo_when_the_defaults_find_them`. The
Appendix C tests (`test_c1_…`, `test_c2_…`, `test_c3_…`, `test_c4_…`,
`test_a_committed_step_file_with_no_row_is_named`) now run their fix
lines as printed (`run_fix`: `subprocess.run(shlex.split(fix), cwd=REPO)`,
no shell), not as parsed arguments with the placeholders replaced.

### S6. `restore-dump` refuses a dump that loads no schema

After `executescript`, a connection still in a transaction means the dump
ended before its `COMMIT` (cut at a statement boundary): refused, `dump
<path> ends inside its transaction (no COMMIT: it is cut); not restored`.
Before the temporary file is moved in, it is opened again as a reader
would (`Read`: meta table, schema version, the seven tables); a failure
there is refused with the dump named. A refusal removes the temporary
file and any directory the command made; no 0-byte `work.db` is left.

Test: `test_s6_restore_dump_refuses_a_dump_cut_at_a_statement_boundary`
(the e2e's three cuts: no `COMMIT`; no triggers and no `COMMIT`; from the
last step row on). `test_restore_dump_refuses_a_dump_that_does_not_load`
still passes.

### S7. A dump that cannot be written is said at the write

`Tx.__exit__`, after the commit, prints one line on stderr when the dump
failed: `work.py: the change is committed, but the dump <path> was not
written (<error>); \`python3 scripts/work.py dump\` writes it again`. Exit
0. From `agent.py` the line starts `agent.py`.

Test: `test_s7_a_dump_that_cannot_be_written_is_said_on_stderr_and_the_write_stands`
(a directory where the dump goes).

### S8. A step whose commit is not in HEAD's history is not counted for stage

`counted_steps()` drops a finished step whose `commit_sha` is not in `git
rev-list HEAD` (when git answered; when it did not, every step counts, as
before). `resume`, `show` and `--compare` pass HEAD's commits. `check`'s
finding now ends "not counted for the stage". The "last step" line of
`resume` still shows the row as recorded.

Test: `test_s8_a_step_off_the_branch_is_not_counted_for_the_stage` (a
build committed on a side branch: `waiting … build in` on the branch,
`planning (criteria written)` back on main, in `resume` and `show`).

### S9. `matter void <id> --why`

Strikes a matter entered by mistake, open or closed. The row stays:
`closed_at` (kept if it was closed), `closed_how` = `void: <why>`. The log
row keeps the old values. A void matter is not open and not counted as an
escape; a closed escape still counts. `matter close --how` refuses text
starting `void:`, so only `void` makes one. Voiding twice is refused. The
feature doc says all of this (Gotchas, "A closed escape still counts").

Test: `test_s9_a_void_matter_is_struck_kept_in_the_log_and_not_counted`.

### S10. Imported promotion times

One `git log -p --reverse` over PROMOTIONS.md as of the import commit gives,
for each line text, the first commit that added it. An entry's time is that
commit's time when the commit falls (local day) on the date the entry
gives; else that date at 12:00 local; else (no date written) the commit's
time, else the import commit's. It is never before the first step of any
item the entry promotes. `promotion.at` and the item's `ended_at` both
take it (`promotion.at` held the written date string before).

My reading of "the promotion commit's time where the file's first commit
gives one": the file is PROMOTIONS.md and the commit is the first one that
added the entry's line. A line edited later (a cell filled in) is found
at its edit, not its first writing; when that edit is on another day the
written date at noon is used instead. The coordinator may read it
otherwise.

At the real import: no promoted item ends before it began; none ends at
`T00:00:00Z`; M28 ends 2026-11-25T03:35:48Z and M31 03:55:26Z (both
created 02:2x).

Tests: `test_s10_an_imported_promotion_is_timed_by_the_commit_that_added_it`
(dated commits: the commit's time, noon of the written date, the first
step's time), and the real-import test now asserts no `ended_at <
created_at` and no midnight.

### N1. `close` on an item not active inserts no step

`step_stop`'s insert (no row yet) refuses an item that is not active, as
`step start` does; `agent_closed` with the report committed calls
`step_finish` with `insert_only_active`. Both say `<item> is queued, not
active; no step written`, which `agent.py` prints as its usual `work state
not recorded (…)` line. A step that exists is finished or stopped as
before.

Test: `test_m33_close_on_an_item_not_active_writes_no_step_and_says_so`
(in `tests/test_m28_agent_tool.py`, report committed and not).

### N4. A refused import leaves no rowless database

When `import-legacy` made the file, a refusal afterwards removes it, its
`-wal`, `-shm` and dump, and the directories it made (when empty). Into a
database that was already there (rowless), a refusal leaves it as it was.

Tests: `test_n4_an_import_refused_inside_its_transaction_leaves_nothing`
(a seed tag git lacks, refused inside the transaction; then the right
seed imports at once), `test_n4_a_refused_import_into_an_existing_empty_database_keeps_it`,
and each S4 case.

### N7. `item-<id>` filled in `resume`

`item_stage()` replaces `item-<id>` with the item's tag name in the
detail: `promoting (tag item-M1 exists)`. `--compare` keeps the
placeholder, since `work.json` carries it.

Test: `test_n7_resume_names_the_tag`.

## Changed tests

- `test_the_coordinators_seed_loads`: 11 items, 44 matters (the
  coordinator's seed change).
- `test_the_real_import_at_the_build_start` (at `4eee8fd` with today's
  seed): 37 items, 68 matters, 44 triaged; adds M34 queued 2 after M33
  and M32 3 after M34, `notes` empty, and the S10 assertions.
- `test_every_write_rewrites_the_dump_private_and_restore_dump_rebuilds_it`:
  `resume` of the restored copy now differs from the original's in the
  database path inside fix lines (they carry `--db`), so both are
  compared with the path replaced.
- `tests/test_m16_backups.py::test_m33_restored_from_the_tar_the_work_database_passes_and_resumes_the_same`:
  the same, for the same reason (found by the first full run: `1 failed,
  7957 passed`).

## The docs

`docs/features/work-state.md`: the two new commands in the table; a
paragraph on one-line text; the stage paragraph (grading, off-branch
steps, no step, the tag named); restore-dump and the dump's stderr line; a
"Fix lines" bullet; the seed's refusals and promotion times in the import
bullet; Gotchas: the grading gotcha rewritten (the stage now follows the
grading; `/work` does not until M34), a closed escape still counts, and
a launch or close on an item not active writes no step. The step-2 text
now names M34.

## Seen, not changed (not in the ruling)

- `status` and `wait` on an agent of an item that is not active still
  insert a finished step when the report is committed (`step_finish`'s
  insert refuses only a dropped item). N1 covered `close`; the same rule
  would fit these. Said in the feature doc.
- Refusal messages on stderr can carry stored text raw: `start` on a held
  item prints `held_why` (`work.py:954`). Written by `hold`, which now
  refuses a newline, so only an older or hand-edited row can carry one.
- A matter's `closed_how` from `matter close --how` and a promotion's
  `--untested` are not checked for newlines (not in the ruling's list);
  neither is printed by `resume`.
- U+202E and the other bidi controls (category Cf) are not escaped; a
  terminal does not act on them as it does on C0 and C1.
- N2, N3, N5, N6: left over, as ruled.

## The trial import

TRIAL

## The suite

SUITE
