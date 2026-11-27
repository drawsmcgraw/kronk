# M32 step 3 test: motion

Verdict: FAILS

Test agent m32-test4 (Opus 5.5), 2026-11-27, 13:41 to 14:05 local (EST), on
`3b493af` (code commit `cd0dcc1`). Contract: `docs/sprint/M32-criteria.md`,
"Step 3: motion (criteria, coordinator, 2026-11-27)", plumbing 1 to 6 and
criteria 3a to 3i. Dev serves the tree's page: `/dev/work` and
`/dev/api/work` stamped `8db32e7c49c1`. I changed no code; this report is
the only file committed.

Criteria 3a to 3i pass as written. Two blockers sit outside their letter,
in the handoff line ("shows by motion which item is being worked and at
what step"), and both showed in the live data. B1: with two items under
way only the lower id can move, so an item being worked behind one that
waits shows no motion at all. That happened for M36's whole life on
2026-11-26 and for M35's build and test. B2: when the item's agent is
between turns, the track draws the unfinished round green with the last
finished round's grade. At 13:57 today M32 showed a green "test 4" while
test 4 (this test) had no verdict. One should-fix (S1), five notes, and the
Queue measurement the brief asked for.

## Verdicts

| Criterion | Verdict |
|---|---|
| 3a, ordinary use at 1920×1080 and 1280×900 | **Passes**: M99 walked criteria → review → build → test FAILS → fix → test 2 PASSES → promoted; at each step the track, the motion and the flash were what plumbing 1 to 3 say (table below). M32's real row and the Done rows as specified. B1 and B2 are found here but are not 3a's letter |
| 3b, `getAnimations()` | **Passes**: every step-3 effect animates `opacity` or `transform` only; at most two endless; none under reduced motion (clocks count); none stale (at 175 s of no export) or after 35 s with no answer (still there at 25 s); none with nothing under way. `cover-show` and `beat` counted apart (below) |
| 3c, motion survives an update | **Passes**: same card, same moving station, pulse and stripe `currentTime` 16,883 → 22,900 ms (1920) and 16,799 → 22,816 ms (1280) across a next-step change; `scrollY` 0 → 0. At 300 px, `scrollY` 300 → 283 by Firefox's scroll anchoring, with the row in view at the same pixel (672.9 before and after) |
| 3d, the flash | **Passes**: one `row-flash` start, on `uw:M32` only, for a next-step change; none on load; none for a `ts`-only export; none for a Queue row's change; on promotion only the new Done row and the new card |
| 3e, real items' tracks | **Passes**: M30 `build 9 · test 9`, no extra stations; M35 all five filled; M31 `review skipped`, dashed ring; M36's test green by `graded` "pass" (its `verdict` is null in today's file, not FAILS: the graded-over-verdict order is in `trackOf` and the build's gjs, not in this file) |
| 3f, `--text3` and 960×540 | **Passes**: `--text3` on separators, section labels, fixed note lines (and the two `code` spans inside them), "… more"; nothing on a track; at 960×540 the state line (81 to 119), M32's head (212 to 232) and its track (307 to 328) are on the first screen |
| 3g, counts after 5 and 500 updates | **Passes**: 3,574 / 3,574 elements, 1 / 1 pending timeout, 0 / 0 intervals, same keys in the same order, two endless and `m32-test4` working throughout |
| 3h, no script error, no "undefined" | **Passes**: no error, warning, rejection, "undefined", "NaN" or "[object" in any session |
| 3i, the suite | **Passes**: `8578 passed, 3 skipped, 62 xfailed` |

## Blockers

### B1. Two items under way: the one being worked does not move

`renderMoving()` puts `moving` only on the first card in Under way, and
Under way is sorted by id. When the first item has no current station
(waiting for the coordinator) and the second is being worked, nothing on
the page moves. When both are worked, only the lower id moves.

Seen in the browser (fixture `tw`, 1920×1080): M32 "waiting for the
coordinator", M99 "implementing, agent m99-build working", M99's build
station current and blue, `m99-build 0:06` counting. `getAnimations()`:
`beat` and `cover-show` only. No pulse, no stripe. M99's card begins below
M32's 685 px card, off the first screen. Fixture `tw2` (both working):
pulse and stripe on M32's test station only; M99 shows `m99-build 0:12`
standing still.

How often, from the work-state database's own log:

- 2026-11-26 12:14 to 2026-11-27 01:09: M36 started and was promoted while
  M32 was active and waiting (its fix 1 in, not retested). Under this
  page, M36's review (12:14 to 12:26), build (12:27 to 12:55) and test
  (00:13 to 00:41) would show no motion anywhere.
- 2026-11-26 04:01 to 05:00: M35 under way beside M30. M35's build (04:44
  to 04:51) and test (04:52 to 04:58) ran while M30 was between test 3 and
  fix 2: no motion.

