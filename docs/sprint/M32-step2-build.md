# M32 step 2 build: rows that unfold

Build agent m32-build2 (Opus 5.5), 2026-11-26, from `c822fd8` (main; HEAD
was `13b0f2b` when the code was committed). Code commit `c5aaf0d`. Built to `M32-criteria.md`, "Steps 2 and 3",
step 2 (plumbing 1 to 9, criteria 2a to 2k), with `M32-review.md` points 5,
6, 7 and 3.9 as the design. No motion added; the slot for step 3's track
is in every row and empty.

## What was built, by plumbing point

1. **Keyed patching** (`work.html`, `patchList`, `patchParts`,
   `longestRun`). One function for Under way, the Queue, Done (and the
   deploys with no item among them), Dropped, the Backlog's sections and,
   inside each, its entries, and the unlinked Shipped entries. A row is a
   node with `data-key` and fixed `_parts`; each part keeps `_sig`, the
   JSON of what it was drawn from. A kept key keeps its node; a part is
   cleared and drawn again only when its signature changed; a key gone is
   removed; a new key is built. Kept nodes are moved only when out of
   place: the longest increasing run of their current positions stays,
   the rest are inserted before their successor (one move when the first
   queued item goes to the end). Keys: `uw:<id>`, `q:<id>`,
   `done:<promotion>:<id>`, `drop:<id>`, `deploy:<promotion>`,
   `sec:<section>`, `bl:<section>:<title>`, `sh:<title>`; a repeated key
   gets `#2`. An item row is `<details class="row">` whose `<summary>`
   holds the parts head, stage and track (empty), and whose body is the
   fourth part. "Other agents" and "Not read" are drawn again only when
   their data changed (`drawIf`).
2. **What a row unfolds to** (`bodyInto`): What (handoff), Shipped as
   (the linked Shipped entries), Why (`source`), Waits on Drew / Decisions
   owed by Drew (`decisions_owed` for the item, with text), Open leftovers
   and decisions (`open_list` less those), Steps (kind and round, start →
   end in the house's zone with the end's clock only on the same day,
   verdict, grade, agent, report), and for an item under way its linked
   ROADMAP entry. A part with nothing in it is not drawn, so an item with
   no handoff and no source shows its title and Shipped line only.
3. **Folds**: the item under way open, every other row closed. Kept per
   tab in `sessionStorage` under `kronk.work.folds`, only where a fold
   differs from its default; one `toggle` listener on `<main>` in the
   capture phase. Backlog entries and the dropped list are kept the same
   way; "Heard" never.
4. **Order**: state line (the live mark is the header dot), Under way,
   Queue, Other agents, Heard, Done (with the unlinked Shipped), Dropped,
   Backlog, Not read. "Other agents" is not in the plumbing's list; I put
   it after the Queue, beside the agent state it belongs to.
