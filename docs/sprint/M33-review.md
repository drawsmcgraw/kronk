# M33 design review: work-item state and the queue in a database

Reviewer: `m33-review` (Fable 5.2), 2026-11-24, at `8a77cd3`. Nothing was
changed but this file. Read: CLAUDE.md, WORKFLOW.md, the M33, M32 and M30
criteria, `scripts/work_export.py`, `tests/test_m27_work_export.py`,
`orchestrator/work_api.py`, `scripts/agent.py`, `docs/features/agent-tool.md`,
PROMOTIONS.md (whole), `scripts/backups.py` (the member list and the sqlite
reader), the M27, M28 and M31 criteria and M29's three commits. Run: read-only
commands, and two scratch SQLite trials in the session scratchpad (the schema
of Appendix A with the four walks and six writes it must refuse; a
two-writer race). The suite was not run:
no code changed.

Twelve points, most important first. Each ends with the change to the
criteria. The brief's nine questions are answered in: Q1 points 4, 5, 7, 8,
12; Q2 Appendix A and B; Q3 point 1 and Appendix C; Q4 point 2; Q5 point 6;
Q6 point 5; Q7 points 3, 4, 9; Q8 points 10, 11; Q9 point 11.

## 1. Do not store the stage. Store rows; keep stage a pure function (Q3)

Plumbing 1 lists "state, stage" as columns of an item, and plumbing 10
makes stage "a stored thing where it is a fact (a step finished with a
verdict)". The fact in that sentence is the step row, not the stage. A
stored stage is a second copy of what the step rows, the promotion row and
herdr already say, and it is the copy that goes stale: every write path
(`work.py`, `agent.py`, a failed write, a session that died) has to keep it
right.

`stage()` today is already the shape that is wanted
(`scripts/work_export.py:740`):

    def stage(steps: list[dict], tag: bool, promoted: bool, agents: list[dict] | None) -> dict:

Keep the function and its seven stage tests (`tests/test_m27_work_export.py:311` to
`:356`); change only where its arguments come from: `steps` from step rows
instead of file names, `promoted` from a promotion row instead of a
PROMOTIONS.md heading, `tag` from git as now, `agents` from herdr as now.
What is stored is an item's place in its life (`queued`, `held`, `active`,
`promoted`, `dropped`), which no evidence can give, and each step.

Appendix C lists the nine states where rows and evidence can disagree and
which wins. The rule in one line: **for anything git or herdr can show
(a report committed, its verdict, a tag, an agent working), the evidence
wins and the tool says the row is behind; for anything only the
coordinator knows (queue order, held, dropped, waits on, next step,
grading), the row wins.**

That needs one more command than plumbing 5 has: `work.py check`, read-only,
which compares rows with `docs/sprint/`, the tags, herdr and
`~/.local/state/kronk-agents/`, and prints each disagreement in one line.
`resume` runs it and prints its findings first.

Change: plumbing 1, strike "stage" from the item's columns. Plumbing 10
becomes "stage is computed at export and at `resume` from rows, tags and
herdr, by the function M27 pinned; nothing stores it". Add `check` to
plumbing 5 and a criterion: each row of Appendix C is produced in a scratch
database and `check` names it.

## 2. The tool copies evidence; nobody types a verdict, a commit or a tag (Q4)

Plumbing 2 says "no fact is written both to the database and to a file".
After this item, as written, these still are:

| Fact | Also in | Source, or how the copy stays honest |
|---|---|---|
| A test's verdict | the report's first 40 lines | The report. `step finish` takes no `--verdict`: it reads the committed report with `work_export.verdicts()`, as `agent.py` does (`scripts/agent.py:300`, `report_verdict`). `check` re-reads and compares. |
| A step's commit | git | Git. The tool records the commit it found the report in; never typed. |
| The tag | `git tag` | Git. `promote` refuses a tag `git for-each-ref` does not list (or takes `--no-tag`: M6, M16b and M28-like items). |
| What, why, Drew's words (plumbing 1) | the criteria file's handoff line and `Source:` paragraph | The criteria file: WORKFLOW step 2 has it written and committed first, and plumbing 11 keeps that. The row stores the path and a title. See point 8. |
| Report path of a step | the file name convention | The row; `check` flags a known step file with no row. |
| Tier | the criteria file ("The coordinator names the tier in the criteria file", WORKFLOW.md:106) | Pick one. I would keep the row (resume and the escape count need it) and have the criteria file stop naming it, or `check` compare. |
| Item shipped | ROADMAP.md Shipped line ("*(2026-11-24, work item M31)*", ROADMAP.md:158) | Both stay: ROADMAP is Drew's text. `check` lists promoted items since M33 with no Shipped line naming them. |
| Next item number | WORKFLOW.md:11, "the item numbers (M24 is next)" (already seven items stale) | The database: `add` allocates the number; a dropped number is never reused. Strike the parenthesis. |
| Rulings | the criteria file's Rulings section | The file. The row carries only the effect: `graded` on a test step, `next_step` on the item. |
| Promotion checks | `M<n>-promote.md` or the row's `verified` text (plumbing 2 allows either, or "the end of the item's last report") | One place: always `M<n>-promote.md`, the row holds its path. Three possible places is how PROMOTIONS.md got its shapes. The coordinator appending to a test agent's report also blurs whose words a report holds. |
| Agent launch record | `~/.local/state/kronk-agents/<name>.json` | The state file while the agent lives (herdr ids live only there); the step row after. `close` deletes the file (`scripts/agent.py:784`), so the row is written before the unlink. |

