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
    as the criteria files have them. Report text, rulings and transcripts
    stay off, as before.

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
