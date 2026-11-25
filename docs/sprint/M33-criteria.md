# M33 pass criteria: work-item state and the queue in a database

Source: Drew, 2026-11-24: "You're using a lot of markdown files to track
these details (right?). You should store them in a database instead. It
would simplify the file structure and, in theory, speed up your work."
On the coordinator's proposed split (state to a database, prose stays in
files): "agreed on the split … roadmap.md stays text. You should be sure
that the db you're using is added to any backup routines." And: "how are
you implementing the queue, btw? Is it just markdown and your context? We
should have a real work queue. Whether that's using the database or
having a simple queue system (redis is way too much but somthing similar)
… it saves state and means any session with you can pick up where we
left off".

Handoff line: one SQLite database holds every work item's state, the
ordered queue, the leftovers and the decisions owed to Drew; one command
tells a fresh session exactly where things stand; and `/work` is drawn
from it instead of from patterns matched in prose.

Plan doc: this file. Design review before build.

## What the investigation found (coordinator, 2026-11-24)

- The queue today is not stored anywhere. An ordered item exists as a
  criteria file; the order of the items, which is next, what each waits
  on and what the coordinator meant to do next live in the coordinator's
  context. On 2026-11-24 the queue was M28, M31, M30, M33, M32 and no
  file said so. A new session would have to work it out from five
  criteria files and the chat.
- State is recovered from prose. `scripts/work_export.py` (M27) reads
  `docs/sprint/` (149 files), `PROMOTIONS.md` (355 lines) and ROADMAP.md
  each minute and matches patterns for ids, verdict lines, tags, dates,
  headings and "For Drew" lines; the stage of an item is a function of
  which files exist, their verdicts, tags, and herdr agent names. M27's
  fix round was this parsing: a table inside a promotions entry, a "For
  Drew" split across a line, Done rows naming an item twice.
- Leftovers are six "Held or left over" blocks inside promotions entries,
  as prose. CLAUDE.md item 8 has the coordinator read them at the start
  of every session. Nothing marks one as closed except a later edit.
- Decisions owed to Drew are in chat reports only.
- What agents and the coordinator write as documents (criteria, review,
  build, test and fix reports, rulings) are committed beside the code,
  and "the report is committed" is how `scripts/agent.py` (M28) knows an
  agent has finished.
- SQLite is already how Kronk stores small state (`metrics.db`,
  `sessions.db`, `solar.db`, `music_taste.db`). `scripts/backups.py`
  carries an explicit list of what is backed up, with a `sqlite` kind
  that copies a live database through the online backup API.
- `data/` is gitignored and mounted into the orchestrator as `/data`;
  dev reads `data/dev/`.

## Plumbing (coordinator's decisions)

1. The split, as agreed with Drew. **In the database:** each item (id,
   title, what it is, why, Drew's words, state, stage, place in the
   queue, tag, dates, what the next step is, what it waits on), each step
   of an item (kind, round, agent, model, report path, commit, verdict,
   start and end), leftovers (open or closed, whose, closed by what),
   decisions owed to Drew (asked, answered, the answer), and an
   append-only log of every change. **In files, as now:** criteria,
   reviews, build, test and fix reports (the prose), ROADMAP.md (Drew:
   "roadmap.md stays text"), WORKFLOW.md, feature docs.
2. PROMOTIONS.md stops growing. A promotion is a row (tag, time,
   deployed or not, what was verified as a short text, leftovers as
   rows); the checks themselves go at the end of the item's last report
   or a short `M<n>-promote.md` when they are long. The file stays as
   the record up to M31 and its header says where later ones are. One
   source per fact (tenet 8): no fact is written both to the database
   and to a file.
3. One database, `data/work.db`, SQLite, WAL, mode 600, written only
   through one tool, `scripts/work.py` (standard library), on the host.
   Nothing else writes it: not the orchestrator, not an agent by hand.
4. The queue is rows, not a service: `state = queued` ordered by an
   explicit position. Redis or a broker is not needed for one writer and
   a queue of ten (tenet 4; Drew: "redis is way too much").
5. `scripts/work.py`, each command one line of JSON or a short table
   (`--json` everywhere):

   | Command | What |
   | :-- | :-- |
   | `resume` | everything a fresh session needs: what is under way with stage and agent, the queue in order with each next step, decisions owed to Drew, open leftovers, items waiting on something outside the repo |
   | `add`, `show`, `list` | an item with its what, why and Drew's words; one item whole; items by state |
   | `queue`, `move`, `hold`, `drop`, `next` | the ordered queue and changes to it |
   | `step start`, `step finish` | a step of an item, with agent, report, commit, verdict |
   | `note` | the coordinator's "next step" and "waiting on" for an item |
   | `promote` | tag, time, deployed, what was verified |
   | `leftover add`, `leftover close` | |
   | `decision ask`, `decision answer` | |
   | `log` | the change log, for an item or a day |

