# M32 step 1 build: /work live and safe

Build agent m32-build (Opus 5.5), 2026-11-26, from `c12db87` (main, clean).
Built to `M32-criteria.md`, "Rewritten after the review", step 1
(plumbing 1 to 4, criteria 1a to 1j), with `M32-review.md` as the design.
Code commit `90d5107`. No redesign: rows, order and looks are as they
were, but for the header dot, the two banners and the cover.

## What was built, file by file

- **`orchestrator/servers.py`**: `read_source()` takes an optional
  `cache` (a dict the caller keeps). With it, the file is opened, `fstat`ed
  and read only when its key (device, inode, mtime_ns, size) differs from
  the kept one. The key comes from the file actually read, so a file
  renamed into place between two polls is never paired with the other's
  parse. A parse that fails is kept as its reason string, not as an
  exception object: re-raising a stored exception grows its traceback on
  every poll. `cache["reads"]` counts the reads. Without a cache, callers
  get exactly the old behaviour (gpu_mem, llama_props, voice_runs status).
- **`orchestrator/work_api.py`**: `SCHEMA_KNOWN = 3`. `PAGE_VERSION`: 12
  hex of SHA-256 over `work.html` and `kronk.css`, taken once at import
  from `STATIC_DIR` (default: `static/` beside the module, which is
  `/app/static` in the image). `stamp()` fills the page's placeholder
  `<meta name="kronk-page-version" content="">`. `work_source(now, have)`
  uses the cache, answers `work: null, unchanged: true` when `have` is the
  file's `ts` as a number (`float(have) == float(ts)`; a non-number, NaN or
  inf never matches), and adds `page_version` and `server_s` to every
  envelope. The route sets `Cache-Control: no-store`.
- **`orchestrator/main.py`**: `_serve(path, version=None)` stamps the
  version when it is given; `/work` returns the stamped page with
  `no-store`.
- **`orchestrator/voice_runs_api.py`**: `no-store` on the answer and on
  the 400. Nothing else in the route changed; its import set is unchanged.
- **`orchestrator/telemetry.py`**: `_DEFAULT_EXCLUDED` gains
  `https?://[^/]+/(work|api/work|api/voice_runs)$`. The instrumentation
  matches scheme, host and path with no query string
  (`opentelemetry-instrumentation-asgi` 0.66b1, `get_host_port_url_tuple`,
  read), so `?have=` needs nothing. nginx strips `/dev/` first. `$`
  anchors it: `/workx`, `/api/workers` and `/api/work/x` are still traced.
- **`orchestrator/static/work.html`**:
  - The version placeholder in `<head>`. CSS: the header dot is now the
    beat (`.header-dot.beat`, resting at opacity 0.3, one 0.9 s fade from 1
    per answered poll, no endless animation), and the cover (`position:
    fixed`, `pointer-events: none`, `animation: cover-show 1s linear 90s
    forwards`). The new CSS sits above the "Heard" block, so that block is
    byte-for-byte M34's.
  - Markup: `#live-banner` (NO ANSWER) and `#reload-note` above the
    route's banner; the cover after `</main>` with its text in the HTML,
    so a dead script still shows it; the Refresh button calls `pollNow()`.
    Done's note names the five UTC days (M34-e2e note 1).
  - Script: `LIVE` and `LIVE_TEXT` as JSON literals; `getText()` (the one
    `fetch` in the page: `no-store`, and given up after 10 s by
    `Promise.race` with a timer, which also aborts it when
    `AbortController` exists); `getJSON()`; a `live` state; `loadAll()` is
    now one poll with `?have=` (at most one in flight, the next due 3 s
    after it ended); `liveFailed()` keeps everything and records since
    when and why; `liveAnswered()` redraws the state line on every answer,
    rebuilds the rows only when the file changed (as before), restarts the
    cover's animation and beats the dot, then `maybeReload()`.
    `houseString()`, `zoneKnown()` and `fmtClockS()` format times in
    `house_zone`. `renderLive()` sets the age and the banner text each
    second on nodes that stay. `maybeReload()` is review 3 items 1 and 2.
    `liveTick()` is the scheduler; `liveStart()` reads the page's own
    version from its `<meta>` and starts it. It is the script's last line,
    marked for the test agent's proxy to strip (1f).
  - "Heard": `heardFetch()` goes through `getJSON()`; the `setInterval`
    and the first direct `heardTick()` are gone (the scheduler runs it
    when `HEARD.every_ms` has passed since its last end, `heard.lastEnd`);
    `heard.limit` is the count loaded (`heardLoad`, `heardOlder`);
    `heardTrim()` after each patch; `heardForget()` after ten minutes
    folded (`heard.foldedAt`, set by the existing toggle listener). Its
    style, markup and everything else are unchanged.