Change: replace plumbing 2's last sentence with this table's rule ("a fact
git holds is copied by the tool, never typed, and `check` re-reads it"),
and state the source for each row above.

## 3. Criterion 5 fails with SQLite's default transactions (Q7)

A command that reads then writes inside a deferred transaction (read the
queue, renumber it; read a step, finish it) gets `database is locked` at
once when another writer got in between: the busy timeout does not apply to
a read lock being upgraded. Scratch trial, two writers, 200 read-then-write
transactions each, WAL, `timeout=5`:

    deferred  [('a', 0), ('b', 200)] errors: 200 {'database is locked'}
    immediate [('a', 200), ('b', 200)] errors: 0 set()

One writer lost every write. With `BEGIN IMMEDIATE` on every writing
command, none.

Two coordinator sessions at once is a different fault: both read the queue,
both act, and the database keeps both writes without complaint. SQLite
cannot see it. What can be done cheaply: every log row carries the session
(the `Claude-Session` value the coordinator's commits already carry, or the
pid and start time), and `resume` prints the last five log rows with who
and when, so a second session sees the first one's hand. `agent.py launch`
already refuses a second working agent (`scripts/agent.py:579`).

Change: plumbing 3 adds "every writing command is one `BEGIN IMMEDIATE`
transaction; busy timeout 10 s for `work.py`, 2 s for `agent.py`". Criterion
5 adds "by processes, not threads, and each command's read-then-write is
inside the transaction". Criterion 6's "locked for 30 s" gets a bound:
`agent.py launch` returns within 3 s of its usual time with the stderr
line.

## 4. There is no nightly backup. Criterion 7 cannot be checked (Q1, Q7)

Plumbing 7 and criterion 7 say "the nightly and the NAS sets". The config
backup is one job, monthly:

    systemd/kronk-backup.timer:  OnCalendar=*-*-10 01:30
    scripts/backups.py:5:        backups.py config  [--dry-run] [--no-notify] [--dest DIR] [--require-mount]

`systemctl --user list-timers` shows its next run as 2026-12-10. The other
two backup timers are the recordings archive (weekly) and the stale check
(daily, reads only). M30's ruling repeats the phrase
(`M30-criteria.md:182`, "in the nightly and the NAS sets"), on Drew's
"back up the transcript db".

So "lost with no backup newer than a day" is really "newer than a month".
What a lost `work.db` costs, given point 2: steps, verdicts, commits, tags
and promotions since M31 can be rebuilt from git and the report files. Not
rebuildable: queue order, held and dropped with their reasons, next step,
waits on, leftovers and decisions with their state, the log.

Recommended, in this order of value:

1. **A text dump on every write.** After each committed transaction
   `work.py` writes `<db>.dump.sql` beside the database (`iterdump`, temp
   file and rename, mode 600), and `work.py restore-dump` rebuilds from it.
   This covers the likely losses: a corrupt file, a bad migration, a tool
   bug that mangles rows. It is a generated copy with one reader, as
   `work.json` is, so it is not a second source; it is gitignored with the
   database, so the tree stays clean for the patch run
   (`scripts/upgrades.py:1354` declines on any `git status --porcelain`
   line). It replaces `work.db.bak-<n>` (plumbing 7): one mechanism.
2. **The monthly tar**, as planned, for a lost disk. Add the member as
   `sqlite`, not secret, not in `required_sources`: a missing `sqlite`
   member is reported as "missing: no such file" (`scripts/backups.py:918`)
   and the run stays OK, which is right for a file that is mostly
   rebuildable.
3. **A `rebuild` path** (the import of point 5, run over all items) for
   what git holds.

A daily copy of the two databases to the NAS would need a new host timer.
That is a question for Drew, once, covering M30 too: "the databases are
backed up monthly with everything else; do you want them daily?" Until he
answers, both criteria files should say monthly.

Restore has a trap the criterion should name: a `work.db-wal` left beside
a restored `work.db` is replayed onto it (the runbook says so for MA,
`docs/runbooks/restore.md:313`).

Change: plumbing 7 and criterion 7, "the nightly and the NAS sets" becomes
"the monthly config tar (`backups.py config --dest <scratch> --no-notify`)";
add the dump and `restore-dump`; the restore test removes `-wal` and `-shm`
first; `resume` on the restored copy is compared ignoring the log's tail.
Fix M30's ruling the same way.

## 5. The import is checked against the parser it is built from (Q6)

Criterion 2 compares the import with today's `work.json`. The import reads
"what `work_export.py` parses today" (plumbing 8). So the check passes
whenever the import repeats the parser, faults included. One fault is live
now: today's `work.json` gives M28 and M31 no tag.

    data/work.json done rows:  ('M31', None, …), ('M28', None, …), ('M27', 'item-M27', …)
    git tag:                   item-M28, item-M31 exist
    work_export.py:112:        TAG_LINE = re.compile(r"^- Tag `([^`]+)`(.*)")
    PROMOTIONS.md:360:         answers in one line of JSON. Tag `item-M28`. **No deploy**: the tool runs

Imported "equal to work.json", M28 and M31 would be promoted with no tag.

What `import-legacy` can recover from M1 to M31: 35 item ids; step rows by
file name with first-commit date and verdict; 33 promotion rows with tag
and date as written. What it cannot:

- **M29.** Its file is deleted; it is not in `work.json` (35 items, none
  M29). Only `git log --all` has it (`7380c43`, `2773110`, `47a8540`).
- **Deployed or not.** The parser reads only "tag only" (`tag_only`);
  M28's "**No deploy**", M6's `none`, M16b's "No tag and no deploy" are
  prose.
- **Tier, grading, rulings' effect.** M28's last step reads FAILS and the
  item is promoted; the reason is in `M28-criteria.md:181`.
- **Step starts, agents, models.** Only a first-commit date exists. Start
  must be nullable for imported steps.
- **The coordinator's fixes after a last test** (M9, M12, M13, M16, M18,
  M22, M23; WORKFLOW.md:171) and any step with no file.
