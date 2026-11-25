# M27 pass criteria: /work, the work items in a page

Revised 2026-11-24 after the design review (`docs/sprint/M27-review.md`):
all 26 of its points are taken; where this file and the review disagree,
this file decides. The review holds the measurements and counts this file
leans on; read it beside this.

Source: Drew's order of 2026-11-24: "i want to be able to track this work
in a UI. A backlog, currently working (planning/reviewing/implementing/
testing/etc), what's already been worked, and so forth. I should able to
visit kronk/work and the page displays. Add this to the backlog to happen
after M26."

Handoff line: /work shows the backlog, each work item under way with its
stage, and what is done, read from the files that already hold it.

## What the investigation found (coordinator and reviewer, 2026-11-24)

- The state of the work is already written down, in the working tree:
  `ROADMAP.md` (five sections holding 60 entries: Now 2, Next 7, Later 5,
  Chores 1, Shipped 45; 59 of them span lines), the 138 item files under
  `docs/sprint/` (30 items with M27; M6 has an audit and no criteria
  file), `docs/sprint/PROMOTIONS.md`, and git tags (`sprint-<item>` to
  M23, `item-<item>` since M24; six promoted items have no tag of their
  own name).
- **The production orchestrator cannot see any of it.** Its image holds
  `*.py`, `static/` and the corpus; it mounts `data/`. It runs from a
  release directory, a snapshot of a tag: an item under way exists only
  in the working tree. The repo's answer to this seam is a host exporter
  on a timer that writes a JSON file under `data/`.
- Which agent is working now is in no file. herdr knows: `herdr agent
  list` returns every agent with `name`, `agent_status` (`idle`,
  `working`, `blocked`, `done`, `unknown`), `cwd` and more, in 2 ms, from
  a plain user systemd unit. The coordinator names agents `<item>-<role>`
  (`m25-build`, `m25-test2`, `m26-review`, `m26-fix1`); its own workspace
  is `kronk-claude` and reports `working` or `done`. A closed workspace
  drops out of the list. **An agent whose report is committed sits `done`
  in an open workspace until the coordinator closes it.**
- The shapes are not uniform; the review's "Counts from the scan" and its
  points 2, 3 and 4 list them. In short: `PROMOTIONS.md` has table rows
  (21 rows for 24 items, labels with glosses, tags that are not built from
  the id), `##` and `###` headings, and later notes on promoted items;
  the test reports' verdicts come in five forms, on line 1 to 39, and one
  file (`M4b-e2e.md`) holds two verdicts for two subjects; five file
  shapes fall outside `criteria/review/build/e2eN/fixN`.
- `ROADMAP.md`'s text is not all fit for a LAN page: "Voice accuracy"
  (open) quotes three mis-heard household utterances; Shipped entries
  quote a spoken request, say the backup tar's secrets are unencrypted on
  a named share, and say `/settings` has no passphrase.
- Git, measured on this tree: first-commit dates by one `git log` per
  file take 2.56 s; one call for the whole directory takes 0.03 s. `git
  status` creates and removes `.git/index.lock`, and an agent's `git add`
  or `commit` in that instant fails.
- Pages are routes of the orchestrator; nginx needs no change. `data/` is
  ignored by git.

## Plumbing, decided by the coordinator

**One source, no second store.** A host exporter, `scripts/work_export.py`,
run by a user timer every 60 s, reads the working tree and writes
`data/work.json` and `data/dev/work.json` atomically. The orchestrator
serves it at `/api/work` and the page at `/work`. Nothing is entered on
the page and nothing is written back.

**What the exporter runs on the host, and nothing else:**

- `git log --no-renames --diff-filter=A --name-only
  --format='@%H%x09%aI' --reverse -- docs/sprint` (every step file's
  first commit, author date), `git for-each-ref refs/tags`, `git
  rev-parse` (branch and short HEAD), with `GIT_OPTIONAL_LOCKS=0`. Never
  `status`, `diff`, `add` or `stash`.
