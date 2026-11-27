# Feature: /work, the work items in a page

**Shipped:** work item M27 (2026-11-24 build); drawn from the work-state
database since work item M34 (2026-11-26 build). **Origin:** Drew's order of
2026-11-24: "i want to be able to track this work in a UI. A backlog,
currently working (planning/reviewing/implementing/testing/etc), what's
already been worked, and so forth. I should able to visit kronk/work and
the page displays." Criteria and evidence: `docs/sprint/M27-criteria.md`,
`M27-review.md`, `M27-build.md`, `M27-e2e.md`, `M27-fix1.md`. The
"Heard" section: work item M30 step C (`docs/sprint/M30-criteria.md`,
`M30-review.md` points 6 and 7, `M30-build3.md`), from Drew's order of
2026-11-24: "the /work endpoint should also include all the stt
transcriptions to help me troubleshoot. Make it the top of the page but
make it foldable so it doesn't pollute the view". M34, from Drew's
2026-11-24 order to keep the work's state in a database
(`docs/sprint/M34-criteria.md`, the section "Revised after the review"
decides; `M34-review.md`, `M34-build.md`): the page shows what the
work-state database says (`docs/features/work-state.md`), not what a parser
reads out of `PROMOTIONS.md` and the file names in `docs/sprint/`. M32
step 1, from Drew's 2026-11-24 order to put the page on a wall display
(`docs/sprint/M32-criteria.md`, the section "Rewritten after the review"
decides; `M32-review.md` is the design; `M32-build.md`): the page updates
within seconds, shows that it is alive, survives deploys and outages, and
cannot strand itself ("Live", below). M32 step 2 (the same criteria file,
"Steps 2 and 3"; `M32-step2-build.md`): every item is a row that unfolds to
what it is and why, its steps, its open leftovers and decisions with their
text; rows are patched in place, never rebuilt ("Rows that unfold",
below). M32 step 3 (the same criteria file, "Step 3: motion (criteria,
coordinator, 2026-11-27)"; `M32-step3-build.md`): a track of stations on
the item under way and on Done rows, the current station pulsing, a stripe
while an agent works, the agent's time counting, a flash on a changed row,
and nothing that carries state in the faintest grey ("Motion", below).

## What it does

`/work` (and `/dev/work`) shows, top to bottom (the header: a dot that
beats once per answered poll and the data's age, counting each second; the
order is M32-review 3.9's, for a wall display that shows what fits its
first screen):

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
   why. Then the age of the work-state database's dump and the number of
   decisions owed by Drew. A dump the exporter could not use (missing,
   empty, unreadable, of a newer schema, of another repo) is a red banner
   with the exporter's sentence and the dump's age, and nothing is shown
   from the database.
2. **Under way**: every work item the database has `active`, open: its
   stage (the one `work.py resume` gives), its next step and what it waits
   on, then what it unfolds to (below) and the ROADMAP entry that names it.
