# M8 build: sentence-level streaming on the voice mount

Build agent, 2026-11-17 (UTC), branch `refactor`. Tested against
`docs/sprint/M8-criteria.md`. Tree before the build: `a7f4112`. Commits, in
order: `bc95d22` (bench script and before measurements), `fba7451`
(spoken-reply line), `0427936` (criterion 16 after-run), `2648eb3`
(sentence splitter), `fb5bc67` (buffer behind `VOICE_STREAM`), `c9c5d0d`
(docs and streamed measurements); this report is the commit after. No push,
no tag. The dev pair is running `c9c5d0d` with `VOICE_STREAM=on`, healthy.

## Summary

- The spoken-reply line works and is the bigger win: on 10 long-answer
  questions × 5 runs, mean reply 753 → 322 characters, 9.3 → 3.0
  sentences, coordinator generation 2.92 → 1.97 s. It is not behind the
  switch.
- Sentence streaming works end to end on dev. HA 2026.5.4 starts streamed
  TTS (`tts_start_streaming`) at Kronk's first chunk, 0.13 to 0.34 s
  before `intent-end`; with the buffered path it fired at `intent-end`.
- The gain from streaming is small once replies are short: the first chunk
  leaves a median 0.29 s (mean 0.29, max 0.82 s) before `done`. With about
  three sentences and a one-sentence hold, sentence 1 leaves when sentence
  2 completes.
- Tool-round text on dev is mostly longer than one sentence: 31 of 33
  voice tool rounds that wrote text wrote more than 80 characters (home
  agent median 447, max 5271). A one-sentence hold would speak the start of
  most of them. Production had none in its 7 voice requests in Tempo, too
  few to clear it.
- Recommendation: production `VOICE_STREAM=off` at promotion; promote the
  line. Details under "Recommendation".
- `./scripts/run_tests.sh`: 4435 passed, 3 skipped, 53 xfailed (baseline
  4235 / 3 / 53; 200 new tests). Voice replay on dev with the switch on:
  no row regressed against the M10 baseline except one flake, rerun 5/5
  pass (below).

## Check first (criterion 1), read-only, before the design

All from this system: HA's installed source in the `homeassistant`
container (2026.5.4), Tempo on 127.0.0.1:3200, container logs, and a
`kronk-dev` pipeline run with no satellite.

- **Streamed TTS in HA.** `assist_pipeline/pipeline.py:107`
  `STREAM_RESPONSE_CHARS = 60`; `pipeline.py:1164` starts streaming once
  the assistant content passes 60 characters (or on a tool call after
  text) and emits `intent-progress` with `{"tts_start_streaming": true}`
  (`pipeline.py:1175`). It only does so when the run has a TTS stream
  (`pipeline.py:1114`: `self.tts_stream and
  self.tts_stream.supports_streaming_input`), so a run that ends at the
  intent stage never shows the event. The Ollama conversation entity
  declares `_attr_supports_streaming = True` (`ollama/conversation.py:41`)
  and the Wyoming TTS entity reports streaming input from the server
  (`wyoming/tts.py:140`).
- **Observed on `kronk-dev`** (start `intent`, end `tts`, no device id,
  before any M8 change): `run-start` carries `tts_output` with
  `stream_response: true`; for a reply over 60 characters
  `tts_start_streaming` fired, but at the same instant as the single
  content delta and `intent-end` (4.195 s, 4.195 s, 4.196 s; trace
  `5be4cdc82e7a7bdb3465ef975a9691b3`). HA was ready; Kronk's buffer was the
  wait. Before-table below.
