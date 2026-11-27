# M32 step 3 fix 1: the worked card moves, no grade on an unfinished round, five steps shown

Fix agent (Opus 5.5), 2026-11-27, from `6ba5e48`. Contract: `M32-criteria.md`,
"Rulings on step 3's test" (B1, B2, note 2 and S1 of `M32-step3-e2e.md`, and
"The first screen at 1920×1080"; one fix round). Code commit `9f9a722`. Only
`orchestrator/static/work.html` changed in the product; no route, exporter or
schema change (schema stays 4).

## What changed

- **B1, `renderMoving`.** It no longer moves the first card. It collects
  every current station on Under way that is not blocked and moves one: the
  first with `working` (set by `trackPart` on a current station while the
  item has an agent `working` and the stage is not blocked), else the first
  of them. With a stage decided by agents and not blocked, the station is
  the working agent's, so `working` is "the current station has an agent
  working". One station moves, so at most two endless effects (pulse and
  stripe) as before. Which card moves is still not in the track's
  signature: a card that begins to move does not flash.
- **B2, `trackOf`.** A new state, `started`: a station whose latest round is
  started and not finished, and that is not current. It comes before
  `done`, so an earlier finished round no longer fills it. Its count is the
  rounds started (unchanged: started steps were already counted). The test
  station's `grade` is null unless the station is `done`, so no verdict
  colour reaches it. Drawn half filled in the text colour (`.stn.started`),
  no green, red, amber or blue. "Latest" is by start, `latestRound()`:
  the steps list is in the order the database recorded them, and M32's own
  file shows that is not the order of rounds (fix 3, started 19:08, sits
  before test 4, started 18:40 and finished 19:08). The test station's
  grade now comes from `latestRound(tests)` too, by the same rule, so a
  finished round placed earlier in the list cannot show an older grade. A
  started review or build also makes review "skipped" when none is recorded
  (`past` reads `open.build || open.test`).
- **Note 2.** `renderMoving` passes over a station with `blocked`: it stays
  red (`.stn.cur.blocked`) and still. Combined with B1: when the first card's
  station is blocked, the next card with a current station moves. That is
  my reading of the two points together; the ruling says nothing more.
- **S1, `.stn-clock`.** `width: 8ch` is now `min-width: 8ch; white-space:
  nowrap`. A time ("7:32", "1:02:09") fits the 8ch and counts without moving
  anything; "start not recorded" widens the box on one line. The text stays:
  it is the specific cause.