- **More than one verdict in a report.** `M4b-e2e.md` reads
  `[('RESTORE', 'PASSES'), ('item', 'FAILS')]`.
- **Five files of unknown kind** (`M6-audit.md`, `M5-change1.md`,
  `M3-e2e2-rows.md`, `M1b-restore-criteria.md`, `M1b-restore-build.md`;
  `not_read` in today's JSON). M6 has no criteria step at all.
- **Combined and repeated promotions.** One tag for M3, M4, M4b and M1b's
  restore; M1b has two done rows. Two entries are notes on a promoted item
  ("M16 installed", "M26, the removal's first step").
- **Queue order, and whether a leftover is still open** (point 7).

A stronger check, all cheap:

1. Import from a named commit (`import-legacy --at <sha>`), so "today" is
   reproducible while M33's own files land.
2. Independent of the parser: every one of the 28 tags in `git tag` is on a
   promotion row or listed as not; every `M*.md` under `docs/sprint/` is a
   step's report or listed; counts printed.
3. A hand-written table for M24 to M31 with M29 in the test brief (tier,
   steps, verdict and grading, tag, deployed), from the coordinator, who
   ran them. The older 26 are checked by 1 and 2 only.
4. The comparison with `work.json` stays, as a regression check, with the
   M28 and M31 tags as expected differences.

Change: criterion 2 becomes those four. Plumbing 8 adds `--at`, and "M29
and the queue are supplied in the brief as tool commands, not parsed".

## 6. The queue and `resume` (Q5)

Rows with a position are enough for order, and only for order. From the
last day's real events:

- **Hold** (M29, "HELD. Drew: 'tell me more about m29…'"): a state, not a
  position. A held item keeps its place and `next` skips it.
- **Drop** (M29): a state with a sentence of why and a time. The criteria
  step keeps its commit so the deleted file can still be read
  (`git show 7380c43:docs/sprint/M29-criteria.md`).
- **Waits on Drew / on something outside**: `waits_on` with a note, on any
  state. Plumbing 1 has it as free text; make the kind an enum (`drew`,
  `outside`, `item`) so `resume` can group.
- **Two items in order**: M32 says "Built after M33: the what, the why and
  the queue come from the database" (`M32-criteria.md:19`). Position alone
  lets `move M32 1` succeed. One nullable column `after_item`; `move` and
  the start of an item refuse while that item is not promoted.
- **Reorder**: renumber 1..n in one transaction on every `move`. No UNIQUE
  on position (a renumbering update collides with itself); `check`
  verifies 1..n.
- `next` is ambiguous in plumbing 5: does it print the next item or take
  it? Name them apart: `queue` prints; `start <item>` moves `queued` to
  `active`.

What `resume` must print for a session with no context, in this order:

1. `check`'s findings (rows that disagree with git, herdr, the files).
2. Under way: item, tier, computed stage, the agent with herdr's status
   and minutes, the last finished step with verdict and grading, next
   step, waits on.
3. The queue in order: next step, waits on, `after_item`, held with why.
4. Owed by Drew: open decisions, oldest first, with the item each blocks.
5. Open leftovers by owner, and the count not yet triaged (point 7).
6. Escapes: the count within the last ten promoted items (WORKFLOW.md:128,
   "The count of escapes is part of the start-of-session report"). The
   criteria do not mention escapes at all; after M33 "where the item's
   promotion is recorded" is a row.
7. The tree: branch, HEAD, clean or not (the patch run needs `main`,
   clean), and agents the tool still knows (`agent.py list`).
8. The last five log rows, with who.
9. What it does **not** cover and the session must still do: the
   corrected voice runs in Grafana and the ROADMAP entries that wait on
   something outside (WORKFLOW.md:149 to 153; "YouTube Music is down; wait
   for upstream", ROADMAP.md:63). Those are ROADMAP entries, not items, and
   ROADMAP stays text. Plumbing 5's "items waiting on something outside
   the repo" reads as if `resume` covered them.

What it would get wrong with the criteria as written:

- A stale stored stage (point 1).
- A held item shown as queued; an item ahead of the one it depends on.
- An item whose criteria file is committed but was never `add`ed: not
  there. `check` catches it (a criteria file with no row).
- A decision Drew answered in chat and nobody recorded: asked again. No
  code fixes this; the WORKFLOW rewrite must say "record the answer before
  acting on it".
- A step `started` by a session that died: shown as under way for ever.
  `check` compares with herdr.
- The two sweeps of item 9 silently dropped by "the start of a session
  becomes one command" (plumbing 11).

Change: plumbing 4 adds hold as a state, `after_item`, the `waits_on`
kinds; plumbing 5's `resume` row becomes the nine lines above; plumbing 11
keeps the Grafana and ROADMAP sweeps in WORKFLOW.md and CLAUDE.md item 8.
Criterion 1: the cold agent is its own launch with a one-line brief and
writes its answer to a file; the test agent (who has read the criteria and
the answer key) compares. As written, the agent doing the test holds the
answers.

## 7. "Six blocks" is not what the file holds, and open or closed cannot be parsed (Q1, Q6)

The investigation says "Leftovers are six 'Held or left over' blocks". The
file has six blocks (M24, M26, M25, M27, M28, M31; 30 bullets) and, before
them, a table whose fifth column is the same thing:

    PROMOTIONS.md:5:  | Item | Tag | Promoted | What changed in production | Held or left over |

21 cells (lines 7 to 26 and 37), plus three "Not done:" bullets (lines 48,
66, 79). WORKFLOW.md:174 calls that column "dozens of notes with no
owner". CLAUDE.md item 8 has the coordinator read the open ones; after M33
that reading is `resume`, so whatever is not imported leaves the sweep
without anyone deciding it.

Criterion 2's "every 'Held or left over' line is a leftover row" cannot be
checked: a line is not a matter. M24's first bullet (PROMOTIONS.md:129 to
134) holds four ("Ollama's 241 GB of blobs…", "A spoken 'Okay Nabu, ask
talkie …'", "'walkie talkie range in the woods' reaches Talkie", "2 of 50
replies were five-word fragments"). And no parser can tell which are
closed. Some are: M27's "A test agent named `-e2e` lands under 'Other
agents'" was met by M28's name check; M24's Ollama bullet is superseded by
M26's two-step removal, whose first step is done (PROMOTIONS.md:211); M31's
last-but-one bullet says of itself "(already listed under M25)".

Change: the coordinator triages the 30 bullets once, in the build brief, as
tool commands (`leftover add … --owner drew|coordinator`, and `leftover
close … --how …` for the closed ones). The 24 older cells and bullets are
imported one row each, text "see PROMOTIONS.md:<line>", marked untriaged,
and `resume` prints their count until the coordinator closes or splits
them. Criterion 2 then reads: "each row of the brief's list is in the
database in the state the brief gives; each older cell is one untriaged row
naming its line".

## 8. Plumbing 1 against plumbing 2, 9 and 12, and M32 (Q1)

- **What and why.** Plumbing 1 puts "what it is, why, Drew's words" in the
  database; plumbing 2 says no fact is in both; plumbing 11 keeps the
  criteria file written first, and it holds all three (every criteria file
  opens with `Source:` and a `Handoff line:`). M32 criterion 6 then tests
  the copy: "they equal the criteria file's handoff line and source".
  Resolve it by not copying: the row holds the criteria path; the exporter
  reads the handoff (it does today, `handoff_of`, `work_export.py:424`) and
  the `Source:` paragraph from the file's head. Plumbing 9's "its prose
  parsers for `docs/sprint/` … leave the live path" gets that one
  exception, which is two anchored reads of a file whose shape WORKFLOW
  step 2 fixes.
- **"Built from the database and one `herdr agent list`" (plumbing 9)
  against "the same keys".** The deployed page reads keys that only git
  or PROMOTIONS.md gives: `w.repo` (branch, HEAD, rebase), `it.uncommitted`,
  `s.committed_at`, `r.line`, `r.heading`, `r.heading_adds`, `r.tag_only`
  (`orchestrator/static/work.html`). Criterion 8's "takes no git lock"
  implies git is still read. Say which: `rev-parse` and `for-each-ref`
  stay (two lock-free commands; `check` needs the tags anyway); the `git
  log` over `docs/sprint` goes, a step's commit coming from its row. For a
  promotion made after M31, `line` is null and `heading` is built from the
  row.
- **What reaches the LAN.** Plumbing 12 covers "`why` and Drew's words".
  Plumbing 9 also adds "open decisions" to the JSON, and M32 point 7 draws
  "its leftovers, the decision it waits on" while M32 point 11 lists what
  may show and does not list them. Leftovers are, in part, a list of known
  weaknesses: "any LAN client can post a large unlock body once editing is
  on", "anything that can read the hash in `.env` can write dev's live
  config" (PROMOTIONS.md:24). M27's own question to Drew about what `/work`
  shows the LAN is still open (PROMOTIONS.md:331, "Say if any of it should
  stay off"). Until he answers: the JSON carries decisions' text (they are
  his to answer) and, for leftovers, counts per item and owner only.
- **"It would simplify the file structure" (Drew).** The item removes no
  kind of file: criteria, review, build, test and fix reports all stay,
  PROMOTIONS.md stays frozen, and plumbing 2 adds `M<n>-promote.md`. What
  goes away is appending to one long file, and reading prose for state.
  Tell Drew that in the promotion report in those words, so the result is
  not measured against a count of files.

Change: as each bullet says; add to "Out of scope": "reducing the number of
report files".

## 9. `agent.py` recording: five things the plumbing leaves open (Q7)

1. **The suite would write the live database.** M28's tests launch with a
   temp repo and a temp state directory
   (`tests/test_m28_agent_tool.py:224`, `monkeypatch.setenv("KRONK_AGENT_STATE", …)`;
   `:248`, `"--repo", repo`). If `agent.py` writes `REPO/data/work.db` by
   its own location, 7,796 tests write production's queue. The database
   path comes from `--repo` (the repo the agent was launched in) or
   `KRONK_WORK_DB`, never from `__file__` alone, and a test proves a
   launch in a temp repo leaves the real file's mtime alone.
2. **One code path.** Plumbing 3 says "written only through one tool,
   `scripts/work.py` … Nothing else writes it"; plumbing 6 has `agent.py`
   write. `agent.py` loads `work.py` the way it loads `work_export.py`
   (`scripts/agent.py:320`) and calls its functions; no SQL in `agent.py`.
3. **`status` is polled and `wait` runs for up to 3 h in the background**
   (`--timeout` default 10800). Finishing a step must be idempotent, and
   `wait` writes concurrently with the coordinator (point 3).
4. **Not done is not finished.** `wait` exits 2 or 3 with the step still
   `started`. `close` on an agent whose step is not finished marks it
   `stopped` with why (M3's retest agent, "stopped by the model's safety
   control", PROMOTIONS.md:11, is the real case). A follow-up `prompt`
   reopens nothing; the next `wait --expect-commit` updates the same row's
   commit.
5. **No step for `--probe`, `investigate` or `--no-report`.** They are on
   no item by the tool's own rule (`docs/features/agent-tool.md`, "not
   attributed to an item").

A write that fails mid-item: the agent command still does its job
(plumbing 6, right), and the hole is found by `check` ("herdr and the state
directory know `m33-build`; no started step") and closed by `step start
--at <time>` from the state file's `launched_iso`. Say so in plumbing 6.

Also for the build brief, not the criteria: no agent runs `work.py` or the
new exporter against the live database (guard rail: "Do not edit … `./data`
… by hand"); every agent run names `--db` in scratch. The coordinator runs
the live import at promotion, as it installs timers.

Change: plumbing 6 gains the five lines; criterion 6 gains "a step left
`started` by a failed write or a dead agent is named by `check` and
repaired by one command".

## 10. Split it in two (Q8)

`PROMOTIONS.md:352`: "An edit to `scripts/work_export.py`, or to the shape
of a file it reads, is live on production's page within a minute." So the
exporter switch reaches production when the build agent commits it, before
any test, and it needs the live database to exist at that minute. That is
a different risk from the rest of the item, and it is where M27's fix
round was.

- **Step 1: the store and the tools.** Schema, `work.py` (with `resume`,
  `check`, the dump), `import-legacy`, `agent.py` recording, the backup
  member, the WORKFLOW.md and CLAUDE.md edits. `work_export.py` is not
  touched; `/work` keeps reading files. Undo is deleting one file.
  Criteria 1, 2, 4, 5, 6 (without the exporter clause), 7, 9. Step 1's own
  promotion is the last entry written to PROMOTIONS.md, so `/work` still
  shows it.
- **Step 2: `/work` from the database.** The exporter reads rows; new keys
  for M32; PROMOTIONS.md frozen with its header. Criteria 3, 6's exporter
  clause, 8, plus point 12's page check. Built in scratch and moved in one
  step, as M27 ruled (`M27-criteria.md:182`).

Between the steps the coordinator runs a real item (M30) on the tool, as
M28 was used on its own item, and step 2 is built against a database with
real use in it. Cost: a second build and test, and for that gap state is
kept twice (files parsed for the page, rows for `resume`). I think the gap
is worth it; M32 waits either way.

## 11. Smaller than the plumbing, and where the file lives (Q8, Q9)

More than one operator and one coordinator need:

- **Two tables for leftovers and decisions.** They are one list: a thing
  about an item (or none), owned by Drew or the coordinator, open or
  closed, closed how. "For Drew" leftovers are decisions already (M31:
  "For Drew: cap those agents' answers, or accept"). One table with a
  kind, and escapes as a third kind (Appendix A).
- **A log of "old and new value" per field.** One row per command: time,
  who, the command line, and the changed rows as JSON. Same history, no
  per-column code.
- **"One line of JSON or a short table (`--json` everywhere)".** The
  reader is a model. JSON lines for everything, as `agent.py` does; plain
  text for `resume` only. Drew's view is `/work`.
- **`step start` for the coordinator's own steps.** Nobody uses when the
  coordinator began writing criteria. One command when the file is
  committed.
- **`work.db.bak-<n>`, three kept**: replaced by the dump (point 4).

Not proposed: a queue in a committed text file. It would be smaller, and
Drew's words settle it ("You should store them in a database instead").

**Where.** As planned, `data/work.db` is inside every production
container's reach, whatever its mode. `docker inspect kronk-orchestrator-1`
shows `…/kronk-releases/item-M31/data->/data:true` (read-write; `data` is
a link to the repo's, `scripts/deploy_tag.sh:84`, `LINKS=(data secrets …)`),
and the image runs as uid 1000 (`orchestrator/Dockerfile:27`, `useradd
--uid 1000`), which is drew's. Mode 600 fences nothing there. `tool_service`
and `voice_tracer` mount `./data` too. That matters more for this file
than for `solar.db`: `resume` prints its `next_step` and leftover texts to
a session that launches agents with permissions skipped, so a write to
this file from a LAN-facing container becomes instructions.

The orchestrator has no need of the file (plumbing 9 keeps the JSON as the
bridge), so it need not be under a mount: `~/.local/state/kronk-work/work.db`,
beside `~/.local/state/kronk-agents/` where M28 put its state for the same
reason ("outside the repo, mode 600", `scripts/agent.py:32`). Costs: the
backup member is a `HOME` path (the list has those: `HOME / "kronk-voice-labels"`,
`scripts/backups.py`), one line in the restore runbook, and it is not with
the other `.db` files. If the coordinator keeps `data/work.db` for
convention, plumbing 3's "Nothing else writes it" should read "nothing
else is meant to; three containers can".

**Dev and production.** One database for both: it describes the repo, not
an instance. The dev orchestrator sees only `data/dev` (`docker inspect
kronk-orchestrator-dev-1`: `…/kronk/data/dev->/data:true`, no `/shared`),
so it cannot read `data/work.db` in either place, and production runs
whatever release is deployed, which may be older than the schema. The JSON
with additive keys is the right bridge for both reasons; keep it, written
to both paths as now (`work_export.py:974`).

M30 for comparison: its database must be under `data/` because the
orchestrator reads it (`mode=ro`, M30 plumbing 1). The two then differ on
purpose: M30's is read by a container and marked secret; M33's is read by
none and is not secret. Same in both: one writer, WAL, mode 600, a
`sqlite` member, and the "nightly" error of point 4.

For M32's test (its criterion 5, "a scratch database served to dev"): the
exporter needs `--db PATH` beside its existing `--out`; and the timer
rewrites `data/dev/work.json` every 60 s (15 s after M32), so the M32
reviewer has to say how a test holds dev's file still. Not M33's to
solve; M33 only has to take `--db`.

Change: plumbing 1 (one table), 3 (location, or the corrected sentence), 5
(JSON lines; `step add` for coordinator steps), 7 (log shape, dump).

## 12. Criteria that cannot be checked as written, the rest (Q1)

- **Criterion 3**, "the same … as before it at the same moment". Two
  exports a minute apart differ in `ts`, `made_at`, `run_s` and anything
  herdr says. Say how: old and new exporter run once each over the same
  commit and the same recorded herdr answer; every key of schema 1
  compared; the keys allowed to differ listed by name.
- **Criterion 3 tests dev's page; production's page is the older one.**
  The deployed `work.html` (from `item-M31`) reads the new JSON the minute
  the exporter changes. Add: the released page, loaded against a new-shape
  JSON holding a promotion with no PROMOTIONS line, a queued item with no
  file and a dropped item, shows no error and no "undefined". By hand in a
  browser (M29 was dropped).
- **Criterion 4**, "`resume` and the exported JSON are right after every
  step": right against what? The test brief carries the expected `resume`
  lines per step, or the criterion names the fields checked.
- **Criterion 6**, "says which in one line": add within what time, and
  what `resume` exits with (non-zero, so a session cannot read a broken
  database as an empty queue: "missing" and "empty" must not print "nothing
  under way").
- **Criterion 2**, "same stage": for 32 of 35 items the stage is
  `promoted`, and the rest are "planning: criteria written". It checks
  almost nothing; point 5's list replaces it.
- **Out of scope, "Agents writing state themselves"** agrees with the
  rest; but the test agent of criterion 4 drives `agent.py` with a fake
  herdr, which writes state. Say "in a scratch database".
- **Plumbing 11** has the build agent rewrite CLAUDE.md item 8 and three
  WORKFLOW.md sections. Those are the coordinator's own files by
  CLAUDE.md's Git rule ("agreed changes to the workflow file and this
  one"), and the rewrite must keep what point 6 lists (the two sweeps, the
  escape count, "record the answer before acting"). Either the
  coordinator writes them, or the criterion lists the sentences that must
  survive.

## Appendix A: a schema to build to (Q2)

Run in scratch with the four walks of Appendix B (`sqlite3` 3.46.1 through
Python). Seven tables.

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;   -- schema_version
CREATE TABLE item (
  id          TEXT PRIMARY KEY,                 -- 'M33', 'M21-step2'; allocated by `add`, never reused
  title       TEXT NOT NULL,
  tier        INTEGER CHECK (tier IN (1,2,3)),  -- NULL before the tiers (M1 to M27)
  state       TEXT NOT NULL CHECK (state IN ('queued','held','active','promoted','dropped')),
  position    INTEGER,                          -- 1..n while queued or held; renumbered on every move
  after_item  TEXT REFERENCES item(id),         -- may not start before this one is promoted
  criteria    TEXT,                             -- path; what, why and Drew's words are read from it
  next_step   TEXT,                             -- the coordinator's note
  waits_on    TEXT CHECK (waits_on IN ('drew','outside','item')),
  waits_note  TEXT,
  ended_why   TEXT,                             -- dropped: why, in a sentence
  created_at  TEXT NOT NULL,
  ended_at    TEXT,
  CHECK ((position IS NOT NULL) = (state IN ('queued','held'))),
  CHECK ((waits_on IS NULL) = (waits_note IS NULL))
);
CREATE TABLE step (
  id          INTEGER PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES item(id),
  kind        TEXT NOT NULL CHECK (kind IN ('criteria','review','build','test','fix','other')),
  round       INTEGER NOT NULL DEFAULT 1,
  name        TEXT NOT NULL DEFAULT '',         -- kind other: 'audit', 'change1', 'restore-build'
  by          TEXT NOT NULL CHECK (by IN ('agent','coordinator')),
  agent       TEXT,
  model       TEXT,
  state       TEXT NOT NULL CHECK (state IN ('started','finished','stopped')),
  report      TEXT,                             -- path
  commit_sha  TEXT,                             -- the commit the report was found in (reads a deleted file too)
  verdict     TEXT CHECK (verdict IN ('PASSES','FAILS','NOT DETERMINED')),   -- as the report says; copied by the tool
  graded      TEXT CHECK (graded IN ('pass','fail')),                          -- the coordinator's grading, when it rules
  note        TEXT,                             -- 'stopped: idle 5 min'; 'RESTORE PASSES' (a second subject)
  started_at  TEXT,                             -- NULL for imported steps: only a commit date exists
  ended_at    TEXT,
  UNIQUE (item_id, kind, round, name),
  CHECK (state = 'started' OR ended_at IS NOT NULL)
);
CREATE TABLE promotion (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL DEFAULT 'promotion' CHECK (kind IN ('promotion','followup')),
  tag         TEXT,                             -- NULL: none (M6, M16b); checked against git by the tool
  commit_sha  TEXT,
  at          TEXT NOT NULL,                    -- as written, for imported rows
  deployed    INTEGER CHECK (deployed IN (0,1)),-- NULL: not known (imported)
  verified    TEXT NOT NULL,                    -- path of M<n>-promote.md; 'PROMOTIONS.md:<line>' for imported
  untested    TEXT                              -- what shipped that no test agent saw (WORKFLOW step 7)
);
CREATE TABLE promotion_item (                   -- one tag for M3, M4, M4b; two promotions of M1b
  promotion_id INTEGER NOT NULL REFERENCES promotion(id),
  item_id      TEXT NOT NULL REFERENCES item(id),
  PRIMARY KEY (promotion_id, item_id)
) WITHOUT ROWID;
CREATE TABLE open_thing (                       -- leftovers, decisions owed, escapes: one list
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('leftover','decision','escape')),
  item_id     TEXT REFERENCES item(id),         -- NULL: about no item
  owner       TEXT CHECK (owner IN ('drew','coordinator')),   -- NULL: imported, not triaged
  text        TEXT NOT NULL,
  blocks      INTEGER NOT NULL DEFAULT 0 CHECK (blocks IN (0,1)),  -- 1: the item waits on this
  source      TEXT,                             -- 'PROMOTIONS.md:421', 'M31-e2e.md note 3'
  opened_at   TEXT NOT NULL,
  closed_at   TEXT,
  closed_how  TEXT,                             -- the answer; 'fixed in M31'; 'struck: …'
  CHECK ((closed_at IS NULL) = (closed_how IS NULL))
);
CREATE TABLE log (                              -- one row per writing command; append-only by trigger
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, cmd TEXT NOT NULL, changes TEXT NOT NULL
);
CREATE TRIGGER log_no_update BEFORE UPDATE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TRIGGER log_no_delete BEFORE DELETE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
```

States. An item: `queued` → `active` → `promoted`; `queued` ⇄ `held`;
`queued`, `held` or `active` → `dropped`. An item split into steps is two
items (`M21-step1`, `M21-step2`) with `after_item`. A step: `started` →
`finished` (report committed) or `stopped` (agent closed with the work not
done); a coordinator's step is inserted `finished`. Stage is not here
(point 1).

## Appendix B: four real items walked through it (Q2)

**M27** (review, build, test PASSES, fix by ruling, test 2 PASSES, deployed).
Item `active`; steps criteria, review, build, test 1 `PASSES`, fix 1, test
2 `PASSES`; one promotion `item-M27`, `44bb86b`, deployed 1; seven
leftovers, one Drew's. Fits. No place, and none needed: "`kronk-work-export.timer`
installed and enabled (18:25)" is a host action by the coordinator between
build and test; it is prose in the promote file. One real gap: after test 1
PASSES the rows say "to promote", while the coordinator had ruled a fix
round. Only `next_step` ("fix round 1 by ruling") says otherwise, so
`resume` must print `next_step` beside the stage, always.

**M28** (FAILS twice, promoted by ruling, no deploy). Tier 1; test 1
`FAILS` graded `fail`; fix 1; test 2 `FAILS` graded `pass` (the ruling at
`M28-criteria.md:181`); promotion `item-M28`, deployed 0. This is why
`graded` exists: without it a promoted item's last verdict reads FAILS with
no reason on the row, and `stage()` would say "waiting for the coordinator"
until the promotion row. `stage()` should read `graded` before `verdict`.
No place: "The second test ran before the tiers were agreed"
(PROMOTIONS.md:365): the tier changed while the item was under way. The log
holds the change; nothing else needs to.

**M29** (ordered, held, dropped, file deleted). `add` (queued, position);
criteria step `finished`, `commit_sha 7380c43`; a decision `open_thing`
with `blocks = 1` ("tell me more about m29. maybe we don't actually need to
do this?") and item `held`, `waits_on = 'drew'`; the decision closed with
Drew's answer; item `dropped`, `ended_why`, position cleared. Fits. Two
things had no place before this schema and are why two columns exist: the
criteria file no longer exists in HEAD, so the step needs its commit; and
the drop changed another item ("M30 criterion 6 checked by hand",
`47a8540`), which stays a ruling in M30's file and is not a row.

**M31** (tier 1, build, one test PASSES, deployed). Fits: three steps, one
promotion deployed 1, six leftovers of which the first is a decision for
Drew ("cap those agents' answers, or accept"). No place, none needed:
`llama-devstral-q4` was stopped after the deploy and memory read 75 s
later; that is the promote file's text. Worth a field: "The suite at the
tag … not rerun by the coordinator" is what `untested`-like prose is for;
it stays prose.

Real events from older items with no place before I added one:
a follow-up entry on a promoted item ("M16 installed (2026-11-20)", "M26,
the removal's first step") → `promotion.kind = 'followup'`; two promotions
of one item and one tag for four (M1b; `sprint-M3-M4-M4b`) →
`promotion_item`; a second verdict subject in one report (`M4b-e2e.md`) →
`step.note`, not a column; the coordinator's own fix after a last test
(seven items) → a `fix` step `by = 'coordinator'` and `promotion.untested`.
Still no place, on purpose: a promotion later reversed by Drew (M1b,
PROMOTIONS.md:10). It happened once; it is a leftover closed with "reversed
by Drew, restored in sprint-M3-M4-M4b".

## Appendix C: where rows and evidence can disagree (Q3)

| # | The rows say | The evidence says | Wins | What the tool does |
|---|---|---|---|---|
| 1 | step `started`, agent named | herdr: no such agent, or `idle`/`done` with no report committed | evidence | `check`: "m33-build is not working; step still started". Stage falls to the last finished step. |
| 2 | no started step | herdr: `<item>-<role>` working (a failed write; an agent started by hand) | evidence | Stage shows the agent, as today; `check` offers `step start --at`. |
| 3 | step `started` | the report is in HEAD since the launch, tree clean | evidence | `check` finishes nothing by itself; names the command. `agent.py status` would have done it. |
| 4 | step `finished`, verdict X | the committed report now reads Y (a report corrected later; the coordinator's fix to a verdict line) | evidence | `check` names both; `step refresh` re-copies. |
| 5 | step `finished`, commit C | C is not in the branch (a rebase, a reset) | evidence | `check` names it; the row is not trusted for stage. |
| 6 | no promotion row | tag `item-<id>` exists | evidence, as today: "promoting" | `stage()` unchanged (`work_export.py:749`). |
| 7 | promotion row with tag T | T is not in git | evidence | `promote` refuses it at write; `check` names it after. |
| 8 | no row for the item | `docs/sprint/<id>-criteria.md` is committed | evidence that an item exists; the row decides nothing yet | `check`: "criteria file with no item"; the exporter lists it under `not_read`, so `/work` shows the gap. |
| 9 | `queued`, `held`, `dropped`, position, `after_item`, `waits_on`, `next_step`, `graded`, tier, an open leftover or decision | nothing: no file, tag or agent can show these | the row | Nothing to compare. Guarded by the log and the dump only. |

Row 9 is the whole reason for the database. Rows 1 to 8 are why a stored
stage is not needed and would be wrong in eight ways.
