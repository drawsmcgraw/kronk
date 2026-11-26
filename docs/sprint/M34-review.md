# M34 design review: /work drawn from the work-state database

Reviewer: m34-review, 2026-11-26, against HEAD `79f6d00`. Nothing was
built or changed but this file. Production's `/api/work` was read once
(03:52 EST); the live database was copied to scratch once with the
`sqlite3` backup API opened `mode=ro`, and `scripts/work.py` was run only
with `--db` on that copy. One side effect of the copy is reported in
point 4.

## Verdict

**Build with these changes.** The direction is right and the item is one
build. Six things should change before the brief is written:

1. The exporter should read the dump (`work.db.dump.sql`), not the
   database. A `mode=ro` reader on a 60 s timer changes `work.db-shm` and
   recreates `work.db-wal`, and the suite fails any run in which those
   files changed (point 4).
2. Keys of `work.json` are only added. Nothing is removed or repurposed;
   a key with no source left carries `null`, `[]` or `0`. The deployed
   page reads the new file from the minute of the move (point 2 and the
   table).
3. A promotion with no item goes in a new top-level key, not in `done`
   (point 6).
4. `work.py` gains one read-only function that both `resume` and the
   exporter call; no writing command and no `connect()` change. The
   exporter loads `work.py` lazily. The old parsers stay in
   `work_export.py`: `work.py` runs on them (point 3).
5. The frozen note on PROMOTIONS.md goes at the end of the file, not the
   top: 57 rows point into it by line number (point 7).
6. Criteria 1, 3, 4, 5, 7 and 9 cannot be checked as written, and four
   are missing (point 9).

## 1. The findings (question 1)

Looked at: `GET http://localhost/api/work` (file made 03:51:43 EST,
schema 1, fresh), and `work.py --db <copy> resume` plus the copy's rows.

Two of the criteria file's findings were true when seen and are not now.
M30 no longer shows "promoting" (its PROMOTIONS entry was committed at
03:50:03, `d246fd4`) and M34 is on the page (its criteria file was
committed at 03:51:32, `79f6d00`). Both healed because a file was
committed, which is the fault in one line: the page is wrong in the
window between a tag and its PROMOTIONS commit, and before a criteria
file exists. Where the page and the database disagree at 03:52:

| # | Page (`work.json`) | Database |
| --- | --- | --- |
| 1 | Under way: M32 ("planning, criteria written") and M34 | Under way: M34 only. M32 is queued, position 1, `after_item` M34 |
| 2 | No queue, no order, no "after" | Queue of one; M32 blocked by M34 |
| 3 | M30, M35, M33, M31, M28: `tag: null`, `tag_in_git: null` (five, not four: M30's entry also names its tag mid-sentence) | `item-M30`, `item-M35`, `item-M33`, `item-M31`, `item-M28`, each listed by git |
| 4 | "(tag only)" on M26 alone | `deployed = 0` on M26, M28 and M33; `1` on six; not recorded (`NULL`) on 24 imported rows |
| 5 | M30, M33 and M35 each list a `-promote.md` file as a step, and all three are under "Not read" | No such steps; M30's and M35's files are the promotion's `verified` path; nothing points at `M33-promote.md` |
| 6 | M34 has one step (criteria) | Two: criteria finished, review started (agent `m34-review`) |
| 7 | Titles of M30 and M35 are the criteria files' heading lines | The coordinator's own wording in the row |
| 8 | M29 absent (its file was deleted) | M29 dropped, with why |
| 9 | "N lines addressed to Drew": 18 in all, a count of words in a log | 11 open decisions owed by Drew, 11 open leftovers owned by Drew, 32 by the coordinator, 24 not triaged; 0 escapes |
| 10 | Chat-buttons deploy absent | Absent too: no promotion row has zero items. PROMOTIONS.md:459 is its only record |

Agreeing: 36 Done rows against 33 promotions and 2 follow-ups (the same
item-promotion pairs), M6 and M16b with no tag in both, M34's stage
("reviewing, agent m34-review working").

Three corrections to the criteria file's own text:

- The timer fires every **60 s**, not 30 (`systemd/kronk-work-export.timer`,
  `OnUnitActiveSec=60`; `work_api.py`, `WORK_EVERY_S = 60`). 30 s is the
  page's own refresh.
- "M32 marked as waiting on M34" (criterion 1): M32's `waits_on` is
  `NULL`. What holds it is `after_item`; `queue_rows()` gives it as
  `blocked_by`. Name that field.
- M34's own row has `criteria = NULL` (so has M6's). Plumbing 3 reads the
  handoff line through that path, so the item under way would show no
  handoff. M34 was seeded before its file existed; `add` fills the
  default, the import does not. Fall back to the finished criteria step's
  `report`, then to `docs/sprint/<id>-criteria.md`, or set the two rows
  with `work.py note --criteria`.