3. **Queue** (since M34): the queued and held items in order, folded, each
   with what holds it (held and why; "M32 follows M34, which is active, not
   promoted"), what it waits on, its next step, the agents named for it
   (M34-e2e note 8), and "next" on the item `work.py start` would take.
4. **Other agents**, when there are any: an agent in this repo whose name
   matches no active, queued or held item (an investigator, an agent for a
   promoted or dropped item, a name that is not `<item>-<role>`).
5. **Heard**, folded: what Whisper heard over the last 90 days; folded, its
   line ends with the newest household run's words (see "Heard" below).
6. **Done**: the database's promotions, newest first: one folded row per
   item, with its title, tag ("tag only" when the row says it was not
   deployed), the house's day of the promotion (a row imported from the
   old log carries the date written there; for M2, M5, M12, M13 and
   M21-step2 that is a UTC day, the house's being the day before), its
   number of steps and its verdicts; a follow-up shows as "Note:
   follow-up, <date>" on the item's row. A deploy with no work item
   (`work.py promote --label`) is a row of its own among them by time: its
   tag, its label, "deploy with no work item". Then the Shipped entries of
   `ROADMAP.md` that are linked to no item, by title and leading `*( … )*`
   only.
7. **Dropped** (M34-e2e note 4), a folded list when there is one: each
   dropped item's id, title, "dropped" and the house's day it was dropped.
   Why it was dropped stays in the database (M34's ruling).
8. **Backlog**: the open entries of `ROADMAP.md` (Now, Next, Later,
   Chores), under the file's own section names, each folded to its title,
   its leading `*( … )*` and its first sentence; a click opens the whole
   entry. `` `code` ``, `**bold**`, `*emphasis*` (so the leads and `*Why:
   …*`) are rendered; `[text](url)` is a link only in the unfolded part of
   an entry and only for a url that starts with `https://` (with
   `rel="noopener"`); anything else, and any link in a title or a folded
   line, is shown as written. An entry linked to an item under way is
   shown with the item instead; one linked to a promoted item stays in the
   backlog.
9. **Not read**: a criteria file that exists and could not be read, a
   `ROADMAP.md` section the exporter does not know.

Every item row (Under way, Queue, Done, Dropped) unfolds to the same
parts, each left out when it has nothing to say:

- **What**: the item's handoff line; **Shipped as**: its linked Shipped
  entries. An item with no handoff line (the 27 from before M24) shows its
  title and Shipped line only (Drew, #91).
- **Why**: the criteria file's `Source:` paragraph, Drew's words, whole
  (`work.py` `SOURCE_MAX` 4,000; the longest is 722).
- **Waits on Drew** (or **Decisions owed by Drew**): the decisions owed by
  Drew for this item, each with its id, day, whether it blocks the item,
  and its text.
- **Open leftovers and decisions**: the rest of the item's open
  leftovers and decisions, each with its id, kind, owner ("not triaged"
  when it has none), day, whether it blocks, and its text (Drew, #22: "show
  all of it on /work").
- **Steps**: each step's kind and round ("criteria (round 2)", "test 1"),
  its start and end in the house's zone, the test's verdict, the
  coordinator's grading, the agent and the report file.

The item under way is open by default, every other row closed (Drew, #92:
on the wall, the item under way open; the rest unfold on a desk browser).

Nothing is entered on the page and nothing is written back.

## How it works

```
host: scripts/work.py (every write) → ~/.local/state/kronk-work/work.db and work.db.dump.sql
      kronk-work-export.path (the dump changed) and kronk-work-export.timer (60 s)
  └─ kronk-work-export.service → scripts/work_export.py
       ├─ reads work.db.dump.sql into an in-memory database (never work.db, nothing else beside it)
       ├─ loads scripts/work.py for overview() and queue_rows(): stage, queue, counts as `resume` has them
       ├─ reads ROADMAP.md, the first 40 lines of each item's criteria file
       ├─ git rev-parse, git for-each-ref refs/tags, git rev-list HEAD     (GIT_OPTIONAL_LOCKS=0)
       ├─ herdr agent list                                                   (once, 5 s limit)
       └─ writes data/work.json and data/dev/work.json (temp file, rename)
orchestrator: GET /api/work[?have=<ts>] → the file and its freshness (fresh / stale / missing / unreadable /
                                         unknown_schema), the page version, the host's clock; work null and
                                         unchanged true when <ts> is the file's
              GET /work      → orchestrator/static/work.html with its version stamped in, which polls
                               api/work (relative) every 3 s
```

- **Why a host exporter.** Production runs from a release directory, a
  snapshot of a tag; an item under way exists only in the working tree.
  The container mounts `data/`, so a file there is the bridge, as for
  `gpu_mem.json` and `llama_props.json` (`resources-live-memory.md`).
- **Why the dump, not the database.** The database is WAL. A `mode=ro`
  reader on a 60 s timer makes `work.db-wal` and `work.db-shm` and changes
  `-shm` on every read, and the suite fails any run in which the live
  files changed (`tests/conftest.py`, M33 criterion 7). `work.py` rewrites
  `<db>.dump.sql` after every committed write, inside the write lock, by
  temp file and rename; loading it takes about 5 ms. The exporter opens
  no other file in the state directory, takes no lock of any kind, and
  states the dump's age (`database.dump_age_s`).
- **Stage, the queue and the counts** come from `work.py`'s read-only
  `overview()`, the function `resume` prints from (M34 plumbing 5), fed
  the dump's rows, the tags, HEAD's commits (`git rev-list HEAD`: a step
  committed off HEAD's history is not counted) and herdr's answer. The
  stage itself is still `stage()` in this file and the tables above it
  (`M27-criteria.md`, "Stage is a pure function"), wrapped by `work.py`'s
  `item_stage()` (a grading is read before the verdict; "no finished step
  recorded"). `work.py` is loaded inside `collect()`, never at import:
  `work.py` and `agent.py` load this file on every call. Only `working`
  and `blocked` agents decide a stage; `blocked` is shown in red. The
  stage means something for active and promoted items; a queued item's
  is "planning".
- **The handoff line and the `Source:` paragraph** are read from the head
  of the item's criteria file: the row's `criteria` path, else its
  finished criteria step's report, else `docs/sprint/<id>-criteria.md`,
  else none (M34's own row was seeded with no path).
- **An agent's item and role** are parsed from its herdr name:
  `^(m\d+[a-z]?(-step\d+)?)-(review|build|test|fix)\d*$`. An agent of an
  active, queued or held item is listed with the item (`items[].agents`);
  every other one is under `other_agents` with why (M32 step 2; before it,
  a queued item's agent was "other").
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
- **Verdicts** are read, never guessed: `work.py` copies a test's verdict
  from its committed report by this file's `verdicts()` (the first line
  among the first 40 that starts with `Verdict: X`, `**Verdict: ITEM X.**`,
  `**<ID> X.**` or `**RESTORE X.**`). The file carries the item's own
  verdict, one per step; a second subject (`M4b-e2e.md`'s RESTORE) is
  prose in the row's note and is not exported.
- **The old reader** of `PROMOTIONS.md` and the step files is
  `collect_legacy()`, import-only: `work.py import-legacy` and `check` use
  its parsers, and `tests/test_m27_work_export.py` pins them. Its output is
  schema 1.
- **Links** from a ROADMAP entry to an item: `work item Mxx`,
  `sprint Mxx` or `sprint item Mxx` (and "and Mxx") inside the entry's
  leading `*( … )*`, after its lines are joined. `M21` links both steps.
  A Shipped entry's lead links only from its first clause (what follows a
  `;` happened later: "2026-07-10; removed 2026-11-15, sprint M1" links
  nothing), and its body links the ids of a parenthesis that opens with
  them, "(M1, M1b)" or "(M7, `docs/…`)" (M32 step 2 fix 1). An id in prose,
  "since sprint M2", links nothing.
- **What leaves the host** is an outline. From the rows: ids, titles,
  state, tier, queue place and what holds an item, next steps, wait notes
  and why an item is held (active, queued and held items only), the end
  time of a dropped item, steps (kind, round, name, report file name,
  agent, who, state, times, verdict, grading), promotions (tag, time,
  deployed, the path of the checks; the label of one with no item), counts
  of open leftovers and decisions by owner, each item's open leftovers and
  decisions (id, kind, owner, whether it blocks, day, text), and the
  decisions owed to Drew by id, item, date, whether they block, and text.
  The text of open leftovers and decisions reaches the LAN since M32 step
  2, on Drew's answer to #22 (2026-11-26): "show all of it on /work". A
  leftover with no item, a coordinator's decision with no item, a closed
  matter and an escape are not exported. Never the log, `meta.repo`, a
  step's model or note, an item's ended-why, a promotion's untested note,
  a matter's source or closing words, or an SQLite error's text (it names
  paths); one marker planted in every text column of every table pins
  this (`tests/test_m34_work_db.py`, criterion 6). From files: handoff lines
  and `Source:` paragraphs (Drew's words: new on the LAN with M34, allowed
  by M32 plumbing 11), the open ROADMAP entries whole, Shipped titles and
  leads. From herdr: agents' names and statuses, never `terminal_title`,
  `agent_session` or `cwd`; an agent outside this repo is counted, not
  named.
- **A dump that cannot be used** (missing, empty, not SQL or cut short,
  tables missing, a newer schema, another repo's) still gives a file: its
  `database.state` says which with a sentence the exporter wrote, the same
  sentence is in `errors` (so a page that predates M34 shows it under the
  state line), no item is listed, the ROADMAP part is there, and the
  exporter exits 0. Nothing is invented from files.
- **Freshness**: older than 180 s is stale, and so is a file dated more
  than the route's slack in the future. A stale outline is still shown,
  marked STALE (it is the last known state); the banner gives the route's
  reason ("written N s ago" or "dated N s in the future: the host clock or
  the file is wrong").
- **Temp files**: each write goes to `.work.json.XXXXXXXX` and is renamed;
  a run killed in mid-write leaves its temp file, and the next run removes
  its own temp files older than ten minutes (regular files of that exact
  name form only).
- **Schema**: `schema` is 4 (M32 step 2; 3 was step 1's, 2 M34's). The exporter only adds keys and raises
  the number when it does; an orchestrator that knows a lower schema still
  reads the file and says that keys were added. A missing or non-integer
  schema is "unknown schema". The schema-1 keys with no source left since
  M34 carry an empty value: `items[].uncommitted` (`[]`), `steps[].title`,
  `verdicts[].written` and `.line` (`null`), `done[].heading`,
  `tag_written`, `kind`, `entry` (`null`), `heading_adds` (`false`),
  `for_drew` and `for_drew_total` (`0`), and `done[].line` for a promotion
  whose checks are a `-promote.md` file (`null`). `items[].title` is the
  row's title, not the criteria file's heading; `done[].date` is the
  house's day of the promotion's time (`done[].at`, UTC); a step's
  `committed_at` is the row's `ended_at`. Keys added in schema 2, for this
  page and M32: per item `state`, `tier`, `position`, `after_item`,
  `blocked_by`, `next`, `next_step`, `waits_on`, `waits_note`, `held_why`,
  `source`, `open` (counts of open leftovers and decisions by owner); per
  step `round`, `state`, `by`, `agent`, `started_at`, `ended_at`,
  `verdict`, `graded`; per Done row `at`, `deployed` (yes, no, not
  recorded), `verified`, `promotion`, `followup`; top level `database`,
  `active`, `queue`, `decisions_owed`, `open_counts`, `escapes`,
  `promotions_no_item`. Added in schema 3 (M32 step 1): top level
  `house_zone` (`name`, the IANA zone when TZ or the `/etc/localtime` link
  gives one whose offset now is the host's, else null; `utc_offset_s`,
  the host's offset at the export). Added in schema 4 (M32 step 2): per
  item `ended_at` (a dropped item's end, UTC; null for every other item)
  and `open_list` (one entry per open leftover and decision of the item:
  `id`, `kind`, `owner`, `blocks`, `date`, the house's day it was opened
  or null when the stored time does not parse, and `text`, clipped at
  4,000 characters); per `decisions_owed` entry `text`.
- **A dump that loads and is not the schema** (a column gone, a row of
  the wrong shape: M34-e2e S1) is state "corrupt" with its own sentence,
  "loads as SQL and is not the schema this exporter reads (<the
  exception's type>)", no item, the ROADMAP part kept, exit 0. With no
  row read (any state but "ok"), every agent is under "Other agents" with
  the reason "the work-state database was not read", not "no item of that
  name" (M34-e2e note 5).

## Heard

A `<details>` after the Queue (first on the page until M32 step 2), folded
on every load. Open or closed is never stored: the page will run on a wall
display nobody can touch, and a section opened once on that browser must
not stay open there.

**Folded**, its line says: runs in the last 24 hours, how many of those
are marked wrong and how many carry an error (household runs; the counts
are the exporter's `summary.household`), then the newest household run's
time and its words as heard, set as text ("newest Nov 26 22:14: “turn off
the kitchen lights”"; "nothing heard" or "not recorded" when the run has
no words; Drew's #89, M32 step 2), and "as of" with the time the exporter
counted them. After that, the word for a status file that is not fresh
(STALE, MISSING, ...), "last export failed" when the last run's `ok` is
false, or "N warnings". The words come from `api/voice_runs?limit=1` (no
filter: the route's household runs), asked on every tick, folded or open,
so the line follows a newer run within one tick (30 s).

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
  status and summary without opening the copy; the page asks for 1 every
  30 s, the newest run for the folded line, since M32 step 2), `before` (the last answer's `next_before`,
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
  looking at in place when a newer one is inserted above it. A reader at
  the top of the page (the section's head in view) is not held in place:
  the new row appears and the page does not scroll. An open row stays
  open.
- **A removal raises no `seq`** (`voice_runs_export.py --remove`), so
  `changed_after` alone never shows a row going away. The page sends the
  copy's `meta.rows` and `meta.built_s` from its last answer; the route
  counts the rows inserted since that copy was built (`first_seen_at` later
  than its `built_s`) and answers `resync` when the copy's row count is
  not the page's plus those. A row leaving the 90 days does the same. On
  `resync`, or when more than 500 rows changed, the page loads its list
  again: as many rows as it had loaded (200, or more after "show older"),
  in pages of 500 at most, keeping open rows and the reader's place by run
  id (the first row in view still there, or the next below it).
- **Built outside `loadAll`**: its own element and fetch, on the page's
  scheduler at its own 30 s (M32 step 1), each fetch with the page's 10 s
  time limit. The rest of the page is rebuilt when the file changes; this
  section is patched. After a failed poll of `api/work` is answered again,
  "Heard" asks at the next tick instead of reading "not read" for up to
  30 s (M32-e2e note 6). The open list holds the count loaded (200, or more
  after "show older"): new runs patched in at the top push the oldest out,
  and "show older" goes on from the oldest kept. A section folded for ten
  minutes drops its rows; opening it again loads the newest 200.
- **What is shown is text.** Every string from a run goes in through the
  page's `el()` (`textContent`); a sentence holding markup shows as
  written.
- **What reaches the LAN**: the published copy's columns for 90 days (what
  Grafana already serves without a login), never `raw_trace`, the audio
  columns or a removed row; anyone who can reach port 80 can read it
  (`M30-review.md`, point 6).

## Live (M32 step 1)

For a page left open for months on a display nobody can touch
(`M32-review.md` points 2 and 3).

- **Poll, not a stream.** One scheduler: a `setTimeout` chain that ticks
  each second (or sooner, when the next poll is due) and starts a poll of
  `api/work` 3 s after the last one ended, at most one in flight, and
  "Heard"'s 30 s after its last. Every request is given up after 10 s, its
  body included (`AbortController` where the browser has one; without it
  the page still stops waiting, and the request is left to finish). A tab
  that comes back into view polls at once. Two timers at most: the tick
  and one request's limit.
- **`?have=`.** The page sends the `ts` of the file it shows; the route
  answers the same envelope with `work: null` and `unchanged: true` when
  the file's `ts` is that one, so an unchanged poll is a few hundred bytes
  and the rows are not touched. The envelope (state, `age_s`) is fresh on
  every poll.
- **The in-memory copy.** The route keeps the parsed file keyed by the
  open file's device, inode, mtime and size (`servers.read_source`'s
  cache): an unchanged file costs an open and an `fstat`, and is read once
  per change however many pages poll. The key is taken from the file that
  is read, so a file renamed into place between two polls is never paired
  with the other's parse. Beside the parse, under the same key, the route
  keeps the JSON encoding of the outline (the answer's `work` part, about
  127 KB): a full answer is the fresh envelope with those bytes spliced
  in, so twenty pages cost one encoding per change of the file, not
  twenty (fix round 1, S2; `work_api.work_body`).
- **`Cache-Control: no-store`** on `/work`, `/api/work` and
  `/api/voice_runs`. The three are left out of the orchestrator's
  telemetry (`telemetry._DEFAULT_EXCLUDED`): no trace per poll.
- **A failed poll keeps the last state.** A 502, a refused connection, a
  hang or an answer that is not the route's leaves the page as it was and
  says "NO ANSWER: since HH:MM:SS (why), N s ago. The page shows the last
  known state." in a banner that floats over the bottom of the window and
  moves nothing (since M32 step 2; it pushed the page down 54 px,
  M32-e2e note 4). The page is never emptied by a failure; a route answer
  of missing, unreadable or unknown schema still empties it, as before.
- **Three signs of life.** The data's age, counting each second (the
  script runs); the header's dot, which beats once per answered poll and
  never moves by itself; and a cover the stylesheet shows by itself: a
  CSS animation with a 90 s delay ("This page has stopped updating"),
  restarted by the script on every answered poll. If the script dies or
  every poll fails, the cover appears with no script needed. Nothing
  moves endlessly.
- **The page's version.** The orchestrator hashes `work.html` and
  `kronk.css` once at start (`work_api.PAGE_VERSION`, 12 hex of SHA-256),
  puts it in every `/api/work` envelope (`page_version`) and stamps it into
  the page it serves (`<meta name="kronk-page-version">`, filled by
  `_serve`). The page learns its own version from its own bytes, never
  from the first answer, which may come from a newer container.
- **The reload cannot strand the page.** When the announced version is
  not the page's own, the page fetches its own URL with `no-store` and
  reloads only on a 200 whose stamped version is the one announced. During
  a deploy nginx answers 502 for the page, and a reload then would land on
  nginx's error page, where no script runs, for good. At most one reload
  in ten minutes by the route's clock (`server_s`, kept in
  `sessionStorage` under `kronk.work.reloaded_at_s`, so the limit survives
  the reload), the page asked at most once in 30 s. A browser with no
  session storage never reloads itself: the page says so. Once a day,
  when nothing is under way, the page reloads by the same path.
- **Clocks.** Ages are the route's `age_s` plus the time since the answer
  by `performance.now()`; the reload limit is the route's clock. The page
  never reads the browser's clock, so a display whose clock is wrong shows
  the right ages.
- **Shown times are the house's**: formatted with `house_zone.name` when
  the browser knows it, else with `house_zone.utc_offset_s` (the offset at
  the export, so a time across a DST change can be an hour off in that
  case only). "Heard"'s run times are the house's as written; its "as of"
  goes through the same `houseString` (`fmtClockS`; fix round 1, note 7).
- **The exporter runs on a write**: `systemd/kronk-work-export.path`,
  `PathChanged=` on the live dump, starts the existing service. `work.py`
  rewrites the dump by rename after every committed write; `agent.py`
  writes through `work.py`. No code in either tool. The timer stays at
  60 s for what only herdr changes (an agent going idle). The service has
  `StartLimitIntervalSec=0`: with systemd's default start limit (five
  starts in ten seconds) a burst of writes failed the service with
  `start-limit-hit` and the path unit with it, for good, and every later
  write silently waited for the timer (step 1 test, S1).

## Rows that unfold (M32 step 2)

`M32-review.md` points 5 to 7; the criteria's "Steps 2 and 3", step 2.

- **Keyed patching, one function for every list** (`patchList`): Under
  way, the Queue, Done, Dropped, the Backlog's sections and their entries,
  and the unlinked Shipped entries. A row is a node with a key and fixed
  parts, each part keeping the signature (the JSON of what it was drawn
  from) it was drawn with. On a new file a row whose key is gone is
  removed, a new one is built, a kept one keeps its node, and only a part
  whose signature changed is drawn again. A kept node is moved only when
  its place is wrong: the longest run of kept nodes already in order
  stays, the rest are moved (one move when the first queued item goes to
  the end). So a change of one item's next step redraws that item's stage
  line and nothing else, an open row stays open, the scroll does not move,
  and a running pulse is not restarted (step 3 draws the track in place:
  "Motion", below).
- **Keys**: `uw:<id>`, `q:<id>`, `done:<promotion>:<id>` (the old
  `<id>@<line>` is `@null` for every promotion since M34), `drop:<id>`,
  `deploy:<promotion>` for a deploy with no item, `sec:<section>` and
  `bl:<section>:<title>` for the Backlog (by title, not line: the line
  moves whenever `ROADMAP.md` is edited above an entry), `sh:<title>`. A
  second row with the same key gets `#2`.
- **The frame of an item row**: a `<details>` whose `<summary>` holds the
  head (id, title), the stage line and the track (step 3; empty and hidden
  on queued, held and dropped rows), and whose body holds the parts listed under "What it does". Titles and
  labels render code and emphasis and never a link: inside a `<summary>`
  a link takes the click that folds (M34-e2e note 11). Leftover and
  decision text goes in through `el()`, as text, never through `mdText`.
- **Bodies are built folded**: every row's body is in the page whether
  it is open or not (about 2,700 elements on today's file, 1,600 before),
  so opening a row costs nothing and the element count is the same after
  500 updates as after 5.
- **Folds are kept per tab**: whether a row is open lives in its node;
  across a reload in `sessionStorage` (`kronk.work.folds`, only the folds
  that differ from their default), never `localStorage`, so a row opened
  once on the display's browser does not stay open there for good, and a
  new tab starts from the defaults. "Heard" is never kept. One `toggle`
  listener on `<main>`, on the way down (`toggle` does not bubble).

## Motion (M32 step 3)

`M32-review.md` point 4 and 10.5; the criteria's "Step 3: motion (criteria,
coordinator, 2026-11-27)". Built for a browser on a panel not yet picked
(Drew #90): no display mode, no larger type, no pixel shift.

- **The track**: five stations, criteria, review, build, test, promote, in
  each row's track slot, on the item under way's card and on Done rows.
  `trackOf(it, current, promoted)` is a pure function of the item's steps
  and the exporter's stage. A station is filled when a finished step of
  one of its kinds exists, wherever it sits in the list (M35's review is
  recorded before its criteria). A fix is a return to build, so "build"
  counts builds and fixes and "test" counts tests; a count over one is
  shown as rounds on the station ("build 9", "test 9" for M30), never as
  more stations. A stopped step is not counted (M36's build). "promote"
  is filled on a Done row. A test station is green, red or amber by the
  last finished test's grading, else its verdict (M36: graded "pass", no
  verdict). A review with none recorded, once a build or a test is filled
  or the current station is past it, is drawn hollow (a dashed ring) and
  labelled "review skipped". The page computes no stage: the current
  station is `STAGE_STATION[items[].stage.stage]`, the exporter's own key
  (`planning` criteria, `reviewing` review, `implementing` and `fixing`
  build, `testing` test, `to_promote` and `promoting` promote; `waiting`
  and `promoted` none). All of it is in the page's `TRACK` and
  `STAGE_STATION` literals; a test checks the map covers every stage key
  `work_export.STAGE_WORDS` has.
- **Drawn in place**: the track is a fixed frame (five station nodes, the
  lines between them, and on the card an agent and a clock) built once;
  later draws change a station's classes one by one, only where they
  differ, and its label. A station keeps its node, so a pulse runs on
  through an update.
- **What moves, the first item under way only**: its current station's
  dot pulses (opacity 1 → 0.35 → 1, 2.4 s, never above its rest); a
  stripe runs across that station while the item has an agent `working`
  (a `::after` moved by `transform` inside the station's `overflow:
  hidden`, 2 s); the agent's time counts each second beside the track
  (`m32-build3 12:41`: the route's clock, the file's `ts` plus the data's
  age, less the start of the step the agent is named on; tabular figures
  in an 8ch box with `contain: content`). Which card moves is set by
  `renderMoving` after each patch, not drawn from the row's data, so the
  second item under way becoming the first does not flash. At most two
  endless animations at any time.
- **Once, on a change**: a row flashes when it is new or one of its
  parts' signatures changed (`patchList`'s `flash`: "change" for Under
  way, "new" for Done, none for the Queue, Dropped and the Backlog); never
  on the list's first draw, so nothing flashes on load or after the page
  comes back from an empty state, and never on an export that changes
  only `ts` or the lines set to the same text on every export
  (M32-step2-e2e note 2). An item promoted: its card leaves Under way and
  its new Done row flashes; nothing slides. A new finished test or grading
  fades the test station in once.
- **Stopping**: endless motion stops when no poll has been answered for
  30 s or the route says the data is not fresh (`.motion-still` on
  `<main>`, set each second; `MOTION`). `prefers-reduced-motion` turns off
  the pulse, the stripe, the flash and the fade; the clocks and the ages
  still count. The step-1 signs stay under both: the cover (one opacity
  fade after 90 s with no answer) and the live mark's beat (one opacity
  fade per answered poll).
- **Only `transform` and `opacity`** in any `@keyframes` on the page
  (the cover's `visibility` was dropped: it is invisible at opacity 0 and
  takes no clicks); no `transition` anywhere (the refresh button's hover
  fade went).
- **Contrast**: `--text3` (3.2 to 1 on `--bg`) is left on the separators,
  the fixed explanatory notes, the "… more" of a folded entry and Heard's
  fold marker. Everything that carries state moved to `--text2` (7.5 to
  1): the data's age, step times, queue positions, Done meta, the notes
  and reasons, "verdict not read", the source lines, Backlog heads,
  "Not read" entries, Heard's rows, the dropped count.

## Gotchas

- **A STALE `/work` means the exporter threw.** The timer runs the
  working tree's script every minute, and the script loads the working
  tree's `scripts/work.py`; `work.py` and `agent.py` load the exporter on
  every call. So an edit to either file is live the second it is written:
  change them in a scratch `scripts/` directory holding both (`--db` on a
  copy, `--out` in scratch; the tests take `M27_WORK_EXPORT=<scratch
  copy>`), and move each in by rename. Check `journalctl --user -u
  kronk-work-export.service`.
- **The page is as fresh as the dump.** A dump write that failed is said
  by `work.py` on stderr at the write, and `work.py check` names
  `dump-stale` and `dump-missing`; the page shows the dump's age. A writer
  killed between the dump's rename and its `COMMIT` leaves the dump one
  write ahead of the database until the next write.
- **Between the exporter's move and a deploy of the new page**, the
  deployed page reads the schema-2 file with "keys added since are not
  shown": under way is the active items (a queued item is nowhere until the
  new page is deployed), steps are the database's (a started step shows
  "written, not committed"), the five tags the parser missed are shown, and
  the follow-up notes read "follow-up, <date>".
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
- **Dates.** A Done row's date is the house's day of the promotion's time,
  computed by the exporter, never parsed in the browser (`new
  Date('2026-11-15')` prints the 14th here). For the 33 promotions imported
  from `PROMOTIONS.md` that time is derived (the commit that added the
  line, or noon of the date written), so only the day is true; for M2,
  M5, M12, M13 and M21-step2 the date written was a UTC day, and the
  page's note says so. Step times are UTC from the rows, shown in the
  house's zone. `ROADMAP.md` dates are shown as written.
- **A write while an export runs** may start no second run: the service
  is a oneshot, and a start asked for while it is running is merged into
  the running job. The timer picks the write up within 60 s.
- **Between the move of the schema-3 exporter and a deploy of step 1**,
  the deployed page reads the file with "work.json has schema 3, newer
  than this page's 2: keys added since are not shown". Only `house_zone`
  is not shown.
- **Between the move of the schema-4 exporter and a deploy of step 2**,
  the deployed page reads the file with "work.json has schema 4, newer
  than this page's 3: keys added since are not shown". The step-1 page
  does not show leftover and decision text or the dropped list, and an
  agent named for a queued item, now listed with its item, is under no
  section on that page.
- **"Heard" opens the voice-run copy every 30 s per page** since M32
  step 2 (`limit=1`, one indexed query; before, a folded section asked
  `limit=0`, which opens nothing).
- **A browser without `AbortController`** (older than 2018) stops waiting
  on a hung request after 10 s but cannot cut it: hung requests are left
  open until the network gives them up.
- **"Waiting for the coordinator" has no current station.** After a
  test with no grading, or graded fail, the exporter's stage is `waiting`;
  the track then shows what is done and nothing pulses until a step or an
  agent starts. Motion means "being worked now".
- **Two items under way: the second stands still.** Its current station
  is drawn blue, without pulse or stripe, so the page never runs more than
  two endless animations.
- **An agent's clock needs a started step naming it.** An agent working
  with no started step (herdr sees it, `work.py step start` was not run)
  shows "start not recorded" beside its name.
- **`document.getAnimations()` always holds the cover's** `cover-show`
  (opacity, one iteration, 90 s delay, restarted on every answered poll)
  and, for 0.9 s after each poll, the beat: they are step 1's and not
  step 3's motion.
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
  check, one-shot, remove), and of `systemd/kronk-work-export.path`
  (install, check, verify, remove; the coordinator installs it).
- Eyeball: `python3 scripts/work_export.py --print` (the live dump), or
  `--db <copy> --print` on a scratch copy made with `work.py --db <copy>
  dump`.
- Health: `systemctl --user list-timers kronk-work-export.timer`;
  `curl -s localhost/api/work | jq .source`.
- Heard: `curl -s 'localhost/api/voice_runs?limit=0' | jq '.source, .summary'`
  (no run text); the exporter's own operations are in `voice-dataset.md`.

## Blog hooks

- A status page with no second store, and then with one: M27 read the
  backlog, the work under way and what was done from the markdown and git
  that held them; M34 moved it to the database, and the page was wrong
  in exactly the windows a parser cannot see (a tag before its log entry,
  an item before its file).
- Reading a WAL database every minute without touching it: a read-only
  reader still writes `-shm`, so the page reads the text dump the writer
  already makes.
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
- A page nobody can touch: the self-reload that would have stranded it on
  nginx's 502 page, and the check that makes it safe (fetch yourself first,
  reload only on a 200 stamped with the version announced).
- Polling beat streaming for a file that changes once a minute: no held
  connection, no shutdown that hangs, and recovery is the next tick.
- A cover drawn by the stylesheet alone, so a dead script still says it
  is dead.
- Patching a page without a framework: one 40-line keyed patch, a
  signature per part, and the longest increasing run so a reorder moves
  one node, not every node after it.
- Folds on a wall display: kept per tab in `sessionStorage`, only where
  they differ from the default.
- Reading the clock of the server, not the browser's, for every age and
  limit on a display whose clock nobody checks.
- A progress track that does not lie about a messy history: M30 had four
  builds, nine tests and five fixes; five stations with round counts draw
  it, and six stations by position misdraw it.
- Motion with a meaning: it runs only while something is being worked
  and the page is hearing from the server, and it stops by itself when
  either is not true.
- A flash that cannot fire on noise: hung on a part's signature, not on
  the file's timestamp, so the four lines rewritten on every export never
  light anything.
- An animation on a pseudo-element moved by `transform`, not a
  `background-position` repainted every frame for months.
