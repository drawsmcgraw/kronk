# M32 step 2 test: rows that unfold

Verdict: FAILS

Test agent m32-test2 (Opus 5.5, effort medium), 2026-11-27, 11:56 to 12:25
local (EST), on `ba32485` (code commit `c5aaf0d`). Contract:
`docs/sprint/M32-criteria.md`, "Steps 2 and 3", step 2 (plumbing 1 to 9,
criteria 2a to 2k). The dev orchestrator serves the tree's page: `/dev/work`
is stamped `b612a3370183`, equal to the hash of the tree's `work.html` and
`kronk.css`; `/dev/api/work` answers `schema_known` 4. I changed no code and
added no test file; this report is the only file committed.

One blocker, against plumbing 2 and Drew's #91 as 2a carries them: 15 Done
rows of old items show no Shipped line, though ROADMAP's Shipped section
names each of them (B1). Everything else in 2a to 2k passes. Twelve minutes
of real exports at a desk and on the wall replaced no row, redrew no part,
moved nothing on screen and closed no fold; 500 updates left the element and
timer counts where 5 left them; markup in titles, leftovers, decisions, next
steps and Heard's words stayed text. One should-fix (S1, Drew's words
clipped at 600 characters) and five notes.

## Verdicts

| Criterion | Verdict |
|---|---|
| 2a, ordinary use at 1280 px | **Fails on B1**: M32 open, 41 other rows unfold and fold by a real click; what and why equal the file on all 40 items with a criteria file; 15 old rows lack their Shipped line |
| 2b, keyed patching | **Passes**: four fixtures, every other row the same node (98 of 98, 99 of 99); open rows open; only the touched parts drawn. `scrollY` moved by the browser's scroll anchoring on three changes, with nothing moving on screen (note 1) |
| 2c, folds across a reload | **Passes** |
| 2d, markup is text; text in the real export | **Passes**: no `img`, `a` or `b` made, nothing ran; 71 of 71 open entries and 3 of 3 owed decisions carry text in the real file |
| 2e, the dropped list | **Passes**: "1 item"; M29, its title, "dropped", 2026-11-24 |
| 2f, first screen at 1920 by 1080 | **Passes**: state line 81, Under way 195 to 671, Queue to 883 px; fits a 994 px viewport too |
| 2g, Heard's folded line | **Passes**: live line carries the newest household run's words; fixture runs shown in 27.1, 30.2, 30.2 s, open in 30.4 s |
| 2h, step 1's page on the schema-4 file | **Passes**: no script error, Under way drawn, no banner, for 40 s |
| 2i, counts after 5 and 500 updates | **Passes**: 2,707 / 2,707 elements, 1 / 1 pending timer, 0 / 0 intervals, 98 keys in the same order |
| 2j, no script error, no "undefined" | **Passes**: none in any of 24 browser sessions |
| 2k, the suite | **Passes**: `8551 passed, 3 skipped, 62 xfailed` |
| Plumbing 4, page order | **Passes**: state line, Under way, Queue, (Other agents), Heard, Done, Dropped, Backlog |
| Plumbing 9, carried notes | **Passes**: the banner moves nothing (0 px in 80 samples); "not read" clears with the banner |

## Blocker

### B1. Fifteen old rows have no Shipped line

Drew's #91 (plumbing 2): the 27 old items with no handoff line show "title
and Shipped line only". The page shows a Shipped line only for items the
exporter has linked to a ROADMAP Shipped entry, and it links an entry only
by the ids in the entry's title and lead. The "Refactor sprint" entry
(`ROADMAP.md`, line 261) names its items in its body: "(M17)", "(M1, M1b)",
"(M3, M4, M4b, M5; …)", "(M2)", "(M7, …)", "(M19, …)", "(M14, …)", "(M11)",
"(M8)", "(M9, …)", "(M12, …)". It lands in `shipped_unlinked` and none of
those rows gets it.

Of the 27 old Done rows, 8 show a Shipped line and 19 show none. 15 of the
19 are named in that entry: `done:16:M12`, `done:15:M9`, `done:14:M8`,
`done:13:M11`, `done:12:M14`, `done:11:M19`, `done:10:M7`, `done:7:M2`,
`done:6:M5`, `done:5:M1b`, `done:5:M3`, `done:5:M4`, `done:5:M4b`,
`done:4:M1b`, `done:1:M17`. The other four (M6, M10, M13, M20) are named in
no Shipped entry; title only is right for them.

How often: every time, in the live data. How to reach it: unfold any of
those rows. What the reader sees: the title, the open leftovers and the
steps, and nothing that says what shipped. Nothing else is wrong on those
rows.

