# M33 pass criteria: work-item state and the queue in a database

Revised 2026-11-24 after the design review (`docs/sprint/M33-review.md`):
all twelve of its points are taken. The section "Revised after the
review" near the end decides wherever it differs from the plumbing and
criteria above it; the build is in two steps, each with its own build and
test. Tier 3.

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

## Revised after the review (this section decides)

**Two steps (review 10).** Step 1 is the store and the tools; `/work`
keeps reading files and is not touched. Step 2 moves `/work` onto the
database. Between them the coordinator runs a real item (M30) on the
tool. Step 1's promotion is the last entry written to PROMOTIONS.md.

### Step 1: plumbing

1. **Where.** `~/.local/state/kronk-work/work.db` (override
   `KRONK_WORK_DB`; every command takes `--db`), beside M28's state
   directory, not under `data/`: three production containers mount
   `data/` read-write as the same uid, and `resume` prints this file's
   text to a session that launches agents with permissions skipped
   (review 11). No container reads it. One database for dev and
   production: it describes the repo.
2. **Schema**: the review's Appendix A, as the thing to build to. Seven
   tables. An item's stored state is its place in its life only:
   `queued`, `held`, `active`, `promoted`, `dropped`. **Stage is not
   stored** (review 1): it is computed at `resume` (and in step 2 at
   export) by M27's `stage()` from step rows, the promotion row, git tags
   and herdr. Leftovers, decisions owed to Drew and escapes are one table
   with a kind (review 11). The log is one row per command: time, who
   (the `Claude-Session` value when the environment gives one, else pid
   and start time), the command line, the changed rows as JSON.
3. **One writer, one code path.** `scripts/work.py`; `scripts/agent.py`
   loads it and calls its functions, with no SQL of its own. Every
   writing command is one `BEGIN IMMEDIATE` transaction; busy timeout
   10 s for `work.py`, 2 s for `agent.py` (review 3). The database path
   never comes from the script's own location alone: a test proves a
   launch in a temporary repo leaves the real file untouched (review 9).
4. **Evidence is copied, never typed (review 2).** A verdict is read
   from the committed report; a commit is the one the report was found
   in; `promote` refuses a tag git does not list, or takes `--no-tag`;
   `add` allocates the item number and a dropped number is not reused.
   What, why and Drew's words are **not** copied into the database: the
   row holds the criteria file's path, and readers take the handoff line
   and the `Source:` paragraph from the file's head. Rulings stay in the
   criteria file; the row holds their effect (`graded` on a test step,
   the next step). Promotion checks go in `docs/sprint/M<n>-promote.md`,
   always, and the row holds its path. The tier is the row's; criteria
   files stop naming it after this item.
5. **Commands** (JSON lines for all but `resume`, which is plain text):
   `add`, `show`, `list`, `queue` (prints), `start <item>` (queued to
   active), `move`, `hold`, `unhold`, `drop` (with why), `after` (an item
   that must follow another: `move` and `start` refuse while that one is
   not promoted), `step add` (a coordinator step, when its file is
   committed), `step start|finish|stop` (agent steps, called by
   `agent.py`), `grade`, `note` (next step; waits on: `drew`, `outside`
   or `item`, with a note), `promote`, `matter add|close` (kinds
   `leftover`, `decision`, `escape`; owner `drew` or `coordinator`),
   `log`, `check`, `resume`, `dump`, `restore-dump`, `import-legacy`.
6. **`check`** (review 1, Appendix C), read-only: compares rows with
   `docs/sprint/`, git tags, herdr and `~/.local/state/kronk-agents/`,
   one line per disagreement. For what git or herdr can show, the
   evidence wins and the row is reported behind; for what only the
   coordinator knows (order, held, dropped, waits on, next step,
   grading), the row wins. It names a criteria file with no row, a step
   left `started` with no live agent, a promoted item with no ROADMAP
   Shipped line, a queue not numbered 1 to n.
7. **`resume`** prints, in this order (review 6): `check`'s findings;
   under way (item, tier, computed stage, agent with herdr's status and
   minutes, last finished step with verdict and grading, next step,
   waits on); the queue in order (next step, waits on, after, held with
   why); owed by Drew (open decisions, oldest first, with the item each
   blocks); open leftovers by owner and the count untriaged; escapes
   within the last ten promoted items; the tree (branch, HEAD, clean);
   agents `agent.py list` knows; the last five log rows with who; and a
   closing line naming what it does not cover and the session must still
   do: the corrected voice runs, and ROADMAP entries that wait on
   something outside. It exits non-zero on a database that is missing,
   empty, corrupt or from a newer schema, and never prints "nothing
   under way" for one.
8. **`agent.py` recording (review 9).** `launch` starts a step; `status`
   and `wait` finish it when done, idempotently; `wait` exiting 2 or 3
   leaves it started; `close` on an unfinished step marks it `stopped`
   with why; a follow-up `prompt` reopens nothing. No step for
   `--probe`, `investigate` or `--no-report`. If the write fails the
   agent command still does its job and says so on stderr, within 3 s of
   its usual time; `check` names the hole and `step start --at <time>`
   closes it.