- **The first screen, `stepsBlock`.** On the item under way's card
  (`itemRow`'s `stepsFold`, only from `renderUnderWay`), the steps and the
  uncommitted ones are one list; the last five (`STEPS_FOLD.shown`) are shown
  and the earlier ones sit inside one `<details class="steps-earlier">`,
  summary "N earlier steps" ("1 earlier step"), made by `foldable()` with key
  `uw:<id>:steps`, closed by default. So it keeps its state like any other
  fold: in the node while the page lives, across a redraw of the body (a
  new step) and across a reload in this tab's sessionStorage; closed again
  it is dropped from the store. Done rows show every step, as before.
- **`docs/features/work-page.md`**: the Steps line, the track's `started`
  state and `latestRound`, what moves and the blocked station, the clock's
  box, and the two "Known gaps" lines on two items and the clock.

## Tests

In the suite (`tests/test_m32_step3.py`, source read, as for step 3):

- `test_the_card_that_moves_is_the_first_being_worked` (replaces
  `test_only_the_first_item_under_way_moves`): the pick (`working`, else the
  first current), one station, never by place alone, `working` only on a
  current unblocked station.
- `test_note2_a_blocked_station_never_moves_and_keeps_its_red`: blocked
  stations are filtered out of the pick; the red rules; no endless rule
  names a blocked station.
- `test_b2_an_unfinished_round_is_never_drawn_with_an_earlier_grade`: the
  `started` state before `done`, `latestRound` by start, the grade only on a
  filled station and from `latestRound(tests)`, the grade class only on
  `done`, no verdict colour or `--text3` on `.stn.started`.
- `test_s1_the_clock_box_does_not_wrap`: `white-space: nowrap`, `min-width:
  8ch`.
- `test_the_card_under_way_shows_its_last_five_steps_and_folds_the_rest`:
  `STEPS_FOLD`, the labels, one `foldable()` closed by default, the slices,
  only on Under way, the key threaded through `itemRow` and `bodyInto`.

Changed: `test_a_station_is_filled_by_kind_and_state_not_place` reads the new
`past`; `test_m32_step2.py::test_the_body_unfolds_to_what_why_decisions_leftovers_and_steps`
reads `stepsBlock(it, stepsFold)`.

What the suite cannot read (no JavaScript engine) ran under gjs: 
`docs/sprint/M32-step3-fix1-gjs/run.js` (step 3's harness's helpers, step 2's
stub DOM, today's dev export as fixture). 36 PASS, 0 FAIL under `TZ=UTC`,
`America/New_York` and `Asia/Tokyo`:

- B1: M32 waiting with M99 building → `uw:M99:build+stripe`; both worked →
  `uw:M32:build 6+stripe`; both current, neither worked → `uw:M32:criteria 3`;
  M32 waiting, M99 at criteria → M99's criteria; neither current → nothing;
  M32 blocked with M99 at criteria or building → M99. At most two endless in
  every case. The move passing from M32 to M99: M99's same card and build
  node, and only M32 (whose data changed) flashed.
- Note 2: M32 alone, `m32-fix3` blocked: `build 6[blocked cur]`, nothing
  moving; unblocked: `build 6[cur moving working]`.
- B2: test 5 started (placed before a finished step in the list), agent
  idle, stage waiting: `test 5[started]`, no grade, nothing moving; working:
  `test 5[cur moving working]`; finished graded pass: `test 5[done pass]`,
  faded in. Today's file with `m32-fix3` `done` and stage waiting:
  `build 6[started]` (it read `build 6[done]` before). M30's Done row as
  before.
- The fold: M32 (14 steps) shows 5, "9 earlier steps" closed with 9 inside;
  opened, stored `{"uw:M32:steps":true}`; a step added: still open, "10
  earlier steps", 5 shown, the card still open; reloaded with the same
  session store: open; closed: store `{}`. M30's Done row: all 20 steps, no
  fold. M99 with 3 steps: no fold.
- S1: `m32-fix9` working with no started step: "start not recorded".

Step 3's harness (`docs/sprint/M32-step3-gjs/run.js`) on the fixed page with
today's export: 59 PASS, 6 FAIL. Five are the old rule it pins ("the second
item under way: current, still", "nothing moving" at M99's test FAILS while
M32's `m32-fix3` works, and so on: with M32 being worked behind M99, M32 now
moves, which is B1's ruling). One is the fixture: its "real data" line names
`m32-build3`, and today's file has `m32-fix3` with a start after the
harness's fixed clock ("m32-fix3 0:00"). Left as step 3 wrote it.

## Dev check

- `docker compose --profile dev up -d --build tool_service-dev
  orchestrator-dev`: `orchestrator-dev` recreated, `tool_service-dev` healthy
  and kept. `kronk-orchestrator-1`, `kronk-tool_service-1`, `kronk-nginx-1`
  (about an hour), `homeassistant` (9 days) and `music-assistant-server`
  (3 weeks) untouched.
- `/dev/work`: 200, stamped `cd6954945009`; its bytes equal the tree's
  `work.html` but for the stamp. `/dev/api/work`: fresh, `page_version`
  `cd6954945009`, `active` `["M32"]`.
- Method of `M32-step2-e2e.md`, "How it was tested": headless Firefox through
  snap geckodriver on port 4444, driven by a bare WebDriver client; the
  profile under `~/snap/firefox/common/m32-fix1-profiles` (removed after).
  Every page came through a scratch loopback proxy that passed only `/dev/`
  paths to port 80 and answered 403 otherwise, and added one script at the
  top of `<head>` of `/dev/work` recording errors, rejections and
  `console.error`/`warn`; Firefox's own traffic went to a second loopback
  port answering 403. Page lexicals reached by an injected script element.
  No screenshot was taken; nothing of Heard's was read.
- Made-up exports were shown in the page only: `liveAnswered()` called with
  a copy of the page's own `live.work`, edited, with `live.inflight` held so
  no poll replaced it while it was read; then the real data fetched back.
  Nothing was written to `data/dev`; `kronk-work-export.timer` was not
  touched.

### The first screen at 1920 by 1080 (today's data, export with `m32-fix3` working)

| Viewport | State line | M32 card | its track | "9 earlier steps" | Queue label | Queue rows | Queue box ends |
|---|---|---|---|---|---|---|---|
| 1920×1080 window (inner 1920×994) | 80.8 to 119.2 | 195 to 740.1 | 307 to 328 | 606.1 to 623.1 | 768.9 to 784.9 | M38 832.9 to 883.9, M37 883.9 to 934.9 | 951.9 |
| 1920×1080 viewport | same | same | same | same | same | same | 951.9 |

The state line, Under way and the whole Queue (box edge included) are on the
first screen of a 994 px page, 42 px to spare. Step 3's test measured M32's
card at 195 to 880 and the Queue box ending at 1,092. The card is now
545 px, 140 px less: five steps and the fold line in place of thirteen
steps. `scrollWidth` 1,908 at 1920: no horizontal overflow. Each fix round
adds two steps behind the fold and none to the first screen; a longer next
step or Why still adds lines.

With the fold opened by a click: the card ends at 920.1, sessionStorage holds
`{"uw:M32:steps":true}`; after a reload of the tab, still open (920.1, same
store); closed by a click: 740.1, store `{}`. The card stayed open
throughout.

### The five, in the browser

- **B1** (M32 waiting, no agent; M99 second, `m99-build` working):
  `getAnimations()`: `stn-stripe` and `stn-pulse` on `uw:M99`'s build
  station, nothing endless on M32; besides them `beat`, `cover-show` and the
  two cards' one-time `row-flash` (M99 new, M32 changed).
- **Note 2** (M32, `m32-fix3` blocked, stage blocked): build station `stn cur
  blocked`, colour and dot `rgb(239, 68, 68)`; no `stn-pulse`, no
  `stn-stripe`; only `beat`, `row-flash`, `cover-show`.
- **B2** (M32, test 5 started, agent idle, stage waiting): test station
  `stn started`, label "test 5", text `rgb(232, 234, 240)`, dot
  `linear-gradient(90deg, rgb(232, 234, 240) 50%, rgba(0, 0, 0, 0) 50%)` on a
  transparent background, border the text colour; no green, no red. Nothing
  endless.
- **S1** (`m32-fix9` working, no started step naming it): the clock reads
  "start not recorded" in a box 124.5 × 14 px, one line; the track row 307
  to 328 (21 px), as with a time. A time ("7:32") on the real data: 55.3 ×
  14.
- **Real data after**: `build 6` current, moving, working; `stn-stripe` and
  `stn-pulse` on `uw:M32`; Queue box ends 951.9. No script error, warning or
  rejection in any of it.

## Decided here

- **A blocked first card hands the motion on** to the next card with a
  current station (B1 and note 2 together).
- **"Latest round" is by start**, the list order on a tie or with no start;
  and the test's grade follows the same rule.
- **`started` is drawn half filled in the text colour**: reached, not done,
  no verdict.
- **The clock's box, not its text**, for S1: "start not recorded" names the
  cause.
- **The steps fold is on the item under way's card only**; Done rows are
  closed by default and keep every step when opened.

## The suite

On the code (commit `9f9a722`), before this report was written:

```
8582 passed, 3 skipped, 62 xfailed, 6 warnings in 217.84s (0:03:37)
```

With this report committed (`134902c`):

```
8582 passed, 3 skipped, 62 xfailed, 6 warnings in 217.96s (0:03:37)
```

## State at the end

No unit installed, enabled, started or stopped; `kronk-work-export.timer`
untouched; nothing written to `data/dev`. Production not rebuilt, restarted
or asked; no deploy, no tag. The dev pair rebuilt once. No sound, no
notification, no household speech read. No browser, geckodriver or proxy
running; the profile removed. `.gitignore` (changed) and `esphome/`
(untracked) are the operator's: not touched, not staged.
