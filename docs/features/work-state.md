# Feature: work-item state and the queue in a database (`scripts/work.py`)

**Shipped:** work item M33 step 1 (2026-11-24 build). **Origin:** Drew,
2026-11-24: "You're using a lot of markdown files to track these details
(right?). You should store them in a database instead", and "We should
have a real work queue … it saves state and means any session with you can
pick up where we left off". Criteria and evidence:
`docs/sprint/M33-criteria.md` (the section "Revised after the review"
decides), `M33-review.md` (Appendix A, the schema; Appendix C, where rows
and evidence disagree), `M33-seed.json`, `M33-build.md`.

## What it does

One SQLite database holds every work item's place in its life, each step
of it, each promotion, the ordered queue, leftovers, decisions owed to
Drew and escapes, and a log of every change. One command, `work.py
resume`, tells a fresh session where things stand. The prose stays in
files: criteria, reviews, build, test and fix reports, ROADMAP.md,
WORKFLOW.md, the feature docs.

Step 1 adds the store and the tools. `/work` is not touched: it still reads
the files (`scripts/work_export.py`, M27). Step 2 moves `/work` onto the
database, after M30 has been run on the tool.

| Command | Does |
| :-- | :-- |
| `init` | an empty database |
| `add --title T [--tier N] [--after ITEM]` | an item, queued at the end; the number is allocated (`--id` for a split step) and a dropped one is never reused |
| `show ITEM`, `list [--state S]`, `queue` | one item whole (with the handoff line and `Source:` read from its criteria file), items, the queue in order |
| `start ITEM` | queued to active |
| `move ITEM N`, `hold ITEM --why`, `unhold`, `drop ITEM --why` | the queue; every change renumbers it 1 to n |
| `after ITEM OTHER` | ITEM may not start or move until OTHER is promoted |
| `note ITEM [--next] [--waits drew\|outside\|item --waits-note] [--tier]` | the coordinator's next step and what the item waits on |
| `step add ITEM KIND` | a coordinator step (criteria), once its file is committed |
| `step start\|finish\|stop\|refresh ITEM KIND [--round N]` | agent steps; `agent.py` calls these itself |
| `grade ITEM pass\|fail` | the coordinator's grading of a test, when it rules |
| `promote ITEM... (--tag T\|--no-tag) --deployed yes\|no` | the promotion row; the checks are in `docs/sprint/<item>-promote.md`, committed |
| `matter add KIND --owner drew\|coordinator --text`, `matter close ID --how`, `matter list` | leftovers, decisions, escapes: one list |
| `log [--item] [--day] [--limit]` | the change log |
| `check` | read-only: every disagreement between rows and git, herdr, the files |
| `resume` | plain text, for a session with no context |
| `dump`, `restore-dump` | the SQL copy beside the database, and the rebuild from it |
| `import-legacy --at SHA --seed FILE [--compare work.json]` | M1 to M33 from the files as of a commit, plus the coordinator's seed |

Every answer is JSON lines on stdout except `resume`. Exit 1 is a refusal;
exit 2 is a database that is missing, empty, corrupt, locked or from a
newer schema (one stderr line naming which); `check` exits 3 when it found
something.

## How

- **Where.** `~/.local/state/kronk-work/work.db` (`--db`, `KRONK_WORK_DB`),
  directory 700, file 600, WAL. Not under `data/`: three production
  containers mount `data/` read-write as the same uid, and `resume` prints
  this file's text to a session that launches agents with permissions
  skipped. No container reads it. One database for dev and production: it
  describes the repo, and records which (`meta.repo`).
- **Nothing makes it by accident.** Only `init`, `import-legacy` and
  `restore-dump` create the file. Every other command, and `agent.py`,
  says "missing".
- **Schema.** Seven tables: `meta`, `item`, `step`, `promotion`,
  `promotion_item`, `open_thing`, `log` (the review's Appendix A, plus
  `item.held_why` and `promotion.note`). An item's stored state is its
  place in its life only: `queued`, `held`, `active`, `promoted`,
  `dropped`. **The stage is never stored**: `resume` computes it with
  `work_export.stage()`, the function `/work` uses, from the finished step
  rows, the promotion row, the tags and herdr.
- **One writer.** Every writing command is one `BEGIN IMMEDIATE`
  transaction (busy timeout 10 s here, 2 s from `agent.py`), so two writers
  queue instead of failing: 400 read-then-write commands from two
  processes lose nothing. Each one writes one `log` row: time, who
  (`KRONK_WORK_WHO`, else the Claude Code session id, else pid and start
  time), the command line, the changed rows as JSON. Triggers make the log
  append-only. A command that changes nothing writes no row.
- **The dump.** Inside the same transaction, after the change and before
  the commit, the whole database is written as SQL to
  `work.db.dump.sql` (temp file, rename, mode 600). The write lock is
  held, so two writers cannot leave an older dump over a newer one.
  `restore-dump` builds a new file from it, checks it and moves it in.
- **Evidence is copied, never typed.** There is no `--verdict` and no
  `--commit`. A step's commit is the last commit that touched its report;
  a test's verdict is read from the committed report by `work_export`'s
  own `verdicts()`; `promote` refuses a tag git does not list. What, why
  and Drew's words are not rows: `show` reads the handoff line and the
  `Source:` paragraph from the criteria file's head.