- **HA's Ollama client.** `ollama/entity.py:245` calls `client.chat(...,
  stream=True)`. The client is `ollama.AsyncClient` (ollama 0.5.1) built in
  `ollama/__init__.py` with no timeout argument; the library default is
  `timeout=None` (`ollama/_client.py:81`), so a chat request has no
  timeout. `DEFAULT_TIMEOUT = 5.0` (`ollama/const.py:15`) wraps only
  `client.list()` at setup and the config flow. Deltas are concatenated
  into one assistant message by `chat_log.async_add_delta_content_stream`.
- **Retracts.** A retract removes exactly the text of a round that ended
  in tool calls (`agents.py`, `round_content_retracted`), which Tempo
  records as a generation span with `kronk.phase = plan_N` and
  `kronk.output_chars > 0`.
  - Production orchestrator log: 0 `round_content_retracted` events, but
    the container was recreated at 13:17 UTC today (not by this build), so
    the log covered minutes.
  - Production in Tempo (data from 2026-11-08 21:56, 113 pipeline traces,
    7 with `kronk.voice = true`): 0.
  - Dev orchestrator log (3 hours at the time): 6 events, largest 955
    characters.
  - Dev in Tempo (from 2026-11-16 00:20, 6661 pipeline traces): 112
    tool-round spans in 111 traces; characters min 17, median 223.5, p90
    955, max 5271 (trace `9589849743755ba0acb8cb330b5bc00a`, a
    `/voice/api/chat` request); 15 of 112 at 80 characters or fewer. By
    root route: 57 `/voice/api/chat`, 33 `/api/chat`, 3 `/v1`, 2
    `/message`, 17 roots not received.
  - The voice traces whose pipeline span carries `kronk.voice = true`
    (33): home agent 21 rounds (median 447, max 5271, 1 at ≤ 80),
    coordinator 8 (median 199, max 297, none ≤ 80), research 3 (median
    134), devops 1 (41). Last one 2026-11-17 11:04 UTC.
  - In this build's replay (137 streamed requests through `kronk-dev`): 0.
- **What the puck does with a chunked reply** (from HA's events and the
  ESPHome integration, no sound made): the satellite gets the TTS URL at
  `run-start` (`esphome/assist_satellite.py:384`); on `intent-progress`
  with `tts_start_streaming` it is sent `{"tts_start_streaming": "1"}`
  (`assist_satellite.py:324-333`) and starts fetching that URL while the
  reply is still arriving; chunks reach Piper as one text stream, so the
  puck sees one audio stream. When first audio actually plays was not
  measured: that needs a speaker, and is left for Drew.
- **wyoming-piper 2.2.2** streams on sentence boundaries by default (plan's
  M8 verified line; `--no-streaming` turns it off). Not touched.

These findings were gathered before any code was written (the bench
script and the before runs are the first commit); this file was written at
the end. The design consequence drawn from them before step 5: build the
hold as specified (criterion 4), make its width a config value, count every
retract the hold cannot honour on the trace, and expect to recommend
production off.

## What changed

- `config/voice.yaml` (new): `spoken_reply` (the line) and
  `stream.hold_sentences` (1), each documented in the file.
- `orchestrator/config_loader.py:69` `VOICE_FILE`; `:246`
  `SPOKEN_REPLY_DEFAULT`; `:254` `VoiceStream` (`hold_sentences`, 1 to 5);
  `:261` `Voice` (`spoken_reply`, one line, at most 400 characters, or
  null); `:426` `_load_voice`; `voice.yaml` is loaded, digested, listed
  among known files for the stray scan and flattened for reload diffs, the
  same way as `lead_in.yaml`.
- `orchestrator/main.py:525-535` the coordinator's `system_extra` gains
  the line, last, when `voice` is set. `:1033` `_voice_stream_on()` reads
  `VOICE_STREAM` per request (`on/true/1/yes`; `off/false/0/no/""`;
  anything else is off and logged once). `:1048` `_ndjson_chunk`. `:1056`
  `_ollama_speech_stream`, the streamed writer (buffer, unhonoured-retract
  event, fault fallback, `aclose()` of the pipeline, `done` record,
  `voice_stream` event and span). `:1180` the voice branch of
  `_ollama_chat_impl` takes the streamed writer only for `stream: true`
  with the switch on; everything after it is the buffered path as it was.
  Docstrings of `_ollama_single_chunk_stream` and `_ollama_chat_impl`
  updated (text only).
- `orchestrator/speech_stream.py` (new): `split_units` (`:128`, pure) with
  its helpers (`_kind`, `_decided`, `_next_line`, `_next_ok`,
  `_inline_closed`), and `SpeechBuffer` (`:213`: `feed`, `retract`,
  `finish`, `_send`).
- `orchestrator/transcript_gate.py:72` `_ends_sentence` renamed
  `ends_sentence` (public, shared); `:68` `OPENERS` exported. No change in
  behaviour (its tests pass unchanged).
- `docker-compose.yml:242` `VOICE_STREAM=off` on production's
  orchestrator (not deployed; production still runs its 13:17 container),
  `:382` `VOICE_STREAM=on` on `orchestrator-dev`.
- `scripts/voice_stream_bench.py` (new): the measurement tool (dev only;
  refuses a base URL not ending in `/dev`; its HA mode requires a pipeline
  whose Kronk URL is under `/dev/voice` with dry-run on, checks each
  sentence with HA's read-only sentence-match debug first, and passes no
  device id).
- Tests: `tests/test_spoken_reply.py` (18), `tests/test_speech_stream.py`
  (81), `tests/test_voice_stream.py` (100), `tests/fixtures/m8/replies.json`
  (100 replies recorded on dev); `tests/test_m21_pins.py` (+1 test, see
  criterion 14); `voice.yaml` added to the YAML-file lists in
  `tests/test_config_hardening.py` and `tests/test_config_fix2.py`.
- Docs: `docs/features/voice-pipeline.md` (new section "Sentence streaming
  and the spoken-reply line", a gotcha, blog hooks),
  `docs/features/render-profiles.md` (the single-chunk paragraph),
  `docs/features/config-directory.md` (the `voice.yaml` row).
- Bench results: `docs/bench/voice_stream_2026-11-17_132515_m8-pre.json`,
  `…_132746_m8-ha-pre.json`, `…_133319_m8-line.json`,
  `…_135113_m8-stream-on.json`, `…_135250_m8-ha-stream-on.json`,
  `docs/bench/voice_replay_2026-11-17_135317_m8-build.json`.

## Criteria

1. Check first: above. HA shows `tts_start_streaming` for a reply over 60
   characters (only on a run with a TTS stage); Ollama client sends
   `stream=True` and has no request timeout; retracts: 0 on production
   (Tempo since 2026-11-08, 113 pipelines), 112 on dev, largest 5271
   characters; the puck is told to start streaming and fetches one audio
   stream.
2. Several NDJSON chunks, each whole units, each through `to_speech`:
   `test_a_long_reply_leaves_in_several_spoken_chunks`. First chunk before
   the model finishes, slow stub: `test_first_chunk_leaves_while_the_model_is_still_generating`
   (the model blocks until the test has seen a chunk). On dev: 41 of 50
   long answers left in 2 to 4 chunks, first chunk a median 0.29 s before
   `done` (table below; per-chunk times in `…_m8-stream-on.json`).
3. Same text as the buffered path: the 19 cases of `test_speech_stream.py`
   CASES (prose, emphasis, list, table, fence, heading, URL, numbers and
   units, abbreviations "Dr." "e.g." "Mr." "No. 5", decimals, ellipsis, no
   final punctuation, one sentence, empty, initials and quotes, ? and !,
   lowercase after a full stop, quote and rule, snake_case) cut and whole;
   1500 generated replies; the 100 recorded dev replies; the same cases fed
   in random token sizes through `SpeechBuffer` with hold 1 and 2; and
   through the full app against the buffered path
   (`test_pipeline_streamed_equals_pipeline_buffered`). Equal apart from
   whitespace between chunks. One listed difference, pinned by
   `test_known_difference_space_before_an_added_full_stop`: on a non-table
   line that contains `|` and ends in spaces, `to_speech`'s cell treatment
   strips the trailing spaces before it appends the closing full stop; a
   sentence cut off that line without the `|` keeps them, so the part
   gives "Moves ." where the whole gives "Moves.". A space before a full
   stop; no word changes. A wider one-off sweep of 30,000 generated
   replies found only this (12 cases) and no withdrawn cut in 3,000
   prefix checks.
4. One unit held: `test_a_sentence_leaves_only_when_the_next_completes`;
   retract inside the hold: `test_a_retract_inside_the_held_text_removes_it_before_anything_is_sent`;
   tool-round preamble then a tool call, nothing of the preamble spoken:
   `test_tool_round_preamble_is_never_spoken` (home agent, terminal
   `play_music`) and `test_coordinator_preamble_before_delegating_is_never_spoken`
   (`ask_home`).
5. Retract larger than the held text: what was already sent stays spoken;
   everything held is dropped; the remainder is recorded as unhonoured;
   later chunks continue normally
   (`test_a_retract_larger_than_the_hold_is_defined`). Logged as
   `voice_stream_retract_unhonoured` (chars, unhonoured, hold, preview) and
   counted on the `voice_stream` span and event
   (`kronk.retract_unhonoured`, `kronk.retract_unhonoured_chars`):
   `test_unhonoured_retract_is_logged_and_on_the_trace`. Widening is one
   config edit: `test_a_wider_hold_keeps_the_whole_preamble`.
6. Blocks: `test_a_list_is_held_until_the_next_line_of_another_kind`,
   `test_an_indented_line_continues_a_list`, `test_a_table_is_held_until_it_ends`,
   `test_a_fence_is_held_until_it_closes`, at app level
   `test_a_table_leaves_as_one_chunk_after_it_ends`; never closed:
   `test_an_unclosed_block_is_released_at_the_end_rendered_as_buffered`,
   `test_an_unclosed_fence_is_released_at_the_end`.
7. No split inside abbreviations, decimals, initials, URLs, times:
   `test_no_cut_inside_abbreviations_decimals_initials_urls_times` (13
   cases) plus CASES. One pure function, `split_units`, with its own tests.
   Shared with `transcript_gate`: `ends_sentence` is the one boundary rule
   (`test_the_boundary_rule_is_transcript_gates` checks every sentence cut
   over 300 generated replies against it). `count_sentences` itself is not
   reused: it counts whitespace tokens of a transcript and returns a
   number, while the splitter needs positions, the line and block
   structure, and `to_speech`'s extra constraints.
8. Short replies keep their shape:
   `test_short_replies_are_one_chunk_with_todays_text_and_done` compares
   the NDJSON with the switch on and off (timestamps aside) for a
   one-sentence answer, a direct dispatch, a terminal-tool result, a
   friendly failure from a direct dispatch with tool_service down, and a
   friendly failure from an unreachable model. Live on dev: "pause" →
   one chunk "The Office speaker is already paused." and `done` (dry run).
   A code-written text of three or more sentences (the news brief) is cut
   like model text; see Departures.
9. Protocol: `test_ndjson_protocol` (each line one JSON object, `done:
   false` chunks then exactly one `done: true`, the same `done` fields and
   values as today's); `stream: false` on the voice mount:
   `test_non_streaming_request_on_the_voice_mount_is_one_object`. HA
   accepted every streamed reply: 72 replay rows executed through
   `kronk-dev` and 8 HA bench runs, 0 pipeline errors; replies of 2, 3
   and 4 chunks among them (dev log: 80 one-chunk, 49 two, 6 three, 2
   four).
10. Failure mid-stream: `test_model_failure_after_sentences_ends_with_the_friendly_sentence`
    (last content ends with `errors.render("llm", …, FRIENDLY)`, one
    `done`, no sentence repeated, joined text equals the buffered path's).
    Failure stays the pipeline's flag; a reply that only reads like a
    failure is spoken as written:
    `test_failure_is_still_decided_by_the_flag_not_the_text`. A bug in the
    buffer: `test_the_stream_survives_a_bug_in_the_buffer` (fails at the
    first split and at the 30th; the rest leaves as one chunk, one `done`,
    `_llm_lock` released).
11. What is stored: the trace's output is `_run_pipeline`'s, unchanged;
    `test_trace_holds_the_final_reply_without_retracted_text` gives the
    same `kronk.output_chars` with the switch on and off for a retracted
    preamble. Voice requests write no Kronk-side history (HA owns it); HA's
    copy is the joined chunks, equal to the buffered text by criterion 3.
12. `VOICE_STREAM`: default in code `off`; dev compose `on`; production
    compose `off` (not deployed). Off is today's path byte for byte:
    `test_off_and_unset_are_todays_single_chunk` (unset, `off`, `OFF `,
    `false`), `test_an_unknown_value_is_off_and_logged`. `tests/test_render.py`
    and `tests/test_retract.py` pass unchanged; no pin in them changed,
    because the default is off (the plan expected `test_render.py:115` and
    `test_retract.py:155` to change; they did not need to).
13. Other routes: `test_other_routes_send_the_same_bytes_with_the_switch_on_and_off`
    (`/api/chat` and `/v1/chat/completions`, streaming and not, and
    `/message`; timestamps and timings normalised). Live on dev:
    `/dev/api/chat` streaming (119 lines of raw tokens),
    `/dev/v1/chat/completions` non-streaming, both answering normally.
14. The line ends the coordinator's system text on voice requests, the
    text before it byte-identical to a typed request's:
    `test_voice_coordinator_text_is_the_typed_text_plus_the_line`,
    `test_line_stays_last_after_a_specialist_failure_block`; absent on
    other mounts: `test_line_absent_on_every_other_mount`. Wording in
    `config/voice.yaml` through the loader, default equal to the file
    (`test_builtin_default_equals_the_shipped_file`), schema errors with
    their line, null, empty file, reload. Either switch value:
    `test_spoken_reply_line_applies_with_either_switch_value`. M21 pins:
    exactly two variants change, `pipeline|direct_voice_origin|call=0` and
    `pipeline|home_failed_friendly_voice|call=1` (the coordinator on the
    speech mount); the pin renderer cuts the line off and the rest still
    matches the fixture, and `test_spoken_reply_line_is_exactly_where_expected`
    asserts the line appears in those two and nowhere else.
15. Coordinator only: `test_routed_specialist_does_not_get_the_line`,
    `test_delegated_specialist_does_not_get_the_line`. Tool choice: the
    replay held its routing and tool expectations on 159 of 160 rows as in
    the baseline and M11's e2e; the 160th
    (`eval-look-for-some-good-deep-house-chill`) answered in words instead
    of calling `ask_home` once, then passed 5 of 5 reruns (Replay, below).
16. Ten questions, five runs, before and after the line: table below.
17. Time to first chunk, off against on, line on in both: table below.
    Through HA: table below; with the switch on HA starts TTS at the
    first chunk, before the reply ends, in 7 of 8 runs (the 8th was a
    one-chunk reply). No audio was played (no satellite; nothing fetched
    the TTS URL). First audio timing is left for Drew.
18. HA does start speech earlier on this system, by the events. The plan's
    fallback rule does not trigger on that ground; the recommendation
    below is on the size of the gain and the leak risk.
19. Latency: section below.
20. 4435 passed, 3 skipped, 53 xfailed (baseline 4235 / 3 / 53, +200
    new). New tests use `no_network`, open no sockets, write only to
    pytest's temporary directories.
21. Replay with the switch on: section below.
22. Dev only. No sound: no satellite in any HA run, dry run on throughout,
    the replay's player check reported "players unchanged (17 checked)" on
    every run. No change to HA, the pucks, `wyoming-piper`, Music Assistant
    or production; no HA or MA restart; nothing sent to the MagicMirror Pi
    or any LAN address. Production containers were created at 13:17 UTC,
    before this build started, and were not touched.
23. Telemetry: the pipeline root carries `kronk.voice_stream = true`; the
    `voice_stream` span (child of the root) carries chunks,
    `first_chunk_ms`, `done_ms`, hold, retracts, retract_held_chars,
    retract_unhonoured, retract_unhonoured_chars, spoken_chars; same on
    the `voice_stream` event. Live: trace
    `73319fbbd13f07404436d3f1b27d45d9` in Tempo shows
    `kronk.voice_stream: true` on `pipeline.shim` and `voice_stream` with
    `kronk.chunks 2, first_chunk_ms 1771, done_ms 2214, retract_unhonoured 0`.
    Buffered voice requests carry neither (see Departures).
24. Docs: `docs/features/voice-pipeline.md` (streamed path, hold, block
    rule, switch, rollback, HA behaviour, telemetry, gotcha),
    `docs/features/render-profiles.md` (single chunk no longer described
    as free), `docs/features/config-directory.md` (`voice.yaml`).

## Before and after

All on the dev pair, `/dev/voice/api/chat`, `stream: true`, one question at
a time, gemma-4-e4b. Before: `a7f4112` (`m8-pre`). Line: `fba7451`, buffered
(`m8-line`). On: `fb5bc67`, `VOICE_STREAM=on` (`m8-stream-on`). Every run's
trace id, chunk times and reply text are in the JSON files.

### Criterion 16: reply length and generation time, before and after the line (medians of 5 runs)

| Question | chars before | chars after | sentences before | sentences after | generation s before | generation s after | trace (before, run 1) | trace (after, run 1) |
|---|---|---|---|---|---|---|---|---|
| explain how a heat pump works | 1210 | 380 | 16 | 4 | 3.78 | 3.15 | `97a725d26760700716b8b66e0c37a374` | `f21b101994093050080207994b6cde94` |
| how does a refrigerator keep food cold | 1019 | 271 | 15 | 3 | 3.59 | 1.44 | `f8bd3ec01eb0bccd326733bebe51ca13` | `5b9e51967932ba466ca84a00a34a10fd` |
| why is the sky blue | 426 | 341 | 4 | 3 | 1.39 | 1.05 | `7496f88b267f69cb3a25f7536cb0a931` | `1fd4a5940f17b62a6dc3780b009b373c` |
| how do vaccines work | 887 | 337 | 7 | 3 | 3.44 | 1.73 | `5a41962b0d67ecd2dc92eb5455158a75` | `5bb76763f7378d82b674466bc2b29f1b` |
| what causes the seasons on earth | 543 | 326 | 5 | 3 | 1.61 | 2.0 | `e0f39b9d00f93011ef7d2ff9afbc4ce7` | `5696cdb5e21bd238443e73c8bd3deadd` |
| how does a solar panel make electricity | 490 | 295 | 5 | 3 | 3.3 | 1.41 | `8847ea90366d63e7f509218ccf77fa86` | `546296f92c90d0a8377da4b4bfd1110e` |
| explain how a car engine works | 1095 | 405 | 14 | 3 | 4.21 | 3.19 | `9e2fe6b3b5ce535a4616dd1828aa7f0c` | `7857cd42ad3c1b0d7c55397d4742a0f5` |
| how do airplanes stay in the air | 1099 | 291 | 15 | 3 | 4.71 | 1.79 | `85298824bb0798d56690da68e002cc9a` | `dc729f7fb488b47ae8815ad00a5d4290` |
| what is the difference between a virus and a bacterium | 423 | 284 | 5 | 2 | 1.95 | 1.22 | `f5baa07439c462eb5dfe20bc14ab85cd` | `216950191dcf6151731687d99430b989` |
| how does a microwave oven heat food | 378 | 273 | 4 | 3 | 0.91 | 2.13 | `d2cffdf292cbd99adb6bca43cc7394e8` | `a5cac7ab0278b18c61399dd0ef4a83fb` |
| **all 50 runs, characters: mean / median** | 753.3 / 647.5 | 321.6 / 324.5 | | | | | | |
| **all 50 runs, sentences: mean / median** | 9.28 / 5.5 | 2.98 / 3.0 | | | | | | |
| **all 50 runs, generation s: mean / median** | 2.92 / 2.9 | 1.97 / 1.75 | | | | | | |
| **all 50 runs, send to done s: mean / median** | 2.94 / 2.91 | 1.99 / 1.78 | | | | | | |

### Criterion 17: time to first content chunk, switch off against on (line on in both; medians of 5 runs, seconds from send)

| Question | off: first chunk = done | on: first chunk | on: done | on: done − first | on: chunks | trace (on, run 1) |
|---|---|---|---|---|---|---|
| explain how a heat pump works | 3.17 | 1.47 | 1.85 | 0.19 | 2 | `457095551390f042b3009080a55dc351` |
| how does a refrigerator keep food cold | 1.47 | 0.54 | 0.79 | 0.26 | 2 | `a1ba36f48ed080af8e6d490cb0dd241d` |
| why is the sky blue | 1.08 | 1.0 | 1.31 | 0.32 | 2 | `f39c207fb2408631c945aae75edd3812` |
| how do vaccines work | 1.75 | 0.88 | 0.9 | 0.35 | 2 | `c3a690b41be07c927bf217254e5d79df` |
| what causes the seasons on earth | 2.02 | 1.06 | 1.59 | 0.26 | 2 | `60256eb434edbd1b357cf7d12f037917` |
| how does a solar panel make electricity | 1.43 | 0.72 | 1.02 | 0.29 | 2 | `0e1bc97943fc32af1099f71e92798648` |
| explain how a car engine works | 3.21 | 1.03 | 1.84 | 0.49 | 3 | `801d8062bd7aa5b116cb3f74b3e5ce02` |
| how do airplanes stay in the air | 1.8 | 1.77 | 2.11 | 0.33 | 2 | `cbc20975e77a427f75487d64af04f64b` |
| what is the difference between a virus and a bacterium | 1.23 | 1.12 | 1.12 | 0.0 | 1 | `ec43fe45544db9ca4b2dfde387f209c0` |
| how does a microwave oven heat food | 2.16 | 0.92 | 1.09 | 0.23 | 2 | `1084ea7be9f4f740fd758dc97cc9479f` |
| **first 5 questions (criterion set), 25 runs: mean / median** | 2.0 / 1.75 | 1.29 / 1.06 | 1.58 / 1.42 | 0.29 / 0.29 | 1.92 | |
| **all 10 questions, 50 runs: mean / median** | 1.99 / 1.78 | 1.3 / 1.09 | 1.59 / 1.46 | 0.29 / 0.29 | 2.02 | |

Single-chunk replies with the switch on: 9 of 50. Max done − first chunk: 0.816 s.

### HA's `kronk-dev` pipeline, intent to TTS, no satellite (seconds from the run request)

| Run | Question | content deltas | `tts_start_streaming` | first content delta | `intent-end` | `tts-start` | lead of `tts_start_streaming` over `intent-end` | trace |
|---|---|---|---|---|---|---|---|---|
| before (buffered) r1 | explain how a heat pump works | 1 | 4.942 | 4.942 | 4.942 | 4.942 | 0.0 | `0b4b63e6c3a1fe1532b683654b4f0709` |
| before (buffered) r1 | how does a refrigerator keep food cold | 1 | 2.992 | 2.992 | 2.992 | 2.992 | 0.0 | `cd5b2f892c9be0b4e2a9ae7c9bc85dc1` |
| before (buffered) | why is the sky blue | skipped: fuzzy HassGetCurrentTime only (strict pass misses) | | | | | | |
| before (buffered) r1 | how do vaccines work | 1 | 3.201 | 3.201 | 3.201 | 3.201 | 0.0 | `64be72e7271c724be50188e057bbe5d7` |
| before (buffered) r1 | what causes the seasons on earth | 1 | 1.619 | 1.618 | 1.619 | 1.619 | 0.0 | `e92159319d55c1440a15e58f2a492140` |
| on r1 | explain how a heat pump works | 2 | 0.594 | 0.594 | 0.728 | 0.728 | 0.134 | `69841deabdd1235ce04fea287a174ceb` |
| on r1 | how does a refrigerator keep food cold | 2 | 1.805 | 1.805 | 1.974 | 1.974 | 0.169 | `3a5ebdebe351fde2649d6d703efab5de` |
| on | why is the sky blue | skipped: fuzzy HassGetCurrentTime only (strict pass misses) | | | | | | |
| on r1 | how do vaccines work | 2 | 0.563 | 0.563 | 0.823 | 0.824 | 0.26 | `a5da1b42df04232557588677316445c9` |
| on r1 | what causes the seasons on earth | 1 | 2.935 | 2.935 | 2.936 | 2.936 | 0.001 | `27042fb41023af60a25c0404133ffab3` |
| on r2 | explain how a heat pump works | 2 | 3.057 | 3.057 | 3.233 | 3.233 | 0.176 | `f1dc7c1ec6436d913710e84e55e329d8` |
| on r2 | how does a refrigerator keep food cold | 2 | 2.887 | 2.887 | 3.143 | 3.143 | 0.256 | `5799f3e7b512116470d4418fcb719eb1` |
| on | why is the sky blue | skipped: fuzzy HassGetCurrentTime only (strict pass misses) | | | | | | |
| on r2 | how do vaccines work | 2 | 1.202 | 1.202 | 1.542 | 1.542 | 0.34 | `2c439294aa5b79b16dfd6ffa66d32392` |
| on r2 | what causes the seasons on earth | 2 | 1.191 | 1.191 | 1.485 | 1.485 | 0.294 | `00a12a9e132939f6a17d1ea40fc1753e` |

run-start `tts_output` (both runs):  {"url": "/api/tts_proxy/w3QIdHDAd_OkKGdUMfePRA.mp3", "mime_type": "audio/mpeg", "stream_response": true}

Reading the criterion-17 table: "off" is the `m8-line` run and "on" a
separate run 18 minutes later, so the difference between them also holds
run-to-run variance (`done` itself was 0.4 s faster in the later run). The
clean measure of what streaming buys is within the "on" run: the first
chunk leaves a median 0.29 s before `done`.

## Latency (criterion 19)

Measured parts are Kronk's, on dev, send to first content chunk and send to
`done` at `/dev/voice/api/chat` (means over 50 runs). STT, Piper's first
sentence and the puck's buffering are unchanged by M8 and were not
measured (no sound); spoken length is converted at an assumed 15
characters per second for Piper `hfc_male-medium`, not measured.

| Answer | Before M8 | Line, switch off | Line, switch on |
|---|---|---|---|
| Short: direct dispatch ("pause") | first = done, one chunk (0.02 s measured once, switch on; same path) | same | same (one chunk) |
| Short: one-sentence model answer | first = done | same | same (one chunk) |
| Long: general question, Kronk first chunk | 2.94 s | 1.99 s | 1.30 s (median 1.09) |
| Long: Kronk done | 2.94 s | 1.99 s | 1.59 s |
| Long: spoken length | 753 chars, ≈ 50 s of speech | 322 chars, ≈ 21 s | same as off |

Expected time to first audio for a long answer: before M8, STT + 2.9 s +
Piper's first sentence; with the line, about 1.0 s less; with streaming on
top, about 0.3 s less again. Expected total time for a long answer: about
30 s shorter, almost all of it from the line. Short answers: unchanged.
Drew's audible test is the one that confirms the first-audio numbers.

## Test output (verbatim)

```
$ ./scripts/run_tests.sh -q
…
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
4435 passed, 3 skipped, 53 xfailed, 6 warnings in 30.40s
```

## Replay (criterion 21)

`python3 scripts/voice_replay.py --label m8-build`, dev pipeline
`kronk-dev`, `VOICE_STREAM=on` on `orchestrator-dev` at `fb5bc67`:

```
voice_replay: {'pass': 132, 'xfail': 22, 'fail': 2, 'skip': 3, 'xpass': 1}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_135317_m8-build.json
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json` (114
rows) row by row, and against M11's e2e run
(`voice_replay_2026-11-17_130856_m11-e2e.json`, 160 rows) for the rows
added since:

- `inc-0930-search-deep-house-ytm`: fail in the baseline, in M11's e2e and
  here (a "YouTube Music" search row; the baseline's own failure). Known
  flapper.
- `eval-look-for-some-good-deep-house-chill`: pass in the baseline and in
  M11's e2e, fail here: the coordinator answered "I cannot directly search
  YouTube Music for a specific genre and start a radio. I can only play,
  pause, or control the music already set up on your speaker." and called
  no tool (trace `61833ebc5bb60bcfa2663e5e513f86e1`). Rerun 5 times with
  `--id`: 5 pass, each `coordinator:ask_home` → `home:play_music` (traces
  `a21cf2f76e50dab8908913ff9629e33a`, `ffafd8eb740d3a912886d9b266de3746`,
  `7ac292850f27b4827da7cc1b0e497449`, `b1da010b73d5fd2f9ec4de34cf5aabeb`,
  `7a91bb8bb86e828dc5565881f709496c`). 1 failure in 6 runs. Not on the
  known-flapper list, and the spoken-reply line ("answer in one to three
  short, plain sentences") is a plausible nudge towards answering in words
  on a composite request, so I cannot rule it out; it is model variance on
  the evidence available. Worth watching in the e2e.
- `m19-what-happens-if-i-ask-kronk-to-pause`: xpass, as in M11's e2e
  (marked `xfail.live`).
- Every other row: same status as in the baseline (rows present there) or
  as in M11's e2e (rows added since).
- No row was stopped or skipped for player state; the replay ran once in
  full, plus the five single-row reruns above.

## Departures from the criteria, and why

- **"Off" side of criterion 17 is the `m8-line` run** (`fba7451`, before
  the streaming code existed), not a fresh off run on `fb5bc67`. Flipping
  the dev switch means recreating the container twice; the buffered path
  is byte-identical by test (criterion 12), and the within-run lead is
  reported as the clean number.
- **HA runs end at the TTS stage, not the intent stage.** HA emits
  `tts_start_streaming` only when the run has a TTS stream, so an
  intent-only run cannot answer criterion 1 or 17. The runs pass no
  device id, so no satellite is involved, and nothing fetched the TTS URL;
  no audio was played anywhere. Whether HA synthesised the audio into its
  cache was not checked. HA runs: 1 per question before, 2 after, on 4
  questions ("why is the sky blue" is skipped because HA's sentence debug
  shows a fuzzy `HassGetCurrentTime` match; the script runs nothing HA
  might take locally).
- **The hold counts units, not strictly sentences**: a whole list, table
  or fence is one unit.
- **A code-written reply of three or more sentences is chunked** like a
  model reply (the news brief is the case). The pipeline gives no flag
  that separates code-written tokens from model tokens, and adding a text
  rule would break the M2 rule. Criterion 8's cases (one sentence, direct
  dispatch, terminal-tool results, friendly failures) are one or two
  sentences and leave as one chunk.
- **`kronk.voice_stream` is absent, not `false`, on buffered voice
  traces.** Setting it on the buffered path means threading an argument
  through `_ollama_collect` and `_kronk_pipeline_tokens`, whose signature
  existing tests fake; the brief says not to touch the buffered path's
  code. TraceQL for buffered voice requests:
  `{ span.kronk.voice = true && span.kronk.voice_stream = nil }`.
- **One whitespace difference** from the buffered text, listed under
  criterion 3.
- **New file `scripts/voice_stream_bench.py`**, not asked for, needed for
  criteria 16 and 17 and kept so the tester can rerun them.
- **`transcript_gate._ends_sentence` became `ends_sentence`** so the
  splitter can share it (criterion 7); no behaviour change.

## Recommendation for production's `VOICE_STREAM`

`off`. Promote the spoken-reply line (it is not behind the switch).

- HA does start TTS at the first chunk, so streaming works. But with the
  line, long answers are about three sentences, and the measured gain is a
  median 0.29 s (max 0.82 s) of first chunk ahead of `done`.
- Against that: tool-round text on dev is rarely one sentence (31 of 33
  voice rounds over 80 characters; home agent median 447). With a
  one-sentence hold the first sentences of a leaked deliberation would be
  spoken, for music before the music starts. Production showed none in 7
  voice requests, which does not clear it. Widening the hold to cover the
  coordinator's median (about 200 characters, two or three sentences)
  would hold most three-sentence answers whole and remove the gain.
- To revisit: run the switch on for dev traffic, watch
  `{ name = "voice_stream" && span.kronk.retract_unhonoured > 0 }` in
  Tempo, and consider the plan's other option (stream only rounds that
  follow a tool result) if long answers come back.

## What the coordinator must do at promotion

1. Production orchestrator from the tag: `docker compose up -d --build
   orchestrator`, then `docker compose restart nginx` (stale upstream IP
   otherwise). Not HA, not MA. `docker-compose.yml` already sets
   `VOICE_STREAM=off` for production; change it to `on` only by decision.
2. `config/voice.yaml` is read from the shared `./config` mount: the line
   takes effect with the new image. Until production runs M8 code, its
   loader does not know `voice.yaml` and lists it as a stray file (a
   warning on `/services`) at its next restart or reload; harmless.
3. Verify live: `/voice/api/chat` with `stream: true` and `false`, and the
   shim `/api/chat`; a long general question on the voice mount should
   answer in about three sentences.
4. Ask Drew for an audible "Okay Nabu" test of a long answer (and, if the
   switch is ever turned on, first-audio timing).

## How to roll back

- Streaming: `VOICE_STREAM=off` in `docker-compose.yml`, `docker compose
  up -d orchestrator` (an env change recreates it), `docker compose
  restart nginx`. Production starts with it off.
- The line: `spoken_reply: null` in `config/voice.yaml`, then
  `scripts/config_reload.sh prod`. No rebuild.
- The hold: `stream.hold_sentences` in `config/voice.yaml`, reload.
- All of M8: deploy the previous tag.
