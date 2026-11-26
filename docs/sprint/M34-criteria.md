# M34 pass criteria: /work drawn from the work-state database

Source: Drew, 2026-11-24, on moving tracked state out of markdown: "You
should store them in a database instead. It would simplify the file
structure and, in theory, speed up your work", and on the queue: "We
should have a real work queue … it saves state and means any session
with you can pick up where we left off". M33 built the database and the
tool; this is its second step, split off by M33's design review
(`M33-review.md`, point 10; `M33-criteria.md`, "Step 2").

Handoff line: `/work` shows what the work-state database says (what is
under way, the queue in order, what was promoted with its tag), not what
a parser reads out of `PROMOTIONS.md` and the file names in
`docs/sprint/`.

Plan doc: this file. Tier 3 (the page's whole data path changes, and it
reads the database every session depends on): design review, build, one
test, a fresh narrow test after any fix.

## What the investigation found (coordinator, 2026-11-26)

- `/work` is wrong today, on production, at 03:55 EST with the database
  saying nothing is under way, M34 and M32 queued, 35 items promoted:
  - "Under way" lists M30 as "promoting" and M32 as "planning". M30 was
    promoted an hour ago (its PROMOTIONS entry and tag exist); M32 is
    queued behind M34 and has only a criteria file.
  - M34, first in the queue, is not on the page at all: it had no file.
  - Done: M35, M33, M31 and M28 show no tag (`tag: null`,
    `tag_in_git: null`); the parser reads a tag only from a line that
    starts "- Tag `…`", and those entries name the tag mid-sentence.
  - The entry "Chat page buttons (no work item)" in PROMOTIONS.md is a
    deploy with no item; the parser has no place for it.
- The page is fed by `scripts/work_export.py` (980 lines; host timer
  `kronk-work-export.timer`, every 30 s) writing `data/work.json` and
  `data/dev/work.json`; `orchestrator/work_api.py` serves it; the page is
  `orchestrator/static/work.html`. The exporter runs `git log` over
  `docs/sprint/`, parses PROMOTIONS.md and ROADMAP.md, and asks herdr
  for agents. One run takes 0.08 s.
- The database (`~/.local/state/kronk-work/work.db`, written only by
  `scripts/work.py`) holds items (state, tier, title, criteria path,
  next step, what it waits on), the queue order, steps (kind, round,
  agent, report, commit, verdict, grade), promotions (tag, commit,
  deployed, verified), and matters (leftovers, decisions, escapes; 78
  open). The stage is computed by `work.py`, not stored.
- The database is outside every container's mount by design
  (`CLAUDE.md`, "The work-state database"): the page cannot read it and
  must keep getting a published file.
- M30 ran whole on the tool (nine tests, five fix rounds, four steps),
  M35 too; `work.py check` is clean. The database has real use in it,
  which is what M33's review asked for before this step.
- Decision #22 (what `/work` may show the LAN) is open with Drew. Until
  he answers, leftovers reach the page as counts only.
- "Heard" on `/work` (M30 step C) has its own route and its own script
  outside `loadAll`; it does not read `work.json`.

## Plumbing (coordinator's decisions, open to the review)

1. `scripts/work_export.py` builds `work.json` from the database
   (`--db`, default the live one, opened `mode=ro`), two lock-free git
   reads (`rev-parse` for HEAD and branch, `for-each-ref` for tags) and
   one `herdr agent list`. The `git log` over `docs/sprint/`, the
   PROMOTIONS parser and the file-name scan leave the live path.
2. Stage, queue order and "next" come from `scripts/work.py`'s own
   functions, imported. The exporter computes no stage of its own.
3. The handoff line and the `Source:` paragraph are still read from the
   head of the item's criteria file (the row holds the path). An item
   with no criteria file yet shows its title and next step.
4. The schema of `work.json` goes to 2. Every key the deployed page
   reads keeps its name and meaning, or is listed by name in the build
   report as gone with what the page shows instead. New keys, for M32:
   per item the tier, state, queue position, what it waits on, next
   step, steps with verdict and grade, counts of open leftovers and
   decisions by owner; top level the queue in order, the decisions owed
   to Drew (text: they are questions to him), the escape count.
