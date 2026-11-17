# M12 fix 1 report: capture what was said

Fix agent, 2026-11-17, branch `refactor`, fix round 1. Inputs:
`docs/sprint/M12-criteria.md`, `docs/sprint/M12-build.md`,
`docs/sprint/M12-e2e.md` (blocks 1-6, must-know 1-11, observations) and the
coordinator's rulings F1-F10. Commits:

| Commit | Fixes |
| --- | --- |
| `2ceb7dc` | F1, F3 |
| `809fa81` | F2, F4, F5 |
| `7e34f7f` | F7 |
| `9f08da2` | F8, F9 |
| `b6ed6a8` | F10 (docs) |
| this commit | F10 (this report, runbooks), replay result file |

F6 is a ruling with no code change (documented below and in
`docs/features/voice-capture.md`).

## Summary

- **The text switch, off, now holds where the tester found it open.**
  Client spans lose their query string in both services (the hook is passed
  as `async_request_hook`); the shopping-list item in
  `DELETE /shopping_list/{item}` is masked in spans and log lines in both
  services; genre terms, HA's play-failure message and the model's broken
  tool-call fragment go through `telemetry.text`. Live on dev with the switch
  off: 0 marker hits in 4 traces / 97 spans and in every log line of the dev
  pair, `kronk-voice_tracer-1` and nginx. With it on: markers present in
  both.
- **Named exceptions** with the switch off: failure detail behind a friendly
  spoken error (ruling 1), exception text (ruling F6), and, known and not
  fixed here, a fetched page's own URL path, nginx's access log, flag-style
  query strings in log lines.
- **voice_tracer no longer loses or late-exports a held run on a stop or an
  HA disconnect.** SIGTERM is handled (`docker stop` 0.40 s, was 10.09 s);
  held runs leave at once with `voice.window_cut_short`; runs older than
  840 s are never exported (`/health too_old`). Loss now needs a hard kill or
  host crash inside the window (`/health held_lost`).
- **Labelling sheet:** a contested join leaves `heard` empty and says
  `ambiguous join` in `notes`. `--out` inside the recordings is refused; the
  listing pages a full search and exits 3 if a minute-wide window is still
  full; the M9 bench tolerates a null preview; the replay reads `true` as the
  services do.
- **Tests:** `4711 passed, 3 skipped, 53 xfailed` (baseline 4646 / 3 / 53;
  65 new tests).
- **Replay: incomplete.** It stopped itself after 29 of 160 rows because a
  TV (`media_player.xbr_49x800d`) went `off` → `unavailable` at 21:11:01 UTC
  during the run. Per the brief it was not retried. The 29 rows match the M10
  baseline apart from one known flapper.
- **Production, HA, MA, pucks:** unchanged. Only the dev pair was rebuilt.

## The fixes

### F1. Outgoing-request spans keep the query string (block 1)

What changed. `orchestrator/telemetry.py:125-127` and
`tool_service/telemetry.py:205-207` pass the hook as
`async_request_hook=_strip_url_attrs_async` as well as `request_hook`.
`_strip_url_attrs_async` (`orchestrator/telemetry.py:241`,
`tool_service/telemetry.py:155`) is a coroutine wrapper of the same
function; `opentelemetry-instrumentation-httpx` 0.66b1 drops an
`async_request_hook` that is not a coroutine function.

Test that would have caught it.
`tests/test_m12_fix1_text.py::test_async_client_span_through_the_instrumentation`
(`[off|on]-[orchestrator|tool_service]`). Each service's own
`instrument_app()` installs the instrumentation; a throwaway FastAPI app
makes outgoing calls through an `httpx.AsyncClient` on httpx's real
`AsyncHTTPTransport` (the class the instrumentation patches) over a fake
connection pool, reached through `httpx.ASGITransport`. No sockets. Off:
`http.url` is `http://searxng:8080/search?<query>`; on: whole. The `httpx`
log lines are checked the same way. Mutation check: removing the async hook
from either service fails that service's `off` case; making `mask_path` a
no-op fails three tests per service.

Live evidence (dev, switch off). Request `search the web for quorvexilant
recipes` on `/dev/api/chat` and `fetch https://example.com/?q=brindlewomp
and summarize it`. Tempo, client spans:
`kronk-orchestrator-dev GET http.url http://tool_service-dev:8103/search?<query>`,
`kronk-tool-service-dev GET http.url http://searxng:8080/search?<query>`,
`kronk-orchestrator-dev GET http.url http://tool_service-dev:8103/fetch?<query>`,
`kronk-tool-service-dev GET http.url https://example.com/?<query>`. Logs:
`INFO:httpx:HTTP Request: GET http://searxng:8080/search?<query> "HTTP/1.1 200 OK"`.
Switch on, markers `vandermoskil` and `threnquibbet`: the same spans read
`http://searxng:8080/search?q=Vander%20Moskil%20recipes&format=json&categories=general&language=en`
and `https://example.com/?q=threnquibbet`.

### F2. Genre terms in tool_service's log (block 2)

What changed. `tool_service/main.py:1891-1893`: `terms` and `rule` (the
household's standing rule, stated taste) through `telemetry.text`.
Neighbouring lines checked: the dry-run line (`:1233`) and the genre pick
line (`:1948` before this fix, now `:1972`) were already gated; the
`music decision` line logs the outcome shape only.

Test. `tests/test_m12_fix1_text.py::test_genre_request_log_line_terms[off|on]`:
off, the line is exactly
`genre request None -> terms None rule None: 0 picks, first []`.

Live (dev, switch off, `play some plinthargow jazz` on `/dev/voice/api/chat`):
`INFO:tool_service:genre request None -> terms None rule None: 103 picks, first [Pick(artist 'De-Phazz' 1.13), ...]`.
On (`play some glorpendash jazz`):
`genre request 'glorpendash jazz' -> terms ['glorpendash jazz'] rule None: ...`.

### F3. Words in a URL path (block 3)

Routes and URLs checked. Every route in `tool_service/main.py` (38): one
carries words in its path, `DELETE /shopping_list/{item}` (`:689`). Every
URL the orchestrator's tools build (`orchestrator/tools.py:530-1004`, 24
calls): one, `DELETE {TOOL_SERVICE_URL}/shopping_list/{item}` (`:595`);
`/shopping_list/clear` is a fixed word. The orchestrator's own routes: one,
`DELETE /files/{filename}` (`orchestrator/main.py:1431`, the web chat's
upload list; its server span is already excluded, its access line was not).
Outgoing calls with variables in the path that are not words: HA
`/api/states/{entity_id}` (entity ids), NWS `/points/{lat},{lon}`
(coordinates). Not a route and not built by the tools: the page URL a
`/fetch` or `/http_check` follows (exception 3 below).