Not a disagreement, but it will show: M30 and M35 are promoted and still
carry `next_step` ("… then promote …"). The exporter should write
`next_step` and the wait fields for active, queued and held items only.

## 2. Keys the page reads (question 2)

Looked at: every `w.`, `it.`, `s.`, `r.`, `e.`, `a.`, `n.`, `v.` read in
`work.html` outside the "Heard" block, and `collect()` in
`work_export.py`. The table is at the end.

Findings from it:

- **Nothing needs to go.** Every key the page reads either has a source
  or has a harmless empty value. The exporter's own header says why this
  matters: "add keys, never rename or repurpose one"
  (`work_export.py:32`). Production's page (from `item-M30`) reads the new
  file within a minute of the move and until `item-M34` is deployed, and
  again after any roll back to an older tag. Plumbing 4's "listed by name
  as gone" should read "kept, empty, and listed by name".
- **`done[].date` is printed as written** (`work.html:490`). A promotion's
  `at` is UTC ISO. Written into `date`, the deployed page prints
  `2026-11-26T08:50:03Z`, and a UTC date is the wrong day in this house
  after 19:00. Keep `date` as the house's local date as text
  (`local_day()` exists in `work.py`), and add the ISO time under a new
  key. For the 33 imported rows `at` is derived (the commit that added
  the line, or noon), so only the date is true; say so in the feature
  doc.
- **`steps[].committed_at`** is shown as the step's time. The row has
  `ended_at`: the commit's date for imported steps, the finish time for
  agent steps. Fill `committed_at` from it, or the deployed page prints
  "written, not committed" on every step.
- **`steps[].verdicts`** keeps its list shape with one entry, `subject:
  "item"`. A second subject survives only as prose in `step.note` (one
  row: M4b). That one loses its coloured second verdict.
- **`items[].title`** changes meaning: it was the file's whole heading
  ("M34 pass criteria: …"), it becomes the row's title. `short_title`
  equals it. Fixed, not gone.
- **`under_way`** becomes the active items. On the deployed page M32
  leaves "Under way" at the move and is nowhere until the new page is
  deployed. Right by the database; say so in the promotion note.
- **`not_read`** loses its eleven step-file lines. What is left: a
  criteria file that could not be read, an unknown ROADMAP section.
- **`schema_known`**: `work_api.py` must go to 2, or the new page shows
  "work.json has schema 2, newer than this page's 1: keys added since are
  not shown" for good. That is a change to the envelope (criterion 7).

New keys M32 needs that plumbing 4 does not list: per step its `state`
(`started`, `finished`, `stopped`), `agent`, `started_at`, `ended_at` and
`by` (M32 plumbing 5: "the current step pulsing", "the agent's elapsed
time counting up"; plumbing 7: "its steps with agents, verdicts and
times"); per item the `Source:` paragraph (`criteria_head()` in `work.py`
already reads it; it is new on the LAN with this item, covered by M32
plumbing 11); per promotion `deployed` as yes, no or not recorded.

## 3. Reusing `work.py` (question 3)

Looked at: `work.py` `item_stage`, `counted_steps`, `stage_steps`,
`queue_rows`, `after_blocks`, `criteria_head`, `tag_commits`, `gather`,
`c_resume`, `c_show`, `connect`, `Read`, `work_export()`, `norm()`.

