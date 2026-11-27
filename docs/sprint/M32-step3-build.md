# M32 step 3 build: motion

Build agent m32-build3 (Opus 5.5), 2026-11-27, from `fb6fef5` (main). Built
to `M32-criteria.md`, "Step 3: motion (criteria, coordinator, 2026-11-27)",
plumbing 1 to 6, with `M32-review.md` points 3.6, 4 and 10.5 as the design
and step 2's note 2 (`M32-step2-e2e.md`). Code commit `cd0dcc1`. Only `orchestrator/static/work.html`
changed in the product; no route, exporter or schema change (schema stays 4).

## What was built, by plumbing point

1. **The track** (`TRACK`, `STAGE_STATION`, `trackOf`, `trackPart`). Five
   stations, criteria, review, build, test, promote, in step 2's
   `row-track` slot: on the item under way's card and on Done rows; queued,
   held and dropped rows leave the slot empty (hidden by step 2's
   `.row-track:empty`). `trackOf(it, current, promoted)` is a pure function
   of the data:
   - a station is filled when a finished step of one of its kinds exists
     (`steps[].kind`, `state === 'finished'`), never by place in the list;
   - "build" counts `build` and `fix` steps (a fix is a return to build),
     "test" counts `test` steps; a count over one is the label's round
     ("build 9", "test 9"); a `stopped` step is not counted;
   - "promote" is filled on a Done row (`promoted`);
   - the test station's colour is the last finished test's `graded`
     (pass, fail, any other word amber), else its `verdict` (PASSES,
     FAILS, NOT DETERMINED), else none;
   - a review with none recorded is "skipped" (a dashed hollow ring,
     labelled "review skipped") once a build or a test is filled or the
     current station is past review;
   - the current station is `STAGE_STATION[items[].stage.stage]`, a lookup
     of the exporter's own key; the page computes no stage. `waiting` and
     `promoted` have none. A Done row has no current station (static).

   `trackPart` returns `[signature, draw, inPlace]`: the frame (five
   station nodes, four lines, and on the card an agent and a clock) is
   built once; later draws change a station's classes one by one where they
   differ, and its label. `patchParts` gained the third element (a part
   drawn in place is not cleared first) and now returns whether a part that
   had been drawn changed.
2. **What moves** (CSS block "The track and its motion"; `renderMoving`,
   `agentClock`, `clockText`, `renderClocks`, `once`):
   - pulse: `.stn.moving .stn-dot`, opacity 1 → 0.35 → 1, 2.4 s, infinite;
   - stripe: `.stn.moving.working::after`, a gradient band moved by
     `transform: translateX(-100%) → translateX(250%)`, 2 s, infinite,
     inside `.stn { overflow: hidden }`; `working` is set when the item has
     an agent with status `working` and the stage is not blocked;
   - elapsed time: beside the track, the first working agent's name and
     `clockText` = `live.ts` + the data's age by `performance.now()` −
     the start of the `started` step that names the agent ("12:41",
     "1:02:09"); updated each second by the scheduler's tick on the node
     that stays; `.stn-clock` is `inline-block`, `width: 8ch`,
     `contain: content`, `font-variant-numeric: tabular-nums`. No started
     step naming the agent: "start not recorded".
   - flash: `patchList` takes `flash` per row: "change" (new, or a part's
     signature changed) for Under way, "new" for Done; none for the Queue,
     Dropped and the Backlog. Never on a list's first draw (`box._patched`;
     `renderEmpty` clears it), so nothing flashes on load. The flash is
     `details.row.flash > summary::after`, an overlay faded 1 → 0 once
     (1.8 s), restarted by `once()` (class off, style flushed, class on).
     The four lines step 2 sets to the same text on every export are
     outside every patched list, so they cannot flash anything.
   - verdict fade: the test station fades in once (`stn-fade`, opacity
     0 → 1, 1.2 s) when another finished test or a new grading arrives;
     never on the first draw.
   - which card moves: `renderMoving()` after each patch of Under way puts
     `moving` on the current station of the first card only. It is not in
     the track's signature: the second item under way becoming the first
     does not flash (found by the gjs walk, below).
