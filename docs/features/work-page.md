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
cannot strand itself ("Live", below).

## What it does

`/work` (and `/dev/work`) shows, top to bottom (the header: a dot that
beats once per answered poll and the data's age, counting each second):

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
   why. Then the age of the work-state database's dump and the number of
   decisions owed by Drew (a count: their text is not shown, decision #22).
   A dump the exporter could not use (missing, empty, unreadable, of a newer
   schema, of another repo) is a red banner with the exporter's sentence and
   the dump's age, and nothing is shown from the database.
2. **Under way**: every work item the database has `active`, with its
   stage (the one `work.py resume` gives), its steps in time order (each
   with its time, the test's verdict and the coordinator's grading; a
   started step says "started" and names its agent), its handoff line, its
   next step and what it waits on, and the ROADMAP entry that names it.
3. **Queue** (since M34): the queued and held items in order, each with
   what holds it (held and why; "M32 follows M34, which is active, not
   promoted"), what it waits on, its next step, and "next" on the item
   `work.py start` would take.
4. **Other agents**, when there are any: an agent in this repo whose name
   matches no active item (an investigator, an agent for a queued or
   promoted item, a name that is not `<item>-<role>`).
5. **Backlog**: the open entries of `ROADMAP.md` (Now, Next, Later,
   Chores), under the file's own section names, each folded to its title,
   its leading `*( … )*` and its first sentence; a click opens the whole
   entry, and an opened entry stays open when the rows are redrawn.
   `` `code` ``, `**bold**`, `*emphasis*` (so the leads and `*Why: …*`) and
   `[text](url)` are rendered; a link is made only for a url that starts
   with `https://` (with `rel="noopener"`), anything else is shown as
   written. An entry linked to an item under way is shown with the item
   instead; one linked to a promoted item stays in the backlog.
6. **Done**: the database's promotions, newest first: one row per item,
   with its title, tag ("tag only" when the row says it was not deployed),
   the house's day of the promotion (a row imported from the old log
   carries the date written there; for M2, M5, M12, M13 and M21-step2
   that is a UTC day, the house's being the day before), and its steps
   and verdicts
   (collapsed); a follow-up shows as "Note: follow-up, <date>" on the
   item's row. A deploy with no work item (`work.py promote --label`) is a
   row of its own among them by time: its tag, its label, "deploy with no
   work item". A linked Shipped title is shown only where it adds a word
   the title lacks. Then the Shipped entries of `ROADMAP.md` that are
   linked to no item, by title and leading `*( … )*` only.
7. **Not read**: a criteria file that exists and could not be read, a
   `ROADMAP.md` section the exporter does not know.

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
- **Links** from a ROADMAP entry to an item: only `work item Mxx`,
  `sprint Mxx` or `sprint item Mxx` (and "and Mxx") inside the entry's
  leading `*( … )*`, after its lines are joined. `M21` links both steps.
- **What leaves the host** is an outline. From the rows: ids, titles,
  state, tier, queue place and what holds an item, next steps, wait notes
  and why an item is held (active, queued and held items only), steps
  (kind, round, name, report file name, agent, who, state, times, verdict,
  grading), promotions (tag, time, deployed, the path of the checks; the
  label of one with no item), counts of open leftovers and decisions by
  owner, and the decisions owed to Drew by id, item, date and whether
  they block. Never the log, `meta.repo`, a step's model or note, an
  item's ended-why, a promotion's untested note, a matter's text, source
  or closing words, or an SQLite error's text (it names paths); one marker
  planted in every text column of every table pins this
  (`tests/test_m34_work_db.py`, criterion 6). From files: handoff lines
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
- **Schema**: `schema` is 3 (M32 step 1 added `house_zone`; 2 was M34's). The exporter only adds keys and raises
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
  the host's offset at the export).
- **A dump that loads and is not the schema** (a column gone, a row of
  the wrong shape: M34-e2e S1) is state "corrupt" with its own sentence,
  "loads as SQL and is not the schema this exporter reads (<the
  exception's type>)", no item, the ROADMAP part kept, exit 0. With no
  row read (any state but "ok"), every agent is under "Other agents" with
  the reason "the work-state database was not read", not "no item of that
  name" (M34-e2e note 5).

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
  section is patched. The open list holds the count loaded (200, or more
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
  says "NO ANSWER: since HH:MM:SS (why), N s ago. Shown below: the last
  known state." The page is never emptied by a failure; a route answer of
  missing, unreadable or unknown schema still empties it, as before.
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
- **A browser without `AbortController`** (older than 2018) stops waiting
  on a hung request after 10 s but cannot cut it: hung requests are left
  open until the network gives them up.
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
- Reading the clock of the server, not the browser's, for every age and
  limit on a display whose clock nobody checks.
