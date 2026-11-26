# M32 pass criteria: /work for a wall display: live, foldable, with what and why

Source: Drew, 2026-11-24. "the work items in /resources should also be
foldable. They should also include more details about the work item.
What it is, why we're doing it are good starting points." (Read as
`/work`: that is where the work items are; reported to Drew.) And:
"/work should be live-updated. I have to refresh it to get new data but
it should be able to pull that information live without me needing to
refresh it. The reason is that this page, and others like it, will
eventually be put onto a display to show details about kronk and said
display won't have the ability to refresh. add some interesting (but
simple) animations to help visualize the state of a current work item."

Handoff line: left open on a screen nobody can touch, `/work` shows a
change within seconds, shows by motion which item is being worked and at
what step, and every item unfolds to what it is and why it is being
done.

Plan doc: this file. Design review before build. Built after M33: the
what, the why and the queue come from the database.

## What the investigation found (coordinator, 2026-11-24)

- The page does refresh itself: `setInterval(loadAll, 30000)` in
  `orchestrator/static/work.html`. What Drew saw is the delay and the
  lack of any sign of life. The data is a file a host timer writes every
  60 s, so a change takes up to 90 s to show; nothing on the page moves
  between loads; and a browser slows timers in a tab that is not in
  front.
- `/api/work` is sent with no `Cache-Control` header.
- A page left open for weeks runs the script it loaded. After a deploy
  the display would keep the old page until someone reloads it, which on
  that display nobody can.
- The cards are thin because the coordinator ruled so in M27 ("an
  outline, not the documents", the page being readable on the LAN).
  Drew's order reverses that for what an item is and why.
- nginx already carries a server-sent-events location for the chat
  route (`/message`), with buffering off.
- `/resources` builds markup from strings with `innerHTML` in 16 places
  (found while M29, since dropped, was being scoped).

## Plumbing (coordinator's decisions)

1. Push, with polling under it. A new route, `/api/work/stream`
   (server-sent events), sends the whole `/api/work` answer when the
   exported state changes and a heartbeat every 15 s. The orchestrator
   notices a change by `stat` on the export file once a second; it calls
   nothing else. The page uses `EventSource`, which reconnects by itself;
   if no event or heartbeat has come for 45 s it falls back to fetching
   `/api/work` every 15 s (`cache: 'no-store'`) and keeps trying the
   stream. `/api/work` gains `Cache-Control: no-store`.
2. Faster at the source. `scripts/work.py` and `scripts/agent.py` run the
   exporter right after a write of theirs, and the timer drops from 60 s
   to 15 s for changes that are nobody's write (an agent going from
   working to idle). Target: a change made through the tools is on the
   page within 5 s; any other within 20 s.
3. The page reloads itself after a deploy. Every stream event and every
   `/api/work` answer carries the page's version (a hash of the served
   `work.html`); when it differs from the one the page was loaded with,
   the page reloads once, keeping what was folded open. Never more than
   one reload per ten minutes.
4. A sign of life that cannot lie: a small "live" mark that beats on
   each heartbeat, and the age of the data counting up each second.
   When the stream is down and the fallback is running it says so; when
   the data is stale it says so, as now.
5. Motion, for the item under way only, simple and CSS-only:
   - a track of the item's steps (criteria, review, build, test, fix,
     promote): done steps filled, with the verdict's colour for a test;
     the current step pulsing; a line drawn between steps as they
     complete;
   - while an agent is working: a slow moving stripe on the current
     step and the agent's elapsed time counting up;
   - on a change: the changed card flashes once, a verdict pops in, an
     item that finishes slides from "Under way" to the top of "Done".
   Nothing else on the page moves. `prefers-reduced-motion` turns it all
   off except the counters. No animation runs when nothing is under
   way. No library.
6. A wall display stays on for months: no element is animated in a way
   that keeps a bright pixel in one place brighter than its neighbours
   forever; the reviewer says whether that needs more than point 5.