- **The dependency already runs the other way.** `work.py` loads
  `work_export.py` (`work.py:193`) for `stage()`, `verdicts()`,
  `step_rank`, `norm_id`, `id_key`, `agent_for`, `git()`, `git_head`,
  `herdr_agents`, `read_head`, `handoff_of`, `parse_roadmap`,
  `resolve_links`, `FILE_RE`, `step_kind`, `title_of`, `short_title`,
  and, for `import-legacy`, `scan_items` and `parse_promotions`.
  `agent.py` loads it too (`agent.py:331`). `norm()` calls it, so **every
  `work.py` command that takes an item id loads `work_export.py`**. Three
  consequences:
  - "The exporter computes no stage of its own" (plumbing 2) is true only
    of the wrapper. `stage()` lives in `work_export.py` and `item_stage()`
    wraps it. Leave it there; say so.
  - The exporter must load `work.py` inside `collect()`, never at import.
    A top-level import makes every `work.py` command parse `work.py` a
    second time under another name, and makes a fault in the new code a
    fault in `work.py start`, `step finish` and `agent.py`.
  - Criterion 9's "the old parser's code removed" would break
    `import-legacy`, `check` and the 63 tests of
    `tests/test_m27_work_export.py`. Only "marked import-only" is open,
    and only for `git_first_commits`, `scan_items`, `parse_promotions`,
    `tag_from_cell`, `bare_word` and the `collect()` they feed.
- **What imports cleanly as it stands.** `item_stage`, `counted_steps`,
  `stage_steps`, `criteria_head`, `tag_commits`, `local_day`, `is_void`:
  no database, no print, no exit; they raise `WorkError`. Loading
  `work.py` has no side effect and takes 10 ms.
- **What does not.** `queue_rows(r)` wants a `Read`, and `Read` opens the
  file read-write (`connect()`, no `mode=ro`); closed last, a read-write
  connection checkpoints the WAL into `work.db`. The stage with agents
  (`stage_of`), the agents per item, the decisions owed in order and the
  escape count are closures and inline code inside `c_resume`
  (`work.py:1817` to `1917`). `gather()` cannot be used: it runs
  `quick_check`, `git status`, `rev-list`, `ls-tree`, a `cat-file` per
  test report and reads the agent state files.
- **`work.py show` is the wrong yardstick** (criterion 3). `c_show`
  passes `agents=None` (`work.py:913`), so for "a build step running
  under a recorded herdr agent" it says "planning" where `resume` says
  "implementing". Compare with `resume`.
- **One more git read.** `item_stage` does not count a step whose commit
  is off HEAD's history, from `git rev-list HEAD`. Plumbing 1 allows two
  git reads; with two the exporter passes `commits=None` and can
  disagree with `resume` after an amend or a rebase. `rev-list HEAD` is
  lock-free and takes 9 ms here (608 commits). Allow it as a third.

**Smallest change.** One new function in `work.py`, read-only, taking the
rows and the outside facts (tags, HEAD's commits, herdr's answer) and
returning plain data: per active item its stage and agents, the queue
with `blocked_by` and `next`, the decisions owed oldest first, leftover
counts by item and owner, the escapes in the window. `c_resume` prints
from it; the exporter serialises it. `queue_rows` needs only `.one` and
`.all`, so the exporter can hand it any object with those two methods; no
change to `connect()` or `Read`.

**What it risks.** `work.py` is the only writer and the first command of
every session. The change touches no writing command, but it rewrites
the middle of `resume`. Guard: `resume` on a copy of the live database,
before and after, is byte-equal but for its first line (the time); the
M33 suite; and the change lands as its own commit before the exporter
moves, so a fault in one is not read as a fault in the other.

## 4. Reading the live database on a timer (question 4)

Looked at: the live directory before and after my copy; SQLite 3.46.1;
experiments in scratch on a WAL copy; `tests/conftest.py:30` to `46` and
`:370`; `scripts/backups.py:793`; `work.py` `Tx`, `write_dump`,
`c_restore_dump`.

The database is WAL. Measured in scratch:

- A `mode=ro` reader holding a snapshot does not delay a writer
  (`BEGIN IMMEDIATE` … `COMMIT` in 0.1 ms with the reader open), and a
  reader opening while a writer holds the write lock reads at once. The
  reader keeps its snapshot. For data, `mode=ro` is enough.