- **`scripts/work_export.py`**: `SCHEMA = 3`. `house_zone(now)` gives
  `{"name", "utc_offset_s"}`: the name from `TZ` (when it is a zone name),
  else from the `/etc/localtime` link, kept only when `zoneinfo` knows it
  and its offset now is the host's. `collect()`'s part built from the rows
  is now `_outline()`. It runs once on the dump's rows inside a `try`
  around the five SELECTs, `queue_rows()`, `overview()` and the build
  itself, so a column missing anywhere in the build is caught too.
  `sqlite3.Error`, `KeyError`, `IndexError` and `TypeError` there make the
  state "corrupt" ("The dump of the work-state database loads as SQL and is
  not the schema this exporter reads (OperationalError)."; the type name
  only, never SQLite's text), drop `not_read` lines added by the failed
  attempt, and build again with no rows: ROADMAP kept, exit 0 (S1). With no
  row read, an agent's `why` is "the work-state database was not read"
  (note 5). Built in a scratch copy (with `work.py` beside it), the suite
  run green against it with `M27_WORK_EXPORT`, then moved in by rename.
  `scripts/work.py` was not edited (`cmp` against the scratch copy).
- **`systemd/kronk-work-export.path`**: `PathChanged=` on the live dump,
  `Unit=kronk-work-export.service`, `WantedBy=default.target`, an
  `# Operations:` block (install, check, verify, remove). Not installed,
  enabled or started.
- **`docs/features/work-page.md`**: a "Live (M32 step 1)" section; the
  schema-3 key, S1 and note 5; "Heard" on the scheduler and trimmed; the
  dates; three gotchas (a write during a run, the schema-3 note until the
  deploy, browsers with no `AbortController`); operations; blog hooks.
- **`docs/sprint/M32-step1-gjs/`**: the gjs harness (`dom.js`, `run.js`,
  a README with the command).
- **`tests/test_m32_live.py`** (new, 33 tests) and the changed pins below.

Decisions taken here, each small:

- **`server_s` in the envelope.** The ten-minute limit must survive the
  reload, so `performance.now()` cannot carry it; the browser's clock is
  ruled out. The stamp kept in `sessionStorage` is the route's clock at
  the reload.
- **`no-store` on `/work`** as well as on the two routes the criteria
  name. A reload must get the new page, never a kept one; a kept page
  stamped with the old version would make the next poll ask again.
- **No session storage, no self-reload.** The limit could not survive a
  reload, and a loop is worse than a stale page. The page shows a note:
  "Another page is deployed (…). This browser keeps no session storage, so
  the page cannot limit its own reloads and does not reload itself: reload
  it by hand."
- **The page itself is asked at most once in 30 s** while a version
  change is announced and the page will not come (`retry_page_ms`).
- **The header dot is the beat.** It was a static green dot that meant
  nothing. Now it rests dim and flashes once per answered poll, so a dead
  script leaves it dim. No element added.
- **Schema 3.** The zone is a key of the file (plumbing 4). The
  exporter's rule raises the schema when a key is added.
- **The scheduler's tick.** One `setTimeout` chain, one second, or sooner
  when the next poll is due (`tick_min_ms` 20). The gjs run found the plain
  1 s tick made the poll every 4 s: a poll due 5 ms after a tick waited for
  the tick after next.

## Criteria

| # | What shows it | Result |
| --- | --- | --- |
| 1a | gjs A2 (fake clock; ten file changes at different phases of the poll, answer 5 ms) | on the page after 1050, 1700, 1400, 1100, 750, 400, 150, 2800, 2500, 2200 ms. **The test agent's browser** on dev with fixtures is the criterion. |
| 1b | the coordinator at promotion (`work.py note`, live) | not mine. The path unit is a reference file until the coordinator installs it. |
| 1c | gjs B (502, refused, hang, not JSON, each 60 s, with and without `AbortController`), B2 (502 for an hour of fake time), B3 (never answered); the dev rebuild below | each: "NO ANSWER: since 05:00:04 AM (api/work: HTTP 502), 60 s ago. Shown below: the last known state.", the same Under way nodes, no reload, no page fetch, live again without a touch, element count unchanged. Hang: the reason is "no answer in 10 s", 5 requests in 60 s. **The test agent's browser** behind the proxy, the real hour and the real rebuild with a page open are the criterion. |
| 1d | gjs C, C2, C3, C4 | another version announced and served stamped with it: one reload, the stamp = the route's clock; the reloaded page with a third version within 10 min: no reload, no page fetch; after 10 min by the route's clock: one. Announced with the page at 502 for 120 s: no navigation, 4 page fetches; a 200 stamped otherwise: no reload; then a 200 stamped right: one. No storage: no reload, the note shown. Clock an hour out: once. **The test agent's proxy must stamp the page it serves with the version it announces**: the page reloads only into the version announced. |
| 1e | `test_one_read_of_the_file_per_change_however_many_polls`, `test_work_and_its_two_routes_leave_the_telemetry`; dev: 40 polls, 0 traces (below) | 20 polls, 1 read; a rename, 1 more. CPU with twenty pages and "no model completion" are **the test agent's**. The route touches no model: no import of one, no call. |
| 1f | gjs E (Date skewed ±1 h: no-argument `Date` and `Date.now`), F (5 and 500 updates), G (Heard through 300 new runs); `test_the_cover_is_the_stylesheets_and_nothing_moves_by_itself` | the same ages under 0, +1 h, −1 h ("data 15 s old"); elements 1605 / 1605 and page timers 1 / 1 after 5 and 500 updates; "Heard" 200 rows kept, 200 in the page, newest R00550, "show older" from the oldest kept; 400 after "show older", held at 400; folded 9 min kept, 10 min dropped, reopened 200. **The cover with the script stopped is a browser's only** (CSS); the line to strip is `liveStart();`, the last of the script. |
| 1g | `test_c2_api_work_byte_for_byte_what_it_was` (changed, below), `test_the_old_page_reads_the_new_route`; gjs H | the answer without `?have=` is the old one plus `unchanged: false`, `page_version`, `server_s`; item-M34's script reads only `resp.source` and `resp.work`; run on the new envelope: no error, Under way drawn, no banner. |
| 1h | `test_s1_…`, `test_note5_…` (both), the strict `xfail` removed; gjs A (no bare undefined/NaN/[object/null, a title `<img src=x onerror=alert(1)>` as text, 0 `img`/`script` elements), G (filter) | pass. |
| 1i | `systemd-analyze --user verify` on scratch copies of the path unit and the service, and on a copy pointing at a scratch dump: rc 0 both. A copy with `PathChangd=` gives rc 1 ("Unknown key", "Path unit lacks path setting. Refusing."), so verify does check. | "a write starts one export" is **the test agent's**: starting a transient unit is outside my guard rails. |
| 1j | the suite | `8472 passed, 3 skipped, 61 xfailed` (below). |

## What a real browser must still confirm

gjs ran the script; it is not a browser. Not shown by it:

- the cover: the CSS animation's 90 s delay, the fade, and its restart
  by `style.animation = 'none'`, a reflow, then `''`; the dot's beat by
  the class toggle and a reflow (the stub has no layout);
- `fetch` with a real `AbortController` against a real hung socket, and
  `cache: 'no-store'`;
- `location.reload()` landing on the new page, and `sessionStorage`
  surviving it in the same tab;
- `Intl` with `timeZone` in the kiosk's browser (gjs's ICU formats
  `America/New_York` the same under TZ=UTC, America/New_York and
  Asia/Tokyo; the offset fallback was run with an unknown name and with
  none);
- `visibilitychange` (registered, not fired in the stub), and timer
  throttling in a hidden tab;
- `details` toggling by a click, the scroll anchor of "Heard" (the stub's
  boxes are all at top 0);
- that nothing overflows or jumps at 1920×1080, 960×540 and 1280.

## The review's "not run" marks

- **uvicorn waits on open event streams at shutdown** (point 2). No
  stream is built, so it does not arise. Read: `docker-compose.yml` sets
  no `stop_grace_period` and no `--timeout-graceful-shutdown` for either
  orchestrator. The poll leaves nothing open; the dev rebuild was a normal
  recreate.
- **`EventSource` does not reconnect after an answer that is not 200**
  (point 2). Not built; the poll's recovery is the next tick, shown in
  gjs B and B2.
- **A write while an export runs may be missed by the path unit**
  (point 2, "to be measured"). Not run: it needs the unit started, which
  my guard rails forbid. By systemd's semantics (a start asked for while a
  oneshot is running is merged into the running job), it can be missed,
  and the timer picks it up within 60 s. Written in the unit's header and
  the feature doc. The test agent's 1i run can measure it.