Why a blocker: it is in the live data on every load, and it is the one
thing Drew asked those rows to carry. A reading that would make it a note:
criterion 2a's "an item with none" read as "an item with no criteria file"
(only M29 has none, and M29 has no Shipped entry). Plumbing 2 says "with
none" of the handoff line, so I read it that way. The fix is the exporter's
link rule (an entry also links the ids its body names), and a fixer should
check that no other Shipped entry then links an item it only mentions in
passing. The coordinator may instead rule the shared entry is not an item's
Shipped line; that is a ruling, not something I can test.

## Should fix

### S1. Drew's words are cut at 600 characters

The why of M32 (685 characters in the file) and M33 (722) ends mid-sentence
with "…": M32's ends "…said display won't have the ability to refresh. add
some in…". `scripts/work.py` `SOURCE_MAX = 600` clips the Source paragraph
before the exporter sees it. This predates step 2, but until now Why was not
the body of a row. Every load, two rows, visible on the wall (M32 is the
open card). The other 38 items' Source paragraphs fit. Raising the limit or
using `ENTRY_MAX` (4,000) for the source would show Drew's words whole.

## Notes

1. **`scrollY` and scroll anchoring.** 2b's `scrollY` was equal for the
   `next_step` change. For the others the browser (Firefox's scroll
   anchoring, not the page) changed `scrollY` to keep what is on screen
   still: dropped at 400 px, 400 → 367; queued at 1,200 px, 1,200 → 1,233;
   promoted, 1,233 → 934.7. In each of those, every row and section in
   view stayed at the same pixel. Where `scrollY` did not change and a row
   came or went in view, the rows under it moved by its height (33 px for a
   queue row, 299 px when the open M32 card left Under way). That is the
   data changing, not the page.
2. **Redraws with the same text.** Through the twelve minutes the only
   mutations were the state line and age, Heard's folded line (rebuilt on
   each 30 s poll, about 8 mutations a time), and four lines set to the same
   text on every new file: `under-way-note`, `queue-note`, `dropped-count`,
   `src-line` (12 times each, once per export). Nothing a person sees.
   Step 3 should not hang its flash on those.
3. **What changed in the ten minutes.** Nothing in the work-state database
   changed during the soak, so the real updates were twelve exports: the
   state line's export time moved each minute and every row stayed as it
   was. The changes to items were tested by fixtures (2b).
4. **The next item starting.** With M32 promoted, M39 started (a scratch
   database through the proxy): `uw:M39` opened by itself, the Queue's row
   for it went away. A fold set on a queued row does not follow it into
   Under way (the key changes). As designed.
5. **"coordinator: not known"** sat on the state line through the whole
   run. Herdr data, not this step.

## How it was tested

**Browser.** Headless Firefox 159.0.1 through geckodriver 0.37.1, driven by
a bare WebDriver client (no Selenium on this host). 24 sessions. Every page
came from a loopback listener of a scratch proxy that passed only `/dev/`
paths to this host's port 80 (`/dev/work`, `/dev/static/kronk.css`,
`/dev/api/work`, `/dev/api/voice_runs`), and served step 1's page once
from `../kronk-releases/item-M32-step1/orchestrator/static/work.html` (read
only) for 2h. Firefox's own traffic went to a second loopback listener that
answered 403 to everything. Most sessions had one injected `<script>` at the
top of `<head>` recording `error`, `unhandledrejection`, `console.error` and
`warn`, and counting pending timeouts and intervals. Viewports set exactly:
1280×900 (desk), 1920×1080 (wall), and 1920×1080 as an outer window (994 px
viewport).