What a person has to do to reach it: nothing. The coordinator starts a
second item while the first waits, which it did twice in three days.

Why a blocker: the handoff line asks the wall to show by motion which item
is being worked, and in this case the wall shows none. A reading that
makes it a should-fix: plumbing 2 says "for the item under way only",
written for one item, and the build recorded "only the first moves" as its
decision to keep "at most two endless". Moving the first card whose current
station has an agent working would keep the limit. That is the
coordinator's call, not something I can test.

### B2. An unfinished round is drawn green with the last round's grade

`trackOf` counts a started step in the label ("round counts include a
started step", the build's decision) but colours the station from the
last *finished* test. When the item's agent is not `working` (herdr's
`done` or `idle` between turns), the stage comes from the files, no
station is current, and the station reads as done.

Seen in the live data: the export of 13:57:31 today caught m32-test4 (this
agent) between turns (`herdr`: `m32-test4 done`). M32's track:
`criteria 3 · review · build 5 · test 4 (green, pass) · promote`, stage
"waiting for the coordinator, build in, not tested". On the wall that
reads "test 4 passed". Test 4 had no verdict; the green is test 3's grade.
The same shows in fixtures `tw` and `nostart`.

How often: every time an item's agent ends a turn mid-step and an export
lands before it starts the next. A test agent that waits on a background
job does this for minutes at a time. What a person has to do: look at the
wall.

Why a blocker: it shows a verdict that does not exist, on the item under
way, in the live data. A reading that makes it a should-fix: the window is
short and the stage line beside it says "not tested". The fix is the
label's or the colour's (count only finished rounds when the station is
not current, or draw an unfinished round as not reached), not mine to
pick.

## Should fix

### S1. "start not recorded" breaks the track onto three lines

The clock box is `width: 8ch`, and "start not recorded" is 18 characters.
With an agent named for the item and working but no started step naming it
(fixture `nostart`: `m32-review9`), the box wraps to three lines and the
track row grows by two lines' height. Rare: `agent.py` records the start at
launch. A person reaches it when an agent is launched by hand, or renamed,
without `step start --agent`.

## Notes

1. **`cover-show` and `beat`.** `cover-show` (the cut cover's 90 s fade,
   `opacity`, one iteration) is in `getAnimations()` in every sample,
   reduced motion and still included. `beat` (the header dot, `opacity`,
   0.9 s, one iteration) runs after each answered poll. Both are step 1's
   liveness signs, as the build said. They are counted apart from step 3's
   motion and are not in any count above.
2. **The pulse on a blocked station.** With `m32-test4=blocked` the test
   station turns red, the stripe stops, and the pulse keeps running.
   Motion on a station whose agent waits for a person. Plumbing 2 does not
   say.
3. **`to_promote` pulses, `waiting` does not.** At M99's test 2 PASSES the
   promote station pulsed with no agent working. At test 1 FAILS nothing
   moved. Both are the coordinator's turn. The build's choice; recorded.