- Also checked from the review's section 1: "no override of
  `TELEMETRY_EXCLUDED_URLS` exists" (grep of the compose files and both
  services: none); "the answer is 126,318 bytes" (now 127,198 on dev in
  full, 235 when unchanged).

## Tests changed, by name

- `tests/test_m34_e2e.py::test_a_dump_that_loads_and_is_not_the_schema_still_gives_a_file`:
  the strict `xfail` on "item lacks its columns" removed (S1). Added
  `test_s1_a_dump_that_is_not_the_schema_says_corrupt_in_its_own_sentence`,
  `test_note5_an_agent_with_the_database_missing_says_so`,
  `test_note5_with_the_database_read_the_reasons_are_as_before`. All three
  fail on the old exporter.
- `tests/test_m27_work_export.py::test_default_outputs_are_data_and_data_dev`:
  `SCHEMA == 3`.
- `tests/test_m34_work_db.py::test_criterion_2_every_difference_from_the_old_reader_has_a_class`:
  `new["schema"] == 3`; and a class for `items[].stage` where the only
  difference is `tag item-<id> exists` against `tag item-M34 exists`. **This
  test failed at `c12db87`, before any change of mine** (run against the
  untouched exporter: `{'UNCLASSIFIED': ['items[M34].stage']}`). The old
  reader leaves `stage()`'s detail unfilled; `work.py`'s `item_stage()`
  fills it (its N7). It shows up once an item has its tag and no
  `PROMOTIONS.md` entry, as `item-M34` has had since its promotion. Neither
  reader is wrong; the test lacked the class. The brief's suite count
  (`8435 passed`) predates that.
