# M30 step C build: the route and the "Heard" section

Suite: every test passes (last lines at the end). Guard rails: none
broken. The live record was not opened; the exporter was not run against
the live paths; no production container rebuilt or restarted; no unit or
timer touched; `scripts/work.py` and `scripts/pipeline_bench.sh` not run.
The dev pair was rebuilt with the exact command of the brief. The real
copies under `data/` were read for their schema and counts only; no
sentence from them is in a file, a test, a log line or this report.

## What was built

| File | What |
|---|---|
| `orchestrator/voice_runs_api.py` | New. `GET /api/voice_runs`. Opens `/data/voice_runs.db` once per request, `mode=ro&immutable=1` (`VOICE_RUNS_DB` overrides). The envelope and the summary from `/data/voice_runs.json` through `servers.read_source` (fresh, stale past 180 s, missing, unreadable, unknown_schema), plus `ok`, `last_ok_at`, `last_error`, `last_error_at` and `warnings`. Newest first, keyset on `(start_s, run_id)`, `limit` default 200, clamped to 500, 0 = status and summary only (the copy is not opened); `before` (`next_before`); `q` bound, `%`, `_` and `\` escaped, `ESCAPE '\'`, over `heard` and `reply`, 200 characters at most; `flagged`; `include_dev`; `changed_after` with `known_rows` and `known_built_s`, answering each changed row with `match` and `resync`. A bad parameter is a 400 with the reason. Each row adds `heard_state` (`text`, `not_recorded`, `nothing_heard`), `reply_head`, `reply_rest`, `flagged`. Imports `datetime`, `os`, `sqlite3`, `pathlib`, `fastapi`, `servers` and nothing else. A plain `def` route, so the SQLite read runs in FastAPI's thread pool |
| `orchestrator/main.py` | Imports and mounts the router beside `work_api`'s. Nothing else |
| `orchestrator/static/work.html` | A `<details class="heard" id="heard">` first in `<main>`, no `open` attribute, set closed again in `heardInit()`. Its own fetch, its own 30 s `setInterval(heardTick, …)`, outside `loadAll`. Folded: `limit=0`. First opening: 200 rows. Then `changed_after` every 30 s, rows patched by `data-run`, dropped when `match` is false, an anchor row kept in place when a row is inserted above it; on `resync` the list is loaded again with open rows and the anchor kept by run id. "show older" with `before`. Filter and switch in `localStorage` (`kronk.work.heard.q`, `.flagged`) through one `try`/`catch` helper. The page's decisions are JSON literals: `HEARD`, `HEARD_BANNER`, `HEARD_ERROR`, `SATELLITE_NAME`. All text through `el()` |
| `scripts/backups.py` | The record (`s.voice_db`) in `required_sources` |
| `tests/conftest.py` | The live-voice-files guard (below) |
| `tests/test_m16_backups.py` | The fixture house holds a stand-in record (it is required now); `voice_record()` replaces it; `no_voice_record()` takes it away. Nine archive tests that list the destination's files, and the clocked stale-check test after its config run, take it away: the weekly copy and the voice stale warning have their own tests in m30 |
| `tests/test_m30_voice_runs.py` | `test_b8_the_record_is_not_yet_required` turned over into `test_b8_the_record_is_required_since_the_first_export` (with the record: OK; without: exit 4, "Kronk backup: config backup INCOMPLETE", the record named). `real_record()` and three no-record tests clear the stand-in first |
| `tests/test_m30_voice_runs_e2e3.py` | The schema-2 restore test clears the stand-in before the exporter makes a real record at the house's path |
| `tests/test_m30_heard.py` | New, 67 tests (below) |
| `docs/features/work-page.md` | The section: what it shows folded and open, the banners, how the route and the patching work, the removal, gotchas, operations, two blog hooks |
| `docs/features/telemetry.md`, `voice-pipeline.md` | One pointer each to the section and the record |
| `docs/features/voice-dataset.md`, `backups.md` | The record is required since the first export; the dataset doc says how the page sees a removal |

