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

## Rulings