What changed. Same rule, same helper, both services:
`_PATH_TEXT` (`orchestrator/telemetry.py:186`, `tool_service/telemetry.py:101`),
`mask_path` / `strip_url_text` (`:205`, `:213` / `:119`, `:127`); the span
hook masks the path part of `http.target`, `http.url`, `url.full` and (new
in the list) `url.path`, and appends `?<query>` only when there was a
query; `_clean_arg` (`:263` / `:177`) applies both to any log argument
with `?` or `/`. The route and the request are unchanged.

Tests. `test_async_client_span_through_the_instrumentation` (the client span
and `httpx` line read `/shopping_list/<item>`, `/shopping_list/clear` is
kept), `test_tool_service_server_span_for_a_shopping_list_item[off|on]`
(server span `http.target` `/shopping_list/<item>?<query>`, name
`DELETE /shopping_list/{item}`), `test_access_line_with_a_shopping_list_item`,
`test_orchestrator_access_line_with_an_upload_name`.

Live (dev, switch off, `remove zembrovatch from the shopping list`; the
item is not on the dev list, so the 404 path ran and nothing was written):
orchestrator `HTTP Request: DELETE http://tool_service-dev:8103/shopping_list/<item> "HTTP/1.1 404 Not Found"`;
tool_service access line `"DELETE /shopping_list/<item> HTTP/1.1" 404 Not Found`;
Tempo `kronk-orchestrator-dev SPAN_KIND_CLIENT DELETE http.url http://tool_service-dev:8103/shopping_list/<item>`,
`kronk-tool-service-dev SPAN_KIND_SERVER DELETE /shopping_list/{item} http.target /shopping_list/<item>`
and `http.url http://tool_service-dev:8103/shopping_list/<item>`. On
(`skivwaddle`): `/shopping_list/skivwaddle` in all four places.

### F4. HA's play-failure message (block 4)

The quoted name could not be separated from HA's message (free text from HA
and MA, varying by provider), so the message is gated and a fixed-vocabulary
kind is kept.

What changed. `tool_service/main.py:1242` `failure_kind(message)`:
`resolve_miss`, `ha_unreachable`, `ha_websocket`, `ha_auth`, `ha_error`.
`music.play_verified` (`:1779-1780`): `music.failure_kind` always,
`music.failure` only with the switch on; `music.outcome` unchanged (the
dashboards read it). Log lines: `ha_call_service` (`:912-913`, HA's error
`code` and the kind kept, the message gated), a failed call that started
anyway (`:1819-1820`), a genre pick that did not start (`:1938-1939`,
`no_start` when there is no message). The spoken sentence is unchanged.

Tests. `test_play_verified_failure_kind_without_the_name[off|on]` (exported
span: `music.outcome` and `music.failure_kind` `resolve_miss`; off, no
`music.failure`, no marker anywhere), `test_failed_but_started_log_line`,
`test_genre_pick_did_not_start_log_line`, `test_ha_call_service_failure_log_line`
(through the scripted HA websocket of `test_error_surfacing.py`; the raised
error still quotes the name), `test_failure_kind_vocabulary`. Against the
pre-fix `tool_service/main.py` all off cases fail.

Live: not reachable on dev. A play failure needs HA to be asked to play, and
dry-run stays on. Unverified until promotion; covered offline.

### F5. The model's tool-call fragment (block 5)

What changed. `orchestrator/llm.py:11` imports `telemetry`; both warnings
(`:138-142`, `:192-195`) log the fragment's length and the fragment only
through `telemetry.text(..., 200)`.

Tests. `test_llm_complete_bad_arguments_warning[off|on]` and
`test_llm_stream_bad_arguments_warning[off|on]` (LiteLLM stubbed with
`httpx.MockTransport`; off, `Fragment (35 chars): None`).

Live: not reachable on dev without making the model emit broken JSON. Covered
offline.

### F6. Exception text (observation; ruling)