**Ordinary use (2a, 2c, 2f).** At 1280: M32 open by default, every other row
closed, Heard and Dropped folded. Every row (42, with M29 inside Dropped)
scrolled into view, clicked open, read, clicked closed: every click toggled,
every row folded again, no link in any row, no empty part. What and Why
compared with my own parse of each `docs/sprint/<id>-criteria.md` ("Handoff
line:" and "Source:" paragraphs, backticks and stars dropped): 40 items with
a criteria file, every one equal or (S1) a clip of it; items with no handoff
show no What, none with no source shows Why. Screenshots at both sizes
looked at with Heard's line hidden (see below). Reload in the same tab: M34's
Done row open, M32 closed, Dropped open, a Backlog entry open, all kept;
Heard folded again though I had opened it; the store held
`{"done:37:M34":true,"uw:M32":false,"dropped":true,"bl:Now — in flight:Home Assistant major upgrade":true}`;
`localStorage` held no keys. A new tab: M32 open only, store `{}`. A second
reload of the first tab: same as the first.

**Ten minutes of real updates.** Two windows, 12 minutes each (12:01 to
12:13), wall (1920×1080, defaults) and desk (1280, M36's Done row and a
Backlog entry opened by click, scrolled to 1,200 px). A snapshot each 10 s
(72 each): every row node and every part node the one taken at the start;
`scrollY` 0 and 1,200 throughout; open set unchanged; 2,707 elements, 1
pending timer, 0 intervals in every sample; no error, no banner, cover at 0.
240 `api/work` and 24 `api/voice_runs` requests each.

**Fixtures (2b, 2d).** The work-state database copied once by the backup
API from a `mode=ro` connection (12:01; it left `work.db-shm` and an empty
`work.db-wal` beside the live file). Fixtures made on scratch copies with
`scripts/work.py --db <scratch>` only: f1 M37's next step changed; f2 M39
added and moved to the front; f3 M32 promoted (`--no-tag --deployed no`);
f4 M38 dropped; x a title, a leftover, a decision owed by Drew and a next
step holding `<img src=x onerror=window.__pwned=1> [link](https://example.invalid/) <b>bold</b> \`code\` *em*`.
Each exported by `work_export.py --db <scratch> --out data/dev/work.json`.
`kronk-work-export.timer` stopped 12:13:47 to 12:14:03 (16 s) while they were
in `data/dev`; then dev's file exported from the live dump (`--out
data/dev/work.json` only) and the timer started. Production's
`data/work.json` was never written by me. The page took 2.89, 1.54, 1.44 and
1.54 s to show f1 to f4.

| Fixture | Same node | Parts drawn | Open rows |
|---|---|---|---|
| f1 next step | 98 of 98 | `q:M37/stage` | kept |
| f2 queued at front | 98 of 98, `q:M39` added | M38 and M37 heads (position), M38 stage ("next") | kept |
| f3 promoted | 98 of 98, `uw:M32` gone, `done:40:M32` added | none of the kept rows | kept (M32's row left Under way) |
| f4 dropped | 99 of 99, `q:M38` gone, `drop:M38` added | M37 head and stage | kept |

x: `window.__pwned` unset, no `img` in `<main>`, 0 links and 0 `b` in the
three rows; the title rendered `code` and `em` and showed the rest as
written; the leftover and the decision (under "Decisions owed by Drew" and
"Open leftovers and decisions") as text with no child element; the next step
the same. Scroll anchoring (note 1) measured after, through the proxy with
the same exports.

**Heard (2g).** Live: the folded line contains the newest household run's
words in quotes; the run is a household run and the summary's newest. Checked
by comparison inside the harness; the words were never printed, and no
screenshot showed them (the line hidden before every shot I looked at; two
unlooked shots that may have shown it deleted). Then the proxy answered
`api/voice_runs` with invented runs (a copy of the live row's shape, every
string mine): new words in 27.1 s, a `nothing_heard` run as "nothing heard"
in 30.2 s, `<b>` and `<img onerror>` in the words as text in 30.2 s (no
element, nothing ran), Heard open and a new run in 30.4 s. A first try used
a heard state the route never sends ("none") and the page printed it as
"none"; the route sends only `text`, `nothing_heard`, `not_recorded`.

**Cut (plumbing 9).** The proxy answered 502 to `api/work` and
`api/voice_runs` for 40 s at 1920×1080, scrolled to 300 px: NO ANSWER in a
44 px box over the bottom of the window from 3.0 s, "The page shows the last
known state."; state line, Under way, Queue and `scrollY` at the same pixel
in all 80 samples. "Heard" read "not read" from 26 s into the cut (its own
failed poll) and cleared at 1.5 s after the cut, with the banner.

**2i.** Through the proxy, an injected page script awaited `loadAll()` 500
times, each answer one of six exports with a new `ts`, half with the queue
reversed; counts taken after one more answer of the same export, held.

**Zones.** Firefox under `TZ=America/New_York`, `Asia/Tokyo` and `UTC`: M32's
steps "Nov 24, 09:53 PM", "Nov 26, 05:14 AM → 05:21 AM", M29's day
2026-11-24, the state line's export time: the same in all three.

**The suite.**

```
===== 8551 passed, 3 skipped, 62 xfailed, 6 warnings in 218.78s (0:03:38) ======
```

The build's gjs harness was not run.

## Guard rails

None broken. Production: no request. Only `/dev/` routes, through the
proxy and by `curl`. No rebuild or restart of anything. One timer stopped
and started (above). No unit installed, enabled or linked. No sound, no
notification. `scripts/work.py` ran only with `--db` on scratch copies. No
household transcript text printed, kept or committed.

## State at the end

No browser, geckodriver or proxy running; `kronk-work-export.timer` active;
dev's `work.json` the live data (M32 under way, M38 and M37 queued). Scratch
holds the copied database and its fixtures (mode 700), the harness and the
masked screenshots. Untracked or changed in the tree and not mine:
`.gitignore` (4 lines added) and `esphome/`; not touched.