3. **What does not move**: no slide to Done (the card leaves, the new Done
   row flashes); no track on queued, held or dropped rows; Done tracks have
   no current station and no `moving`.
4. **Rules**: every `@keyframes` on the page holds only `opacity` or
   `transform` (the cover's `visibility` dropped: at opacity 0 with
   `pointer-events: none` it is not seen and takes no click); no
   `transition` anywhere (the refresh button's `transition: all 0.15s`
   went); two endless rules, both on `.stn.moving`, one card only.
   `renderMotion` puts `.motion-still` on `<main>` each second when no poll
   has been answered for 30 s or the route's `source.state` is not
   `fresh` (`MOTION`); under it the pulse and the stripe are `animation:
   none` (the stripe also `display: none`). `prefers-reduced-motion:
   reduce` turns off the pulse, the stripe, the flash and the fade; the
   clocks and ages still count. No library.
5. **Contrast**: `--text3` is left on `.state-line .sep`, `.note-line`
   (fixed explanatory notes), `.entry .more` ("… more") and Heard's ▸.
   Moved to `--text2`: `.last-updated` (the data's age), `.src`, `.reason`,
   `.notes`, `.step .t`, `.v-none`, `.entry em`, `.done-meta`, `details
   summary` (Backlog heads), `.queue-pos`, `.not-read li`, Heard's
   `.hr-time`, `.hr-sat`, `.hr-heard.none`, `.hr-route`, `.hr-more`; the
   dropped count (a `note-line` that is state) gets class `count`, drawn in
   `--text2`. Track: no `--text3` (labels `--text2`/`--text`, rings and
   lines `--text2`).
6. **Not built** (Drew #90): no display mode, no larger type, no pixel
   shift.

## Tests

New, `tests/test_m32_step3.py` (16): the `TRACK` literal; `STAGE_STATION`
covers exactly `work_export.STAGE_WORDS`' keys and maps to stations or
null; `trackOf` looks the stage up and computes none, fills by kind and
state, never by position, skips stopped steps, counts rounds, takes
`graded` before the verdict, draws a skipped review hollow; the track only
on Under way and Done; drawn in place; every `@keyframes` only `opacity`
or `transform`, no `transition`, no `background-position`; exactly two
endless rules (`.stn.moving .stn-dot`, `.stn.moving.working::after`), the
pulse never above 1; both off under `.motion-still` and reduced motion
(with the flash and the fade), `MOTION` 30 s, the fresh state read; only
the first card moves and moving is out of the signature; the flash hung on
`patchParts`' change, "change"/"new"/none per list, never on a first draw,
the note-2 lines outside the lists; the verdict fade; the clock on the
route's time (no `Date.now`, no `new Date()`), its box; `--text3` used by
exactly the four selectors above.

Changed, each because the plumbing changed what it pinned:

- `test_m32_step2.py`: `test_a_row_is_a_fixed_frame_and_the_track_is_left_empty`
  → `..._with_a_slot_for_the_track` (the slot is filled now);
  `test_no_endless_motion_yet` → `test_the_rows_add_no_motion` (the rows'
  block still has none; step 3's block is tested on its own).
- `test_m32_live.py::test_the_cover_is_the_stylesheets_and_nothing_moves_by_itself`:
  "no `infinite` in the style" is now "none before step 3's block".
- `test_m34_work_db.py::test_heard_is_untouched_since_before_the_build`:
  Heard's style is compared with `--text3` read as `--text2` (plumbing 5
  moved its rows' colours; nothing else in it changed).

What the suite cannot read, and only a browser shows: that the pulse and
stripe run and stop, `getAnimations()`, the flash seen, a node's animation
`currentTime` across an update, layout and the first screen, contrast as
drawn, reduced motion. Checked by me on dev (below); the criteria's
browser runs are the test agent's.

## gjs

`docs/sprint/M32-step3-gjs/` (`run.js`, a README; step 2's stub DOM
unchanged). Fixture: dev's schema-4 export of 2026-11-27. 65 PASS, 0 FAIL
under `TZ=UTC`, `America/New_York` and `Asia/Tokyo`:

- 3e: M30 `criteria review build 9 test 9 promote`, all filled, test
  green; M35 (review before criteria in the list) all filled; M31 (no
  review) `review skipped` hollow; M36 test green by `graded` "pass" (its
  verdict is null in the file) and its stopped build unfilled; a made-up
  step graded "pass" with verdict FAILS is green; no grading, the verdict.
- Real data: M32 `criteria 3, review, build 5 (current, moving, working),
  test 3 (pass), promote`, `m32-build3` and its clock.
- 3a walk, M99 in front of M32: criteria (current, pulse, no agent) →
  review (stripe, `m99-review 10:03`) → build → test → test FAILS graded
  fail (nothing current, nothing moves, test red, faded in) → fix
  (`build 2` current) → test 2 → test 2 PASSES graded pass (green, faded
  in; promote current, pulse) → promoted (card gone, `done:999:M99`
  flashed and nothing else, its Done track all filled, no motion; M32 now
  first moves and did not flash). At each step the card the same node,
  every other row the same node, it alone flashed, one station moving.
- 3c: `next_step` changed, and a step added to the list: the moving
  station the same node, still `moving working`; the clock 10:27 → 10:31.
- 3d: no flash on first load; none on two exports that change only `ts`.
- 3b: no answer: `motion-still` at 35 s, not at 25 s; answered again:
  off; data stale: on.
- 3g: elements 3,623 / 3,623 and page timers 1 / 1 after 6 and after 500
  updates, an agent working throughout (build and test alternating, M32's
  next step changing).

Step 2's harness on the new page: every line passes but the one that pins
an empty track slot (changed on purpose) and three that fail on `HEAD`'s
page too with today's fixture (`2b promoted`, two `2c` lines: the newest
Done rows are now deploys with no item, so "the first Done row" and "the
third Done row" are not item rows). Left as step 2 wrote it.

## Dev check

- `docker compose --profile dev up -d --build tool_service-dev
  orchestrator-dev`: orchestrator-dev recreated, tool_service-dev healthy
  and kept. `kronk-orchestrator-1`, `kronk-tool_service-1`,
  `kronk-nginx-1`, `homeassistant` (9 days) and `music-assistant-server`
  (3 weeks) untouched.
- `/dev/work`: 200, `cache-control: no-store`, 101,092 B, stamped
  `8db32e7c49c1`, equal to the hash of the tree's `work.html` and
  `kronk.css`; it was `b612a3370183` (step 2). `/dev/api/work`: 200,
  fresh, schema 4, `page_version` `8db32e7c49c1`.
- Headless Firefox 159.0.1 through geckodriver on loopback, Firefox's own
  traffic sent to a dead loopback proxy, localhost direct; only `/dev/`
  URLs; the page's scope reached by an injected script element. The
  real data (M32 under way, `m32-build3` working):
  - `getAnimations()` at 1920×1080: `stn-stripe` (`::after`, `transform`,
    infinite), `stn-pulse` (`.stn-dot`, `opacity`, infinite), `cover-show`
    (`opacity`, 1). After an update: also `beat` and `row-flash` (both
    `opacity`, 1).
  - Track: `criteria 3 · review · build 5 (current) · test 3 (pass) ·
    promote`, `m32-build3 12:41`, then `12:44` three seconds later; the
    clock's box 55.3 px both times.
  - An update in the page changing M32's next step only (the file's `ts`
    kept): the same card, the same moving station, `scrollY` 120 before and
    after, flashed `uw:M32` only. The pulse's `currentTime` read 8,116 ms
    before and after: the two reads were in one frame, so this shows the
    animation was not restarted, not that it moved on; 3c's "larger" is the
    test agent's.
  - `answeredAt` moved back 31 s: `<main class="motion-still">`, only
    `cover-show` left; the clock still counted. A poll: pulse and stripe
    back.
  - `ui.prefersReducedMotion` 1 (`matchMedia` true): only `cover-show`;
    the clock counted 13:08 → 13:10.
  - First screen: state line ends at 119 px, M32's head 232, its track 328;
    at 960×540 (viewport 466) all three are on it (3f). No horizontal
    overflow at 1920, 1280 or 960 (`scrollWidth` 12 px under the window).
  - Elements drawn in `rgb(92, 100, 128)` (`--text3`) with text of their
    own: the separators, section labels, the fixed note lines (and the
    `code` inside two of them), Heard's ▸, "… more". No state.
  - No "undefined", "NaN" or "[object" in the page's text.
  - Screenshots in scratch, not committed. geckodriver and Firefox stopped
    after.

## Decided here

- **A fix counts on "build"**, not on a station of its own and not on
  "test": "a fix is a return to build and test". So M30 reads "build 9 ·
  test 9", M32 "build 5" (three builds and two fixes; its third build is
  under way).
- **Round counts include a started step**, so the current station reads
  the round being worked; "filled" needs a finished one.
- **`waiting` has no current station**: nothing pulses while the item
  waits for the coordinator, by review 3.6's "motion means being worked".
- **A review is "skipped" only once a build or a test is filled or the
  current station is past it**: an item still at criteria shows review as
  not reached, and promoted alone does not make it skipped.
- **A Done row's build with only a stopped step is unfilled** (M36): the
  rule says filled needs a finished step.
- **Two items under way: only the first moves**, to keep "at most two
  endless". The second shows its current station still.
- **The track sits after the stage line** (step 2's frame order kept).
  On today's data that is enough for 3f at 960×540.
- **The cover's `visibility` and the button's `transition` were removed**
  so that every animation on the page is opacity or transform; the step-1
  signs (the cover's 90 s fade, the beat) stay under reduced motion and
  when still: they are the page's liveness, not step 3's motion.
- **Flashes on Under way and on a new Done row only**: plumbing 3 says
  nothing moves for a queued, held, promoted or dropped item, and its own
  exception is the Done row of the item just promoted.

## For the coordinator

- **2f no longer holds on today's data, with or without the track.** At
  1920×1080 (viewport 994) the Queue ends at 1,072 px; with the track
  hidden, 1,043. M32's open body is 505 px (twelve steps, Drew's 685-character
  Why, one open leftover); step 2 measured the Queue ending at 863 when M32
  had eight steps. The track adds 29 px. Not a step 3 criterion; recorded.
- **The test agent will always see `cover-show` in `getAnimations()`**
  (and `beat` for 0.9 s after each poll), under reduced motion and when
  still too. Both are step 1's, opacity, one iteration.
- **"Waiting for the coordinator" shows no current station** (above). If
  the coordinator wants the coordinator's turn drawn, it is one entry in
  `STAGE_STATION`.

## The suite

On the code (commit `cd0dcc1`), with this report moved out of the tree:

```
8578 passed, 3 skipped, 62 xfailed, 6 warnings in 217.74s (0:03:37)
```

With this report in the tree and not committed, one failure, the one step
2's fix report met: `test_m34_work_db.py::test_criterion_2_every_difference_from_the_old_reader_has_a_class`,
`AssertionError: {'GONE': ['items[M32-step3]']}` (the old reader reads the
working tree; `import-legacy --at HEAD` only committed files). With the
report committed:

```
SUITE_COMMITTED
```

## State at the end

No unit installed, enabled, started or stopped; `kronk-work-export.timer`
untouched; no fixture written to `data/dev`. Production not rebuilt,
restarted or asked; no deploy, no tag. The dev pair rebuilt once. No
sound, no notification, no household speech read. `.gitignore` (changed)
and `esphome/` (untracked) are the operator's: not touched, not staged.