6. `scripts/agent.py` records what it already knows: `launch` starts a
   step, `status`/`wait` on done finishes it with the commit and the
   report's verdict, `close` records the close. If the database write
   fails the agent command still does its job and says so on stderr: the
   tool for staying in contact with an agent does not depend on the
   bookkeeping.
7. History. The database is not in git, so: the change log table (who,
   when, what, old and new value); `data/work.db` added to
   `scripts/backups.py`'s list as a `sqlite` member, in the nightly and
   the NAS sets; and a restore shown from a backup tar. `work.py` takes
   its own copy (`data/work.db.bak-<n>`, three kept) before a schema
   migration.
8. Migration: M1 to M31 and today's queue are imported once, from what
   `work_export.py` parses today plus the six leftover blocks and the
   coordinator's list of open decisions (supplied in the build brief).
   The import is a command (`work.py import-legacy`), repeatable into an
   empty database, and its result is compared with today's `work.json`
   item for item: same items, same groups, same stages, same verdicts.
9. `/work` after this item shows what it shows today, from the database.
   `scripts/work_export.py` keeps writing `data/work.json` and
   `data/dev/work.json` with the same keys (deployed pages read them;
   additive only), built from the database and one `herdr agent list`;
   its prose parsers for `docs/sprint/` and PROMOTIONS.md leave the live
   path and stay only inside `import-legacy`. The ROADMAP parser stays:
   ROADMAP.md is still text. New keys the queue brings (order, next
   step, waiting on, what, why, open decisions) are added to the JSON
   for M32 to draw; the page is not changed here.
10. Stage stays a computed thing where it is evidence (an agent is
    working now: herdr) and becomes a stored thing where it is a fact
    (a step finished with a verdict). The reviewer is asked to find any
    state where the two can disagree and say which wins.
11. The start of a session becomes one command. WORKFLOW.md ("Start of
    every working session", the steps, "Reporting to Drew") and CLAUDE.md
    item 8 are rewritten by the build agent to: run `scripts/work.py
    resume`; the criteria file is still written and committed first; the
    queue is changed only through the tool. The coordinator reviews
    those two edits line by line before promotion.
12. Not in the database: transcripts (M30 has its own), ROADMAP entries,
    any report's text. `why` and Drew's words are short and are already
    on the LAN-visible page's side of the line once M32 shows them.

## Criteria

1. A session with no context, given only CLAUDE.md, runs `scripts/work.py
   resume` and can say, correctly: what is under way and at what stage,
   what is next and why, what Drew owes an answer on, what is left over.
   Tested by a fresh agent told nothing else, its answer compared with
   the coordinator's own list in the test brief.
2. The import equals today's state: every item of today's `work.json` is
   in the database in the same group, with the same stage and verdicts;
   every "Held or left over" line is a leftover row; differences are
   listed and each explained.
3. After the switch `/dev/work` and `/dev/api/work` show the same items,
   groups, stages and verdicts as before it at the same moment; only
   added keys differ.
4. A full life of a made-up item in a scratch database, through the
   tools only: added, queued behind another, moved ahead, criteria step,
   build launched by `agent.py` (fake herdr), finished, test fails, fix,
   test passes, promoted, a leftover opened and closed, a decision asked
   and answered. `resume` and the exported JSON are right after every
   step, and the log holds every change.
5. Two writers at once (an `agent.py wait` finishing a step while the
   coordinator moves the queue, 200 times): no lost write, no "database
   is locked" reaching the caller.
6. A database that is missing, empty, locked for 30 s, corrupt, or from
   a newer schema: `work.py` says which in one line; `agent.py` still
   launches, waits and closes; the exporter keeps the last good
   `work.json` and marks it stale with the reason.
7. Backups: the nightly set and the NAS set built to a scratch
   destination (`--dest`, `--no-notify`) contain `work.db`, it passes
   `integrity_check`, and `resume` run against the restored copy matches
   the live one.
8. The exporter's run stays under 1 s and takes no git lock.
9. The suite passes; last lines quoted.

## Out of scope

- Any change to what `/work` shows (M32).
- Moving ROADMAP.md, or any report's text, into the database.
- Agents writing state themselves.
- A web form for changing the queue.

## Rulings

(none yet)