5. Leftovers' text does not reach the file. Counts per item and owner
   only (decision #22).
6. A promotion with no item (the chat-buttons deploy) is recorded in
   the database as a promotion with no item, by a `work.py` command if
   one is missing, and shown under Done by its tag. PROMOTIONS.md gets a
   header saying it is frozen as of M34 and where the record now is; no
   entry is appended after M34's own.
7. ROADMAP.md's part of the page (the backlog, shipped lines) is
   unchanged: Drew ruled the roadmap stays text.
8. The page (`work.html`) changes only as far as the new file needs:
   under way from the database's active items, a queue list in order,
   Done from promotions. M32 does the rest (folding, what and why, live
   updates). "Heard" is not touched.
9. Built in scratch and moved in one step: the new exporter is run
   beside the old one against the same commit and a recorded herdr
   answer until the comparison passes; the timer's unit does not change
   its command line.
10. A database that is missing, locked, of a newer schema or unreadable:
    the exporter writes a file that says so, the page shows the reason
    and the age, and nothing is invented from files.

## Criteria

1. On the live database (read-only, a copy in scratch): the file shows
   nothing under way when nothing is, M34 and M32 queued in that order
   with M32 marked as waiting on M34, and every promotion with its tag
   (M28, M31, M33, M35, M30 and the chat-buttons deploy among them).
2. Old and new exporter over the same commit, the same database copy
   and the same recorded herdr answer: every difference between the two
   files is listed by key in the test report and each is one of: fixed
   (wrong before), new, or gone by plumbing 4. Nothing else differs.
3. An item walked through its life in a scratch database (added,
   queued, started, a build step running under a recorded herdr agent,
   a test FAILS graded fail, a fix, a test PASSES, promoted with a tag,
   and one dropped, one held, one waiting on Drew): the file after each
   step shows the stage `work.py show` gives.
4. The page in a browser on dev against scratch files for each of: the
   live copy; a promotion with no PROMOTIONS line; a queued item with no
   criteria file; a dropped item; a held item; an agent herdr reports
   that no item owns; the database missing; stale. No script error, no
   "undefined", no "null", markup in a title or next step stays text.
   "Heard" still loads.
5. The exporter takes no git lock and no write lock on the database
   (`strace`, or the method M27's test used), finishes in under 0.5 s
   on the live copy, and never blocks `work.py` (a writer running
   beside twenty exports).
6. No leftover's text is in `work.json`; a marker string planted in a
   leftover in scratch is not found in the file.
7. `/api/work`'s envelope (fresh, stale, missing) is unchanged; the
   route reads no database.
8. The suite passes; last lines quoted.
9. By the coordinator at promotion: the live page against `work.py
   resume` item by item; the old parser's code removed or marked as
   import-only; PROMOTIONS.md's header.

## Out of scope

- Folding, what and why per item, live updates, animations (M32).
- Showing leftovers' text (decision #22).
- Any change to "Heard", to ROADMAP.md's place on the page, or to the
  database's schema beyond a promotion with no item.
- Reducing the number of report files in `docs/sprint/`.

## Revised after the review (this section decides)

Coordinator, 2026-11-26, on `M34-review.md` (build with these changes).
All taken. Where the text above differs, this holds.

Corrections of fact: the export timer fires every 60 s (30 s is the
page's refresh). M32 is held by `after_item` (shown by `queue_rows()` as
`blocked_by`), not by `waits_on`. M30 no longer shows "promoting" and
M34 is on the page: both healed when a file was committed, which is the
fault (the page is wrong between a tag and its PROMOTIONS commit, and
before a criteria file exists). Five tags are missing, not four (M30's
too). The other disagreements are the review's table in its point 1.

### Plumbing

1. **The exporter reads the dump, never the database.** `work.py`
   rewrites `<db>.dump.sql` after every committed write; the exporter
   loads it into an in-memory database and opens no other file in the
   state directory (a `mode=ro` reader of a WAL database makes and
   touches `-wal` and `-shm`, and the suite's guard fails on that).
   `--db PATH` means "read `PATH.dump.sql`". The file states the dump's
   age. The `log` table, `meta.repo`, `step.model`, `step.note`,
   `open_thing.closed_how` and `source`, and any SQLite error text are
   never exported.
2. **Keys are only added.** Schema 2. No key of schema 1 is removed or
   given another meaning; one with no source left carries `null`, `[]`,
   `0` or `false`, and the build report lists each by name (the
   review's table). `items[].title` becomes the row's title, which is a
   fix. `done[].date` stays a date as text, the house's local day; the
   ISO time goes under a new key. `steps[].committed_at` is filled from
   the step's `ended_at`. `next_step` and the wait fields are written
   for active, queued and held items only.
3. **New keys** (for M32 and the smaller page here): per item the state,
   tier, queue position, `blocked_by`, what it waits on, next step, the
   `Source:` paragraph, counts of open leftovers and of open decisions
   by owner; per step its state, agent, `by`, started and ended times,
   verdict and grade; per promotion whether deployed (yes, no, not
   recorded) and the path of its checks; top level the queue in order,
   the active items, the decisions owed to Drew, the escape count in
   the window, the promotions with no item, and the state of the
   database (`ok`, `missing`, `empty`, `corrupt`, `newer`, `other
   repo`) with a sentence the exporter wrote and the dump's age.
4. **Decisions reach the file without their text**: id, item, date and
   whether it blocks. Three of the eleven open ones describe
   weaknesses, which is the reason leftovers are held to counts. Text
   for both waits on Drew's answer to #22. (This reverses the criteria
   above, which would have shown it.)
5. **`work.py` gains one read-only function** that returns plain data
   (per active item its stage and agents, the queue with `blocked_by`
   and `next`, the decisions owed oldest first, leftover counts by item
   and owner, the escapes in the window) from rows and outside facts
   handed to it (tags, HEAD's commits, herdr's answer). `resume` prints
   from it; the exporter serialises it. No writing command and no
   `connect()` changes. It lands as its own commit, first, proved by
   `resume` on a copy of the live database being byte-equal before and
   after but for its first line. `stage()` stays in `work_export.py`,
   where `work.py` already loads it from; the exporter loads `work.py`
   inside `collect()`, never at import. A third lock-free git read,
   `rev-list HEAD`, is allowed, so the exporter agrees with `resume`
   after an amend.
6. **The old parsers stay** in `work_export.py`, marked import-only
   (`git_first_commits`, `scan_items`, `parse_promotions`,
   `tag_from_cell`, `bare_word` and the collect they feed):
   `import-legacy`, `check` and the M27 tests use them.
7. **A promotion with no item**: `work.py promote` accepts no item with
   `--label`, `--verified PATH` and `--at`; the schema already allows
   the row. It goes in its own top-level key, never in `done`. The
   chat-buttons deploy is recorded that way by the coordinator after
   the build.
8. **PROMOTIONS.md is frozen by a note at its end**, not its top: 57
   rows point into it by line. M34's own entry is not written. The
   coordinator changes WORKFLOW.md, CLAUDE.md and ROADMAP.md's
   convention (a Shipped line points at `docs/sprint/<item>-promote.md`);
   the build agent changes `work.html`'s note about the promotions log,
   `work-page.md` and `work-state.md`.
9. **The page changes little**: under way from the active items, a
   queue list in order with what holds each, Done from promotions with
   the no-item deploys in date order, the count of decisions owed, the
   reason when the database cannot be read. `work_api.py`'s
   `schema_known` goes to 2; nothing else in the route.
   `tests/test_m30_heard.py`'s assertion that `work_export.py` and
   `work_api.py` are unchanged since M30's base is retired by name.
10. **The move.** The timer runs the working tree's file, and `work.py`
    and `agent.py` load it on every call, so the move is the moment the
    file is written. Order: the `work.py` commit; then, with the suite
    green against a scratch copy of the pair (`M27_WORK_EXPORT`, and a
    scratch `scripts/` directory holding both files), the new
    `work_export.py` and its tests in one commit; then the page and
    route. Undo is a revert of the second commit; the deployed page
    reads either file because keys were only added. The reference unit
    files' comments are brought up to date; the installed copies are the
    coordinator's.

### Criteria (these replace the list above)

1. On a copy of the live database taken at test time (the backup API
   opened `mode=ro`, then `work.py --db <copy> dump`): the file agrees
   with `work.py --db <copy> resume` item by item (what is under way
   and its stage, the queue in order with `blocked_by`, the counts),
   and every promotion carries its tag.
2. Old and new exporter over the same commit, the same copy and the
   same recorded herdr answer: every difference listed by key and
   class in the test report (a time's format and source, a title, step
   order, the `-promote.md` steps, the five tags, `not_read`, a key
   with no source left, a new key). Nothing else differs.
3. An item walked through its life in a scratch database (added,
   queued, started, a build step running under a recorded herdr agent,
   a test FAILS graded fail, a fix, a test PASSES, promoted with a tag;
   one dropped, one held, one waiting on Drew, one queued behind
   another): after each step the file shows the stage `resume` gives,
   and the wait and hold fields as the rows have them.
4. In a browser, by the test agent (headless Firefox behind a loopback
   proxy, as `M30-e2e6.md` did; it may stop `kronk-work-export.timer`
   while it holds a fixture in `data/dev` and must start it again), for
   both **the new page** and **the page as deployed at `item-M30`**
   reading the new file: the live copy; a promotion with no item; a
   queued item with no criteria file; a dropped item; a held item; an
   agent no item owns; each database state of plumbing 3; stale. No
   script error, no "undefined", no "null" shown, markup in a title or
   next step stays text, "Heard" still loads.
5. The exporter opens neither `work.db` nor any file beside it but the
   dump, read-only, and takes no git lock (`strace`); a run takes under
   0.5 s on the live copy; twenty exports beside a writer making fifty
   writes delay no write by more than 50 ms; two exporter runs at once
   leave one whole file.
6. A distinct marker planted in every text column of every table of a
   scratch database: the file holds exactly the markers of the columns
   plumbing 1 to 4 allow, and none of a leftover's or a decision's
   text.
7. `/api/work`'s envelope is unchanged but for `schema_known` 2; the
   route reads no database and no dump.
8. For each of dump missing, empty, not loadable, of a newer schema and
   describing another repo: the file is written, says which, holds no
   item, still holds the ROADMAP part, and the exporter exits 0.
9. `work.py --db <copy> resume` is byte-equal before and after the
   `work.py` commit but for its first line. The M33 suite passes.
10. The suite reads neither the live database nor its dump; M27's four
    "real tree" tests are rewritten for a scratch `--db`. The suite
    passes; last lines quoted.
11. By the coordinator at promotion: the live page against `work.py
    resume`; the chat-buttons deploy recorded; the note at the end of
    PROMOTIONS.md; WORKFLOW.md, CLAUDE.md and ROADMAP.md brought up to
    date; the installed unit files.

## Rulings