No change. Exception text stays with the switch on or off:
`agent_llm_error.error`, `Agent '<x>' stream failed`, span status
descriptions (`kronk.error` spans, tool_service's `span()`), the
`exception.message` span events, and the exception argument of error log
lines. It carries the request's words only when an exception or a LiteLLM
error quotes them. Documented in the table below and in
`docs/features/voice-capture.md`, "With the switch off".

### F7. A held run lost or exported late (block 6, must-know 3)

What changed, `voice_tracer/main.py`:

- `main()` (`:840-848`) installs SIGTERM and SIGINT handlers that set a stop
  event, and calls `serve()` (`:856`), which replaces the old reconnect loop.
- `serve()`: runs `run_once()` until the stop event or a disconnect. On a
  disconnect (any exception out of `run_once`) it calls
  `cut_short("ha_disconnected")` and then `idle()` for the backoff, which
  keeps the Kronk join going against Tempo (`:667`). On the stop it calls
  `shutdown()` (`:676`): one last join attempt for runs still joining
  (`resolve_joins(final=True)`, `:481`, budget `SHUTDOWN_JOIN_S` = 2 s,
  `:124`), then `cut_short("shutdown")`, a state save, and an exporter
  flush.
- `cut_short(reason)` (`:642`): every held run is exported now with whatever
  mark it has, `voice.window_cut_short` = `shutdown` | `ha_disconnected`,
  recorded as emitted; runs still joining are flagged and exported the
  moment their join finishes (`_finish_voice`, `:559`). `/health cut_short`
  counts them.
- Late corrections across a restart: the facts of a cut-short run (no
  transcript) are written to the state file under `cut` (`_cut_facts`,
  `:368`) and read back at start (`:335`), so a correction that arrives
  after the export, before or after a restart, is logged and counted in
  `marks_late`. They are pruned with the other facts after
  `AGE_LIMIT_S + WINDOW_S`.
- Export age: `_emit` (`:708`) refuses any run, voice or blueprint, whose
  first span is older than `EXPORT_AGE_MAX_S` = 840 s (`:121`; Tempo's slack
  is 900 s, the batch delay 5 s, the exporter's deadline 10 s). The run is
  recorded as seen (nothing fetches it again), logged at WARNING with its key
  and age, and counted in `/health too_old`.
- The `held_lost` warning now names the causes it can have (a hard kill or
  crash, then HA restarted, its last-10 limit, or the age).

What the code does with a run that is too old when it is finally seen: it
does not export it; it marks it seen, logs
`pipeline:<run id> not exported: its first span is <n>s old, past the 840s export limit (Tempo's ingestion slack is 15 min)`,
and adds one to `too_old`. Which paths can still reach that: a held run after
a hard kill whose hold, with a later run still joining, outlasts 840 s from
its start (only for a run longer than about two minutes, since the hold caps
at 720 s after the end), or a blueprint run listed late. In the normal and
cut-short paths a run leaves well inside the limit.

After this fix the only way to lose a held run is a hard kill (SIGKILL, OOM,
`docker kill`) or a host crash inside its window, and then only if HA no
longer lists the run when voice_tracer comes back (HA restarted, more than 10
newer runs on the pipeline, or older than 660 s). `/health held_lost` counts
it and the log names the run id. Stated in `docs/features/voice-capture.md`
and `docs/features/telemetry.md`.

Tests, `tests/test_m12_fix1_tracer.py` (18). They drive the real `serve()`
loop with a fake HA websocket (keeps the last 10 runs per pipeline, forgets
them on restart, sends the satellite's idle event at a run's end), a fake
clock advanced by every wait the loop makes, stubbed Tempo (a Kronk trace
searchable 20 s after the run) and an exporter that records when each span
left. E is the recorded Kronk-answered run (`Play French Dinner Radio.`).

| Test | Sequence | Result |
| --- | --- | --- |
| `test_sequence_a_stop_then_ha_restarts_in_the_gap` | stop at E.end+100 s, HA restarted at +110, back at +130 | E once, by +103, joined, `window_cut_short=shutdown`, `held_lost` 0 |
| `test_sequence_b_stop_then_twelve_more_runs` | stop at +100, 12 runs from +105, back at +160 | E once by +103; each of the 12 at most once |
| `test_sequence_c_long_stop[630,720]` | stop at +100, down 630 or 720 s | E once by +103 |
| `test_ha_outage_1200s_exports_the_held_run_at_once` | HA-local E, websocket down from +30 for 1200 s | E once by +34, `ha_disconnected`; oldest export under 60 s |
| `test_ha_and_tempo_down_while_the_run_is_still_joining` | HA down from +10 for 1200 s, Tempo down | E once by +75, `kronk.join=none` |
| `test_ha_drop_while_joining_keeps_the_join` | HA down from +5, Tempo up | E once by +33, joined |
| `test_correction_after_a_stop_is_counted_late_across_the_restart` | stop at +10, a Kronk "stop" at +20 while down, back at +40 | each once; state file `cut` holds E without transcript; `marks_late` 1 |
| `test_a_run_past_the_export_age_is_counted_not_sent` | hard kill at +60, back 500 s later, held to a stop at +1200 | not exported, seen, `too_old` 1, not fetched again |
| `test_emit_refuses_a_run_older_than_the_limit` | `_emit_run` at 841 s and 839 s | refused and counted; exported |
| `test_limits_stay_inside_tempos_slack` | constants | `AGE_LIMIT_S < EXPORT_AGE_MAX_S < 885`; shutdown budget under 10 s |
| `test_a_stop_at_any_point_exports_each_run_once_and_in_time[3,45,200]` | E, an HA-local "Stop." 20 s later; stop at every 8 s from +2 to +258, back after 3/45/200 s | 99 cases: each run exactly once, inside the limit; `held_lost` 0; E marked `stop` when the stop came after the correction was classified; joined unless stopped before Kronk's trace was searchable |
| `test_a_hard_kill_at_any_point_never_exports_twice[3,45,200]` | same, hard kill | 99 cases: never twice; each run once (HA still lists them) |
| `test_sigterm_sets_the_stop_and_main_returns` | `main()` with SIGTERM raised once the handler is confirmed | stop event set, handler removed at loop close |

Mutation check on the new paths (each alone, against the voice_tracer test
files): no cut at an HA drop (3 tests fail), no cut at a stop (6), no age
guard (2), cut facts not restored (1), no joins while HA is away (2), no last
join at a stop (3), the cut flag ignored when a join finishes (1). All
killed. Old tests that flushed at a run's end + 1000 s now flush at + 300 s
(`tests/test_voice_marks.py`, three tests), and two service-name tests that
emit a days-old recorded run lift the limit (`tests/test_voice_tracer.py`).

Live. A scratch image built as compose builds it (`docker buildx build
--build-context orchestrator=./orchestrator -t m12-fix1-vt-scratch
./voice_tracer`): `marks`, `command_forms`, `convert` and `main` import as
uid 1000 with `--network none`; md5 sums equal the tree's
(`main.py de74ee3e…`, `marks.py ffbe5c5c…`, `convert.py 24f64595…`,
`command_forms.py e8e529cb…`). The same image run with `--network none`,
`HA_TOKEN=x`, `TELEMETRY_ENABLED=false`: `docker stop took 0.40 s`, exit code
0, last log line `voice_tracer: stopped: 0 held run(s) exported at the stop`.
Container and tag removed. Production `kronk-voice_tracer-1` was not touched.

### F8. The labelling sheet's silent wrong join (must-know 6)

What changed. `scripts/voice_label_sheet.py`: `_candidates` (`:117`; same
pipeline, an STT stage when the run states its stages, same device when both
know it, the mtime window), `_best_pairings` (`:136`; every one-to-one
pairing of a group's recordings with their candidates that pairs the most
recordings, cut off at 20,000 steps), `join` (`:171`; groups are
recordings linked through a shared candidate). A recording takes a run only
when every best pairing gives it that run; when they disagree, or the search
is cut off, `heard` and the run's fields stay empty and `notes` reads
`ambiguous join: candidate runs <ids>; <n> other recording(s) fit the same
run(s); heard left empty`. A certain join that rests on time and pipeline
alone says `joined by time and pipeline only (no device id on one side)`.
The manifest gains `recordings_ambiguous`. The outcome no longer depends on
the order directories sort in. The CSV columns are unchanged.

Tests, `tests/test_m12_fix1_scripts.py`, the tester's exact inputs (seconds
from T0):

| Case | Result |
| --- | --- |
| Same puck, A 0-20 (WAV 8.5), B 21-23 (WAV 22.9), both traced | A→A, B→B |
| A 0-30 (WAV 9), B 31-33.2 (WAV 33) | A→A, B→B |
| Two pucks, simultaneous, both with device ids | each to its own |
| A has no trace, A's WAV 3.0; B 4.5-6.6, B's WAV 6.5; directory names in time order and against it (`99999999999` / `100000000000`) | both rows `ambiguous join: candidate runs B; 1 other recording(s) fit the same run(s); heard left empty`, `heard` empty, device kept; same either order |
| Same, B starts 2.5 s after A's WAV closed (5.5-7.6, WAV 7.5) | A `no trace`, B→B |
| A device-less run R is the only candidate for its puck's recording and another puck's untraced one | both ambiguous |
| Same, the other puck's run traced | R→its recording with the time-only note, the other→its run |
| Three recordings with mtimes rewritten to one run's moment | all three ambiguous |
| Host clock stepped back 10 s | `no trace` |
| A text run (no STT stage) and a voice run in range | joined to the voice run |

Against the pre-fix script 9 of the file's 23 tests fail. The build's
`tests/test_m12_scripts.py` passes unchanged.

### F9. Output path, search limit, bench, replay check (must-know 2, 10, 11)

- `scripts/voice_label_sheet.py:258` `refuse_inside_recordings`: `--out`
  equal to or inside the recordings directory (symlinks resolved) is refused
  before anything is written. Test
  `test_sheet_refuses_out_inside_the_recordings` (three paths and one through
  a symlink; the recordings tree is unchanged).
- `scripts/voice_transcripts.py:150` `_search`: a search returning
  `SEARCH_LIMIT` (5000) traces is halved and repeated down to
  `MIN_WINDOW_S` (60 s); a minute-wide window still full is warned on stderr
  and listed in `truncated`; `main` writes what it read and returns 3
  (`:285`). The label sheet passes the same list through and returns 3
  (`voice_label_sheet.py:354`). Tests `test_listing_splits_a_full_search`,
  `test_listing_warns_and_exits_nonzero_when_the_smallest_window_is_full`,
  `test_label_sheet_exits_nonzero_when_tempo_was_truncated`.
- `scripts/m9_lock_bench.py:140`: `(e.get('text_preview') or '')[:40]`.
  Test `test_m9_lock_bench_tolerates_a_null_preview`.
- `scripts/voice_replay.py:714-718`: `require_text_logs` takes the last
  `TELEMETRY_RECORD_TEXT=` value and accepts it when `.lower() == "true"`,
  the services' rule. Test
  `test_replay_text_check_reads_true_as_the_services_do` (`true`, `TRUE`,
  `True` accepted; `false`, `1`, empty, missing refused).

### F10. Docs and runbooks

- `docs/features/voice-capture.md`: a section "With the switch off: what is
  covered, and what is not" with the exceptions; the hold, cut short at a
  stop or an HA disconnect, the loss paths with `held_lost`, the age limit
  with `too_old`; the label sheet's certain-only join and `notes` values;
  `--out` refusals; the listing's exit 3; the compose rule (below); turning
  transcripts off through `deploy_tag.sh`; the bind mount after the end
  date.
- `docs/features/telemetry.md`: the switch-off claim names its exceptions;
  "Never more than 12 min" replaced by what is true now.
- `docs/TELEMETRY_GUIDE.md`: `music.failure_kind`; the switch-off claim.
- `README.md`: the switch-off claim.
- The production and end-date runbooks below supersede the build report's.

## Every text-carrying field, switch off and on (after fix 1)

| Service | Field | Off | On |
| --- | --- | --- | --- |
| orchestrator | `kronk.input` / `kronk.output` on `pipeline.*`, `routing.decide`, `agent.*`, `llm.*`, `tool.*` | absent (`*_chars` kept) | the text (≤ 500 chars; on `llm.*` that is the start of the message list) |
| orchestrator | `http.url`, `http.target`, `url.full`, `url.path`, `url.query` on server and client spans | query → `?<query>`; `/shopping_list/<item>`, `/files/<name>` | unchanged |
| orchestrator | events `request.text_preview`, `route.text_preview`, `round_content_retracted.preview`, `voice_stream_retract_unhonoured.preview`, `escalate.note`, `tool_syntax_leak.fragment`, `mm_update_refused.said` | `null` | the text |
| orchestrator | log "terminal tool result had no speech mapping" | length, text `None` | the text |
| orchestrator | log "tool-call arguments were not valid JSON" (F5) | length, fragment `None` | the fragment (≤ 200) |
| orchestrator | `httpx` "HTTP Request:" and `uvicorn.access` lines | `?<query>`, `<item>`, `<name>` | unchanged |
| orchestrator | `kronk.failure.debug_text`, event `failure_spoken_friendly.debug` | **kept (exception 1)** | kept |
| orchestrator | `agent_llm_error.error`, "Agent '<x>' stream failed", span status descriptions, `exception.message` events, exception arguments of error log lines | **kept (exception 2)** | kept |
| tool_service | `music.media_id` on `music.dry_run` | absent | the media id |
| tool_service | `music.failure` on `music.play_verified` (F4) | absent; `music.failure_kind` and `music.outcome` kept | HA's message (≤ 200) and the kind |
| tool_service | `http.url`, `http.target`, `url.*` on server and client spans | `?<query>`, `/shopping_list/<item>` | unchanged |
| tool_service | client spans and `httpx` lines of a `/fetch` or `/http_check` hop | host and path of the fetched page kept, query `?<query>` (**exception 3**) | whole URL |
| tool_service | logs: music request, station name said, normalized query and media id, voice hand-off, genre request text, **genre terms and rule (F2)**, genre pick phrase, music search text, resolver text, library lookup name, station lookup query, shadow failure said, stated taste value and meaning, dry-run reply | `None` | the text |
| tool_service | logs carrying HA's play-failure message (F4: `HA <domain>.<service> failed`, "reported failure but … started playing", "genre pick … did not start") | `code=`, `kind=` kept, message `None` | the message too |
| tool_service | log "music decision {…}" | outcome shape only | the full JSON |
| tool_service | `httpx` and `uvicorn.access` lines | `?<query>`, `<item>` | unchanged |
| tool_service | exception text in error logs and span status (`span()` sets `Type: message`, ≤ 200) | **kept (exception 2)** | kept |
| voice_tracer | `voice.transcript`, `voice.speech` on `voice.run`; `voice.speech` on `voice.intent` | absent (`*_chars` kept) | the text |
| voice_tracer | `bp.media_name`, `bp.area_or_player` on `blueprint.run` | absent | the text |
| voice_tracer | state file `cut` facts (new) | never a transcript | never a transcript |
| nginx | access log request lines (journald) | **kept (exception 4)**: paths through nginx, `/files/<name>` from the web chat included | same |

Exceptions, named:

1. Failure detail behind a friendly spoken error (ruling 1, the build).
2. Exception text wherever it goes (ruling F6).
3. A fetched page's own host and path on tool_service's client spans and
   `httpx` lines. Seen live with the switch off:
   `https://www.quorn.us/recipes/all` (a page the model chose from search
   results). Not one of Kronk's routes and not a URL the tools build, so
   ruling F3's rule does not reach it. Gating it means masking the path of
   every external client span, or a per-request context in the fetch
   handler; for the coordinator.
4. nginx's access log. Outside both services; for the coordinator.
5. The log filter leaves a query with no `=` or with a bracketed key in log
   lines (spans strip everything after `?`). No Kronk URL has either.
6. Not text by definition, on in both: catalog names (`music.candidates`,
   picks in the genre lines), entity ids, rule names, `kronk.direct_action`,
   `music.failure_kind`, HA error codes, `voice.window_cut_short`.
7. Stores outside Tempo and container logs that take text whatever the
   switch says (the build's stores table): the M11 shadow log, the dev
   session store, the taste DB.

## Production runbook (coordinator, at promotion)

Supersedes the build report's. `$HA_TOKEN` from `.env`.
`D=~/backups/ha/m12-$(date +%F)`. `<tag>` is the M12 tag the coordinator
makes.

**Rule: no `docker compose` command is typed in the working tree against the
production Kronk stack (project `kronk`).** Production runs from a release
directory (`scripts/deploy_tag.sh`); `.env` reaches it through the release's
link and takes effect only through `scripts/deploy_tag.sh deploy <tag>`. A
`docker compose up` in the repo recreates the named services, and `searxng`
with them, from the working tree and moves production off the tag
(M12-e2e must-know 1). Home Assistant's stack (`docker-compose.ha.yml`,
project `kronk-ha`) is not deployed from tags; its commands below are typed
in the repo.

**T. Transcripts on (the `.env` line goes in before the deploy)**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| T0 | `grep -c '^TELEMETRY_RECORD_TEXT' .env; tail -c1 .env \| od -c` | `0`; the file ends in `\n` (so the append lands on its own line) | none |
| T1 | `printf 'TELEMETRY_RECORD_TEXT=true\n' >> .env` | `grep -c '^TELEMETRY_RECORD_TEXT=true$' .env` → `1`. Nothing is running with it yet: it takes effect at T2 | `sed -i '/^TELEMETRY_RECORD_TEXT=true$/d' .env` (before T2: nothing else to undo) |
| T2 | `scripts/deploy_tag.sh plan <tag>`, read it, then `scripts/deploy_tag.sh deploy <tag>` (builds orchestrator, tool_service, voice_tracer from the tag, restarts nginx, checks `/api/status` and the `/api/chat` shim) | `scripts/deploy_tag.sh status` → every kronk container `tag <tag>`; `for c in kronk-orchestrator-1 kronk-tool_service-1 kronk-voice_tracer-1; do docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $c \| grep '^TELEMETRY_RECORD_TEXT='; done` → `=true` three times; `curl -s http://localhost/probe/voice_tracer` has the keys `held`, `cut_short`, `too_old` | remove the line (T1's undo) and `scripts/deploy_tag.sh deploy <tag>` again; to roll the code back, deploy the previous tag (`sprint-M9`) after removing the line (a pre-M12 tag keeps text on in the orchestrator and voice_tracer while the line is there) |
| T3 | (the next spoken request; **wait five minutes after it**: 150 s hold, up to 5 s batch, 30-90 s Tempo search lag) | TraceQL `{ name = "voice.run" && span.voice.transcript != nil }` over the last 15 min returns it; `python3 scripts/voice_transcripts.py --since 1h --voice-only` shows `heard` filled and exits 0; `curl -s http://localhost/probe/voice_tracer` shows `connected: true`, `held: 0`, `too_old: 0` | none |

**H. Audio capture on (one HA restart)**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| H1 | `mkdir -p $D && docker cp homeassistant:/config/configuration.yaml $D/configuration.yaml` | `git show b50834c:ha/configuration.yaml \| diff - $D/configuration.yaml` → empty (the live file is M17's) | none |
| H2 | `mkdir -p ~/kronk-voice-recordings && chmod 755 ~/kronk-voice-recordings` | `ls -ld ~/kronk-voice-recordings` → `drwxr-xr-x drew drew`; `ls -A ~/kronk-voice-recordings` → empty | `rmdir ~/kronk-voice-recordings` (only while empty, and only with H7 undone: the compose file's mount needs it) |
| H3 | `docker tag ghcr.io/home-assistant/home-assistant:stable ghcr.io/home-assistant/home-assistant:2026.5.4` | `docker image inspect ghcr.io/home-assistant/home-assistant:2026.5.4 -f '{{.Id}}'` equals `docker inspect homeassistant -f '{{.Image}}'` (`sha256:ceb1202133a5…5716`) | `docker rmi ghcr.io/home-assistant/home-assistant:2026.5.4` (the tag only) |
| H4 | `docker cp ha/configuration.yaml homeassistant:/config/configuration.yaml && docker exec homeassistant chown root:root /config/configuration.yaml` | `docker exec homeassistant cat /config/configuration.yaml \| diff - ha/configuration.yaml` → empty. From here the key takes effect at the next HA start, whatever causes it | `docker cp $D/configuration.yaml homeassistant:/config/configuration.yaml` and the same chown |
| H5 | `curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/config/core/check_config` | `"result":"valid"`. Anything else: H4's undo, stop | none |
| H6 | `curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states \| jq -r '.[] \| select(.entity_id \| startswith("assist_satellite.") or startswith("media_player.") or (startswith("select.") and (contains("_assistant") or contains("_wake_word")))) \| "\(.entity_id) \(.state) \(.last_changed)"' > $D/states-before.txt` | every `assist_satellite.*` is `idle`; any `media_player` `playing` → pick the moment with Drew; every `select.*` line's `last_changed` is older than 15 minutes: `awk -v t=$(date -u -d '15 min ago' +%FT%T) '/^select\./ && $3 > t' $D/states-before.txt` → empty | none |
| H7a | Nothing held by voice_tracer, and no spoken request in the last five minutes | `curl -s http://localhost/probe/voice_tracer \| jq '.held'` → `0`; `docker logs --since 5m kronk-voice_tracer-1 2>&1 \| grep -c 'voice run .* held for the correction window'` → `0`. If either is not, wait five minutes and look again. (With fix 1 an HA restart no longer loses a held run, it exports it at once with `voice.window_cut_short=ha_disconnected`; waiting keeps its correction window whole.) | none |
| H7b | Re-run H2's and H3's checks now | both as written; if either fails, stop: with the directory missing Docker refuses to create HA (`create_host_path: false`) after HA was stopped, and with the tag missing compose pulls from ghcr | none |
| H7 | `docker stop -t 120 homeassistant && docker compose -f docker-compose.ha.yml up -d homeassistant` (the 120 s timeout because the old container still has Docker's 10 s default; this is the one restart) | `docker inspect homeassistant -f '{{.Config.Image}} {{.Image}} {{.Config.StopTimeout}}'` → `…:2026.5.4 sha256:ceb12021… 120`; `docker inspect homeassistant -f '{{json .Mounts}}'` lists `/home/drew/kronk-voice-recordings` → `/voice_recordings`, RW; `docker events --since 10m --until 0s --filter container=homeassistant --filter event=kill` → signal 15 only (no 9); HA's log has no "could not validate that the sqlite3 database … was shutdown cleanly" | H4's undo, then `docker stop -t 120 homeassistant && docker compose -f docker-compose.ha.yml up -d homeassistant`; to drop the compose change too, `git revert` it first |
| H8 | (wait for HA) | `curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/` → `200`; H6's command into `$D/states-after.txt`, compared with before on the first two columns (`diff <(cut -d' ' -f1,2 $D/states-before.txt) <(cut -d' ' -f1,2 $D/states-after.txt)`): the three satellites (`home_assistant_voice_0ac919`, `satellite1_d8d1bc`, `home_assistant_voice_0ab117`) not `unavailable`, the six slot-2 selects still `kronk-dev` / `Hey Jarvis`, players as before; `python3 scripts/voice_replay.py --check-only --out $D/replay-check.json` passes (pipelines `kronk` and `kronk-dev`, devices, areas; it writes that one file, outside the repo, and refuses if the dev pair's text switch is off); `curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services \| jq '.[] \| select(.domain=="rest_command") \| .services \| keys'` lists the commands as before; `docker ps` shows `music-assistant-server` with its old start time; `curl -s http://localhost/probe/voice_tracer \| jq '{connected, held, cut_short, too_old}'` → connected, `too_old` 0 | as H7 |
| H9 | (the next spoken "Okay Nabu" request) | `find ~/kronk-voice-recordings -name '01_stt-*.wav' -newermt "<H7 time>"` → one file under `<device_id>/kronk/<n>/`; `python3 -c "import wave,sys; w=wave.open(sys.argv[1]); print(w.getframerate(), w.getsampwidth(), w.getnchannels())" <file>` → `16000 2 1`; log the capture start date | none |

**R. Reminder timer (same day as H9)**

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| R1 | `END=$(date -d '+14 days' +%F); sed "s/@END_DATE@/$END/" systemd/kronk-voice-capture-check.service > ~/.config/systemd/user/kronk-voice-capture-check.service; cp systemd/kronk-voice-capture-check.timer ~/.config/systemd/user/; systemctl --user daemon-reload; systemctl --user enable --now kronk-voice-capture-check.timer` (the `sed` also fills the placeholder in the unit's comment: cosmetic) | `systemctl --user cat kronk-voice-capture-check.service \| grep VOICE_CAPTURE_END` → the date 14 days from the start; `systemctl --user list-timers kronk-voice-capture-check.timer` shows the next 09:00 | `systemctl --user disable --now kronk-voice-capture-check.timer; rm ~/.config/systemd/user/kronk-voice-capture-check.{service,timer}; systemctl --user daemon-reload` |
| R2 | `systemctl --user start kronk-voice-capture-check.service` | `journalctl --user -u kronk-voice-capture-check.service -n 5` → `before the end date <END>; nothing to do`; no push | none |
| R3 | `VOICE_CAPTURE_END=$END python3 scripts/voice_capture_check.py --test-push` (the one real test push) | Drew's phone shows "Kronk: TEST voice capture reminder"; exit 0 | none |

## End-date runbook (coordinator, from the reminder)

| Step | Command | Check | Undo |
| --- | --- | --- | --- |
| E1 | In the repo, comment the two `assist_pipeline` lines of `ha/configuration.yaml` again; commit | `python3 -c "import sys; sys.path.insert(0,'scripts'); import voice_capture_check as c; print(c.capture_configured(open('ha/configuration.yaml').read()))"` → `False` | revert the commit |
| E2 | `E=~/backups/ha/m12-end-$(date +%F); mkdir -p $E && docker cp homeassistant:/config/configuration.yaml $E/` | file present, has the uncommented key | none |
| E3 | `docker cp ha/configuration.yaml homeassistant:/config/configuration.yaml && docker exec homeassistant chown root:root /config/configuration.yaml`; then H5's check | `valid` | copy E2's backup back, chown |
| E4 | H6's command into `$E/states-before.txt` | as H6 | none |
| E5a | H7a (nothing held, no spoken request in the last five minutes) | as H7a | none |
| E5 | `docker compose -f docker-compose.ha.yml restart homeassistant` (a clean stop since H7: `StopTimeout` 120) | as H8; `docker events --since 10m --until 0s --filter container=homeassistant --filter event=kill` → no signal 9 | E2's file back, restart |
| E6 | (the next spoken request) | `find ~/kronk-voice-recordings -name '*.wav' -newermt "<E5 time>"` → nothing | none |
| E7 | `systemctl --user start kronk-voice-capture-check.service` | journal: `capture off (...)`, no push | none |
| E8 | R1's undo (remove the timer) | `systemctl --user list-timers` no longer lists it | R1 |
| E9 | `python3 scripts/voice_label_sheet.py --recordings ~/kronk-voice-recordings --out ~/kronk-voice-labels/$(date +%F)`. **Path rule:** `--out` outside the repo and outside the recordings directory (both are refused, exit 2); a new directory each time (an existing `labels.csv` is refused) | exit 0 (exit 3: Tempo's search limit was hit; read the warning, the rows marked `no trace` may have a run); stderr `N of M recordings -> …/labels.csv (… without a trace, … voice runs without audio, … ambiguous joins)`; `manifest.json` counts; `ls ~/kronk-voice-labels/<date>/audio \| wc -l` = sampled | the output directory can be removed; recordings untouched |
| E10 | Recordings: nothing deleted; Drew decides. Keep `~/kronk-voice-recordings` in place while `docker-compose.ha.yml` declares the mount: without it the next HA recreate fails closed | none | none |

Transcripts stay on after the end date (Drew: kept 90 days, matching Tempo).

## What stays unverified until promotion

- voice_tracer's new code against live HA and Tempo: the hold, marks on real
  corrections, `kronk_routing` on a live trace, the state file's `held` and
  `cut` keys on the bind mount, `/health`'s new keys, a real
  `docker stop` of the production container exporting a held run, a real HA
  restart cutting a window short. Verified offline (harness) and in a
  scratch container (imports, SIGTERM).
- `voice.transcript` / `voice.speech` on a live run, and production
  orchestrator and tool_service text (needs the `.env` line).
- F4 live: a real play failure with the switch off (dev runs dry-run; no
  play is sent). F5 live: a model emitting broken tool-call JSON. Both
  covered offline through the real code paths.
- The failure-detail fields on a friendly-style run live (dev runs
  `ERROR_STYLE=debug`).
- Tempo's handling of a re-export with the same ids, and of a span older
  than its slack on this version (the age guard avoids sending one).
- HA writing into the bind mount, the WAV layout, the clean stop, the tag
  step: H3-H9. The reminder's live run and the real notifier (R3).
- The voice replay beyond its first 29 rows (below).

## Test output

`./scripts/run_tests.sh -q`, working tree at `b6ed6a8`:

```
4711 passed, 3 skipped, 53 xfailed, 6 warnings in 40.73s
```

Baseline `4646 passed, 3 skipped, 53 xfailed`. Added 65:
`tests/test_m12_fix1_text.py` 24, `tests/test_m12_fix1_tracer.py` 18,
`tests/test_m12_fix1_scripts.py` 23. Skips and xfails unchanged; the 6
warnings are the existing Pydantic and pytest-asyncio deprecation warnings.
New tests use `no_network` and temporary directories and open no sockets.

## Live verification on dev

- Rebuilt only the pair: `docker compose --profile dev up -d --build
  tool_service-dev orchestrator-dev` (dry run first: only the pair named).
  Image ids and start times of every running container before and after the
  whole session: only `kronk-orchestrator-dev-1` (`sha256:a0cdb86b…` →
  `sha256:d2f7181e…`) and `kronk-tool_service-dev-1` (`sha256:648db072…` →
  `sha256:097cd5e3…`) changed. Production, HA, MA, piper, Tempo, Grafana,
  LiteLLM, searxng, nginx and voice_tracer: same image, same start time.
- Switch off by a scratch compose override in the session scratch directory
  (`TELEMETRY_RECORD_TEXT: "false"` on the two dev services; dry run first),
  `--no-build`. Four requests with invented markers (search, fetch, genre in
  dry run, shopping-list remove). Logs since the recreate:
  `kronk-orchestrator-dev-1: 75 lines, 0 marker hits`,
  `kronk-tool_service-dev-1: 24 lines, 0 marker hits`,
  `kronk-voice_tracer-1: 0 lines, 0 marker hits`,
  `kronk-nginx-1: 5 lines, 0 marker hits`. Tempo (loopback, read-only, every
  span of every dev trace in the window): `traces 4, spans 97`,
  `marker hits: 0`. URL attributes as quoted under F1 and F3.
- Switch back on: recreated without the override (`--no-build`); four more
  requests with other markers. Logs: 7 hits in the orchestrator, 8 in
  tool_service. Tempo: `traces 4, spans 122`, hits in client and server
  `http.url`, `kronk.input` and the other listed fields.
- The dev pair at the end: `kronk-orchestrator-dev-1: TELEMETRY_RECORD_TEXT=true
  VOICE_STREAM=on MODEL_LOCK=server MUSIC_DRY_RUN=1`;
  `kronk-tool_service-dev-1: MUSIC_DRY_RUN=1 TELEMETRY_RECORD_TEXT=true`. The
  modules in the images equal the tree's: 24 orchestrator modules and 12
  tool_service modules compared by md5, 0 differ.
- The marker requests went to SearXNG and `example.com` as any dev search
  does, and added lines to `data/dev/sessions.db` and the dev logs (not
  edited by hand).

## Replay output

`python3 scripts/voice_replay.py --label m12-fix1`, dev path, dry-run on:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 160 rows (0 not selected)
  . pass  checked    plan-anime-pop-radio-wet
  . pass  checked    inc-1108-anime-pop-transcript
  . pass  checked    inc-1108-anime-pop-wet-basement
  . pass  checked    inc-1108-mushroom-jazz-basement
  . pass  checked    inc-1108-artist-portishead-basement
  . pass  checked    inc-1108-pause-basement-player
  . pass  checked    inc-1108-timer-2min
  . pass  executed   plan-orbital-station-youtube
  . pass  executed   inc-1004-tell-kronk-orbital
  . pass  checked    inc-1004-play-orbital-radio
  x xfail executed   plan-like-massive-attack-radio
  x xfail executed   inc-1004-well-play-orbital
  . pass  checked    inc-1004-french-dinner-pandora
  . pass  checked    inc-1004-french-dinner
  . pass  checked    inc-1004-french-cafe-pandora
  . pass  checked    plan-girl-with-the-sun
  . pass  checked    plan-shuffle-anime-bangers
  . pass  checked    plan-put-on-some-jazz
  . pass  executed   plan-stop-bang
  . pass  executed   inc-0910-stop-transcript
  . pass  checked    plan-ninetys-two-thousands
  x xfail executed   plan-mishearing-pasta
  x xfail executed   plan-mishearing-whats-up
  . pass  checked    inc-0915-daisies-album-eels-ytm
  . pass  checked    inc-0915-pause-kitchen
  . pass  checked    inc-0915-skip-kitchen
  x xfail checked    inc-0915-who-is-this
  . pass  executed   inc-0930-search-deep-house-ytm
  . pass  executed   inc-1019-thumbs-up-dot
voice_replay: STOPPED: media_player state changed before inc-1019-thumbs-up: [{'entity_id': 'media_player.xbr_49x800d', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}}]
voice_replay: {'pass': 24, 'xfail': 5}; executed 10, checked read-only 19; players CHANGED: [{'entity_id': 'media_player.xbr_49x800d', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}}] (17 checked)
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_211008_m12-fix1.json
```

Exit code 3. The replay's own player-state guard stopped it: the TV
`media_player.xbr_49x800d` (XBR-49X800D) went from `off` to `unavailable`,
`last_changed 2026-11-17T21:11:01.321360+00:00` (read-only, after the stop;
still `unavailable` then). A dry-run text request does not reach a TV; an
`unavailable` state is HA losing contact with the device. Per the brief, a
step stopped by a safety control was not retried.

Row by row against
`docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`: 29 rows ran,
all 29 are baseline rows, 28 have the same status; one differs,
`inc-0930-search-deep-house-ytm` fail → pass (a known YouTube Music
flapper). No failures. **131 of 160 rows were not run**, so criterion 14's
replay half is not re-established by this fix round; it needs a rerun once
the TV is back or with the coordinator's call on the guard.

## Departures

1. **The replay is incomplete** (above): stopped by its guard, not retried.
2. **F4 and F5 are not shown live**: unreachable on dev without sound or
   fault injection; covered offline through the real code paths.
3. **At a stop, runs still joining get one last join attempt** (2 s) and on
   an HA disconnect they keep joining against Tempo, rather than being
   exported unjoined at once. The ruling names held runs; these runs are in
   the state file's `held` list too. This keeps the Kronk link (and the
   rule-based mark) for a run in its first 15-60 s, and still exports it
   within about a minute of its end.
4. **One small change beyond the letter of F3**: the orchestrator's
   `DELETE /files/{filename}` access line is masked by the same rule, since
   the check found it carries an upload's name.
5. **One small change beyond the letter of F8**: runs that state they had no
   STT stage are no longer join candidates (a text run records nothing).
6. **Not fixed, for the coordinator**: exception 3 (a fetched page's URL
   path) and exception 4 (nginx's access log).
