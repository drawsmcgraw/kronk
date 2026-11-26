# M34 build: /work drawn from the work-state database

Build agent m34-build, 2026-11-26, from `ef298a9` (criteria revised after
the review). The section "Revised after the review" of `M34-criteria.md`
was built as written; where this report says "criterion N" it means that
section's list.

## The three commits

| Commit | Holds |
| --- | --- |
| `2545a55` M34 build 1 | `scripts/work.py`: `overview(g)`, one read-only function (no database, no print, no git, no herdr) returning per active item its stage, agents, started steps and last step; the queue as `queue_rows()` gives it; the decisions owed to Drew oldest first and the others; open leftovers by owner and counts by item and owner; the escape window and its escapes; and the stage of every item. `c_resume` prints from it. No writing command and no `connect()` changed. `tests/test_m34_work_db.py` (three tests of `overview()`). |
| `86584fe` M34 build 2 | `scripts/work_export.py`: the live `collect(repo, db)` reads `<db>.dump.sql` into an in-memory database, loads `work.py` inside `collect()` (never at import; the loaded `work.py` is handed this module, so it is not parsed twice) and serialises `overview()`. Schema 2. The old reader is `collect_legacy()`, marked import-only, with the parsers it feeds. Tests: M27's in-process tests call `collect_legacy()`; its subprocess tests and its "real tree" tests read a scratch database made by `work.py import-legacy --at HEAD` from their own tree; `test_m33_work_state.py`'s compare test calls `collect_legacy()` and honours `M27_WORK_EXPORT`; the M34 exporter tests; `test_m30_heard.py`'s `test_c2_work_api_work_export_unchanged_since_base` retired by name. |
| `48e5690` M34 build 3 | `orchestrator/static/work.html` outside "Heard"; `orchestrator/work_api.py` `SCHEMA_KNOWN = 2` and its docstring; `work.py promote` with no item (`--label`, `--verified PATH`, `--at`; `--at` also for an item); `docs/features/work-page.md`, `docs/features/work-state.md`, README's timer line, the reference unit files' comments (command line unchanged); `test_m27_work_page.py` for schema 2; `test_m30_heard.py`'s `test_c2_api_work_byte_for_byte_what_it_was` re-scoped (see "Tests changed"); the route, page and no-item promotion tests. |

Not done here, as the brief says: the chat-buttons deploy is not
recorded, `PROMOTIONS.md`, `WORKFLOW.md`, `CLAUDE.md` and `ROADMAP.md` are
not edited, no unit installed, nothing deployed or tagged.

## The `resume` comparison (criterion 9)

The copy: `sqlite3` backup API from a connection opened `mode=ro` on the
live `work.db`, into scratch, at 04:03 EST. As the review said it would
(point 4), that open left `work.db-shm` (32768 bytes) and an empty
`work.db-wal` in the live directory; harmless, the next `work.py` write
that closes last removes them. Nothing else of this build opened the live
database; the timer's exporter reads the live dump since `86584fe`, as
intended.

Old and new `work.py` side by side in scratch (each in its own
`scripts/` with the exporter of `ef298a9` beside it), the same copy, the
same repo, `KRONK_AGENT_STATE` an empty directory so "min since launch"
does not tick between runs:

```
== 1      (herdr reachable)
byte-equal but for line 1 (100 lines, 10312 bytes)
== 2      (again)
byte-equal but for line 1 (100 lines, 10312 bytes)
== noherdr (PATH=/usr/bin:/bin)
byte-equal but for line 1 (102 lines, 10735 bytes)
```

(`diff` printed nothing at all: line 1 fell in the same second.) In the
tree, `python3 scripts/work.py --db <copy> resume` before the commit and
after it:

```
1c1
< Work state: …/scratchpad/db/work.db (schema 1), at 2026-11-26T09:05:27Z
---
> Work state: …/scratchpad/db/work.db (schema 1), at 2026-11-26T09:10:07Z
86c86
< == Tree: branch main, HEAD ef298a9, clean
---
> == Tree: branch main, HEAD 2545a55, clean
```

The second difference is the commit itself. The M33 suite passed before
the commit (`137 passed` with the new tests), and the whole suite:
`8403 passed, 3 skipped, 61 xfailed`.

## Each criterion and its test

All in `tests/test_m34_work_db.py` unless named.