- **`agent.py`.** `launch` starts a step, `status`/`wait` finish it when
  done (idempotent: the same evidence writes nothing), a follow-up's
  commit moves the same row, `close` finishes it or marks it `stopped`
  with why. It calls `work.py`'s `agent_started`, `agent_finished`,
  `agent_closed`; it holds no SQL. It writes only when the database
  describes the repo the agent was launched in, so a launch in a
  temporary repo (every M28 test) never touches the real file.
- **`check`** names, one line each with the command that settles it: a
  step started with no live agent; an agent working with no step (a
  failed write, a hand start), with the `step start --at <launch time>`
  that closes the hole; a started step whose report is already in; a
  verdict the report no longer says; a step commit outside the branch; a
  tag with no promotion, a promotion whose tag is gone; a criteria or step
  file with no row; a promotion since the import with no ROADMAP Shipped
  line; a queue not numbered 1 to n; a stale or missing dump; a database
  of another repo. For what git or herdr can show, the evidence wins; for
  what only the coordinator knows (order, held, dropped, waits on, next
  step, grading), the row wins.
- **`resume`** prints, in order: `check`'s findings; what is under way
  (tier, stage, agent and minutes, last finished step with verdict and
  grading, next step, waits on); the queue (next step, waits on, after,
  held with why); decisions owed by Drew, oldest first; open leftovers by
  owner and the count not triaged; escapes within the last ten promoted
  items; the tree; the agents `agent.py` knows; the last five writes; and
  what it does not cover (the corrected voice runs, ROADMAP entries that
  wait on something outside).
- **The import.** `import-legacy --at <sha>` reads `docs/sprint/` and
  PROMOTIONS.md as of that commit (`git ls-tree`, `git cat-file`; nothing
  is checked out), through `work_export`'s own `scan_items()` and
  `parse_promotions()`, and takes the tags reachable from it. What no
  parser can recover comes from the seed: tiers, gradings, deployed or
  not, the dropped item, the queue, and the triage of the six "Held or
  left over" blocks. The older table cells and "Not done" bullets become
  one untriaged leftover each, "see PROMOTIONS.md:<line>". It fills an
  empty database only, and prints counts: every tag on a promotion or
  listed, every `M*.md` a step's report or listed. `--compare` checks the
  result item for item against a `work.json`.
- **Backups.** `work-state/work.db` in the monthly config tar
  (`scripts/backups.py`, kind `sqlite`, online backup API), not secret,
  not required: missing is reported and the run stays OK. Restore:
  `docs/runbooks/restore.md`, step 14.

## Gotchas

- **The busy timeout does not cover a deferred transaction.** A
  read-then-write in SQLite's default transaction gets "database is
  locked" at once when another writer got in first; the review's trial
  lost all 200 writes of one side. `BEGIN IMMEDIATE` on every writing
  command fixes it.
- **A held write lock does not stop a reader in WAL.** `resume` answers
  while another process writes. Only an exclusive-mode lock blocks
  readers; then `resume` says "locked" after 10 s and exits 2.
- **A `-wal` beside a restored file is replayed onto it.** Remove `-wal`
  and `-shm` before putting a copy back.
- **There is no nightly backup.** The config tar is monthly (the 10th).
  The dump covers a corrupt file or a bad write; the tar covers a lost
  disk, a month old at worst. Whether to copy daily is asked of Drew.
- **The stage can say "waiting for the coordinator: test 2: FAILS" for an
  item the coordinator has graded a pass.** The stage is the pure function
  of the evidence; `resume` prints the grading and the next step beside
  it. Read them together.
- **`resume` runs herdr once.** With herdr down it says so and computes
  stages without agents.
- **One coordinator at a time.** SQLite cannot see two sessions acting on
  the same queue; the last five writes, with who, are in `resume` so a
  second session sees the first one's hand.

## Operations

```bash
W=scripts/work.py
$W resume                                   # start of a session
$W check                                    # exit 3 when rows and evidence disagree
$W add --title "..." --tier 2               # then commit its criteria file, then:
$W step add M34 criteria
$W start M34                                # before launching its first agent
$W note M34 --next "design review" --waits drew --waits-note "the go"
$W matter add decision --owner drew --item M34 --text "..." --blocks
$W matter close 12 --how "Drew, 2026-11-25: ..."
$W promote M34 --tag item-M34 --deployed yes  # after docs/sprint/M34-promote.md is committed
$W --db /tmp/x.db import-legacy --at <sha> --seed docs/sprint/M33-seed.json --compare data/work.json
```

## Blog hooks

- Drew asked for a database; the design review's first point was to store
  less in it: the stage is a function of evidence, and a stored copy is
  the one that goes stale, in eight named ways.
- `BEGIN IMMEDIATE`, or how a busy timeout silently does not apply: 200
  of 200 writes lost in a two-writer trial with SQLite's defaults.
- A text dump on every write as the cheap half of a backup plan, written
  while the write lock is still held.
- Importing two weeks of a workflow from prose: what the parser could
  read, and what only the person who ran it could say (tiers, gradings,
  a dropped item whose file was deleted).
- Keeping the bookkeeping from breaking the tool: `agent.py` still
  launches, waits and closes with the database missing, locked, corrupt
  or someone else's.