9. **History and loss (review 4).** After every committed write the tool
   rewrites `<db>.dump.sql` beside the database (temp file, rename, mode
   600); `restore-dump` rebuilds from it. No `.bak` copies. The database
   is added to `scripts/backups.py` as a `sqlite` member (a `HOME` path),
   not secret, not required. **There is no nightly backup**: the config
   backup is monthly (`kronk-backup.timer`, the 10th). Whether the
   databases should be copied daily is asked of Drew once, for M30 too.
10. **Migration (review 5, 7).** `import-legacy --at <sha>` reads the
    files as of a named commit. M29 (dropped, file deleted), the queue,
    tiers, gradings, and deployed-or-not for M24 to M31 are supplied by
    the coordinator in the build brief as tool commands, not parsed. The
    30 bullets of the six "Held or left over" blocks are triaged by the
    coordinator in the brief (`matter add`, and `matter close --how` for
    the ones already met). The 24 older cells and "Not done" bullets
    become one untriaged row each, text "see PROMOTIONS.md:<line>".
11. **Docs.** The coordinator, not the build agent, rewrites WORKFLOW.md
    ("Start of every working session", the steps, "Reporting to Drew",
    the stale "M24 is next") and CLAUDE.md item 8 at promotion, keeping:
    the two sweeps `resume` does not cover, the escape count, and "record
    Drew's answer before acting on it". The build agent writes
    `docs/features/work-state.md`.
12. No agent runs `work.py` against the live database; every agent run
    names `--db` in scratch. The coordinator runs the live import at
    promotion.

### Step 1: criteria

1. A cold start: a separate agent, launched with a one-line brief
   ("read CLAUDE.md, then say where the work stands"), writes its answer
   to a file; the test agent compares it with the answer key in the test
   brief (review 6).
2. The import, checked four ways (review 5): reproducible from the named
   commit; every tag in `git tag` is on a promotion row or listed as
   not, and every `M*.md` in `docs/sprint/` is a step's report or
   listed, with counts; M24 to M31 and M29 equal the coordinator's table
   in the test brief; and item for item against `work.json` as a
   regression check, with the missing M28 and M31 tags as expected
   differences. Each matter in the brief's list is in the state the brief
   gives; each older cell is one untriaged row naming its line.
3. A full life of a made-up item in a scratch database, as the original
   criterion 4, with the expected `resume` lines for each step given in
   the test brief.
4. Two writers, as processes, 200 read-then-write commands each: no lost
   write, no "database is locked" reaching the caller.
5. Missing, empty, locked for 30 s, corrupt, newer schema: `work.py`
   says which in one line within 12 s and exits non-zero; `agent.py`
   still launches, waits and closes, within 3 s of its usual time.
6. Each row of the review's Appendix C is produced in a scratch database
   and `check` names it; a step left started by a failed write or a dead
   agent is repaired by one command.
7. The suite leaves the live database's mtime alone.
8. Backup and loss: `backups.py config --dest <scratch> --no-notify`
   holds the database; restored with any `-wal` and `-shm` removed
   first, it passes `integrity_check` and `resume` matches the live one
   but for the log's tail. `restore-dump` from the dump gives the same.
9. `/work`, `/api/work` and `scripts/work_export.py` are unchanged
   (byte-for-byte for the script).
10. The suite passes; last lines quoted.

### Step 2 (criteria written after step 1 is promoted and M30 has run on it)

The exporter builds `work.json` from rows, two lock-free git commands
(`rev-parse`, `for-each-ref`) and herdr, with `--db`; the `git log` over
`docs/sprint/` and the PROMOTIONS parser leave the live path; the
handoff line and `Source:` paragraph are still read from the criteria
file's head. Same keys, additive. Compared by running old and new
exporter over the same commit and the same recorded herdr answer, the
keys allowed to differ listed by name. The deployed page, loaded against
the new JSON with a promotion that has no PROMOTIONS line, a queued item
with no file and a dropped item, shows no error and no "undefined".
Built in scratch and moved in one step. What reaches the LAN: decisions'
text; for leftovers, counts per item and owner only, until Drew answers
M27's open question on what `/work` may show.

### Out of scope, added

- Reducing the number of report files. Criteria, reviews and reports
  stay; what goes is appending to PROMOTIONS.md and reading prose for
  state. (Drew's "it would simplify the file structure": to be said to
  him in those words at promotion.)

## Out of scope

- Any change to what `/work` shows (M32).
- Moving ROADMAP.md, or any report's text, into the database.
- Agents writing state themselves.
- A web form for changing the queue.

## Rulings

- 2026-11-24, coordinator: the design review's twelve points are all
  taken, as written in "Revised after the review". Two steps.