5. **Heard's folded line**: counts · `newest Nov 26 04:12: “words”` ·
   as of. The newest run is asked for on every Heard tick with
   `api/voice_runs?limit=1` (no filter: the route's household runs),
   folded or open, and kept in `heard.newest`; the words are set as text.
   "nothing heard" / "not recorded" when the run has no words; the
   summary's time alone when no run could be read.
6. **Exporter, schema 4** (`scripts/work_export.py`, keys only added):
   `items[].ended_at` (dropped items; null otherwise), `items[].open_list`
   (`id`, `kind`, `owner`, `blocks`, `date`, `text`; open leftovers and
   decisions with an item, oldest first; escapes and closed matters out;
   text clipped at `ENTRY_MAX` 4,000, the longest open one is 315; `date`
   is null when the stored time does not parse, never the stored value),
   `decisions_owed[].text`. `work_api.SCHEMA_KNOWN = 4`. Built in a
   scratch copy beside `work.py`, the tests run against it with
   `M27_WORK_EXPORT`, moved in by rename (mode kept).
   M34's marker test: `("open_thing", "text")` moves to ALLOWED with the
   rule "open matter" (open, a leftover or decision, with an item or a
   decision owed by Drew), `("item", "ended_at")` with the rule
   "dropped"; NEVER loses those two and nothing else; an assertion that
   the rule's rows exist (2 shown, a dropped item).
7. **Dropped**: `<details class="fold-list">` after Done, folded, its
   count in the summary; each row id, title, "dropped", the house's day
   (`houseDay`, `en-CA` in the house's zone). It is an item row and
   unfolds like the others.
8. **Titles and labels**: `mdText(parent, s, links)` makes a link only
   when `links === true`, passed only for the body of a ROADMAP entry
   (its unfolded rest and an entry with no rest). Every title and every
   `<summary>` renders code and emphasis and shows `[t](url)` as written.
   **A queued item's agent** goes to its row: the exporter lists an agent
   with its item when the item is active, queued or held (`on_row`); the
   queue row shows `name: status`.
9. **Carried notes**: the NO ANSWER banner and the reload note are in a
   fixed box over the bottom of the window (`.float-banners`, solid
   background), outside `<main>`; its text now ends "The page shows the
   last known state." ("Shown below" was false at the bottom). On the
   first answered poll after a failure, `heard.lastEnd = -Infinity`, so
   "Heard" asks at the next tick.

## Tests

New, `tests/test_m32_step2.py` (25): the exporter's schema-4 keys on a
walk of every item state and matter kind (open list, owed text, the
dropped item's end and never its reason, the unparseable day, the clip,
no database), the queued and held agents on their rows, the real dev
export carrying the text (read only; skips below schema 4); and the
page: one patch function and no rebuild, the keys, the frame and its
empty track, the body's parts and its signature covering what it reads,
the step line, text-only matters, links only in entry bodies, the folds,
the page order, the dropped list, the queue's agents, Heard's line, the
floating banner and its wording, note 6, the schema-4 keys read, no
endless motion.

Changed, each because the plumbing changed what it pinned:

- `test_m34_work_db.py`: the marker test (point 6, above); `SCHEMA` 4 and
  `SCHEMA_KNOWN` 4 with the note at schema 5;
  `test_an_agent_no_active_item_owns_is_other` (queued and held agents on
  their rows; a dropped item's agent is still other); the needle
  `el('span', 'notes', fill(QUEUE_TEXT.next_step`.
- `test_m34_e2e.py`, the second walk: the decision's and leftover's text
  are now in the file, once in `open_list` and, for Drew's decision,
  once in `decisions_owed`.
- `test_m27_work_page.py`: `schema_known` 4, "reads schema 4", the
  newer-schema note at 5; the needle `details[open]` became `foldable(`.
- `test_m27_work_export.py`: `SCHEMA == 4`. `test_m32_live.py`: the two
  `SCHEMA == 3` pins read `>= 3`.
- `test_m30_heard.py`: Heard is after the Queue and the other agents,
  before Done (renamed `test_c3_heard_is_folded_on_every_load_after_the_queue`);
  `sessionStorage` appears four times (the reload stamp and the folds);
  the summary's literals (`as_of`, `newest`, `newest_time` are their own);
  the byte-for-byte route test adds a schema-4 body and `schema_known` 4.

What only a browser shows, run by me on dev (below) and the test
agent's to judge: layout and the first screen, a real click and toggle,
`sessionStorage` across a real reload, the banner over the page, a link
colour in a title (gone with the links).

## gjs

`docs/sprint/M32-step2-gjs/` (step 1's stub with `toggle` reaching
capture listeners and a move counter; `run.js` and a README). Fixture: dev's
schema-4 export of 2026-11-26; "Heard" runs invented. 48 PASS, 0 FAIL under
`TZ=UTC`, `America/New_York` and `Asia/Tokyo`:

- 2a: M32 open, 41 other rows closed, Heard and Dropped folded; on 38 Done
  rows What/Why/Shipped/Steps as the file says, no empty part; M32's
  steps "criteria (round 2)", "m32-test", PASSES, "graded pass",
  "Nov 26, 05:14 AM → 05:21 AM"; M24's 9 open entries with their text.
- 2b: `next_step` changed: 98 of 98 rows the same node, 97 of 97 other
  rows' parts untouched, M32's stage part drawn again and its head and
  body not, open rows open, 0 moves. M99 queued at the front, M32
  promoted to the top of Done, M38 dropped: each time every other row the
  same node and 0 moves. The first queued item moved to the end: 1 move.
- 2c: the store holds only differing folds (`{"done:37:M34":true,
  "uw:M32":false,"dropped":true,"bl:Now — in flight:Home Assistant major
  upgrade":true}`), never Heard's; a reload restores them, Heard folded; a
  new tab gets the defaults; storage that throws: no error.
- 2d: a title, a leftover and an owed decision holding
  `<img src=x onerror=alert(1)> [link](https://example.invalid/)`: no
  `img`, `script` or `a` in any row, the text shown as written, the title's
  `code` and `em` rendered.
- 2e: "1 item"; M29, its title, "dropped", 2026-11-24.
- 2g: "newest Nov 26 04:12: “fixture words 2”"; a newer run that heard
  nothing shows "nothing heard" 31 s later; `<b>` in the words is text;
  open, the line still follows the newest run.
- Note 6: page live and "Heard" read again at the same tick (3,500 ms
  after the cut ended).
- 2i: elements 2,698 / 2,698 and page timers 1 / 1 after 5 and 500
  updates (half of them reordering the queue).
- 2h: `item-M32-step1`'s page on the schema-4 file: no error, Under way
  drawn, no banner.
- 2j: no bare undefined/NaN/[object/null.

Step 1's harness (`M32-step1-gjs`) on the new page: every check passes
but the eight B lines whose regex expects "Shown below: the last known
state.", changed on purpose (point 9). Left as step 1 wrote it.

## Dev check

- `docker compose --profile dev up -d --build tool_service-dev
  orchestrator-dev`, twice (the second for the banner's wording):
  orchestrator-dev recreated, tool_service-dev healthy and kept.
  `kronk-orchestrator-1`, `kronk-nginx-1` (up 3 h), `homeassistant` (9
  days) and `music-assistant-server` (3 weeks) untouched.
- `/dev/work`: 200, `cache-control: no-store`, 85,714 B, stamped
  `b612a3370183`, equal to the hash of the tree's `work.html` and
  `kronk.css`. It was `7a2332b2f04f` (step 1).
- `/dev/api/work`: 200, `no-store`, fresh, `schema` 4, `schema_known` 4,
  no schema note, `page_version` `b612a3370183`; 71 open entries across
  the items, `text` on all three owed decisions, M29 `ended_at`
  `2026-11-25T02:41:01Z`. `/dev/api/voice_runs?limit=0`: 200.
- Headless Firefox 159 through geckodriver on loopback, Firefox's own
  traffic sent to a dead loopback proxy; only `/dev/` URLs. At 1920×1080
  (viewport 994): state line 81, Under way 195 to 651 with M32 open, Queue
  680 to 863: all on the first screen (2f). 1280 and 960 laid out the same
  column, no horizontal overflow (`scrollWidth` 12 px under the window).
  No script error, no "undefined", 42 rows, no link in any row. A faked
  failed poll (`liveFailed` then `renderLive` in the page): the banner
  shown, the state line and Under way at the same pixel. A click on the
  first Done row, then a reload: still open, Heard folded, M32 open.
  Screenshots in scratch, not committed. geckodriver stopped after.

## Decided here

- **Heard's words by `limit=1`, page only.** The route's summary has no
  words and `limit=0` must not open the copy (an M30 test); `limit=1`
  with no filter gives the newest household run from the deployed
  route. Cost: one indexed query per page per 30 s, and while Heard is
  open, two requests per tick.
- **Shipped lines moved from Done's folded line into the body** (all
  item rows), and the step count with its verdicts moved onto Done's
  folded line. Folded Done rows are one line shorter.
- **Every row's body is built folded** (2,698 elements against 1,605):
  simpler signatures, and opening costs nothing.
- **Other agents after the Queue** (point 4 names no place for it).
- **The reload note floats with NO ANSWER.**
- **A dropped item's agent stays under "Other agents"** ("its item is
  dropped").

## For the coordinator

- The tree's exporter is schema 4 since its move, so production's `/work`
  (schema 3 page) shows "work.json has schema 4, newer than this page's
  3: keys added since are not shown" until step 2 is deployed. On that
  page an agent of a queued item is under no section (it left
  `other_agents`).
- Leftovers with no item (4 open) and Drew's decision with no item (#40)
  carry text in the file only in `decisions_owed` (the decision); neither
  is shown on a row, and the page lists no decision with no item. Not
  asked for; a question for #22's follow-up if wanted.
- M36's test step shows "verdict not read" beside "graded pass": its row
  has no verdict. Data, not the page.
- `item-M32-step1` is recorded as a deploy with no work item (it shows as
  such in Done).
- Untracked, not mine, not touched: `scripts/hooks/` and
  `tests/test_stop_failure_hook.py` appeared in the tree during this
  build. The tree is clean of my work; those two remain.

## The suite

```
===== 8551 passed, 3 skipped, 62 xfailed, 6 warnings in 217.09s (0:03:37) ======
```

## State at the end

No unit installed, enabled, started or stopped; the timer untouched.
Production not rebuilt, restarted or asked; no deploy, no tag. The dev
pair rebuilt twice. No sound, no notification, no household speech read.
