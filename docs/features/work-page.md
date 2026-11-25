# Feature: /work, the work items in a page

**Shipped:** work item M27 (2026-11-24 build). **Origin:** Drew's order of
2026-11-24: "i want to be able to track this work in a UI. A backlog,
currently working (planning/reviewing/implementing/testing/etc), what's
already been worked, and so forth. I should able to visit kronk/work and
the page displays." Criteria and evidence: `docs/sprint/M27-criteria.md`,
`M27-review.md`, `M27-build.md`, `M27-e2e.md`, `M27-fix1.md`.

## What it does

`/work` (and `/dev/work`) shows, top to bottom:

1. **One state line**: when the export was made, from which branch and
   commit (or "detached HEAD", and "rebase in progress"), the
   coordinator's state, and "herdr: N agents seen" (and "N not readable
   (skipped)" when herdr described an agent in a shape the exporter cannot
   read). The coordinator reads "working" when its pane works, "waiting on
   an agent" when it does not and a named agent in this repo does, "idle"
   only when nothing here is working, and "not known" otherwise (herdr not
   reachable, no single unnamed pane, or status `unknown`). A coordinator
   pane that herdr calls `blocked` reads "idle" or "waiting on an agent".
   With herdr not reachable the line says agent state is not known, and
   why.
2. **Under way**: every work item that has a step file in `docs/sprint/`
   and no entry in `docs/sprint/PROMOTIONS.md`, with its stage, its
   steps in commit order (time of each file's first commit, each test's
   verdict), its handoff line, and the ROADMAP entry that names it.
3. **Other agents**, when there are any: an agent in this repo whose name
   matches no item under way (an investigator, an agent for a promoted
   item, a name that is not `<item>-<role>`).
4. **Backlog**: the open entries of `ROADMAP.md` (Now, Next, Later,
   Chores), under the file's own section names, each folded to its title,
   its leading `*( … )*` and its first sentence; a click opens the whole
   entry, and an opened entry stays open across the 30 s refresh.
   `` `code` ``, `**bold**`, `*emphasis*` (so the leads and `*Why: …*`) and
   `[text](url)` are rendered; a link is made only for a url that starts
   with `https://` (with `rel="noopener"`), anything else is shown as
   written. An entry linked to an item under way is shown with the item
   instead; one linked to a promoted item stays in the backlog.
5. **Done**: the promotions log, newest entry first, each item named
   once: its id, its title without the id and "pass criteria:" in front,
   tag, date as written, its steps and verdicts (collapsed), and "N lines
   addressed to Drew in its promotions entry; some may be settled since"
   when its entry has any (a count of words in a log, not a list of what
   is open). The entry's heading and a linked Shipped title are shown only
   where they add a word the title lacks (ids, dates and a few small words
   aside). Then the Shipped entries of `ROADMAP.md` that are linked to no
   item, by title and leading `*( … )*` only.
6. **Not read**: the files the exporter listed but did not read as a
   known step (`-audit`, `-change1`, `-restore-*`, `-e2e2-rows`), a test
   report with no verdict line, a file it could not parse.

Nothing is entered on the page and nothing is written back.

## How it works

```
host: kronk-work-export.timer (60 s)
  └─ scripts/work_export.py
       ├─ reads ROADMAP.md, docs/sprint/PROMOTIONS.md, the first 40 lines of each item file
       ├─ git log --no-renames --diff-filter=A --name-only … -- docs/sprint   (first commit of each file)
       ├─ git for-each-ref refs/tags, git rev-parse                         (GIT_OPTIONAL_LOCKS=0)
       ├─ herdr agent list                                                   (once, 5 s limit)
       └─ writes data/work.json and data/dev/work.json (temp file, rename)
orchestrator: GET /api/work  → the file and its freshness (fresh / stale / missing / unreadable / unknown_schema)
              GET /work      → orchestrator/static/work.html, which fetches api/work (relative)
```

- **Why a host exporter.** Production runs from a release directory, a
  snapshot of a tag; an item under way exists only in the working tree.
  The container mounts `data/`, so a file there is the bridge, as for
  `gpu_mem.json` and `llama_props.json` (`resources-live-memory.md`).
- **Stage** is computed in the exporter, by `stage()` and the tables
  above it, from four facts: the item's committed step files, whether
  the tag `item-<id>` exists, whether it has a promotions entry, and the
  agents named for it with their status. The table is in
  `M27-criteria.md` ("Stage is a pure function"); one test per row in
  `tests/test_m27_work_export.py`. Only `working` and `blocked` agents
  decide a stage; `blocked` is shown in red.
- **An agent's item and role** are parsed from its herdr name:
  `^(m\d+[a-z]?(-step\d+)?)-(review|build|test|fix)\d*$`.
- **The state line's word for the coordinator** (`herdr.coordinator.shown`)
  is decided in the exporter, `coordinator_shown()`, and tested there.
- **The fold of a backlog entry** (`fold`: `head`, `rest`) is decided in
  the exporter, `fold()`: the first `.`, `?` or `!` (with closing quotes,
  brackets or stars) before a space, after the title and lead, where no
  `code`, bold, emphasis or link is left open and the word before is not a
  bare number ("1." of a list); at most 320 characters, else cut at a
  space. `text` is `head` + " " + `rest`.
