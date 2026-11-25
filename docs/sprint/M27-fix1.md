# M27 fix round 1: herdr shapes, a table inside an entry, a folded backlog, Done rows that name an item once

Fix agent: Opus 5.5 (`m27-fix1`), 2026-11-24, about 18:52 to 19:20 local.
Contract: `docs/sprint/M27-criteria.md`, the ruling "on the test" (fix
round 1); findings from `docs/sprint/M27-e2e.md`. Base: `dce6d92`.

## What changed

| Job | Where | Change |
|---|---|---|
| S1 | `scripts/work_export.py` | Each herdr entry is read by `herdr_agent()`: not an object, a status that is not a string or null, a `cwd` holding a NUL, or any exception → counted in the new `herdr.skipped` and left out. `collect()` also catches an exception from `herdr_agents()` as a whole: agent state "not known", reason "herdr agent list: the answer could not be read (<type>)". The page adds "N not readable (skipped)" to the state line when N > 0. |
| S4 | exporter | `FOR_DREW` is `\b[Ff]or\s+Drew\b(?!['’]s)`: any white space between the words, a line break included. |
| S5 | exporter | A heading entry's body runs to the next `#`/`##`/`###` heading, tables included (the old loop also stopped at a line starting `|`). A heading with no id ("After the sprint") still ends the entry, so the M16 table after it is read as before. `LABEL_ID` is `(?<![\w-])M\d+[a-z]?(?: step \d+\|-step\d+)?(?![\w-])`: `ARM64` is no item, `M30-step1` is `M30-step1`. |
| N8 | exporter | `remove_old_temps()`, called by `write()` before `mkstemp`: removes `.<name>.XXXXXXXX` (the exact mkstemp form, 8 of `[a-z0-9_]`) in the output directory when it is a regular file (by `lstat`) older than 600 s. Errors ignored. |
| S3 | `tests/test_m27_work_export.py` | A fixture test (the One-line entry linked to promoted M1 stays in Now) and a real-tree test ("Kronk's voice: TTS trial", linked to M20, promoted, stays in the backlog). |
| S2 | `orchestrator/static/work.html` | `SOURCE_BANNER.stale` is `{"text": "{reason}. Shown below: the last known state.", "strip": "stale: "}`: the banner prints the route's reason, less its "stale: " prefix. |
| S6, change 1 | exporter and page | The exporter adds `fold: {head, rest}` to each open entry (`fold()`; `text == head + " " + rest`). The page shows `head` in a `<summary>` with " … more", and `rest` when opened; opened entries (backlog and M27's card) keep their state across the 30 s refresh by key, as Done rows do. `mdText()` now renders `*…*` (so `*( … )*` and `*Why: …*`) as `<em>` and `[text](url)` as a link when url starts with `https://` (`MD_LINK`), `rel="noopener"`, text set as text; any other url is shown as written. |
| Change 3 | exporter and page | The exporter adds `items[].short_title` (title less its own id and "pass criteria:"), `done[].heading_adds` and `items[].shipped[].adds` (`adds_to()`: a word of 3+ letters, other than ids, dates and a few small words, that the title lacks). The Done row shows id, short title, tag, date; the heading only where `heading_adds`; "Shipped as: …" only where `adds`. |
| Change 5 | exporter and page | The exporter adds `herdr.coordinator.shown`, one of "working", "waiting on an agent", "idle", "not known" (`coordinator_shown()`); the page prints it. `coordinator.word` is unchanged. |
| Change 2 | page | `DONE_TEXT`: "N lines addressed to Drew in its promotions entry; some may be settled since" (for 1: "1 line … ; it may be settled since"). |

Keys only added; schema stays 1. On today's tree, the export of the old
and the new exporter, with the added keys and `ts`, `made_at`, `run_s`
taken out, are equal; no key removed. Added: `herdr.skipped`,
`herdr.coordinator.shown`, `backlog[].entries[].fold`,
`items[].roadmap[].fold`, `items[].short_title`, `items[].shipped[].adds`,
`done[].heading_adds`.

Decisions I made where the ruling left room:

- **Which coordinator states read what.** The ruling's two clauses
  ("waiting on an agent" when an agent of an item under way works; "idle"
  only when nothing is working) leave a gap for an investigator working
  while the coordinator is not. I count any named agent in this repo that
  is `working` (an item's or not): it reads "waiting on an agent", never
  "idle". A `blocked` agent does not count as working (the item's card
  shows it in red). A coordinator pane herdr calls `blocked` reads "idle"
  or "waiting on an agent": the brief named four words, and `blocked`
  is not one. Its raw status stays in `coordinator.status` and `word`.
- **A status that is a string herdr may add** (`"WORKING"`) or null
  stays "unknown", as before; only a non-string is "not readable". A
  `cwd` that is not a string stays "elsewhere", as before.
- **The fold** lives in the exporter, not the page, so the suite can test
  it. Its cap is 320 characters; today's heads run 56 to 229.
- **Change 3 is a word test, not a rule per row.** On today's tree the
  heading is shown for M26, M16b, M16, M8, M11 and the four rows of "M3,
  M4, M4b, M1b restore", and hidden for the other 21 rows; "Shipped as"
  is shown for M26, M24, M16b, M21-step1, M21-step2 and M1.

## Evidence per job

**Tests that fail on the code as it was.** The new exporter tests against
`scripts/work_export.py` at `dce6d92`, the first set written: `53 failed,
89 passed` (every new
S1, S4, S5, N8, fold, short-title, adds, coordinator test failed; the two
S3 tests passed, as they pin present behaviour and are held by mutant
below). The new page tests against `work.html` at `dce6d92` (put back in
the tree for one run, then the new file restored and `cmp`-checked): `7
failed, 21 passed`, the seven being the six new tests and the changed
banner test.

**The exporter's move.** Built and tested in the session scratchpad
(`M27_WORK_EXPORT=<copy>`); moved in at 19:07:56 by `cp` to
`scripts/.work_export.py.new` and `mv` onto `scripts/work_export.py`
(one rename; `cmp` against the scratch copy). The timer's next runs:

```
Nov 24 19:08:21 kronk systemd[2731]: Starting kronk-work-export.service - Kronk — export the work items for /work...
Nov 24 19:08:21 kronk systemd[2731]: Finished kronk-work-export.service - Kronk — export the work items for /work.
```

`data/work.json` after it: `errors []`, `run_s 0.064`, `herdr {'known':
True, 'seen': 2, 'skipped': 0, 'coordinator': {'status': 'done', 'word':
'idle', 'shown': 'waiting on an agent'}}`; `data/dev/work.json` byte-equal;
no `.work.json.*` in `data/` or `data/dev/`. Seven runs since 19:07, all
"Finished", no failure line. `/dev/api/work`: `fresh`, age 39.6 s.

**S1, live shape.** A fake herdr answering the coordinator, `m9-test`
working and a third agent whose `agent_status` is `["working"]`, then
`{"s": "working"}`, then whose `cwd` holds a NUL: the script, run as a
process with `--out`, exits 0 and writes `seen 3, skipped 1`, M9 "testing,
agent m9-test working". Before, the first two raised `TypeError:
unhashable type: 'list'` / `'dict'` and the third `ValueError: lstat:
embedded null character in path`, and no file was written.

**S2.** `test_s2_a_file_dated_in_the_future_shows_the_routes_reason` runs
the route on a file dated 3600 s ahead and fills the page's table the way
`renderState()` does:

- before: "STALE: work.json was written -3600 s ago (the exporter runs
  every 60 s; older than 180 s is stale). Shown below: the last known
  state." (the e2e's S2)
- after: "STALE: work.json is dated 3600 s in the future: the host clock
  or the file is wrong. Shown below: the last known state."

An old file still reads "STALE: work.json was written 400 s ago (the
exporter runs every 60 s; older than 180 s is stale). Shown below: the
last known state." Not seen live in a browser (that needs the compose
override; see "Not done").

**S4.** `**For\n  Drew:**` and `left for\nDrew to decide` count 2; `waits
for\n  Drew's go` does not. Today's counts unchanged: M24 1, M26 2, M25 2.

**S5.** In a `## M2` entry, a table with rows `M39 (next item)` and
`ARM64 build` is body: the entries are M1, M33, M2, M16, M30-step1, M31
(the last three from a table after "## After the sprint"); "For Drew"
after the table is counted; M39 stays Under way; no M64, no M9 (from the
gloss "M33 (see M9)").

**N8.** A directory holding an 11-minute-old `.work.json.ab_d1234`, a
9-minute-old `.work.json.wxyz5678`, an old `.other.json.abcd1234`, an old
`.work.json.keep-me`, an old directory `.work.json.dir12345` and an old
symlink `.work.json.lnk12345`: after `write()` only the first is gone and
the symlink's target is untouched. The suite's strace test still finds no
`unlink` (no old temp file there).

**Render (job 6).** Dev pair rebuilt at 19:08 with `docker compose
--profile dev up -d --build tool_service-dev orchestrator-dev`
(`orchestrator-dev` recreated, `tool_service-dev` left running). Headless
Firefox through geckodriver (WebDriver on 127.0.0.1:4467) driven by a
script of mine; a new profile `~/snap/firefox/common/m27-fix1-render`
whose `user.js` sends everything but `localhost` and `127.0.0.1` to a dead
proxy (127.0.0.1:9), turns off site icons, update, telemetry,
safebrowsing and captive-portal checks, and sets the user agent. The prefs
took effect: `navigator.userAgent` read `m27-fix1-ua`, and
`http://example.invalid/` gave `about:neterror?e=proxyConnectFailure`.

- 1280: `innerWidth` 1280; `scrollWidth`/`clientWidth` 1268/1268 on the
  document and the body.
- 390, and it is 390: the window will not go under 500, so the page was
  loaded in a same-origin `<iframe>` 390 px wide and read from its
  document: `innerWidth` 390; 378/378 (390 less the scrollbar).
- At both widths, a script compared the page with `/dev/api/work` fetched
  by the page at the same moment: 14 backlog entries, each summary's text
  equal to its `fold.head` with the markup taken out, a `<details>` iff
  `fold.rest`; 30 Done rows, each top line "<id> <short_title>", meta
  spans tag, date, the heading iff `heading_adds`, the Drew text iff a
  count; "Shipped as" lines iff `adds`; no id twice where the heading is
  hidden; the state line holds `coordinator: <shown>`: **0 mismatches**.
  Clipped boxes (header, cards, rows, steps, entries, meta, summaries,
  state line; `scrollWidth > clientWidth` or past the viewport): 0.
  "undefined", "NaN", "null" in the rendered text of `<main>`: 0 (the
  `2>/dev/null` entry is folded now). Backlog text holding `*(`, `)*`,
  `*Why` or `](http`: 0. Links: one, `yt-dlp/ejs#80` →
  `https://github.com/yt-dlp/ejs/pull/80`, `rel` `noopener`. `<em>` in
  the backlog: 7. An entry opened by a click, then `loadAll()` called:
  still open in the new DOM. Requests: `/dev/work`,
  `/dev/static/kronk.css`, `/dev/api/work` (three, the last two mine), by
  Resource Timing; site icons were off in the profile.

The state line (390, 19:10):

> export made Nov 24, 07:09 PM, 58 s ago, from main @ dce6d92 ·
> coordinator: waiting on an agent · herdr: 2 agents seen

Before (the e2e, 18:28): "… · coordinator: idle · herdr: 2 agents seen".

M27's card:

> M27 / M27 pass criteria: /work, the work items in a page / fixing /
> fixing, agent m27-fix1 working / /work shows the backlog, each work item
> under way with its stage, and what is done, read from the files that
> already hold it. / criteria Nov 24, 05:45 PM M27-criteria.md / review
> Nov 24, 05:53 PM M27-review.md / build Nov 24, 06:24 PM M27-build.md /
> test 1 Nov 24, 06:48 PM PASSES M27-e2e.md / Backlog entry / /work: the
> work items in a page (work item M27, ordered 2026-11-24; after M26) —
> Drew: "i want to be able to track this work in a UI. … more

One backlog entry, folded (the lead in italics, the file name as code):

> ▸ **YouTube Music is down; wait for upstream** *(Drew, 2026-11-24)* —
> since 2026-11-24 Music Assistant cannot stream any YouTube Music track:
> its bundled yt-dlp fails YouTube's new player
> (`INVESTIGATION_2026-11-24_griz_album.md`). … more

and opened ("… more" gone, the rest below it, the link a link):

> ▾ **YouTube Music is down; wait for upstream** *(Drew, 2026-11-24)* — …
> (`INVESTIGATION_2026-11-24_griz_album.md`).
> Pandora and the NAS library work. Decided: no patch to our MA image;
> wait for the fix to reach a stock MA release. The chain to watch, in
> order: [yt-dlp/ejs#80] merges; a yt-dlp-ejs and a yt-dlp release carry
> it; a Music Assistant release bundles that yt-dlp. Then an MA update on
> a day and at a moment Drew picks (an MA restart), checked by streaming
> one YouTube track with no `[jsc]` error in MA's log. Checked at the
> start of each working session until then.

Before (the e2e's S6): the whole 1,000-odd characters at once, reading
`*(Drew, 2026-11-24)*` and `[yt-dlp/ejs#80](https://github.com/yt-dlp/ejs/pull/80)`
as written.

One Done row, before (the e2e's change 3): "M25 | M25 pass criteria:
/resources shows real usage | item-M25 | 2026-11-24 | M25 (/resources
shows real usage), 2026-11-24 | Shipped: /resources shows real usage
(2026-11-24, work item M25)" and "2 lines for Drew". After:

> M25 /resources shows real usage / item-M25 2026-11-24 2 lines addressed
> to Drew in its promotions entry; some may be settled since / ▸ 8 steps ·
> PASSES → FAILS → PASSES

and M26, where the heading and the Shipped title add words:

> M26 nothing depends on the Ollama install / item-M26 (tag only)
> 2026-11-24 M26 (nothing of Kronk's depends on the Ollama install),
> 2026-11-24 / 2 lines addressed to Drew in its promotions entry; some
> may be settled since / Shipped as: Kronk's own ROCm libraries
> (2026-11-24, work item M26) / Note: M26, the removal's first step
> (2026-11-24) / ▸ 6 steps · PASSES → PASSES

Cleanup: the browser and geckodriver were stopped at the end of each of
the five runs; `pgrep -a firefox` and `pgrep -a geckodriver` print nothing
after the last; `~/snap/firefox/common/` holds only `.cache` and
`.mozilla`, the profile directory removed by the script.

**Suite (job 7).** `./scripts/run_tests.sh -q -p no:warnings`:

```
.....................................                                    [100%]
7684 passed, 3 skipped, 54 xfailed in 108.57s (0:01:48)
```

7613 before; 71 new tests (65 in `test_m27_work_export.py`, 6 in
`test_m27_work_page.py`, counting each parameter; one page test changed). The strace test ran
(not skipped).

## Mutants

29 one-line mutants of the new exporter, each in a scratch copy, against
`tests/test_m27_work_export.py` and `tests/test_m27_work_page.py`
(`M27_WORK_EXPORT=<mutant>`). First round 23 killed, 6 survived. Five of
the six were real gaps; tests added (`herdr_agent()` read per entry
without raising, an entry that raises is skipped, a lead with a full stop
before a space, "the" in a heading). Second round: **28 killed, 1
survived.**

| Mutant | Killed by |
|---|---|
| S3: `if any(l in under_way …)` → `if e["links"]` (the e2e's) | `test_open_entry_linked_to_a_promoted_item_stays_in_the_backlog` |
| S4: one space only | `test_for_drew_broken_across_a_line_is_counted` |
| S5: a `|` line ends a heading entry; no left boundary on ids; no `-stepN` in a label; ids in the gloss counted | `test_a_table_inside_a_heading_entry_is_its_body` |
| S1: status type not checked; NUL in `cwd` not checked; per-entry guard removed | `test_herdr_agent_reads_one_entry_without_raising`, `test_an_agent_entry_that_raises_is_skipped` |
| S1: the guard in `collect()` removed | `test_a_herdr_reader_that_throws_still_writes_the_file` |
| Coordinator: a blocked agent counts as working; `unknown` not special; agents checked before the coordinator's own `working` | `test_coordinator_shown_word` |
| N8: 20 min; 5 min; not only regular files; any name; cleanup not called | `test_its_own_temp_files_older_than_ten_minutes_are_removed` |
| Fold: a bare number ends a sentence; markup balance ignored; single stars not balanced; search from the start (inside the lead); no cap | `test_fold_of_an_open_entry`, `test_fold_of_a_long_sentence_is_cut_at_a_space` |
| Adds: small words count; any length; `heading_adds` always true; Shipped `adds` always true | `test_adds_to_title`, `test_done_rows_say_what_adds_something`, `test_real_tree_done_rows_name_the_item_once` |
| `short_title` strips any id | `test_short_title` |

Survivor, equivalent: in `coordinator_shown()`, dropping `a["name"] is
not None` changes nothing, because the only unnamed agent is the
coordinator, whose `working` returns first, and two unnamed panes make it
"not known" before the check.

The page's script is still not run by the suite (no JavaScript runtime on
the host). The new page tests check the tables (`SOURCE_BANNER.stale`,
`DONE_TEXT`, `MD_LINK`, `AGENT_TEXT.skipped`), S2 through the route and a
Python fill of the same table, and as text that the script reads the
added keys and no longer reads `coordinator.word`. `mdText()`'s pairing of
`*` and links, the fold's `<details>` and the keep-open are held only by
the render above.

## Not done

- S2 not rendered live (it needs the compose override of `WORK_PATH`);
  held by the route-plus-table test.
- The e2e's notes N1 to N7, N9 to N13 and the other survivors in its
  "Mutants" (any tag ending in the id, `e2e` in the role pattern, the
  verdict forms, `sprint item`): left over by the ruling.
- `LINK_RE` (ROADMAP links) still has no right word boundary after the id
  (`sprint M20x`); the ruling's word boundaries were for promotions.
- Under way cards still show "M27 M27 pass criteria: …": change 3 named
  Done rows only.
- The tester's change 4 ("Now — in flight" under Backlog) and N15 (no
  favicon): not in this round.
- `ROADMAP.md` and `PROMOTIONS.md` untouched; nothing installed, started
  or stopped but the dev orchestrator's rebuild; no request to production
  beyond nginx's `/dev/` path; herdr only `agent list`; `data/` written by
  the timer only.
