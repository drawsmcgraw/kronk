# M32 step 3 narrow retest: motion, after fix 1

Verdict: PASSES

Test agent m32-test5 (Opus 5.5), 2026-11-27, 14:27 to 14:45 local (EST), on
`39f0f0a` (code commit `9f9a722`). Contract: `M32-criteria.md`, "Rulings on
step 3's test"; the brief's checks 1 to 7. Narrow: the fix's diff, the
criteria it touches, the suite. Dev serves the tree's page: `/dev/work`
stamped `cd6954945009`, its bytes equal to `orchestrator/static/work.html`
but for the stamp. I changed no code; this report is the only file
committed.

## Verdicts

| Check | Verdict |
|---|---|
| 1. B1, two items under way | **Passes**: M32 waiting, M99 worked: M99's build station moves (pulse and stripe), nothing endless on M32. Both worked: M32's test station only, two endless |
| 2. B2, an unfinished round | **Passes**: M32 with test 5 started and its agent idle: "test 5" drawn `started`, no grade, nothing endless. The real data's M32 row the same. No Done row of 38 drawn `started`, `cur` or `moving` |
| 3. Note 2, a blocked agent | **Passes**: `stn cur blocked`, red kept, no pulse, no stripe; with M99 worked beside it, M99 moves |
| 4. S1, "start not recorded" | **Passes**: one line, 124.5 × 14 px, track row 21 px, as with a time |
| 5. The first screen and the fold | **Passes**: at a 1920×1080 window (994 px of page) the Queue box ends at 951.9, 42 px spare. The fold opens, closes, keeps its state across a new step and across reloads, and is dropped from the store when closed |
| 6. 3b, 3c, 3d on the touched parts | **Passes**: on the moved second card, `opacity`/`transform` only, at most two endless, none under reduced motion (clock counts), none stale; same node and animations across an update, `currentTime` larger, `scrollY` 0; one flash on the changed row only, none on a `ts`-only export |
| 7. The suite | **Passes**: `8582 passed, 3 skipped, 62 xfailed`, exit 0 (second run; the first tripped the live-database guard on my own reads, note 1) |

No blocker. No should-fix. Three notes.

## 1. B1

Fixture: the live database copied, M99 added and started, criteria
finished, build started with agent `m99-build`. herdr answered a fixed
list.

- **M32 waiting, M99 worked** (`m32-test5=idle`, `m99-build=working`):
  M32 `criteria 3 · review · build 6 · test 5 [started] · promote`, no
  current station. M99 `criteria · review skipped · build [cur working
  moving] · test · promote`, `m99-build 1:58` counting.
  `getAnimations()`: `stn-stripe` (`::after`, `transform`, Infinity) and
  `stn-pulse` (`opacity`, Infinity) on `uw:M99`; besides them `beat` and
  `cover-show` (one iteration each). Two endless, both on M99.
- **Both worked** (`m32-test5=working`, `m99-build=working`): M32's test 5
  `cur working moving`; M99's build `cur working`, not moving, its clock
  still counting (2:15). Two endless, both on `uw:M32`. Across the change
  from the case above: the same M32 and M99 cards and the same M99 build
  node; animation starts were one `row-flash` on `uw:M32` (its data
  changed), M32's pulse and stripe, and `beat`. No flash on M99.

## 2. B2

