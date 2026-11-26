# Feature: /work, the work items in a page

**Shipped:** work item M27 (2026-11-24 build). **Origin:** Drew's order of
2026-11-24: "i want to be able to track this work in a UI. A backlog,
currently working (planning/reviewing/implementing/testing/etc), what's
already been worked, and so forth. I should able to visit kronk/work and
the page displays." Criteria and evidence: `docs/sprint/M27-criteria.md`,
`M27-review.md`, `M27-build.md`, `M27-e2e.md`, `M27-fix1.md`. The
"Heard" section: work item M30 step C (`docs/sprint/M30-criteria.md`,
`M30-review.md` points 6 and 7, `M30-build3.md`), from Drew's order of
2026-11-24: "the /work endpoint should also include all the stt
transcriptions to help me troubleshoot. Make it the top of the page but
make it foldable so it doesn't pollute the view".

## What it does

`/work` (and `/dev/work`) shows, top to bottom:

0. **Heard**, folded: what Whisper heard over the last 90 days (see
   "Heard" below).
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

## Heard

The first section, a `<details>` folded on every load. Open or closed is
never stored: the page will run on a wall display nobody can touch, and a
section opened once on that browser must not stay open there.

**Folded**, its line says: runs in the last 24 hours, the newest run's
time, how many of those are marked wrong and how many carry an error, and
"as of" with the time the exporter counted them (household runs; the
counts are the exporter's `summary.household`). After that, the word for a
status file that is not fresh (STALE, MISSING, ...), "last export failed"
when the last run's `ok` is false, or "N warnings".

**Open**: a text filter (what was heard, or the reply) and a "wrong or
error only" switch, both kept across reloads in `localStorage`
(`kronk.work.heard.q`, `kronk.work.heard.flagged`); the newest 200
household runs, grouped by the house's day, one row per run: the time (as
the house wrote it, not the browser's), the satellite (a three-line map in
the page; the entity id is the data and is shown on opening), what was
heard, whole ("not recorded" for a run traced without text, "nothing heard"
when Whisper heard no words), tier and rule (a dash where a blueprint
hand-off has none), a mark for marked wrong and one for an error, and the
reply's first line. Opening a row shows the rest of the reply, the voice
trace id, the Kronk trace id and the run id as text to copy (no page here
builds a Grafana link), the stages, tools, error, the mark's detail, a
window cut short, which Whisper heard it and the stage times. "show older"
pages back through the 90 days, 200 at a time. Dev runs are left out of the
page; the route gives them with `include_dev=1`.

Above the rows, a banner for each thing that is wrong: the status file
stale (older than 180 s), missing, unreadable or of an unknown schema; the
last export failed, with `last_error` and its time (a `last_error` from an
earlier run, `ok` being true now, is a quiet line with its time); the
status file's other signals, since `ok: true` is not the only one (a read
cut short by Tempo's search limit, failed whole-trace reads, failed
Kronk-trace enrichments, rows the hourly check found differing, an hourly
check under way, the record gone); the copy missing, unreadable, of an
unknown schema, or damaged (its row count is not its meta's; its rows are
still shown).

### How it works

```
host: kronk-voice-runs-export.timer (60 s)       docs/features/voice-dataset.md
  └─ scripts/voice_runs_export.py → data/voice_runs.db  (and data/dev/: the last 90 days, built whole, renamed in)
                                  → data/voice_runs.json (and data/dev/: status and summary, no text)
orchestrator: GET /api/voice_runs → orchestrator/voice_runs_api.py
```

- **The route** reads the status file through `servers.read_source` (the
  envelope: fresh, stale, missing, unreadable, unknown_schema) and opens
  the copy once per request, `mode=ro&immutable=1`: nothing is made beside
  it, and a copy the exporter renames into place mid-request is not seen
  (the open file is the old one, read whole). It calls no Tempo, no HA, no
  model, and imports no HTTP client. Both instances read `/data`: the
  production orchestrator mounts `./data`, dev mounts `./data/dev`, and the
  exporter writes the same content to both.
- **Parameters**: `limit` (default 200, clamped to 500; 0 answers the
  status and summary without opening the copy, which is what a folded
  section asks every 30 s), `before` (the last answer's `next_before`,
  `<start_s>,<run_id>`: keyset paging, so two runs sharing a timestamp are
  neither dropped nor shown twice), `q` (bound, its `%`, `_` and `\`
  taken literally; matched in `heard` and `reply`; 200 characters at most),
  `flagged`, `include_dev`, and `changed_after` with `known_rows` and
  `known_built_s`. A bad value is a 400 with the reason.
- **Updates without a re-render.** Every row carries `seq`, which the
  exporter raises when a row is inserted or changed. The open section asks
  `changed_after=<max_seq it has seen>` every 30 s and gets only those rows,
  each with `match` (whether it passes the filter); it patches them in by
  run id, drops one that no longer matches, and keeps the row a reader was
  looking at in place when a newer one is inserted above it. An open row
  stays open.
- **A removal raises no `seq`** (`voice_runs_export.py --remove`), so
  `changed_after` alone never shows a row going away. The page sends the
  copy's `meta.rows` and `meta.built_s` from its last answer; the route
  counts the rows inserted since that copy was built (`first_seen_at` later
  than its `built_s`) and answers `resync` when the copy's row count is
  not the page's plus those. A row leaving the 90 days does the same. On
  `resync`, or when more than 500 rows changed, the page loads its list
  again, keeping open rows and the reader's place by run id.
- **Built outside `loadAll`**: its own element, fetch and 30 s timer. The
  rest of the page is rebuilt every 30 s; this section is not. M32 then
  swaps only the timer for its stream.
- **What is shown is text.** Every string from a run goes in through the
  page's `el()` (`textContent`); a sentence holding markup shows as
  written.
- **What reaches the LAN**: the published copy's columns for 90 days (what
  Grafana already serves without a login), never `raw_trace`, the audio
  columns or a removed row; anyone who can reach port 80 can read it
  (`M30-review.md`, point 6).

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

- **Heard shows what the copy holds, three to four minutes behind the
  room.** A satellite run reaches Tempo that late, and the exporter reads
  Tempo every minute; "as of" is when the exporter counted, not when the
  last run happened.
- **The folded counts are household runs**; the copy holds the three dev
  runs with audio too, which the page leaves out (`include_dev=1` on the
  route shows them).
- **A browser restores form fields on reload** in some cases; the filter
  and the switch are set from `localStorage` on every load, so what shows
  is what was stored.

## Operations

- Install: the header of `systemd/kronk-work-export.service` (install,
  check, one-shot, remove).
- Eyeball: `python3 scripts/work_export.py --print`.
- Health: `systemctl --user list-timers kronk-work-export.timer`;
  `curl -s localhost/api/work | jq .source`.
- Heard: `curl -s 'localhost/api/voice_runs?limit=0' | jq '.source, .summary'`
  (no run text); the exporter's own operations are in `voice-dataset.md`.

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
- A removal that changes no counter: noticing a row going away by the
  row count and the insert times, when the cursor only ever goes up.
- A folded section for a wall display: why "remember if it was open" is a
  bug on a screen nobody can touch.