- `herdr agent list`, once, with a 5 s limit.
- Reads of `ROADMAP.md`, `docs/sprint/PROMOTIONS.md` and the first 40
  lines of each item file, from the tree.
- No network. No write but the two output files and their temp files.

**What is exported is the outline, not the documents.**

- From an item file: its title (the first line), its handoff line if it
  has one, a verdict word. No other text.
- From `PROMOTIONS.md`: item ids, tags, dates as written, an entry's
  heading, and a count of the lines addressed to Drew ("For Drew",
  "**For Drew:**") in the item's entry. No cell or body text.
- From `ROADMAP.md`: for the open entries (Now, Next, Later, Chores) the
  whole entry; for Shipped entries the title and the leading `*( … )*`
  only.
- From herdr: an agent's `name` and `agent_status`, and how many agents
  herdr returned. Not `terminal_title`, `agent_session` or `cwd`. Agents
  whose `cwd` is not this repo are counted, not named.
- The branch and short HEAD the export was made from, a `schema` number,
  and its time.

**Ids and files.** An item id is `M\d+[a-z]?(-step\d+)?`. Step files are
`<id>-criteria`, `-review`, `-build`, `-e2e`, `-e2eN`, `-fixN`. Other
suffixes (`-audit`, `-changeN`, `-restore-criteria`, `-restore-build`,
`-e2e2-rows`) are listed as steps under their own names and named in the
export's "not read" list; never dropped, never read as a test. M1b's
restore files are steps of M1b. A step is a file git has a first commit
for; a file in the tree with no commit is listed as "written, not
committed" with no date.

**Verdicts are read, never guessed.** The verdict of a test report is the
first line among its first 40 that matches one of these, anchored at the
start of the line: `Verdict: X`, `**Verdict: ITEM X.**`, `**<ID> X.**`
(the id in any case, `M21 STEP n` included), `**RESTORE X.**`, with X one
of PASSES, FAILS, NOT DETERMINED. A file with verdicts for two subjects
yields both, each with its subject: `M4b-e2e.md` gives RESTORE PASSES and
M4B FAILS. A report with none yields "verdict not read" and is named in
the "not read" list.