- `tests/test_m34_work_db.py::test_criterion_7_the_route_knows_schema_2_and_reads_no_database`
  (its name kept): `SCHEMA_KNOWN == 3`, the envelope's three new
  keys, the note at schema 4.
- `tests/test_m34_work_db.py::test_heard_is_untouched_since_before_the_build`:
  compares "Heard"'s style and markup with M34's base; its script and
  route change by order and are pinned in `test_m32_live.py`.
- `tests/test_m27_work_page.py`: fixture `work_file` resets the route's
  kept copy; `test_fresh` (`schema_known == 3`), `test_unknown_schema`
  ("reads schema 3"), `test_newer_schema_is_read_with_a_note` (schema 4,
  "this page's 3"), `test_relative_urls_only` (`LIVE.url`, not
  `fetch('api/work')`).
- `tests/test_m30_heard.py`: `test_c2_api_work_byte_for_byte_what_it_was`
  (schema_known 3, the three keys popped and checked, a schema-3 body
  added); `test_c3_open_or_closed_is_never_stored_the_filter_and_the_switch_are`
  (`sessionStorage` allowed once, for the reload stamp; `localStorage`
  still only in `store()`); `test_c3_built_outside_loadAll_with_its_own_timer`
  (no `setInterval`; Heard from `liveTick`); `test_c3_text_only_relative_urls_and_no_link_is_built`
  (`getJSON(...)` and the one `fetch` with `no-store`).