`/api/work`, `work.json`, `scripts/work_export.py`, M32's criteria and
CLAUDE.md are not touched.

## The mount

No compose change. Production's orchestrator mounts `./data:/data`, dev's
`./data/dev:/data`; the exporter writes the same copy and status file to
both. The container runs as uid 1000, the copies' owner, so their mode
600 is readable. SQLite in the image is 3.46.1 (row values in `WHERE`
need 3.15).

## The criteria, and the test that shows each

All in `tests/test_m30_heard.py`. Copies are built in `tmp_path` by the
exporter's own `build_copy()` and `publish_ddl()`; one test reads the copy
and status file of a scratch export against M30's fake Tempo. Every
sentence is invented.

- **C1.**
  - Paging: `test_c1_paging_returns_every_row_once_with_two_rows_sharing_a_timestamp` (9 rows, two at the same `start_s`, pages of 2: each once, in order); `test_c1_a_tie_on_the_page_boundary_is_not_dropped` (three rows at one timestamp, the boundary inside them).
  - `q`: `test_c1_q_is_bound_and_its_wildcards_are_literal`, 11 cases: `%`, `_`, `'`, `"`, `<b>`, `100%`, `a_b`, `\`, a word only in the reply, no match, and case. A decoy row `axb 100x` would match `a_b` and `100%` if the wildcards were live. The markup row comes back as stored. `test_c1_a_q_of_sql_is_only_text`, `test_c1_q_longer_than_the_limit_is_refused`.
  - `limit`: `test_c1_limit_out_of_range` (600 → 500, 500, 1, -1 → 400, `abc` → 400, `2.5` → 400), `test_c1_the_default_is_200_rows`, `test_c1_limit_0_answers_the_summary_without_opening_the_copy` (`open_copy` patched to raise).
  - Other parameters: a bad cursor (5 forms), `changed_after` with `before`, a flag that is not 0 or 1: 400. `test_c1_flagged_and_include_dev`.
  - The copy: missing (`test_c1_the_copy_missing`), not a database (`…_not_a_database`), schema 0 without meta (`…_of_an_unknown_schema`), schema 2 with the columns (read, with a note) and without one (`unknown_schema`), meta's row count wrong (`damaged`, rows still served).
  - Replaced mid-request: `test_c1_the_copy_replaced_mid_request_is_read_whole_from_the_old_one`. `copy_meta` is wrapped so that a new copy is renamed in between the meta read and the rows read. The answer is the old copy whole (2 rows, `max_seq` 2); the next request reads the new one.
  - `test_c1_reading_the_copy_makes_nothing_beside_it_and_changes_no_byte`.
  - `changed_after`: new and changed rows with `match` under a filter; a removal (no seq raised) answers `resync`; a removal and an insert in the same export answer `resync`; an insert alone does not; without `known_rows` it resyncs; more than 500 changed resyncs.
  - The status file: fresh, stale at 181 s (summary still given), fresh at 170, missing, unreadable, unknown schema. `test_c1_ok_true_is_not_the_only_signal`: truncated windows, failed trace reads, failed enrichments, rows the hourly check found differing, and a check under way each give a warning; `last_error` is passed with its time while `ok` is true. A failed run, and a lost record.
  - `test_c1_the_three_states_of_heard_and_the_reply_split`, `test_c1_a_blueprint_hand_off_has_no_rule`, `test_c1_no_column_the_copy_does_not_hold_is_asked_for`, `test_c1_the_route_against_a_real_scratch_export`.
- **C2.** `test_c2_api_work_byte_for_byte_what_it_was`: three `work.json` bodies (fresh, stale, newer schema) at a pinned clock, through this tree's app and through `work_api.py` as it stood at `56e9368` (loaded from `git show`). The bytes are equal. `test_c2_work_api_work_export_unchanged_since_base`: `git diff --quiet 56e9368` on both files.
- **C3, without a browser.** First in `<main>` with no `open` attribute, set closed at init. `localStorage` appears twice in the code, both in `store()` inside `try`/`catch`, and every call to `store()` passes one of the two filter keys. `loadAll`'s body names nothing of Heard, and the section has its own `setInterval`. The needles of keyed patching (`data-run`, `changed_after`, `known_rows`, `resync`, `match`, `det.open = wasOpen`, the anchor) are present. No `innerHTML`, `outerHTML` or `insertAdjacentHTML`. The fetch is relative and `no-store`. No `.href` and no Grafana in the section; ids are `<code>`. The banners cover every state the route gives (status and copy). The words "not recorded" and "nothing heard" map to the route's `heard_state`. The summary template carries "as of". The satellite map has three lines. The summary keys the page reads exist in the exporter.
- **C4.** `test_c4_the_module_imports_no_http_client` (an AST walk of the imports against an allow-list); `test_c4_twenty_loads_call_nothing` (20 rounds of the three requests the page makes, with `httpx.AsyncClient`, `httpx.Client`, `urlopen` and `subprocess.run` patched to raise and sockets blocked by `no_network`); `test_c4_the_routes_are_registered`.
- **C5.** The suite, below.

## The conftest guard (a change outside the brief)

The guard step B added fails the run when the live record or a copy or
status file under `data/` changed while the tests ran. Since the timer
went live it rewrites the two status files and the record every 60 s, so
every run longer than a minute failed it. The first subset run here did:
`FAILED: the test run changed the live voice-run record or its copies:
…/voice_runs.db, …/data/voice_runs.json, …/data/dev/voice_runs.json`.
The full suite takes 3 min 24 s. It could not pass with the timer
running, and touching the timer is not allowed.

The fix: a change is the timer's when the file's mtime (the new one, or
the old one for a file now gone) falls inside one of the unit's runs. The
runs come from its user journal, `journalctl --user -u
kronk-voice-runs-export.service -o json --output-fields=MESSAGE`: each
"Starting …" through the next "Finished …" or "Failed …", ±0.2 s. A live
run takes about 0.08 s, and its writes land inside it (status 01:09:31.719,
run 01:09:31.644 to .733). Any other change still fails the run, and so
does every change when the journal cannot be read. That unit logs counts,
never text. Tested in `test_the_guard_reads_the_timers_runs_from_its_journal`.
In the last full run the timer ran three or four times and the guard
passed.

The guard is weaker than it was. A test that wrote a live file inside the
same ~0.1 s as a timer run would pass. The coordinator may want something
else.

## Choices made

- **`last_error`**: shown with its time always. When `ok` is false it is a
  red banner ("The last export run failed (<time>): …"). When a later run
  went well it is a quiet line ("last error <time>: …"), so an old error
  is visible but not alarming.
- **Removal**: noticed by the row count, not `content_sha256`. The sha
  changes on every insert as well, so it cannot tell a removal from one.
  The route counts the rows inserted since the copy the page last saw
  (`first_seen_at` later than that copy's `built_s`, to the second). It
  answers `resync` when the copy's row count is not the page's plus those.
  This holds because an insert and that copy's build are at least one
  timer run apart. A row leaving the 90 days resyncs too (cheap: 200 rows).
- **`changed_after` ignores the filter for selection and returns `match`**,
  so a row that stops matching is dropped, and the filter logic stays in
  Python, not duplicated in the page.
- **Dev runs are not on the page** (the summary line counts household
  runs; the route gives dev with `include_dev=1`). Three dev runs today. A
  switch would be one line if wanted.
- **"nothing heard"** is a third state beside "not recorded": `heard`
  empty with `heard_chars` 0 is Whisper hearing no words (the
  `stt-no-text-recognized` case), not a run traced without text. None in
  the live copy today.
- **Times**: a row's time and day are the house's, from `local_time` and
  `local_day` as written. "as of" is in the browser's time (a wall display
  in the house is the same zone).
- **Satellite names**: kitchen (`…0ac919…`), basement (`…0ab117…`), office
  (`satellite1_d8d1bc`), from the repo's own notes (M17 findings,
  `test_blueprint_handoff.py`).
- **No JavaScript engine on the host** (no node). Firefox is installed,
  but its headless run can contact Mozilla. So the script was checked by
  reading it, and in the browser by nobody yet.

## What a browser is still needed for (C3, the test agent's)

Folded on load and after a reload; the summary line and "as of"; markup
in heard, reply, satellite and rule shown as text; the filter, the switch
and their survival across reloads; an open row and the scroll through
three refreshes with a new run arriving (a fixture published to
`data/dev` with the exporter's `--publish` and `--status`); "not
recorded"; stale (status older than three minutes) and missing banners;
200 rows then "show older"; and C4's twenty loads of `/dev/work` against
the dev orchestrator's traces. The page has not been run in a browser. A
script error would leave `/dev/work` saying "fetching…" in both the Heard
line and the state line.

## The dev check

`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev`: only `kronk-orchestrator-dev-1` was recreated
(`tool_service-dev` "Running", unchanged); the container list was the
same before and after, and no other container's uptime reset. Healthy in
seconds.

- `http://localhost/dev/work`: 200, 43,536 bytes, one `<details
  class="heard" id="heard">`.
- `http://localhost/dev/api/voice_runs?limit=3`: 200. Keys `copy`,
  `max_seq`, `next_before`, `query`, `resync`, `runs`, `source`,
  `summary`. `source`: `voice_runs.json`, fresh, age 10.8 s, max 180, `ok`
  true, schema 1, 0 warnings, no `last_error`. `copy`: ok, schema 1, 113
  rows, `max_seq` 113, built 2026-11-26T06:04:19+00:00. 3 runs, all
  `heard_state` `text`, none flagged; `next_before` set; `resync` false.
  Row fields: `corrected_after_s`, `corrected_by`, `corrected_by_run_id`,
  `correction_kind`, `correction_of_run_id`, `direct_action`, `end_s`,
  `error_code`, `first_seen_at`, `flagged`, `heard`, `heard_chars`,
  `heard_source`, `heard_state`, `intent_ms`, `is_dev`, `kronk_trace_id`,
  `listen_ms`, `local_day`, `local_time`, `mark_basis`, `pipeline`,
  `reply`, `reply_chars`, `reply_head`, `reply_rest`, `response_type`,
  `rule`, `run_id`, `satellite_id`, `seq`, `service`, `stages`,
  `start_s`, `stt_engine`, `tier`, `tools`, `trace_id`, `transcribe_ms`,
  `wake_detection`, `window_cut_short`. `summary.household`: 3 runs in 24
  h, 0 wrong, 0 error.
- Paged at 50 with `include_dev=1`: 3 pages, 113 rows, 113 distinct.
  Household only, `limit=500`: 110 rows, 68 `text` and 42 `not_recorded`
  (step B's 71 with text less the 3 dev runs; the 42 are step D's), 3
  flagged. `changed_after=113` with the copy's `rows` and `built_s`: 0
  rows, no resync. `limit=0`: 200. `limit=-1`: 400, `{"error":"limit must
  be 0 or more, not -1"}`.
- `data/dev/` after the checks: `voice_runs.db` (mtime 01:04, unchanged)
  and `voice_runs.json`; no `-wal`, `-shm` or `-journal`.

Production was not rebuilt: `/api/voice_runs` and the section reach it
at the next tagged deploy.

## Suite

```
8304 passed, 3 skipped, 54 xfailed, 6 warnings in 204.29s (0:03:24)
```

(from 8237 passed: 67 new in `test_m30_heard.py`; the turned-over B8 test
is one for one.)