**Promotions.** An entry is a table row, or a `##` or `###` heading that
starts with an item id. The ids of a row are every
`M\d+[a-z]?( step \d)?` in its label before the first parenthesis ("M3,
M4, M4b, M1b restore" is four). The tag is read from the cell, never
built from the id. A second entry for an id already promoted is a note on
it, listed by its heading. M1b has two promotions; both are shown. A
heading that starts with no id ("After the sprint") is no entry.

**Backlog links.** An entry's lines are joined first. An entry is linked
to an item only by `work item Mxx`, `sprint Mxx` or `sprint item Mxx`
inside its leading `*( … )*`, and "and Mxx" there; `M21` links both
steps. A mention in the body is not a link. A linked open entry is shown
with its item when that item is under way, and in the backlog otherwise.
A linked Shipped entry's title is shown on its item's Done row and not
repeated.

**The page, top to bottom:**

1. One line of state: when the export was made and from which branch and
   commit; "coordinator: working" or "coordinator: idle" (the
   `kronk-claude` workspace's status; not known if herdr is not
   reachable); "herdr: N agents seen".
2. *Under way*: every item that has a step file and no promotions entry,
   and nothing else. A line under the heading says it lists work items
   only.
3. *Other agents*, when there are any: agents that match no item under
   way (an investigator, an agent for a promoted item, a name the pattern
   does not parse), as name and status.
4. *Backlog*: the open entries under `ROADMAP.md`'s own section names,
   verbatim ("Now — in flight", "Next — agreed, not started", "Later —
   wanted", "Chores"), in file order.
5. *Done*: `PROMOTIONS.md` in reverse file order, each item with its
   title, tag, the date as written, its steps and verdicts collapsed, and
   "N lines for Drew" when its entry has any; then the Shipped entries
   that are linked to no item.

**Stage is a pure function** of an item's step files, whether a tag
`item-<id>` exists, whether a promotions entry exists, and the agents
named for it with their status. An agent's role is parsed from its name:
`^(m\d+[a-z]?(-step\d+)?)-(review|build|test|fix)\d*$`.

| Agents for the item | Last step file | Tag | Stage shown |
|---|---|---|---|
| one `working`, role review / build / test / fix | any | no | reviewing / implementing / testing / fixing, "agent `<name>` working" |
| one `blocked` | any | no | the role's stage, "agent `<name>` BLOCKED" (shown prominently) |
| two `working` | any | no | both, as above |
| none working or blocked | criteria, or review | no | planning ("criteria written" / "review in; criteria being revised") |
| none | build, or a fix | no | waiting for the coordinator ("build in, not tested" / "fix N in, not retested") |
| none | a test, FAILS or NOT DETERMINED | no | waiting for the coordinator ("test N: FAILS") |
| none | a test, PASSES | no | to promote ("test N: PASSES") |
| any | any | yes, no promotions entry | promoting |
| any | any | promotions entry | promoted (the item is in Done) |

Agents that are `idle`, `done` or `unknown` do not decide a stage; the
item's row may note "`m27-test`: done, workspace still open". With herdr
not reachable the agent rows of the table are skipped and the page says
agent state is not known. An empty answer from a reachable herdr is "0
agents seen", which is different, and shown.

**Dates.** A step's time is its file's first commit's author date, ISO
8601 with offset; the browser shows local time. A date-only value from
`PROMOTIONS.md` or `ROADMAP.md` is shown as the text written, never
parsed (a parsed `2026-11-15` prints as the 14th in this house).

**Text from the repo goes into the page as text**, never as markup.

**Staleness.** The file carries its time. Older than 180 s, missing or
unreadable: `/api/work` says which, the page says so with the age; a
stale file's outline is still shown, marked STALE (it is the last known
state).

**The exporter and production.** The installed timer runs the working
tree's script and production reads the file. The exporter is developed in
scratch (`--print`, `--out`) and moved into `scripts/` in one step, in
the build and in every fix round. Once `item-M27` is deployed, keys are
only added and `schema` says so.

**Units.** `systemd/kronk-work-export.service` (`Type=oneshot`,
`TimeoutStartSec=30`) and `.timer` (every 60 s), with an Operations
header like the other two exporters'. The coordinator installs them
before the test. Agents install nothing. Every list of host timers and
units in the repo that the new pair belongs in is joined (grep for the
existing exporters' names: `README.md`, `CLAUDE.md`'s topology paragraph,
`kronk-context.md`, tests).

## Scope

1. `scripts/work_export.py` with its unit and timer files.
2. `/api/work` and `/work` (`orchestrator/static/work.html`), in the look
   of the existing pages, on relative URLs.
3. The page's line in `/services`' list of pages.
4. `docs/features/work-page.md` (what, how, gotchas, blog hooks: it says
   that a STALE `/work` after an edit under `docs/sprint/` means the
   exporter threw, that an agent between turns can read `done` for a
   cycle, and that the worktree-per-agent item on the roadmap changes
   what this exporter can see); `README.md`'s service map and page list;
   `docs/sprint/WORKFLOW.md`: one paragraph saying the page reads the
   step files and the agent names, so the naming `<item>-<role>` and the
   closing of finished workspaces are now load-bearing.

Out of scope: editing anything from the page; the text of the reports; a
launcher for agents; charts; notifications; changing the shape of
`ROADMAP.md`, `PROMOTIONS.md` or the report files to suit the exporter.

## Criteria

1. **Every item.** On today's tree the export holds 30 items: Under way
   is exactly M27; Done holds the other 29. For each item: id, title,
   steps in commit order with times, each test's verdict, promotion tag
   and date as written. Shown against a hand-checked table for M7, M9
   (last file a fix, no retest), M16, M3 (NOT DETERMINED; a rows file
   that is not a test), M21-step1 and M21-step2, M1b (two promotions,
   restore files), M4b (`-e2e.md` with two verdicts), M6 (audit only),
   M13 and M20 (tag only), M11 (`sprint-M11-shadow`), M16b (a `###`
   heading, no tag), M25, M26 (a later note under a `###` heading).
2. **Verdicts.** All 49 test reports yield what a person reads in them;
   the report lists every file with the line number the verdict was read
   from. `M4b-e2e.md` yields RESTORE PASSES and M4B FAILS.
   `M3-e2e2-rows.md` yields nothing and is in the "not read" list.
   Fixtures: no verdict line; a verdict word inside a sentence ("First
   test (FAILS)") that must not be read; a verdict on line 41.
3. **Stage is the table.** One test per row of the stage table, with a
   fixture tree and a fake `herdr` on PATH answering each status
   (`working`, `blocked`, `idle`, `done`, `unknown`), no agents, two
   agents of one item, an agent for no item, an agent for a promoted
   item, an agent with an unparseable name, and an agent whose `cwd` is
   another repo. With herdr absent, exiting 1 with its JSON error, or
   slower than 5 s, the export is written within 10 s with agent state
   "not known".
4. **Live, testing.** While the test runs, the test agent shows
   `/dev/work` with M27 as "testing, agent `m27-test` working" and its
   steps so far. (The other agent stages are shown by criterion 3 only.
   That M27 leaves "testing" when the agent stops is the coordinator's to
   record at promotion, from two reads of `/dev/api/work` a minute
   apart.)
5. **Backlog.** The 15 open entries of today's `ROADMAP.md` appear once
   each, under their section's name verbatim, in file order, with the
   whole entry joined; counted per section between its `## ` heading and
   the next. Fixtures cover: an entry on one line; a blank line between
   entries; a nested numbered list inside an entry; `*( … )*` that wraps
   a line; a title with backticks; the bullets of the Conventions
   section, which are not entries.
6. **Done.** `PROMOTIONS.md` in reverse file order; each item with tag
   and date as written; the count of lines for Drew equals a hand count
   for M24, M25 and M26; Shipped entries linked to an item appear on its
   row and not below; the others follow, title and leading `*( … )*`
   only.
7. **Links.** On today's tree the open entries link to M27 and M20 and
   to no other item; "Refactor sprint" (15 ids in its body) and the five
   old "item N" entries link to nothing. Fixtures for a reference broken
   across a line and for "sprint M16 and M16b".
8. **Nothing but the outline leaves the repo.** A fixture tree carries a
   marker string in: every report body, criteria bodies, `-audit`,
   `-change` and `-rows` files, `PROMOTIONS.md`'s cells and entry bodies,
   Shipped entries' bodies, and herdr's `terminal_title`, `agent_session`
   and `cwd`; the marker is nowhere in the export. On the real tree a
   check finds no line of 20 characters or more from any item file body,
   `PROMOTIONS.md` body or Shipped body in the export, other than titles,
   handoff lines and leading `*( … )*`.
9. **The exporter is safe to run every minute.** Under `strace -f`: no
   path ending `.lock` is opened; no write but the two output files and
   their temp files; no socket but herdr's. The git commands are the
   three named. A kill in mid-write leaves the old file whole. A run on
   the real tree takes under 1 s. It does not fail on: an empty file, a
   binary file, a criteria file with no title line, a detached HEAD, a
   rebase in progress (fixture), an untracked step file (listed "written,
   not committed").
10. **Staleness.** `work.json` older than 180 s, missing, unreadable, or
    of an unknown `schema`: `/api/work` says which and the page says so
    with the age; a stale outline is still shown, marked STALE. Fixtures,
    and once live on dev by the compose override method
    (`M25-criteria.md`).
11. **The page.** `/dev/work` rendered in a headless browser: every item,
    step, verdict and stage on the page equals `/dev/api/work` fetched at
    the same moment; one step time and one date-only value checked as
    displayed against the file; all its requests are under `/dev/`;
    nothing reads "undefined", "null" or "NaN"; a fixture title holding
    `<img src=x onerror=…>` is shown literally; at 390 px wide there is
    no horizontal scroll and no row's text is clipped (`scrollWidth <=
    clientWidth` on the body and on each row). The page's state-to-text
    decisions are tables or small functions the suite checks; the report
    says plainly what of the page's script no test runs.
12. `/dev/api/work` answers in under 300 ms at the median of 20 requests
    and causes no model request.
13. Docs changed as scope 4 says.
14. `./scripts/run_tests.sh` passes (before: `7504 passed, 3 skipped, 54
    xfailed`).
15. Nothing is installed, started, stopped or deployed by the build or
    the test, apart from the dev pair. No chat request. `data/` is
    written only by the installed timers. herdr is only read (`agent
    list`). Production unchanged; HA and MA untouched; no sound; no
    notification.

## Rulings (coordinator)

- 2026-11-24, before the test: `kronk-work-export.timer` installed and
  enabled at 18:25 local (first run 0.07 s, 82 KB, 30 items, M27 alone
  under way, herdr known). Remove: `systemctl --user disable --now
  kronk-work-export.timer`.
- 2026-11-24, the coordinator's half of criterion 4: after `m27-test`
  stopped, two reads of `/dev/api/work` 65 s apart gave "testing, agent
  m27-test working" (the export of the minute before) and then "to
  promote, test 1: PASSES", resting on the files.
- 2026-11-24, on the test (`M27-e2e.md`, PASSES, no blocker): **fix round
  1**, for what Drew sees first and for what can blank the page:
  - S1: an agent herdr describes in a shape the exporter cannot read is
    counted and skipped; the exporter still writes its file.
  - S2: a file dated in the future shows the route's reason, not the
    stale sentence.
  - S3: a test for an open entry linked to a promoted item (today "Kronk's
    voice: TTS trial", linked to M20): it stays in the backlog.
  - S4: "For Drew" is counted when a line break falls inside it.
  - S5: inside a heading entry a table is body, not promotions; item ids
    match on word boundaries (`ARM64` is no item); a row label
    `M30-step1` is that item.
  - S6 and the tester's first change: a backlog entry shows its title,
    its lead and its first sentence, and opens to the whole entry on a
    click, as Done rows do; `*( … )*`, `*…*` and `[text](url)` are
    rendered (a link as a link with `rel="noopener"`, its text set as
    text), not shown as markup.
  - The tester's third and fifth changes: a Done row names its item once
    (id, title, tag, date; the entry heading and the Shipped title only
    where they add something); the state line says "coordinator: waiting
    on an agent" when the coordinator is not working and an agent of an
    item under way is, and "idle" only when nothing is working.
  - The tester's second change: beside the count, the words "lines
    addressed to Drew in its promotions entry; some may be settled
    since".
  - N8 (temp files left by a kill): the exporter removes its own temp
    files older than ten minutes.
  - Left over: N1 to N7, N9 to N13; the suite does not run the page's
    script.
- **For Drew, not decided here:** the open `ROADMAP.md` entries are on a
  page any device on the LAN can open. "Voice accuracy" quotes three
  mis-heard utterances with a run id; "Build workflow" says agents run
  with permissions skipped; others name the HA version and what is down.
  This matches what `/settings` and Grafana already show the LAN. The
  fix round folds each entry to its first sentence, which hides none of
  it from a click.

- 2026-11-24, on the second test (`M27-e2e2.md`, PASSES, no blocker): no
  further round. S1 (an agent name holding invalid Unicode still stops
  the write; herdr sends none today), S2 (the rule that only `https://`
  becomes a link is held by a browser run and by no test: the suite does
  not run the page's script) and S3 (a row label's right-hand boundary
  has no test) show on no tree today. Left over, with its notes; S2 is
  the one to close first, with the page-script gap `/resources` shares.
  Promote.

Reports: `docs/sprint/M27-review.md` (done), `docs/sprint/M27-build.md`,
`docs/sprint/M27-e2e.md`, `docs/sprint/M27-fix1.md`,
`docs/sprint/M27-e2e2.md`.