- **Fixture** (as in 1, `m32-test5=idle`): M32's stage "waiting for the
  coordinator, fix 3 in, not retested", from the files. Test station `stn
  started`, label "test 5", colour `rgb(232, 234, 240)` (the text colour),
  dot `linear-gradient(90deg, rgb(232, 234, 240) 50%, rgba(0, 0, 0, 0)
  50%)`, border the text colour. No `pass`/`fail` class, no green, no red.
  Before the fix this read as test 4's grade.
- **Real data**: the live database's dump as it stood at 14:28, exported
  with herdr answering `m32-test5=idle` and nothing else: the same, `test 5
  [started]`, no endless effect. With the live export (`m32-test5`
  working, the timer running) at the end: `test 5 [cur working moving]`,
  `m32-test5 10:41`, two endless on `uw:M32`.
- Done rows (real data): 38, none with a `started`, `cur` or `moving`
  station. M30 `criteria · review · build 9 · test 9 [done pass] ·
  promote` as before.

## 3. Note 2

- `m32-test5=blocked`, `m99-build=idle`: M32's stage "testing, agent
  m32-test5 BLOCKED". Test station `stn cur blocked`, colour and dot
  `rgb(239, 68, 68)`, dot `animation-name: none`, `::after`
  `animation-name: none`. The one endless effect on the page is M99's
  pulse (note 2 below on why M99's criteria is current).
- `m32-test5=blocked`, `m99-build=working`: M32's station still `stn cur
  blocked`, red; M99's build moves, two endless, both on `uw:M99`.

## 4. S1

Real data with herdr answering `m32-fix9=working`, a name no started step
carries: `m32-fix9 start not recorded`. The clock box: 124.5 × 14 px, one
client rect, `white-space: nowrap`; the track row 307 to 328 (21 px). With
`m32-test5` working: "5:08", 55.3 × 14, the track row 307 to 328.

## 5. The first screen and the fold

Live export (`m32-test5` working), the timer running, a 1920×1080 window
(inner 1920×994), M32 open by default, fold closed by default:

| Fold | State line | M32 card | its track | "10 earlier steps" | Queue label | Queue rows | Queue box ends |
|---|---|---|---|---|---|---|---|
| closed | 80.8 to 119.2 | 195 to 740.1 | 307 to 328 | 606.1 to 623.1 | 768.9 to 784.9 | M38 832.9 to 883.9, M37 883.9 to 934.9 | 951.9 |
| open | same | 195 to 940.1 | same | same | 968.9 to 984.9 | M38 1,032.9 to 1,083.9, M37 1,083.9 to 1,134.9 | 1,151.9 |

The state line, Under way and the whole Queue, box edge included, are on
the first screen with the fold closed. `scrollWidth` 1,908: no horizontal
overflow. Opened, the Queue goes below the fold of the page: that is a
person's choice on a desk browser, not the wall's default.

The fold, by clicks:

- Live data: closed, store `{}`, 10 inside, 5 shown. Opened: store
  `{"uw:M32:steps":true}`. Reloaded: open, same store, the card open.
  Closed: card back to 740.1, store `{}`.
- Fixture, a new step added to M32 (`work.py step add M32 other` on the
  scratch database) while the fold was open: still open, "11 earlier
  steps", 11 inside, 5 shown, the card open. Reloaded: open. Closed:
  store `{}`; reloaded: closed.

## 6. 3b, 3c and 3d on what the fix touched

The fix moved the motion off "the first card". These ran on M99, the
second card, moving.

- **3b.** Every effect seen animates `opacity` or `transform`. At most two
  endless in every sample above. Reduced motion (a second window, 1280×900,
  `ui.prefersReducedMotion` 1, `matchMedia` true): M99's build `cur working
  moving`, no endless effect, only `cover-show`; the clock 4:13 → 4:17.
  Stale: the fixture held with no export, at 188 s (`/dev/api/work`
  `stale`, `age_s` 188.2): `.motion-still` on, M99's station still `cur
  working moving`, no endless effect, the clock counting (8:29). Not
  re-run: the 30 s no-answer path (same `.motion-still` class as stale,
  untouched by the fix).
- **3c.** `work.py note M99 --next ...` and an export: the same M99 card,
  the same build node, the same two `Animation` objects; `currentTime`
  35,600 → 42,800 ms for both; `scrollY` 0 → 0.
- **3d.** That change: one `row-flash`, on `uw:M99`; nothing else
  started. A `ts`-only export after it: no animation start at all.

No script error, warning or rejection in either window; no "undefined",
"NaN" or "[object" in the page text.

## 7. The suite

First run, started 14:27 while I copied the database:

```
===== 8582 passed, 3 skipped, 62 xfailed, 6 warnings in 217.16s (0:03:37) ======
FAILED: the test run changed the live work database: /home/drew/.local/state/kronk-work/work.db-wal, /home/drew/.local/state/kronk-work/work.db-shm
```

exit 1. `work.db-wal` was created at 14:28:38 and `work.db-shm` changed at
14:30:58: the two times I copied the live database by the backup API from
a `mode=ro` connection. `work.db` itself was last written at 14:27:59 (this
step's start, before the copies). Second run, nothing reading the live
database:

```
===== 8582 passed, 3 skipped, 62 xfailed, 6 warnings in 215.70s (0:03:35) ======
```

exit 0.

## Notes

1. **A read-only copy of the live database trips the suite's guard.** A
   `mode=ro` connection to a WAL database creates `-wal` and touches
   `-shm`; the suite compares those files before and after. A test agent
   that copies the database (the method of step 3's test and this one)
   while the suite runs gets a false FAILED. Copy first, then run the
   suite.
2. **A build agent between turns early on: criteria pulses, build half
   filled.** M99 with `m99-build=idle`: the exporter's stage from the
   files is "planning, criteria written", so criteria is current and
   pulses (no stripe, no agent working) while the build station to its
   right is drawn `started`. By the ruling (no card worked: the first
   with a current station moves) and plumbing 1 (the page computes no
   stage). The stage is the exporter's, outside this diff. Like step 3's
   note 3: motion on a station nobody is working.
3. **"review skipped" once a build is started.** M99 had no review step;
   with its build only started, review is drawn hollow. The fix's `past`
   now reads started builds and tests. Per the rule; recorded.

## How it was tested

**Browser.** Headless Firefox 159.0.1 through snap geckodriver (two, on
loopback ports 4444 and 4445, one session each), driven by a bare
WebDriver client; profiles under `~/snap/firefox/common/m32-test5-profiles`,
removed at the end. Every page came through a scratch loopback proxy that
passed only `/dev/` paths to port 80 and answered 403 otherwise, and added
one script at the top of `<head>` of `/dev/work` recording errors,
rejections, `console.error`/`warn` and `animationstart` events (name, row
key). Firefox's own traffic went to a second loopback port answering 403.
No screenshot was taken; Heard's text was not read.

**Fixtures.** The live work-state database copied by the backup API from a
`mode=ro` connection, twice (14:28, 14:31; note 1). `work.py --db
<scratch> --repo <scratch clone>` made every change (M99 added, started,
criteria, build; M99's next step; a step on M32); the clone (of this repo
at `39f0f0a`, user set per repo) held M99's two committed report files and
the scratch database's `meta.repo` pointed at it. A second copy of the
live dump served the real-data fixtures with this repo as `--repo`.
Exports used `work_export.main()` unchanged except `herdr_agents`, which
answered a fixed list. Every fixture export went to `data/dev/work.json`
only.

**Timer.** `kronk-work-export.timer` stopped at 14:31:09 and started at
14:38:00, after dev's file was re-exported from the live database
(`work_export.py --out data/dev/work.json`). Active at the end.

**Guard rails.** No production request; only `/dev/` routes and the
exporter's own reads. No rebuild, no restart. One timer stopped and
started as above; nothing else touched. No sound, no notification.
`work.py` ran only with `--db` on scratch copies.

## State at the end

No browser, geckodriver or proxy running. Profiles removed.
`kronk-work-export.timer` active; dev's `work.json` holds the live data.
Scratch holds the copied databases, the clone, the fixtures and the
harness. `.gitignore` (changed) and `esphome/` (untracked) are the
operator's: not touched, not staged.