4. **"build 5" on M32** is three builds and two fixes; "build 9" on M30 is
   four and five. Per plumbing 1 ("a fix is a return to build"). A reader
   counting builds will read it otherwise.
5. **M36's Done row shows build unfilled** (its only build step is
   `stopped`). A shipped item with an empty build ring. Per the rule;
   recorded.

## The first screen at 1920×1080 (asked by the brief)

Real data, M32 open (its default), export of 13:58:31 (m32-test4 working):

| Viewport | State line | M32 card | Queue label | Queue rows | Queue box ends |
|---|---|---|---|---|---|
| 1920×1080 (inner) | 81 to 119 | 195 to 880 | 909 | M38 973 to 1,024, M37 1,024 to 1,075 | 1,092 |
| 1920×1080 window (inner 994) | same | same | same | M38 cut at 994, M37 off | 1,092 |

At a 1080 px viewport both Queue rows are on screen and the box's bottom
edge (17 px) is not. With a window 1080 px tall (994 px of page), M37 is
off the first screen and M38 cut through. 2f does not hold there.

M32's card is 685 px: head 20, stage part 59 (the stage and a two-line
next step), track 21 (plus 8 px margin), body 525. Body: Steps 276 (13
steps at about 20 px each), Why 109 (Drew's 685 characters, five lines),
Open leftovers 54, What 53. The Steps list is the height, and it grows by
about 20 px per step: each fix round adds two. At 13:57, with a three-line
next step, the card was 708 and the Queue ended at 1,115. The track's
share is 29 px. The build measured 1,072 against 994 at an earlier export.

## 3a: what a person in the room sees

Two windows open through the walk, 1920×1080 and 1280×900, defaults (M32,
then M99, open). Each step was applied by `work.py --db <scratch>` and
exported to `data/dev/work.json`. Shots were taken when the page had the
new `ts` ("a", 2.1 to 3.1 s after the export: the flash running) and 4 s
later ("b", 6.1 to 7.1 s). Both windows saw the same thing at every step.

| Step | Track (M99) | Moves | Flashed |
|---|---|---|---|
| s0, live copy | M32 `criteria 3 · review · build 5 · test 4 (current) · promote`, `m32-test4 5:14` | pulse + stripe on M32's test | nothing (first load) |
| s1, M32 promoted, M99 started | M32's card gone; M99 `criteria (current)`, rest rings | pulse on criteria | `uw:M99`, `done:41:M32` |
| s2, criteria committed | same (stage still planning) | pulse | `uw:M99` (steps changed) |
| s3, review, m99-review working | criteria green, review current, `m99-review 0:03` → `0:07` | pulse + stripe | `uw:M99` |
| s4, build | review green, build current, `m99-build` | pulse + stripe | `uw:M99` |
| s5, test | build green, test current, `m99-test` | pulse + stripe | `uw:M99` |
| s6, test FAILS, graded fail | test red, fades in; nothing current; "waiting for the coordinator" | nothing | `uw:M99`; `stn-fade` on test |
| s7, fix, m99-fix working | `build 2` current, test still red | pulse + stripe | `uw:M99` |
| s8, test 2 | `build 2` green, `test 2` current, `m99-test2` | pulse + stripe | `uw:M99` |
| s9, test 2 PASSES, graded pass | `test 2` green, fades in; promote current | pulse only | `uw:M99`; `stn-fade` on test |
| s10, promoted | card gone, "none" under Under way; Done row `done:42:M99` all five filled | nothing | `done:42:M99` only |

Endless effects per step: 2, 1, 1, 2, 2, 2, 0, 2, 2, 1, 0. Every effect
`opacity` or `transform`. `scrollY` 0 throughout. The flash is a pale blue
wash over the row's head that fades in 1.8 s. The stripe is a light band
crossing the blue current pill every 2 s. The pulse dims the dot to 35 %
and back every 2.4 s. The clock box held 55 px at every value seen.

Real data afterwards: M32 as in s0. 38 Done rows carry a static track, none
with `moving`. Queue rows (M38, M37) and Dropped (M29) carry none.

## How it was tested

**Browser.** Headless Firefox 159.0.1 through geckodriver 0.37.1 (snap),
driven by a bare WebDriver client; profiles under
`~/snap/firefox/common/m32-test4-profiles` (snap cannot write `/tmp`),
removed at the end. Every page came through a scratch loopback proxy that
passed only `/dev/` paths to port 80 and answered 403 otherwise; Firefox's
own traffic went to a second loopback port answering 403. The proxy added
one script at the top of `<head>` of `/dev/work` recording errors,
rejections, `console.error`/`warn`, `animationstart` events (name, row
key, pseudo-element) and pending timeouts and intervals. Page lexicals
(`live`, `loadAll`) were reached by an injected script element. Reduced
motion: `ui.prefersReducedMotion` 1 (`matchMedia` true). Viewports set to
exact inner sizes, plus one 1920×1080 outer window (994 inner).

**Fixtures.** The work-state database was copied by the backup API from a
`mode=ro` connection. `work.py --db <scratch> --repo <scratch clone>` made
the changes. The clone (of this repo at `3b493af`, user set per repo) held
M99's committed report files, so stages and verdicts came from the
exporter's own reading. The scratch database's `meta.repo` was pointed at
the clone. Exports used `work_export.main()` unchanged, with one
substitution: `herdr_agents` answered a fixed list (`m99-build=working`
and so on), since herdr cannot be given a made-up agent. Every export went
to `data/dev/work.json` only. Production's `data/work.json` was never
written by me.

**Timer.** `kronk-work-export.timer` was stopped and started four times
while fixtures were in `data/dev`: 13:45:44 to 13:48:23 (the walk),
13:51:34 to 13:52:07 (a first try of session 2 that stopped on a refused
`work.py move`), 13:52:59 to 13:57:31 (session 2, with the stale hold), and
13:59:54 to 14:00:21 (B1, B2, S1 fixtures). Each time dev's file was
re-exported from the live database (`--out data/dev/work.json` only) before
the start. It is active at the end.

**3b to 3d** (session 2): three windows, 1920×1080, 1280×900 scrolled to
300 px, and 1920×1080 with reduced motion, on the live copy with
`m32-test4` working. Then a `ts`-only export, a next-step change on M32,
and a next-step change on queued M38. Then no export until the route said
not fresh (175 s after the last), a fresh export, and the proxy answering
502 to `api/work` for 35 s. The clocks counted under reduced motion (11:07
→ 11:10), stale (16:00 → 16:03) and cut (16:43 → 16:46).

**3g.** In one 1920×1080 window the page's `getJSON` was replaced for
`api/work` only, answering a real envelope around six exports (M32 testing
with `m32-test4`, its next step changed twice, M38's changed, M32 fixing
with `m32-fix3`), each with a new `ts`. `loadAll()` awaited 5 times, then
494 more, each run ending on the same export and held 4 s before counting
(6 and 501 answers in all). The clock read "56720:52:48" there: the fake
`ts` base was 2e9. A harness artifact, not the page's.

**Real data** (session 3): 1920×1080, the 994 window, 1280×900 and 960×540
on the 13:58:31 export, with Heard's words made transparent before each
screenshot.

**Guard rails.** No production request. Only `/dev/` routes. No rebuild or
restart. One timer stopped and started as above; nothing else touched. No
sound, no notification. `work.py` ran only with `--db` on scratch copies.
The walk's screenshots were taken with Heard's folded line unmasked. I
looked at one (s1, 1280) and saw it. All the walk shots were deleted after,
and its words are not in this report. Later shots were masked.

## State at the end

No browser, geckodriver or proxy running. Profiles removed.
`kronk-work-export.timer` active. Dev's `work.json` holds the live data.
Scratch holds the copied database, the clone, the fixtures, the harness
and the screenshots. `.gitignore` (changed) and `esphome/` (untracked) are
the operator's: not touched, not staged.