- **A `mode=ro` reader writes two files.** With `work.db-wal` and
  `work.db-shm` absent (the state `work.py` leaves, being the last to
  close), opening and reading creates both and leaves them. With them
  present, each read changes `work.db-shm`'s mtime. With the directory
  not writable and no `-shm`, the read fails ("attempt to write a
  readonly database"). **My own copy did this to the live directory**:
  `work.db-shm` (32768 bytes) and `work.db-wal` (0 bytes), dated 03:52,
  were not there at 03:51. They are harmless and the next `work.py` write
  that closes last removes them.
- **The suite fails on exactly that.** `conftest.py` records mtime and
  size of the live `work.db`, `-wal`, `-shm` and dump at start and fails
  the session if any differ at the end ("FAILED: the test run changed the
  live work database", M33 criterion 7). A 60 s reader changes `-shm`
  every minute. Every suite run longer than a minute would fail once the
  new exporter is live. This blocks plumbing 1 as written.
- When the last connection to close is read-only, a WAL with frames in it
  stays (8272 bytes in the test) and a copy of `work.db` alone lacks the
  last commits. `backups.py` uses the backup API, so the monthly tar is
  whole. `restore-dump` and `init` refuse while a `-wal` is beside the
  path; the message says to move it aside.
- A read-only connection cannot run WAL recovery after a crashed writer;
  it errors until a writer has opened the file. That is one more state
  for plumbing 10.
- `work.py dump` is a read transaction; it and a reader do not meet.
  `restore-dump` replaces the file by rename after the operator has moved
  the old one aside; a reader in that gap sees "missing".

**Read the snapshot that already exists.** After every committed write
`work.py` rewrites `<db>.dump.sql` by temp file and rename, inside the
write lock. The exporter can load that file into an in-memory database
(4.7 ms today) and never open `work.db`: no lock of any kind, no file
made or touched in the state directory, the suite's guard left as it is,
nothing to say about restore, and "locked" drops out of plumbing 10.
`validate()` takes a connection and checks the schema version on it as it
does on the file. `--db PATH` then means "read `PATH.dump.sql`"; a copy
for a test needs `work.py --db <copy> dump` once.

What the dump costs, to be written down rather than solved:

- It is behind when a dump write failed. `work.py` says so on stderr at
  the write, and `check` names `dump-stale` and `dump-missing`. The
  exporter can state the dump's age in the file.
- The rename comes before `COMMIT`. A writer killed between the two
  leaves a dump one write ahead of the database until the next write.
- It holds the `log` table, 55% of it today (117 KB of 214 KB) and
  growing with every write. Loading it is wasted work; at today's rate it
  is years from mattering to a 0.5 s budget, and the growth is M33's to
  cap.

If the coordinator keeps `mode=ro` on the file instead: the guard in
`conftest.py` must stop watching `-wal` and `-shm` (a write always
changes the dump, so `work.db` and the dump still catch one), criterion 5
must name the `-shm` and `-wal` opens as allowed, the exporter needs one
`BEGIN` for one snapshot and a busy timeout near 1 s, not `work.py`'s
10 s, and plumbing 10 gains "needs recovery".

## 5. What reaches the LAN (question 5)

Looked at: every text column of the copy. No household speech is quoted
here.

- **No secret, no token, no password, no address** in `item`, `step`,
  `promotion` or `open_thing`. Paths are repo-relative report paths, plus
  the repo's absolute path in `meta.repo`.
- **Must not be exported**: the `log` table (command lines, and `who`
  holds Claude Code session ids), `meta.repo`, `step.model` (harmless,
  but no reader), `open_thing.closed_how` and `source`, and any SQLite
  error text (it carries the database's absolute path). For plumbing 10
  the file should carry a state word (`missing`, `empty`, `corrupt`,
  `newer`, `other repo`) and a sentence the exporter wrote.
- **Decisions' text (11 open) is not only questions.** #22, #35 and #40
  describe weaknesses: what the LAN can already read, how a typed request
  can make voice wait, and a list of music faults. That is the kind of
  text for which leftovers are held to counts ("a list of known
  weaknesses", `M33-review.md` point 8). "They are questions to him"
  describes the reader intended, not who can read. #3 and #4 each quote a
  short utterance from M24's testing; I did not find either in the corpus
  or the tests, so the coordinator should say whether they are a test's
  phrases or something heard. Recommendation: until #22 is answered,
  export for each decision its id, item, date and whether it blocks, and
  the text only for decisions the coordinator has marked as fit to show;
  or put the text question to Drew as part of #22 before the build. The
  rest of the item does not wait on it.
- **Next steps and wait notes** (5 rows have a next step, none a wait
  note today) are plain plans and name agents. Fine to show. `ended_why`
  on M29 quotes Drew's order; fine.
- **`step.note`** (4 rows) is coordinator prose about grading. Not asked
  for by M32; leave it out.
- **`Source:` paragraphs** quote Drew's orders. New on the LAN with this
  item; allowed by M32 plumbing 11.
- **Markup**: `work.py` refuses control bytes, not `<` or `*`. Every new
  string must go in with `el()` or `mdText()` as now; criterion 4 covers
  it.

Criterion 6 plants one marker in one leftover. Plant a distinct marker in
every text column of every table instead and assert the file holds
exactly the markers of the columns allowed. That is the test that fails
when a later change starts exporting a column.

## 6. A promotion with no item (question 6)

Looked at: the DDL, `c_promote`, `findings`, `c_resume`, `work.html`
`renderDone`.

- **The schema allows it**: `promotion_item` is a separate table and
  nothing requires a row in it. No schema change is needed.
- **No command makes one.** `promote` takes one or more items
  (`nargs="+"`), defaults `verified` to `docs/sprint/<first item>-promote.md`
  and refuses unless that path is in HEAD, and stamps `at` with now. The
  chat-buttons deploy was 2026-11-25 22:19 EST and its checks are in
  PROMOTIONS.md. The command needs: no item, a label (the `note` column
  is free and `promote` never sets it), `--verified docs/sprint/PROMOTIONS.md`,
  and `--at`, as `step start` has.
- **`check` and `resume` are unharmed** by such a row (read: `tag-gone`,
  `no-shipped-line`, the escape window; none assumes an item).
- **Not in `done`.** The deployed page builds a Done row from `r.id`: a
  null id gives an empty id, the title "no title line", and the fold key
  `null@null` shared by every such row. Put these in a new top-level key
  (the old page ignores it); the new page lists them under Done in date
  order, the tag where the id goes and the label as the title.

## 7. PROMOTIONS.md frozen (question 7)

Looked at: `grep PROMOTIONS` over the repo outside the item reports.

Reads it today: `work_export.py` `collect()` (leaves the live path);
`work.py import-legacy` (reads it at a named commit, through git);
`tests/test_m27_work_export.py` and `tests/test_m33_work_state.py` (their
own fixtures, and M27's four "real tree" tests through the exporter).
`deploy_tag.sh` does not. Written only by the coordinator by hand
(WORKFLOW.md step 7).

- **Rows point into it by line.** 33 promotions have `verified =
  PROMOTIONS.md:<line>` and 24 open leftovers read "see
  PROMOTIONS.md:<line>" (checked: line 440 is still M33's heading). A
  header added at the top moves every one. Put the frozen note at the
  **end** of the file.
- **To change so nothing waits on a new entry**: WORKFLOW.md step 7
  ("adds the entry to `docs/sprint/PROMOTIONS.md`"), its line 196 ("Until
  work item M34 …"), and "the promotions entry says so" in step 6;
  CLAUDE.md read-list item 7 ("what was last promoted"), "From the item's
  criteria file to its promotions entry", and "promotions entries" in the
  Git rule; ROADMAP.md's convention that a Shipped line points at
  `docs/sprint/PROMOTIONS.md, "M<n>"` (new lines should point at
  `docs/sprint/<item>-promote.md`) and its line 254; `work.html:168` ("The
  promotions log (`docs/sprint/PROMOTIONS.md`) … dates as written
  there") and `UNDER_WAY_NOTE`; `docs/features/work-page.md` (items 2, 5,
  6, the diagram, "Dates … are shown as written") and `work-state.md`
  lines 22 to 24 and 185 to 187. The first three files are the
  coordinator's own.
- `M33-criteria.md` says M33's entry was to be the last. Three more were
  written since (chat buttons, M35, M30), rightly, because the page still
  read the file. M34's own entry is not needed by anything once the page
  reads rows; write it only if the old page should show M34 between the
  move and the deploy.

## 8. "Built in scratch and moved in one step" (question 8)

Looked at: the unit, `M27-criteria.md` ("The exporter and production"),
`tests/test_m27_work_export.py:30`, `tests/test_m30_heard.py:509`.

It is workable, and it is stricter than the words suggest:

- The timer runs the **working tree's file**, so the move is the moment
  the file is written to `scripts/`, not the commit. And the same file is
  loaded by `work.py` and `agent.py` on every call, so a copy that does
  not import breaks the coordinator's tool in the same second, not within
  a minute.
- The one step: with the suite green against scratch copies
  (`M27_WORK_EXPORT` points the M27 tests at one; `work.py` has no such
  switch, so its change lands first as its own commit, proved by
  `resume` being byte-equal), copy the new `work_export.py` over the old
  and commit it and its tests in one commit. Before the copy, run the
  pair from a scratch `scripts/` directory holding both files
  (`work.py` finds the exporter beside itself): `work.py --db <copy>
  resume`, and the exporter with `--out` in scratch.
- Undo is `git revert` of that commit; the old file is live again within
  60 s and the deployed page reads it, because keys were only added.
- The page and `work_api.py` are a second step by mechanics: they reach
  production only by the tagged deploy. Between the two the deployed page
  shows the new file with the "newer schema" note, which is true.
- `tests/test_m30_heard.py:509` asserts `scripts/work_export.py` and
  `orchestrator/work_api.py` are unchanged since M30's base. It fails on
  the first edit of either. The item must retire or re-scope it, by name.
- The unit's command line can stay. Its comment block and the timer's
  describe files the exporter will no longer read; the reference copies
  change in the repo and the installed copies are the coordinator's to
  install.

## 9. The criteria (question 9)

Not checkable as written:

- **1**: the live database has moved on (M34 is active). Rewrite: on a
  copy taken at test time, the file agrees with `resume` on that copy,
  item by item, and every promotion carries its tag.
- **2**: fine in form. Expect the differences by class, not by instance:
  every step's time (format and source), every title, step order within
  an item, three `-promote.md` steps, five tags, `not_read`.
- **3**: `resume`, not `show` (point 3). "Waiting on Drew" is a field,
  not a stage; name what the file must show.
- **4**: say who opens the browser and what is recorded; the suite has no
  JavaScript runtime and M33's review left this "by hand". The timer
  rewrites `data/dev/work.json` every 60 s, so "dev against scratch
  files" needs a way to hold the file still (the exporter's `--out` and a
  local page server on loopback, or a ruling). And it tests only the new
  page: add **the deployed page (`item-M30`'s `work.html`) against the new
  file**, same cases.
- **5**: with the dump, "opens neither `work.db` nor any file beside it
  but the dump, read-only". With `mode=ro`, name the allowed opens. Put a
  number on "never blocks".
- **7**: `schema_known` changes to 2. Say "unchanged but for that".
- **9**: "removed" is not possible (point 3).

Missing:

- Plumbing 10 has no criterion of its own. For each of dump missing,
  empty, not loadable, of a newer schema, and describing another repo
  (`meta.repo` against `--repo`, as `guard_repo` does): the file is
  written, says which, holds no item, still holds the ROADMAP part, and
  the exporter exits 0 so the page shows the reason instead of going
  stale.
- The suite does not read the live database or its dump: the exporter in
  a test gets a scratch `--db` (conftest already points `KRONK_WORK_DB`
  at a path that does not exist), and M27's four "real tree" tests are
  rewritten for it.
- `resume` byte-equal before and after the `work.py` change.
- Two exporter runs at once (the timer and, after M32, a run started by a
  write) leave one whole file.

**Size.** One build, in the order of point 8: the `work.py` function; the
exporter; the page and `work_api.py`. If it is cut, the cut is between
the exporter (host, live at the move, provable on the deployed page with
no deploy) and the page (containers, live at the tag). M32 plumbing 7
redraws the queue and every row, so plumbing 8 here should stay as small
as it is written: a queue list, the decisions, the no-item deploys, the
reason when the database cannot be read.

## Table: every key of `work.json` the page reads (question 2)

Sources: **row** (a column), **computed** (`work.py`), **git**,
**herdr**, **criteria** (the file's head), **ROADMAP**, **exporter**
(its own clock or errors). The route's `source.*` keys are not in the
file and do not change.

| Key | Read at (`work.html`) | Source under the plumbing |
| --- | --- | --- |
| `made_at`, `run_s` | 346, 533 | exporter |
| `repo.branch`, `.head`, `.detached`, `.rebase`, `.error` | 345 to 358 | git (`rev-parse`) |
| `herdr.known`, `.seen`, `.reason`, `.elsewhere`, `.skipped` | 350 to 352 | herdr |
| `herdr.coordinator.shown` | 349 | herdr |
| `errors[]` | 357 | exporter (git errors; the database's state is a new key) |
| `items[].id` | 410, 555 | row `item.id` |
| `items[].title` | 411, 485 | row `item.title` (meaning changes: was the file's heading line) |
| `items[].short_title` | 485 | row `item.title` |
| `items[].stage.stage`, `.words`, `.detail`, `.blocked` | 413 to 416 | computed (`item_stage`, with herdr and git tags) |
| `items[].handoff` | 418 | criteria (path from the row; see point 1 for a null path) |
| `items[].notes[]` | 419 | herdr (an idle agent whose workspace is open) |
| `items[].steps[].kind` | 248, 366, 469 | row `step.kind` |
| `items[].steps[].n` | 248 | row `step.round`, for test and fix |
| `items[].steps[].name` | 248 | row `step.name` |
| `items[].steps[].file` | 248, 371 | row `step.report`, its base name |
| `items[].steps[].committed_at` | 365 | row `step.ended_at` (commit date for imported steps, finish time for agent steps) |
| `items[].steps[].verdicts[].verdict`, `.subject`, `.written` | 252, 369, 470 | row `step.verdict` as one entry, subject "item". A second subject has no source left but prose in `step.note` (one row, M4b) |
| `items[].uncommitted[]` | 378, 508 | **no source left**: `[]`. The nearest fact, a started step, goes under new keys |
| `items[].roadmap[]` (`.line`, and the entry keys below) | 421 to 424 | ROADMAP, linked by ids from rows |
| `items[].shipped[].adds`, `.title`, `.lead` | 497 to 502 | ROADMAP, with the row's title for `adds` |
| `under_way[]` | 435 | row `item.state = active` |
| `other_agents[].name`, `.status`, `.why` | 443 to 449 | herdr, against rows |
| `backlog[].section`, `.entries[]` | 457 to 462 | ROADMAP |
| entry `.fold.head`, `.fold.rest`, `.text`, `.title`, `.lead`, `.line` | 389 to 401, 462 | ROADMAP |
| `done[].id` | 481, 484 | row `promotion_item.item_id` |
| `done[].tag` | 488 | row `promotion.tag` |
| `done[].tag_only` | 488 | row `promotion.deployed = 0` with a tag |
| `done[].tag_written` | 488 | **no source left** as a value (prose in `promotion.note`, one row, M6): `null` |
| `done[].date` | 490 | row `promotion.at`, as the house's local date in text |
| `done[].heading`, `.heading_adds` | 493 | **no source left**: `null`, `false` |
| `done[].for_drew_total` | 494 | **no source left** (a count of words in PROMOTIONS.md): `0`. Open decisions and leftovers by owner go under new keys |
| `done[].notes[].heading` | 504 | **no source left**: the two follow-up rows hold a path, not a heading: `[]`, or a line the exporter writes ("follow-up, <date>") |
| `done[].line` | 506 | row `promotion.verified` when it is `PROMOTIONS.md:<n>`, else `null` (used only in the fold key) |
| `shipped_unlinked[]` (`.title`, `.lead`) | 516 to 518 | ROADMAP |
| `not_read[].file`, `.why` | 523 to 528 | criteria files not readable, ROADMAP sections not known; the step-file lines end |

Read by nothing on the page and free to stay or go empty: `schema`,
`ts` (the route reads both), `items[].tag_ref`, `.promoted`, `.agents`,
`steps[].commit`, `.title`, `done[].entry`, `.kind`, `.tag_in_git`,
`.for_drew`, `.notes[].date`.