- **A Done row's words** (`short_title`, `heading_adds`, a Shipped
  entry's `adds`) are decided in the exporter, `short_title()` and
  `adds_to()`.
- **An agent herdr describes in a shape the exporter cannot read** (not
  an object, a status that is not a string, a `cwd` holding a NUL) is
  counted in `herdr.skipped` and left out; an error while reading herdr's
  answer makes agent state "not known". Neither stops the file being
  written.
- **The coordinator** is the one agent in this repo with no name.
  `herdr agent list` carries no workspace label, so `kronk-claude` cannot
  be read by name; two unnamed panes in the repo make it "not known".
- **Verdicts** are read, never guessed: the first line among a test
  report's first 40 that starts with `Verdict: X`, `**Verdict: ITEM X.**`,
  `**<ID> X.**` or `**RESTORE X.**`; a report with two subjects yields
  both (`M4b-e2e.md`).
- **Promotions entries**: a table row, or a `##`/`###` heading that starts
  with an item id. A heading entry's body runs to the next heading, tables
  included: a table inside an entry is its body, not promotions (a
  heading with no id, "After the sprint", ends the entry, so its table is
  read). Ids in a row's label match on word boundaries (`ARM64` is no
  item) and take `M30-step1` as written. "For Drew" is counted with any
  white space, a line break included, between the two words.
- **Links** from a ROADMAP entry to an item: only `work item Mxx`,
  `sprint Mxx` or `sprint item Mxx` (and "and Mxx") inside the entry's
  leading `*( … )*`, after its lines are joined. `M21` links both steps.
- **What leaves the repo** is the outline: titles, handoff lines, verdict
  words, ids, tags, dates as written, headings, a count of "For Drew"
  lines, the open ROADMAP entries whole, Shipped titles and leads,
  agents' names and statuses. Never a report body, a promotions cell, a
  Shipped body, or herdr's `terminal_title`, `agent_session` or `cwd`; an
  agent outside this repo is counted, not named.
- **Freshness**: older than 180 s is stale, and so is a file dated more
  than the route's slack in the future. A stale outline is still shown,
  marked STALE (it is the last known state); the banner gives the route's
  reason ("written N s ago" or "dated N s in the future: the host clock or
  the file is wrong").
- **Temp files**: each write goes to `.work.json.XXXXXXXX` and is renamed;
  a run killed in mid-write leaves its temp file, and the next run removes
  its own temp files older than ten minutes (regular files of that exact
  name form only).
- **Schema**: `schema` is 1. Once `item-M27` is deployed the exporter
  only adds keys and raises the number when it does; an orchestrator
  that knows a lower schema still reads the file and says that keys were
  added. A missing or non-integer schema is "unknown schema".

## Gotchas

- **A STALE `/work` right after an edit under `docs/sprint/` means the
  exporter threw.** The timer runs the working tree's script every
  minute, so an edit to `scripts/work_export.py`, or a file shape it does
  not survive, is live within a minute. It never fails loud on the page;
  check `journalctl --user -u kronk-work-export.service`. Change the
  exporter in a scratch copy (`--repo`, `--print`, `--out`; the tests
  take `M27_WORK_EXPORT=<scratch copy>`) and move it in in one step.
- **The index lock.** The exporter never runs `git status`, `diff`, `add`
  or `stash`: `git status` takes `.git/index.lock`, and an agent's `git
  add` or `commit` in that instant fails. The three commands it runs take
  no lock (shown under `strace` in a test).
- **An agent between turns can read `done` for a cycle.** herdr reports
  `done` when a turn ends; an agent waiting on a background job may sit
  there for a minute, and the item shows the stage its files give
  ("waiting for the coordinator") until the agent works again.
- **Agent names and closed workspaces are load-bearing.** An agent not
  named `<item>-<role>` shows under "Other agents", not on its item. A
  finished agent whose workspace is still open shows as "done, workspace
  still open" on its item.
- **A git worktree per agent** (ROADMAP "Build workflow", step 2) changes
  what this exporter can see: step files would reach `main` only at
  merge, so an item's stage would rest on its agent alone until then.
  That item should name this page.
- **Dates from `PROMOTIONS.md` and `ROADMAP.md` are shown as written**,
  never parsed (`new Date('2026-11-15')` prints the 14th here). Step times
  are git author dates with their offset, shown in the browser's time.
- **`/services` links `/work` absolutely**, like every page there: from
  `/dev/services` it opens production's `/work`.

## Operations

- Install: the header of `systemd/kronk-work-export.service` (install,
  check, one-shot, remove).
- Eyeball: `python3 scripts/work_export.py --print`.
- Health: `systemctl --user list-timers kronk-work-export.timer`;
  `curl -s localhost/api/work | jq .source`.

## Blog hooks

- A status page with no second store: the backlog, the work under way
  and what is done, read from the markdown files and git that already
  hold them.
- "Verdicts are read, never guessed": 49 test reports in five forms, and
  the two that hold a verdict word in a sentence.
- `git status` is not read-only: it takes the index lock, and an
  unattended agent's commit in the same instant fails.
- What a LAN page may show: the outline of a document, not the document.
- Folding a markdown paragraph to its first sentence without a markdown
  parser: where a sentence ends when `2026.11.2`, `1.` and `` `a. b` ``
  are not ends.
- The terminal multiplexer as the source of truth for "who is working".