1. **The file agrees with `resume`, every promotion carries its tag.**
   `disagreements(data, resume_text)` compares under way and each stage,
   the queue in order with `next`, `blocked_by` and `held_why`, the
   decisions owed, the open leftovers by owner, the escapes. Used in
   `test_criterion_3_…` after every step, `test_criterion_1_every_promotion_carries_its_tag`,
   and the no-item test. On the live copy (same recorded herdr answer):
   `disagreements: []`; under way M34 "implementing (implementing, agent
   m34-build working)"; queue M32, `blocked_by` "M32 follows M34, which
   is active, not promoted"; 11 decisions owed; leftovers drew 11,
   coordinator 32, not triaged 24; escapes 0; 36 item promotions, 34
   with a tag, every one carried; M6 and M16b have none in the rows
   either; M28, M31, M33, M35, M30 each show `item-<id>`.
2. **Old and new over the same commit, copy and herdr answer.**
   `differences(old, new)` names a class for every difference;
   `test_criterion_2_…` runs it on this repo's HEAD (the old reader of the
   files against a database imported from the same files) and fails on
   any "UNCLASSIFIED" or "GONE". On the live copy (`ef298a9`'s exporter
   against `86584fe`'s, commit `2545a55`, the recorded herdr answer):

   | n | class | keys |
   | ---: | --- | --- |
   | 2103 | new key | the new keys below |
   | 333 | a key with no source left | `done[].entry`, `for_drew`, `for_drew_total`, `heading`, `heading_adds`, `kind`, `tag_written`; `steps[].title` |
   | 160 | a time's format (UTC) | `steps[].committed_at` (same instant) |
   | 66 | no source left (the verdict's line and form; a second subject) | `steps[].verdicts` (same verdict word) |
   | 38 | a title (the row's, not the file's heading) | `items[].title`, `short_title`, `shipped[].adds` |
   | 24 | a time's source (the step's end, not the file's first commit) | `steps[].committed_at` |
   | 14 | a time's format and source (the house's day of the row's time) | `done[].date` |
   | 5 | fixed: a tag the parser missed | `done[].tag` of M28, M31, M33, M35, M30 |
   | 3 | the run | `schema`, `ts`, `run_s` |
   | 3 | a `-promote.md` step | M30, M33, M35 |
   | 3 | fixed: the row's kind and round | M30 `build2`, `build3`, `build4` were "other"; the rows say build rounds 2 to 4 |
   | 2 | no source left (a follow-up's heading) | `done[].notes` (now "follow-up, <date>") |
   | 2 | fixed: deployed as the row has it | `done[].tag_only` of M28, M33 |
   | 2 | no source left (checks in a `-promote.md` file) | `done[].line` of M30, M35 |
   | 1 | fixed: under way is the active items | M32 leaves `under_way` |
   | 1 | `not_read`: the step-file lines end | |
   | 1 | new: an item the rows hold and no file names | M29 (dropped) |
   | 1 | new: a step the rows hold and no committed file names | M34's started build |
   | 1 | step order (by the step's time) | M35 |
   | 1 | a commit (the report's last, not its first) | M35 criteria |
   | 1 | row order within one promotion (by id) | M3, M4, M4b, M1b |

   Nothing unclassified. The script that made this table is the test
   module's `differences()`, run on the two files.
3. **An item walked through its life.** `test_criterion_3_an_item_walked_through_its_life`:
   five items added (one queued behind another), M1 started, criteria,
   a build started under a fake herdr agent, finished, a test started and
   FAILS graded fail, a fix, test 2 PASSES, M2 dropped, M3 held, M4
   waiting on Drew, M1 promoted with a tag. After each step the file
   agrees with `resume` and the state, position, next, wait, hold and
   `blocked_by` fields are as the rows have them. `resume` is the
   yardstick, not `show` (review point 3).
4. **The browser** is the test agent's. What I could check without one is
   under "What a browser is still needed for".
5. **No lock, nothing beside the dump, fast, never in the way.**
   `test_m27_work_export.py::test_under_strace_no_lock_no_other_write_no_socket`
   now also asserts that the only open in the database's directory is
   the dump, `O_RDONLY`; `test_the_exporter_takes_no_write_lock_and_opens_only_the_dump`
   (an exclusive lock held throughout, no file in the directory changed);
   `test_git_reads_are_three_lock_free_commands` (`rev-parse`,
   `for-each-ref`, `rev-list`, each `--no-optional-locks`);
   `test_a_run_on_the_real_tree_takes_under_half_a_second`;
   `test_twenty_exports_beside_fifty_writes_delay_no_write_by_50_ms`;
   `test_two_exports_at_once_leave_one_whole_file`. On the live copy under
   strace: one open in the directory, `work.db.dump.sql`, `O_RDONLY`; no
   `.lock`; no socket; the only file made is the output's temp file.
   `run_s` 0.051, 0.049, 0.048, 0.050, 0.051; wall 0.09 s.
6. **Exactly the allowed columns.** `test_criterion_6_a_marker_in_every_text_column`:
   rows in every state, then a distinct marker in every free text column
   of every row that has a value (and a log row of markers); the set of
   markers in the file must equal the set allowed, row by row (a next step
   only for active, queued and held items; `held_why` only when held; a
   promotion's note only with no item; `opened_at` only of an open
   decision owed by Drew). The free columns are read from the schema, so
   a column added later fails the test until it is classed. No decision's,
   leftover's or escape's text is in the file.
7. **The route.** `test_criterion_7_the_route_knows_schema_2_and_reads_no_database`
   (no `sqlite3` import, no string naming a dump, a `.db` or the state
   directory; schema 1 and 2 get no note, 3 does), and
   `test_m30_heard.py::test_c2_api_work_byte_for_byte_what_it_was`, which
   now asserts the answer equals the M30 base route's but for
   `schema_known` and, for a schema-2 file, the note.
8. **A dump that cannot be used.** `test_criterion_8_a_dump_that_cannot_be_used`,
   for missing, empty, not SQL, cut short, no tables, not text, newer,
   other repo: exit 0, nothing on stderr, `database.state` as named, its
   sentence also in `errors`, no item, queue, Done or decisions, the
   ROADMAP entry still there, no path of the state directory, no other
   repo's path and no SQLite text in the file. `test_an_ok_dump_says_so_with_its_age`.
9. **`resume` byte-equal; the M33 suite passes.** Above.
10. **The suite reads neither the live database nor its dump.**
    `test_the_default_database_in_the_suite_is_the_scratch_one`;
    `test_a_run_with_the_default_database_never_opens_the_live_directory`
    (strace: the live state directory is never named). M27's five
    "real tree" tests read a scratch import of the tree. The conftest
    guard did not fire in any run.
11. The coordinator's.

Plumbing 7 (`promote` with no item): `test_promote_with_no_item_takes_a_label_the_checks_and_a_time`
(the refusals; the row; no `promotion_item`; `check` names nothing for
it; the file lists it under `promotions_no_item`, never in `done`) and
`test_an_items_promotion_takes_at_too`. Page: `test_the_page_has_a_word_for_every_database_state`,
`test_the_page_reads_the_new_keys_and_sets_them_as_text`,
`test_heard_is_untouched_since_before_the_build` ("Heard"'s CSS, markup
and script byte-equal to `ef298a9`'s; `voice_runs_api.py` unchanged).

## Schema-1 keys that now carry an empty value

- `items[].uncommitted`: `[]`.
- `items[].steps[].title`: `null`.
- `items[].steps[].verdicts[].written`, `.line`: `null`; one entry, subject
  `item`. M4b's second subject (RESTORE) is prose in the row's note and
  not exported.
- `done[].heading`: `null`; `done[].heading_adds`: `false`.
- `done[].tag_written`: `null`.
- `done[].for_drew`, `done[].for_drew_total`, `done[].notes[].for_drew`: `0`.
- `done[].kind`: `null` (was "row" or "heading").
- `done[].entry`: `null` (was an index into the parsed file; the promotion
  id is the new key `promotion`, not this one, so no key changed meaning).
- `done[].line`: `null` for a promotion whose checks are a `-promote.md`
  file (M30, M35); the line number for the 33 imported rows.
- `done[].notes[].heading`: no heading exists; the exporter writes
  "follow-up, <date>".

Changed by plumbing 2, not emptied: `items[].title` and `short_title` are
the row's title; `done[].date` is the house's day of the promotion's time
(`done[].at` is the UTC time); `steps[].committed_at` is the row's
`ended_at` (null for a started or stopped step); `steps[].commit` is the
report's last commit (the row's), not its first.

## New keys

- Per item: `state`, `tier`, `position`, `after_item`, `blocked_by`,
  `next`, `next_step`, `waits_on`, `waits_note` (these five for active,
  queued and held items only), `held_why` (held only), `source`, `open`
  (`{"leftovers"|"decisions": {"drew", "coordinator", "not_triaged"}}`).
- Per step: `round`, `state`, `by`, `agent`, `started_at`, `ended_at`,
  `verdict`, `graded`.
- Per Done row: `at`, `deployed` ("yes", "no", "not recorded"), `verified`,
  `promotion`, `followup`; per note `at`, `verified`.
- Top level: `database` (`state`, `says`, `schema`, `dump_at`,
  `dump_age_s`), `active`, `queue`, `decisions_owed` (`id`, `item`,
  `date`, `opened_at`, `blocks`; no text), `open_counts`, `escapes`
  (`window`, `count`), `promotions_no_item` (as a Done row's promotion
  keys plus `label`).

## What the deployed page (`item-M30`) shows now

Production's `/api/work` has served the schema-2 file since 04:31:02 EST,
with "work.json has schema 2, newer than this page's 1: keys added since
are not shown". On that page:

- Under way: M34 only. M32 leaves it and is nowhere until the new page is
  deployed (no queue on the old page).
- M34's steps: criteria, review, and the started build, which reads
  "written, not committed" (it has no `committed_at`).
- Done: the five tags (M28, M31, M33, M35, M30) shown; M28 and M33 read
  "(tag only)" as their rows say; dates are the house's day, never "as
  written"; no promotions heading under a row, no "N lines addressed to
  Drew"; follow-up notes read "Note: follow-up, <date>"; the three
  `-promote.md` steps are gone; M30's builds 2 to 4 read "build".
- Titles are the rows' wording, so M30 and M35 no longer show their
  criteria files' headings.
- "Not read" says "nothing".
- A database the exporter cannot read reaches the old page as a line
  under the state line (the sentence is in `errors`).

## What a browser is still needed for

`gjs` (SpiderMonkey) is on the host; node is not, and snap Firefox cannot
reach the scratch directory. So I ran the script part of both pages
(`work.html` at `48e5690`, and `item-M30`'s), up to the "Heard" marker,
under `gjs` against a stub DOM (text nodes only) and a canned `/api/work`
answer, for: the live copy; a fixture with a held item, a queued item with
no criteria file, a dropped item, the no-item deploy, an agent no item
owns, markup in a title and a next step; each of missing, empty, corrupt,
newer, other repo; a schema-1 file; and the live copy stale. All eighteen
ran without an exception and wrote no bare "undefined", "null", "NaN" or
"[object" (ROADMAP's own `2>/dev/null` aside). That is a check of the
code paths, not of a page. Criterion 4 still needs the real thing: layout
at 1280 px, the classes and colours, the queue section shown and hidden,
the banners' look, a fold kept across the 30 s refresh, "Heard" loading,
and that markup stays text in a real DOM.

## The dev check

After the suite: `docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev` (orchestrator-dev recreated;
tool_service-dev unchanged, not recreated; nginx untouched).
`http://localhost/dev/work`: 200, serving the new page.
`http://localhost/dev/api/work`: `fresh`, `schema` 2, `schema_known` 2,
`schema_note` null, `database.state` "ok", under way M34, queue M32, 11
decisions owed. The timer's run at 04:31:02 wrote `data/work.json` and
`data/dev/work.json` at schema 2 (`journalctl --user -u
kronk-work-export.service -n 3`: "Finished" each minute since).
`python3 scripts/agent.py list` answered straight after the move.

## Tests changed, by name

- Retired: `tests/test_m30_heard.py::test_c2_work_api_work_export_unchanged_since_base`.
- Re-scoped (not named in the brief; `schema_known` 2 changes the answer
  it pinned byte for byte): `tests/test_m30_heard.py::test_c2_api_work_byte_for_byte_what_it_was`
  now compares the parsed answers and allows exactly `schema_known` and
  the schema-2 note.
- `tests/test_m27_work_page.py`: `test_fresh` (`schema_known` 2),
  `test_unknown_schema` ("reads schema 2"), `test_newer_schema_is_read_with_a_note`
  (schema 3 against 2).
- `tests/test_m27_work_export.py`: as in the commit table; the five
  "real tree" tests rewritten for a scratch `--db`.

## For the coordinator

- The chat-buttons deploy, by the new form: `python3 scripts/work.py
  promote --label "Chat page buttons" --tag chat-buttons-2026-11-25
  --deployed yes --verified docs/sprint/PROMOTIONS.md --at
  2026-11-25T22:19:00-05:00` (tried on a scratch copy: the row, no item,
  shown under Done between M35 and M33).
- M34's own row has `criteria = NULL`; the exporter falls back to its
  criteria step's report, so its handoff shows. `work.py note M34
  --criteria docs/sprint/M34-criteria.md` would make the row say so too.
- One thing done not as the brief's order has it: in step 3 I edited
  `scripts/work.py` in place, not by rename from scratch, before I
  remembered that the timer's exporter now loads it. It was checked to
  load at once (`queue` and an export on the copy) and no run failed
  (the journal shows every run finished).

## The suite

Before commit 1: `8403 passed, 3 skipped, 61 xfailed, 6 warnings in 209.30s`.
Against the scratch pair before the move: `8425 passed, 3 skipped, 61
xfailed in 214.94s`. In the tree before commit 2: `8425 passed, 3 skipped,
61 xfailed in 215.57s`. Before commit 3:

```
8431 passed, 3 skipped, 61 xfailed in 216.13s (0:03:36)
```