## The gjs run

`docs/sprint/M32-step1-gjs/` (stub DOM built from the page's `<body>`,
fake clock, scripted network, storage that can throw). Fixture: a copy of
`data/dev/work.json` raised to schema 3 with `house_zone`. "Heard" runs
are invented ("fixture words N"); no voice-run data was read. Run under
`TZ=America/New_York`, `UTC` and `Asia/Tokyo`: 118 PASS, 0 FAIL each.
One note printed by design: without `AbortController` a hung request
cannot be cut, so five stay open in a minute while the page waits on one
at a time.

## The dev check

- Rebuilt with exactly `docker compose --profile dev up -d --build
  tool_service-dev orchestrator-dev` (orchestrator-dev recreated;
  tool_service-dev healthy and kept). Before and after:
  `kronk-orchestrator-1` and `kronk-nginx-1` up 45 min, `homeassistant` up
  8 days, `music-assistant-server` up 3 weeks: untouched.
- `GET /dev/work`: 200, 67,101 B, `cache-control: no-store`,
  `<meta name="kronk-page-version" content="c86f8fcebf43">`, equal to the
  tree's `work_api.PAGE_VERSION`.
- `GET /dev/api/work`: 200, 127,198 B, `no-store`, `page_version`
  c86f8fcebf43, `schema_known` 3, `house_zone` America/New_York.
  `?have=1795690678.6944323` (the answer's `ts`): 200, 235 B, `unchanged:
  true`, `work: null`, fresh, `age_s` 14.8, `no-store`.
- `GET /dev/api/voice_runs?limit=0` 200 and `?limit=-1` 400: both
  `no-store` (status only, no run text).
- Tempo (`localhost:3200/api/search`, `q={}`, kept where
  `rootServiceName` is `kronk-orchestrator-dev`; a TraceQL filter on
  `resource.service.name` returned nothing on this Tempo and was not
  used). Control before the rebuild: two `curl` of `/dev/api/work` at T0
  gave exactly two `GET /api/work` traces at T0. Someone's open `/dev/work`
  was also leaving `GET /work` and `GET /api/voice_runs` traces every 30 s
  until the rebuild. After: ten rounds of `/dev/work`, `/dev/api/work`,
  `/dev/api/work?have=1` and `/dev/api/voice_runs?limit=0` (40 requests)
  and one positive control, `/dev/api/work/x` (404, must stay traced).
  Since T1, 36 s later: one trace, the control's (`GET`, the unmatched
  route); none for the three routes.
- `python3 scripts/agent.py list` answers after the exporter's move (one
  line, `m32-build`). The timer's next run, 05:53:56, `Finished`; the file
  is schema 3, `database.state` ok, 38 items, active M32, `house_zone`
  America/New_York.
- Production's `/api/work`, read only: fresh, schema 3, `schema_known` 2,
  "work.json has schema 3, newer than this page's 2: keys added since are
  not shown". That banner is on production's `/work` until step 1 is
  deployed; only `house_zone` is not shown.

## Found in passing

- `test_criterion_2…` failing at `c12db87` (above): fixed in the test,
  no code changed.
- The 1 s tick made the 3 s poll a 4 s one (above): fixed before commit.
- "Heard"'s "as of" (`clockOf`) is still this browser's time zone. The
  brief left "Heard" as it was but for the scheduler, the trim and
  `no-store`. Its `last_error_at` goes through the shared `fmtTime` and is
  now the house's. One line for step 2 or 3 if wanted.

## The suite

```
8472 passed, 3 skipped, 61 xfailed, 6 warnings in 216.12s (0:03:36)
```

Run on the tree at `90d5107`'s content, after the exporter's move. The
run before it, against the scratch exporter, had two failures in
`tests/test_m32_live.py` (the page version): I had edited comments in
`work.html` during that run, after the version was hashed at import. The
two passed on rerun, and the run above is clean.

## State at the end

Tree clean after the report's commit. No unit installed, enabled,
started or stopped; the timer untouched. Production not rebuilt or
restarted; no deploy, no tag. The dev pair rebuilt once. Scratch files
under the session scratchpad only.
