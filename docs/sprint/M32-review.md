# M32 design review: /work for a wall display

Reviewer m32-review (Fable 5.2), 2026-11-26, on `4f7155d` (main, clean;
`item-M34` deployed, and production's `/work` is byte-equal to the tree's
`work.html`). Nothing built, nothing changed but this file.

## Verdict

**Rewrite the criteria.** The order stands and can be built. The file as
written cannot: of its eleven plumbing points six change (1, 2, 3, 4, 5,
10), one is partly done by M34 (7), one is a separate matter (9), and six
of its ten criteria cannot be run as worded by an agent under the guard
rails (1, 2, 3, 4, 5, 8). The three changes that matter most:

1. **Poll, do not stream** (point 2). The data changes once a minute. A
   stream buys about two seconds over a three-second poll and costs a
   held connection, a watcher, a shutdown that hangs, and a client that
   does not come back after a 502.
2. **A page nobody can touch must never navigate to an error page and
   never go blank** (point 3). The criteria's self-reload does the first
   during a deploy; today's page does the second on one failed fetch.
3. **Cut it in three** (point 9). Step 1 is the data path and the page's
   survival, with no redesign; it alone gives a page that can go on a
   display.

What I ran: GETs of `/work`, `/dev/work`, `/api/work`, `/dev/api/work`
and `/resources` (headers and the schema-2 answer's keys), `systemctl
--user list-timers` and `cat` (read-only), `docker inspect` of the
orchestrator's log and stop settings, and reads of the files the brief
names. No browser. "Heard" rows and the voice-run data were not read.
What is reasoned from code or a specification and not run is marked
"not run".

## 1. What is out of date after M30 and M34

### The findings section

| Line | Now |
| --- | --- |
| `setInterval(loadAll, 30000)` | Still true (`work.html:679`). There is now a second 30 s timer, "Heard"'s (`:1064`). |
| `/api/work` has no `Cache-Control` | Still true. Also no `ETag`, no compression; the answer is 126,318 bytes. `/api/voice_runs` likewise (M30-e2e6 note 7). |
| The display keeps the old page after a deploy | Still true. `/work` is `_serve("work.html")` (`main.py:130`): the file read per request, no `ETag`, no version anywhere. |
| "Built after M33: the what, the why and the queue come from the database" | The queue does. The what and the why do not: `handoff` and `source` are read from the head of the criteria file on every export (`work.py:880`, "never copied into rows"). |
| nginx has an SSE location for `/message` | It has no such location. `location /` and `location /dev/` carry `proxy_buffering off` and a 300 s read timeout for everything (`nginx.conf:56-87`). |
| `/resources` has 16 `innerHTML` writes | Still 16 (point 8). |

Not in the findings and true today: `/api/work` and `/api/voice_runs`
are not in the orchestrator's telemetry exclusions
(`telemetry.py:134-140` lists `system`, `servers`, `status` and others;
no override of `TELEMETRY_EXCLUDED_URLS` exists). Each poll is a trace
sent to Tempo: two per open page per 30 s now, 28,800 a day per display
at a 3 s poll. Whatever is built, both routes and `/work` go on that
list.

### Plumbing, line by line

| # | Status | What remains, with the schema-2 keys |
| --- | --- | --- |
| 1 stream, fallback, `no-store` | **Replace** (point 2). `no-store` still to do. | The envelope's `source.state`, `source.age_s`, `work.ts`. |
| 2 exporter after a write; timer to 15 s | **To do, another way** (point 2): a systemd path unit on the dump, no code in `work.py` or `agent.py`. The timer stays at 60 s in step 1. | `database.dump_at`, `database.dump_age_s`. |
| 3 version reload | **To do, reworded** (point 3): the version stamped into the page, a check that the new page can be fetched before reloading. | A new envelope key (not in `work.json`: the route knows the page, the exporter does not). |
| 4 sign of life | **To do, reworded** (points 3 and 4): driven by answers, never an endless CSS animation; a stylesheet-only cover when the script stops. | `source.age_s`, `work.ts`. |
| 5 motion | **To do, reworded** (point 4). The track is not six fixed stations; the slide to Done is cut. | `items[].stage.stage`, `.blocked`; `steps[].kind`, `round`, `state`, `verdict`, `graded`, `agent`, `started_at`; `items[].agents[]`. |
| 6 bright pixels | Answered in point 4: the risk is the static parts, not the motion. | none |
| 7 folding and detail | **Partly done by M34**: the queue in order with next step, what holds it and what it waits on is on the page (`queue`, `items[].position`, `next`, `next_step`, `blocked_by`, `waits_on`, `waits_note`, `held_why`). **To do**: every item a row that unfolds; what (`handoff`); why (`source`); steps (keys above, with `by`, `ended_at`). **Wrong now**: "its leftovers" as text; M34 holds them to counts (`items[].open.leftovers.{drew,coordinator,not_triaged}`), and decisions to `open.decisions` and `decisions_owed[]` (`id`, `item`, `date`, `blocks`), until decision #22. **Needs rewording**: "stays open across reloads" (point 6). |
| 8 no rebuild | **To do.** "Heard" does it; the other five sections `clear()` and rebuild (`renderUnderWay`, `renderQueue`, `renderOthers`, `renderBacklog`, `renderDone`). | Keys for rows in point 6. |
| 9 text as text; `/resources` | `/work`: **done** since M27 (the one `innerHTML` in the file is a comment). `/resources`: **separate** (point 8). | none |
| 10 one script file | **Defer** (point 6): keep it in the page until a second page uses it. | none |
| 11 what may show on the LAN | **Partly overtaken.** `handoff` and `source` have been on the LAN since M34 (`work-page.md`, "What leaves the host"), as have `next_step`, `waits_note` and `held_why`. Report text and rulings stay off. Leftovers' and decisions' text waits on #22. | `handoff`, `source`. |

### What the schema-2 file lacks for this item

- An item's end time. `item.ended_at` exists in the table and is not
  exported; a dropped item's row needs it (point 7).
- A reason of its own for "other agents" when the database was not read
  (point 7).
- A place for a leftover's or a decision's text (point 5).
- 27 of 38 items have no `handoff` and 23 no `source`: the criteria
  files of M1 to M23 carry no "Handoff line" (26 files), and M29
  (dropped) has no file. Criterion 6 as written ("every item unfolds to
  its what and why, and they equal the criteria file's") fails on them
  with nothing wrong in the build. Reword: where the file has them. For
  the others the row shows the title and the linked Shipped entry
  (`items[].shipped[]`, already exported) and says nothing more.

Adding a key raises `schema` to 3 by the exporter's own rule
(`work-page.md`, "Schema"), and the deployed page then shows "keys added
since are not shown" until the deploy. Keys stay add-only.

## 2. Live updates: poll

**What a stream buys.** The file changes when the timer fires (60 s) or,
after this item, within a second of a `work.py` write. A stream shows it
about 1 s after the file changes; a poll every 3 s shows it within 3 s.
Nothing else: the stream as specified sends the whole answer on a
change, as a poll does.

**What a stream costs here.**

- A connection and a once-a-second `stat` loop per display in the
  orchestrator, for as long as the page is open.
- Shutdown. uvicorn waits for open responses when it is told to stop,
  and an event stream never ends. The containers set no
  `--timeout-graceful-shutdown` and no `stop_grace_period`, so every
  deploy and every dev rebuild with a display connected would wait
  Docker's 10 s and end in a kill, taking any chat or voice request in
  flight with it (not run; read from uvicorn's shutdown and the compose
  file). Curable, by more code.
- Recovery. `EventSource` reconnects after a dropped connection. It does
  not after an answer that is not 200: the specification says to fail
  the connection for good. During a deploy nginx is up and the
  orchestrator is not, so the answer is 502; `/dev/` answers 502
  whenever the dev pair is stopped. The page therefore needs its own
  timer to open a new `EventSource`, plus the polling fallback the
  criteria already ask for, plus the 45 s silence rule. That is a poll
  loop and a stream to maintain and test (not run).
- A deploy restarts nginx and the orchestrator: every stream drops on
  every deploy, by design.
- nginx needs nothing: buffering is off and the 15 s heartbeat is inside
  the 300 s timeout.

**What a poll costs.** One small request per display per interval. No
held connection, no watcher, nothing at shutdown. Recovery is the next
tick, whatever went wrong: orchestrator restarted, nginx restarted, 502,
the network gone for an hour, the browser asleep (a timer that was
throttled or suspended runs when the tab is back; a `visibilitychange`
handler polls at once).

**Recommendation: poll every 3 s. No stream, no `EventSource`, no
heartbeat route.** Details the criteria should fix:

- **The conditional is in the query, as "Heard"'s is.** `GET
  /api/work?have=<work.ts>` answers the same envelope with `work: null`
  and `unchanged: true` when the file's `ts` is the page's. An `ETag`
  and a 304 do not fit: the envelope's `age_s` and `state` change with
  time while the file does not, and the page needs both on every poll.
  The route keeps the parsed file in memory keyed by the file's mtime,
  size and inode, so an unchanged poll costs one `stat` (today each
  request reads and parses 126 KB). `Cache-Control: no-store` on both
  API routes.
- **One scheduler for the page.** A `setTimeout` chain (the next poll is
  set when the last one ends), each fetch aborted after 10 s. Today
  `fetch('api/work')` has no time limit and `setInterval` starts another
  every 30 s: with the network black-holed they pile up, and a late
  answer can draw over a newer one. "Heard" joins the same scheduler at
  its own 30 s; its line in `work-page.md` ("M32 then swaps only the
  timer for its stream") becomes "joins the page's scheduler".
- **A failed poll keeps the last state.** Today one failed fetch gives
  the UNREADABLE banner and `renderEmpty()`: the page goes blank on a
  network blip. The page keeps what it had and says "no answer since
  HH:MM:SS (reason)".
- **The exporter runs on a write by a path unit**:
  `kronk-work-export.path` with `PathChanged=` on
  `work.db.dump.sql`, starting the existing service. `work.py` rewrites
  the dump by rename after every committed write, so the unit fires
  within a moment; the tool neither waits nor knows. `agent.py` writes
  through `work.py`, so one unit covers both. No code in either tool,
  which matters because an edit to them is live the second it is
  written. The service is a oneshot, so runs do not overlap; a write
  that lands while a run is under way may be missed by the path unit
  and is picked up by the timer (not run; to be measured). The unit is
  the coordinator's to install. Writes with `--db` on a scratch copy do
  not touch the watched file.
- **The timer stays at 60 s in step 1.** Agent launches, finishes and
  closes are `work.py` writes and ride the path unit. What is left to
  the timer is herdr-only change (an agent going idle between turns).
  If a minute for that proves too slow, lowering the timer is one later
  change, with `WORK_EVERY_S`, `WORK_MAX_AGE_S` and the unit comments
  following it. Target: a change made through the tools on the page
  within 5 s; any other within 65 s.

## 3. A wall display

What a page left open for months needs that the criteria do not ask for:

1. **The reload must not strand the page.** `location.reload()` during a
   restart lands on nginx's 502 page, where no script runs, for good.
   Before reloading, the page fetches `work` with `no-store` and reloads
   only on a 200 whose stamped version is the one announced. One reload
   per ten minutes at most, the last reload's time kept in
   `sessionStorage` so the limit survives the reload.
2. **How the version is known.** The orchestrator hashes `work.html`
   and `kronk.css` once at start (the static files are baked into the
   image; dev mounts only `/data` and `/config`) and puts the hash in
   every `/api/work` envelope. `_serve` stamps the same hash into the
   page it serves (a `<meta>`), so the page knows its own version from
   its own bytes. The criteria's "the one the page was loaded with",
   taken from the first API answer, is wrong when the page came from
   the old container and the answer from the new. A StaticFiles `ETag`
   is no use: it is made of mtime and size, which change on every
   release checkout.
3. **A daily reload** by the same safe path (for instance at 04:00 when
   nothing is under way) costs nothing and covers a browser's own
   growth over months. Recommended.
4. **Clocks.** The page never subtracts the server's time from
   `Date.now()`. The age of the data is the route's `age_s` plus the
   time since the answer by `performance.now()`; an agent's elapsed
   time is (`work.ts` + `age_s`) minus `started_at`, plus the same.
   A display whose clock is an hour out then shows right ages.
5. **Time zone.** Step times are shown "in this browser's local time";
   a kiosk may run in UTC. Shown times use the house's zone: the
   exporter writes its offset or zone name once, the page formats with
   it. "Heard" already shows the house's time as written.
6. **Alive, and seen to be.** Three signs, each with one meaning:
   - the age of the data, counting each second (the script runs);
   - a mark that beats once per answered poll, fired by the script and
     not an endless CSS animation, which would keep beating after the
     script died;
   - a cover the stylesheet shows by itself: a CSS animation with a
     90 s delay that dims the page and shows "this page has stopped
     updating", restarted by the script on every answered poll. If the
     script dies or every poll fails, the cover appears with no script
     needed.
   When the data is stale, or no poll has been answered for 30 s, all
   endless motion stops: motion means "being worked now".
7. **Memory.** After points 2 and 6: two timers at most, one fetch in
   flight, rows patched in place, no listener added per render (one
   delegated listener per section). "Heard": the open list is trimmed
   to the count loaded (200, or more after "show older") when a new run
   is patched in, and a section folded for ten minutes drops its rows.
   A check: the number of elements and of timers is the same after 500
   updates as after 5.
8. **Read from across a room.** The page's type is 11 to 13 px on a
   14 px base; `--text3` on `--bg` has a contrast of 3.2 to 1 (text2
   7.5, text 15.7; computed from `kronk.css`). Kiosk zoom covers size:
   the layout is fluid under `max-width: 1000px` and showed no overflow
   at 1280 (M34-e2e). Zoom does not cover contrast: nothing that
   carries state uses `--text3`. The page is dark already.
9. **No hover, focus or scroll.** Nothing essential is behind a
   `title`, a hover style or a click. That collides with the handoff
   line: "every item unfolds" needs a hand. On the wall, what shows is
   what is open by default and what fits the first screen. So the order
   of the page matters: state and live mark, Under way (open), Queue,
   then the rest. Today "Heard" is first, then the state line; Backlog
   (about 150 nodes) sits between the Queue and Done.
10. **Sizes.** Tested at 1920 by 1080, at 960 by 540 (the same display
    at 200% zoom) and at 1280. A layout made for the display stays out
    of scope, but "Under way and the Queue fit the first screen at 1920
    by 1080 with one item under way" is a criterion worth having.

**"Heard" on a wall.** Folded, which is how it loads every time, it
shows counts and times and no speech. Its rows show only after a click.
The choices, which are the operator's:

- (a) leave it: the folded line (runs in 24 h, newest, marked wrong,
  errors) is on the wall for anyone in the room;
- (b) a display mode (`/work?display=1`) that leaves "Heard" out and
  never fetches it, the full page unchanged for a desk browser;
- (c) take "Heard" off `/work` to a page of its own.

A display mode is also where larger type and a first-screen layout
would go later. I recommend (b), in step 3.

## 4. Animation

CSS-only motion is enough and can be safe for months, with these rules
in the criteria:

- **Only `transform` and `opacity` are animated.** As specified, two of
  the effects would not be: a moving stripe is usually
  `background-position` (repainted every frame, for hours, on a
  low-power display) and a line "drawn" is usually `width`. Both are
  done as a pseudo-element moved or scaled inside `overflow: hidden`.
  The pulse is opacity, not `box-shadow`.
- **At most two endless animations at once** (the current step's pulse
  and the stripe while an agent works), both stopped when the data is
  stale or unanswered (point 3.6), both off under
  `prefers-reduced-motion`.
- **Counters do not shift their neighbours**: tabular figures in a
  fixed-width box with `contain: content`.
- **What moves**: the current step pulses; the stripe runs while
  `items[].agents[]` has a `working` agent; the agent's elapsed time
  counts; a row whose content changed flashes once; a verdict fades in
  once; the live mark beats per poll.
- **What does not**: the slide of a finished item from "Under way" to
  "Done". It needs positions measured in script across two containers,
  it is the one effect that is not CSS-only, and nobody is watching at
  that second. The card leaves and the new Done row flashes. Nothing
  flashes on first load. Nothing moves for a queued, held, promoted or
  dropped item.
- **The track.** "criteria, review, build, test, fix, promote" as six
  stations misdraws real items: M30 had four builds, nine tests and
  five fixes. Stations: criteria, review, build, test, promote. A fix
  is a return to build and test, shown as a round count on the station
  ("test 3"). A station is filled when a finished step of its kind
  exists (`steps[].kind`, `state`), not by position in the list: steps
  are ordered by time and M35's review sorts before its criteria
  (M34-e2e note 10). The current station comes from
  `items[].stage.stage`, which is `work.py`'s own; the page computes no
  stage. A test station takes its colour from `graded` when present,
  else `verdict`. A skipped review is drawn hollow.
- **Plumbing 6.** No specified motion holds a pixel bright; a pulse
  dips below its resting brightness and never rises above it. The
  static parts are the risk on an OLED panel: the sticky header, the
  section labels, the live mark, in one place for months. On an LCD it
  does not matter. If the panel is OLED, a slow shift of the whole page
  by a few pixels (one `transform`) is the cure; otherwise nothing more
  than point 5 is needed. The panel type is a question below.

## 5. What and why per item

| Wanted | Exists | Missing |
| --- | --- | --- |
| What | `handoff` (11 of 38 items), `title`, `shipped[]` | nothing; the old items have none to show |
| Why | `source` (15 of 38), clipped at 600 characters | nothing |
| Steps, agents, verdicts, times | `steps[]`: `kind`, `round`, `name`, `state`, `by`, `agent`, `started_at`, `ended_at`, `verdict`, `graded`, `file`; `items[].agents[]`: `name`, `status`, `role` | since when an agent has had its status (herdr does not say; the step's `started_at` serves) |
| Tier, queue place, what holds it | `tier`, `position`, `next`, `blocked_by`, `after_item`, `held_why` | nothing |
| The decision it waits on | `waits_on`, `waits_note` (text, already shown); `open.decisions`; `decisions_owed[]` by `item` | the decision's text (#22) |
| Leftovers | `open.leftovers` by owner | the list and its text (#22) |
| When it ended | `done[].at`, `date` for promoted | `ended_at` for dropped |

**Text later with no rebuild of the page.** The exporter runs from the
working tree and is live when written; the page is in a tagged release.
So the page is built now to show what the file does not yet hold:

- the exporter adds, per item, `open_list`: one entry per open leftover
  and decision with `id`, `kind`, `owner`, `blocks`, `date`, and no
  `text`; the page lists them ("leftover #41, coordinator,
  2026-11-25");
- the page shows `text` on an entry, and on `decisions_owed[]`, when
  the key is there, through `el()` (plain text, not `mdText`, so
  nothing in it becomes a link);
- a fixture carrying `text` with markup is part of this item's browser
  test, so the day the operator answers #22 the change is to the
  exporter and to M34's marker test (criterion 6 there), and nothing is
  deployed.

Whether ids and dates of leftovers may show before #22 is answered is
itself part of #22; if not, `open_list` ships empty and the counts stay.

## 6. No full re-render

- **Keys.** Item cards, queue rows and dropped rows by item id. Done
  rows by `done[].promotion` and id (today's key is `id@line`, which is
  `M30@null` for every promotion since M34). A deploy with no item by
  its `promotion`. Backlog entries by title, not by `line`: the line
  moves whenever `ROADMAP.md` is edited above it, and an open fold then
  jumps to another entry. Steps by `kind`, `round`, `name`.
- **Patch.** One function for every list: walk the wanted keys in
  order; a node whose data is unchanged (compared by the row's JSON) is
  left alone and not moved unless its place is wrong; a changed one is
  updated part by part; a missing one is built and inserted; an
  unwanted one is removed. A card is a fixed frame with parts (head,
  stage, track, body), each compared on its own, so a change of
  `next_step` does not replace the node the stripe runs on. Moving or
  replacing a node restarts its animation; that is the whole reason for
  the rule.
- **The flash is driven by a row's change, not the file's**: `ts`
  changes every minute with nothing new in it.
- **Folds.** Whether a row is open lives in the node while the page
  lives. Across a reload it is kept in `sessionStorage` (this tab
  only), never `localStorage`: a row opened once on the display's
  browser must not stay open there for good, which is M30's rule for
  "Heard" and holds for the rest. "Heard" stays folded on every load.
- **Sharing with "Heard".** Not the list code: "Heard" gets changes
  from the server by `seq`, groups by day, pages, and anchors the
  scroll; the work rows get the whole file and compare. Shared: `el`,
  `clear`, `fill`, the banner builder, the scroll anchor, and the
  scheduler of point 2.
- **One script file (plumbing 10): later.** The page's texts are JSON
  literals the suite reads out of `work.html` (it has no JavaScript
  runtime), a second file needs its own cache-busting and a place in
  the version hash, and no second page uses it in this item. Extract it
  in the item that puts `/resources` on a display.

## 7. The carried items

- **S1** (a dump that loads and lacks a column). In `collect()`, the
  five `SELECT`s, `queue_rows()` and `overview()` go inside one `try`;
  `sqlite3.Error`, `KeyError`, `IndexError` and `TypeError` there give
  state "corrupt" with a sentence of its own ("loads as SQL and is not
  the schema this exporter reads"; today's "does not load as SQL" would
  be false), no items, the ROADMAP part kept, exit 0. The strict
  `xfail` in `tests/test_m34_e2e.py` loses its mark. Step 1.
- **Note 1** (five dates are UTC days). The page's note says "each date
  is the house's day of the promotion". For the 33 rows imported from
  the old log the date is the one written there. One sentence in the
  page and in `work-page.md`; the rows are not changed. Step 1.
- **Note 3** (the note under "Under way"). Already done: it described
  the page of `item-M30`. The deployed page's `UNDER_WAY_NOTE` reads
  "Work items the work-state database has active: started and not yet
  promoted." Nothing to do.
- **Note 4** (dropped items). Held items have a place, the Queue, with
  their reason. Dropped items get a short folded list after Done:
  id, title, "dropped", the day. That needs `ended_at` exported;
  `ended_why` stays private, as M34 ruled. Step 2.
- **Note 5** (agents when the database cannot be read). The exporter
  writes the reason, so the cure is there: with no rows, `why` is "the
  work-state database was not read", not "no item of that name". The
  page shows it as it comes. Step 1.
- Also from that report, because this item redraws the rows: note 8 (a
  queued item's row shows no stage and no track; its agent stays under
  "Other agents"), note 10 (above, the track), note 11 (`mdText` makes
  a link of `[text](https://…)` in a title: inside a `<summary>` a link
  takes the click that should fold, so titles and labels get code and
  emphasis and no links).

## 8. `/resources`

Not in this item. The 16 writes build seven blocks (memory bar, legend,
stats, disk, server cards, other processes, the activity table); the 36
`esc()` calls cover every string I traced. What is interpolated bare is
numeric by type (`port`, `n_ctx`, `total_slots`, counts), which is a
thin assumption and not a known hole. Turning it to element building is
about 200 lines across those seven functions and, more than that, a
browser comparison of the page before and after in every state of its
three sources: a test the size of this item's own, for a page the order
does not mention. Six other pages have the same pattern (`index` 13,
`services` 11, `performance` 6). It belongs with the item that puts
`/resources` on a display, or nowhere; criterion 8 goes with it.

## 9. Size and cut

Not one build. Three steps, each deployable:

**Step 1, live and safe (the smallest worth a display).** No redesign.
- Route: `?have=`, the in-memory copy, `no-store`, the page version in
  the envelope and stamped by `_serve`; both routes and `/work` out of
  telemetry.
- Page: one scheduler with time limits, last state kept on a failed
  poll, the age counting, the live mark, the stylesheet's cover, the
  safe reload and the daily one, ages by `performance.now()`, the
  house's zone, "Heard" on the scheduler and trimmed.
- Host: the path unit (reference files by the build agent, installed by
  the coordinator).
- Exporter: S1, note 5, the zone. Page text: note 1.
- The rows still rebuild on a change, as now; folds survive as now.

With kiosk zoom this is a page that updates within seconds, shows it is
alive, survives deploys and outages, and cannot strand itself.

**Step 2, rows.** Keyed patching for the five sections; every item a
row that unfolds to what, why, steps and `open_list`; dropped items;
folds in `sessionStorage`; the order of the page; exporter keys
(`ended_at`, `open_list`), schema 3.

**Step 3, motion and the display.** The track and the animations of
point 4; contrast; `?display=1` if the operator wants it.

Steps 2 and 3 can be one build if the coordinator prefers two steps;
step 1 should stay apart, because it is the part that touches the
route, the exporter and a unit, and it is the part worth having first.

## 10. Are the criteria checkable

By the test agent, in headless Firefox behind its loopback proxy, as
`M34-e2e.md` did. As written, six are not:

| # | Problem | Reworded |
| --- | --- | --- |
| 1 | "A step started through the tools" means `work.py` on the live database, which no agent runs; the path unit watches only the live dump. | In parts. Test agent: a fixture written to `data/dev/work.json` (exporter with `--db <scratch>`, the timer stopped and started again, which the criteria must allow by name as M34's did) is on the page within 4 s, ten times, times listed. Coordinator at promotion: one real `work.py note`, timed from the write to the page, three times. |
| 2 | An agent may not restart nginx; headless has no offline switch; no `EventSource`. | The proxy makes the cuts: 502, connection refused, a connection that hangs, each for a minute and one for an hour (a shortened clock is not accepted: the hour is run). Plus one real rebuild of the dev pair. After each: the page said so, kept its last state, and is live again without a touch. |
| 3 | A changed page needs a commit to make. | The proxy announces another version: the page checks, reloads once, not again within ten minutes. The proxy announces another version and answers 502 for the page: no navigation. The build's own dev rebuild is the real case. |
| 4 | No `stat` loop to count. | Twenty pages for ten minutes: no model completion, no trace sent, one file read per change of the file, orchestrator CPU under 2%. |
| 5 | "Described frame by frame" cannot be failed. | `document.getAnimations()`: every running effect animates only `transform` or `opacity`; at most two endless; none with the reduced-motion preference set; none when the data is stale; screenshots at stated times at 1920 by 1080, 960 by 540 and 1280. |
| 8 | Leaves with `/resources`. | |

Criteria 6 (reworded in point 1), 7, 9 and 10 stand. For 7, "does not
restart an animation" is checked as: the same node before and after an
update, its animation's `currentTime` larger, `scrollY` equal.

Missing:

- the page with a failed poll shows its last state, never empty;
- the stylesheet's cover appears with the script stopped (the proxy
  serves the page with the scheduler removed);
- a browser clock an hour wrong (an injected `Date`) shows the same
  ages;
- element and timer counts equal after 500 updates and after 5;
  "Heard" open through 300 new fixture runs holds 200 rows;
- `text` in `open_list` and `decisions_owed` from a fixture shows as
  text, markup included, and the real export still carries none
  (M34's marker test);
- S1's `xfail` passes; an unreadable database gives the agents' new
  reason;
- an old page (`item-M34`'s) reads the schema-3 file with no error, as
  M34 checked for its own move;
- the first screen at 1920 by 1080 holds the state line, Under way and
  the Queue;
- by the operator, once, on the real display: readable from where he
  sits. No agent can check that.

## Steps proposed

1. **Step 1, live and safe**: point 9. Tier 2: build, one test.
2. **Step 2, rows**: keyed patch, unfolding with what and why,
   `open_list`, dropped items, page order. Tier 2.
3. **Step 3, motion and display**: track, animations, contrast, display
   mode. Tier 2.
4. **Not in M32**: `/resources`' `innerHTML` (point 8); the shared
   script file (point 6); a 15 s timer unless step 1's measurements ask
   for it.

## Questions for the operator

1. **"Heard" on the wall**: leave its folded line (counts and times, no
   speech), leave it out in a display mode, or move it to its own page?
2. **The display**: what panel (LCD or OLED), what size and resolution,
   and how far away is it read from? It decides the pixel shift, the
   zoom and whether a display mode is built in this item.
3. **Decision #22**, still open: may `/work` show the text of leftovers
   and decisions on the LAN, their ids and dates only, or counts only?
   The page is built to take the text either way.
4. **The 27 old items with no handoff line**: show title and Shipped
   line only (recommended), or have the coordinator write what and why
   for them?
5. **Unfolding on a display nobody can touch**: is it enough that the
   item under way is open and the rest unfold on a desk browser, or
   should the display show what and why for the queue as well?