7. Folding and detail. Every item, under way, queued or done, is a row
   that unfolds to: what it is (the handoff line), why (the source, with
   Drew's words), its steps with agents, verdicts and times, its
   leftovers, the decision it waits on if any. The item under way is
   open by default; the rest closed. The queue shows in order with each
   item's next step and what it waits on. What is open stays open across
   updates and reloads.
8. A rendered update never rebuilds the page: only what changed is
   replaced, so an opened row, the scroll position and a running
   animation survive an update.
9. All text is set as text. In passing, `/resources` is changed the same
   way: its 16 `innerHTML` writes become element building, with no
   change to what it shows (compared before and after in a browser).
10. The live part (stream client, fallback, version reload, the live
    mark) is one small script file other pages can load; only `/work`
    uses it here. `/resources` on the stream is a later item.
11. What may show on the LAN: what, why and Drew's words for each item,
    as the criteria files have them. Report text and rulings stay off,
    as before. (What Whisper heard is on the page by Drew's order, in
    M30's "Heard" section; that is not this line's subject.)

## Criteria

1. With `/dev/work` open and untouched: a step started through the tools
   shows within 5 s; an agent going idle shows within 20 s; measured ten
   times each, times listed.
2. The stream cut (orchestrator-dev restarted, nginx reloaded, the
   network pane's offline switch): the page says so, falls back, and is
   live again by itself. Left open for two hours with one of each cut:
   still live, memory not growing, one `EventSource` open.
3. A new build of the dev pair with a changed page: the open page
   reloads itself once and keeps what was open.
4. Twenty pages open at once cause no model completion and no more than
   one `stat` a second each in the orchestrator; the orchestrator's CPU
   stays under 2% with all twenty idle.
5. Each motion in point 5 seen in a browser at 1280 px and on a 1920 by
   1080 window as the display would show it, with a made-up item walked
   through its life by the tools in a scratch database served to dev;
   described frame by frame in the test report. With reduced motion set,
   none of it runs.
6. Every item unfolds to its what and why, and they equal the criteria
   file's handoff line and source. Markup in any of them stays text.
7. An update does not close an opened row, move the scroll, or restart
   a running animation.
8. `/dev/resources` shows the same figures and text as before the
   `innerHTML` change; hostile strings in a fixture become text.
9. The shims and the chat stream are unaffected: `/voice/api/chat`,
   `/api/chat` and a `/message` stream answer on dev with twenty `/work`
   pages open.
10. The suite passes; last lines quoted.

## Out of scope

- Putting `/resources` or any other page on the stream.
- The display itself: its browser, kiosk mode, a layout made for it.
- Changing the queue from the page.

## Rulings

(none yet)

## Rewritten after the review (this section decides)

Coordinator, 2026-11-26, on `M32-review.md` (rewrite the criteria). The
order stands; everything above this section is history and is not built
from. M30 ("Heard") and M34 (the page on the work-state database,
`work.json` schema 2) changed the page since it was written. The review
is the design: where a point below says "review N", that point of
`M32-review.md` is the text to build to.

Decided:

- **Poll, no stream** (review 2). No `EventSource`, no heartbeat route.
- **Three steps, each deployed on its own tag**, tier 2 each (build, one
  test; a narrow retest only if the test finds a blocker):
  1. Live and safe. 2. Rows that unfold. 3. Motion and the display.
- **Out of M32**: `/resources`' `innerHTML` (review 8), a script file
  shared with "Heard" (review 6), a faster timer.
- Carried from M34 into step 1: S1 and note 5 (exporter), note 1 (page
  text). Into step 2: notes 3 and 4.
- Waiting on Drew, none blocking step 1: "Heard" on the wall; the
  display's size and distance; what text `/work` may show (#22); the 27
  old items with no handoff line; what the wall shows unfolded.

### Step 1: live and safe

No redesign. A change made through the tools is on the page within
seconds, the page shows it is alive, survives deploys and outages
untouched, and cannot strand itself.

Plumbing (review 2, 3 and 9, "Step 1"):

1. Route: `GET /api/work?have=<ts>` answers the envelope with `work:
   null` and `unchanged: true` when the file's `ts` is the page's; the
   parsed file is kept in memory keyed by the file's mtime, size and
   inode; `Cache-Control: no-store` on `/api/work` and
   `/api/voice_runs`. The orchestrator hashes `work.html` and
   `kronk.css` once at start, puts the hash in every `/api/work`
   envelope and stamps it into the page it serves. `/work`, `/api/work`
   and `/api/voice_runs` leave the telemetry (no trace per poll).
2. Page: one scheduler (a `setTimeout` chain, each fetch aborted after
   10 s), polling every 3 s, "Heard" on the same scheduler at its 30 s;
   a failed poll keeps the last state and says since when and why; the
   data's age counting each second; a mark that beats once per answered
   poll; a cover shown by the stylesheet alone when no poll has been
   answered for 90 s; a reload only after a 200 whose stamped version
   is the one announced, at most once in ten minutes, and once a day by
   the same path when nothing is under way; ages by `performance.now()`
   and the route's `age_s`, never the browser's clock; shown times in
   the house's zone (the exporter writes it); "Heard"'s open list
   trimmed to the count loaded, and its rows dropped after ten minutes
   folded. Rows still rebuild on a change, as now; folds survive as now.
3. Host: `systemd/kronk-work-export.path`, `PathChanged=` on the live
   dump, starting the existing service. Reference file by the build
   agent, proven with `systemd-analyze --user verify`; installed by the
   coordinator. The timer stays at 60 s.
4. Exporter: a dump that loads and is not the schema writes state
   "corrupt" with its sentence and exits 0 (M34 S1; its `xfail` loses
   its mark); with the database unreadable an agent's reason says so
   (M34 note 5); the house's zone in the file. Page text: the date note
   says which rows are UTC days (M34 note 1). Schema stays 2 unless a
   key must be added, and then keys are only added.

Criteria (review 10, as reworded there):

- 1a. By the test agent in a browser on dev (headless Firefox behind a
  loopback proxy; it may stop `kronk-work-export.timer` while it holds
  a fixture in `data/dev` and must start it again): a fixture written
  to `data/dev/work.json` is on the page within 4 s, ten times, times
  listed.
- 1b. By the coordinator at promotion, on the live system: one real
  `work.py note`, timed from the write to the page, three times; within
  5 s.
- 1c. The proxy cuts the page off (502, connection refused, a
  connection that hangs), each for a minute and one for a real hour;
  and one real rebuild of the dev pair. After each: the page said so,
  kept its last state, never went blank or to an error page, and is
  live again without a touch.
- 1d. The proxy announces another page version: one reload, not again
  within ten minutes. It announces another version and answers 502 for
  the page: no navigation.
- 1e. Twenty pages for ten minutes: no model completion, no trace sent
  to Tempo by these routes, one read of the file per change of the
  file, orchestrator CPU under 2%.
- 1f. The cover appears with the script stopped (the proxy serves the
  page with its scheduler removed). A browser clock an hour wrong (an
  injected `Date`) shows the same ages. Element and timer counts are
  equal after 500 updates and after 5. "Heard" open through 300 new
  fixture runs holds 200 rows.
- 1g. `/api/work` without `have` answers as it did but for the added
  envelope keys; an old page (`item-M34`'s `work.html`) reads the new
  route with no error.
- 1h. S1's `xfail` passes; an unreadable database gives the agents'
  new reason; no script error, no "undefined", markup stays text;
  "Heard" still loads and filters.
- 1i. The path unit: `systemd-analyze --user verify` clean; on a
  scratch dump and a scratch copy of the unit pointing at it (run with
  `systemd-run --user` or shown by reading, the test agent says which),
  a write starts one export.
- 1j. The suite passes; last lines quoted.

Steps 2 and 3 get their criteria when step 1 is promoted, from review
4, 5, 6, 9 and 10 and Drew's answers.
