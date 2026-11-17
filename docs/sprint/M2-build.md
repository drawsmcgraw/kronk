# M2 build: friendly errors on voice, and one voice signal in the pipeline

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M2-criteria.md`. Build commit: `60fa59a`; this report is the
commit after it. No push, no tag.

## Summary

- `origin.voice` (`orchestrator/origin.py:53`) is a request-scoped
  `ContextVar[bool]`, default `False`. `ollama_chat_voice`
  (`/voice/api/chat`) is the only caller that passes `voice=True`;
  `_run_pipeline` sets it beside `origin.said` and resets it in its
  `finally`. Nothing a client sends can set it.
- `errors.style_for(voice)` keys on it. `ERROR_STYLE_VOICE` covers only the
  speech mount and defaults to `friendly` in code. `ERROR_STYLE` covers
  `/message`, `/api/chat` and `/v1` and stays `debug`. No `.env` edit.
- The root span carries `kronk.voice`. Failed spoken requests in one
  TraceQL query: `{ span.kronk.voice = true } && { status = error }`.
- Tests: `2276 passed, 2 skipped, 18 xfailed` (baseline `2215 passed,
  2 skipped, 18 xfailed`; +61).
- Live on dev, `tool_service-dev` stopped: three failure kinds, each on
  `/dev/voice/api/chat` and `/dev/api/chat`. Replay rerun: all rows ran;
  the only changes from the M10 baseline are on known flapper rows plus
  the new M4b rows.

## What changed

| File:line | Change |
| --- | --- |
| `orchestrator/origin.py:53` | New `voice: ContextVar[bool]` (`"kronk_voice"`, default `False`), with a comment naming its producer and its readers (errors now; M8, M14 later). Module docstring mentions it. |
| `orchestrator/main.py:283` | `_run_pipeline` takes a required keyword `voice: bool`. |
| `orchestrator/main.py:303` | `error_style = errors.style_for(voice)` (was `style_for(transport)`). |
| `orchestrator/main.py:308` | `telemetry.start_pipeline(..., voice=voice)`. |
| `orchestrator/main.py:315`, `:509` | `origin.voice.set(voice)` after `origin.said.set`; reset in `finally`. |
| `orchestrator/main.py:539` | `/message` passes `voice=False`. |
| `orchestrator/main.py:658`, `:681` | `_kronk_pipeline_tokens` takes `voice` (default `False`) and passes it to `_run_pipeline`. |
| `orchestrator/main.py:715`, `:874` | `_ollama_pipeline_stream`, `_ollama_collect` take `voice` (default `False`) and pass it down. |
| `orchestrator/main.py:910-930` | `_ollama_chat_impl(req, voice: bool)` replaces `profile: str`. `if voice:` selects `render.to_speech` and passes the same `voice` into the pipeline. |
| `orchestrator/main.py:958`, `:972` | `/api/chat` → `voice=False`; `/voice/api/chat` → `voice=True` (the only literal `True`). |
| `orchestrator/errors.py:53` | `style_for(voice: bool)`: voice reads `ERROR_STYLE_VOICE` (default `friendly`), others read `ERROR_STYLE` (default `debug`); unknown values → `debug`. |
| `orchestrator/errors.py:64` | `current_style()`: `style_for(origin.voice.get())`. |
| `orchestrator/errors.py:37-50`, `:71` | Friendly renderings: routing has its own sentence; `llm` splits LiteLLM 4xx ("refused") from everything else ("couldn't reach"). Debug renderings unchanged. |
| `orchestrator/errors.py:98-111` | `friendly_tool_failure(tool)`: names the service of a terminal tool that raised. |
| `orchestrator/errors.py:114` | Friendly specialist-failure instruction now also asks for what kind of thing failed, and lists error names, URLs, ports and model names. Debug instruction unchanged. |
| `orchestrator/errors.py:146-162` | `FRIENDLY_TOOL_PHRASING` reworded for a tool result; `is_failed_tool_result()` matches the code's own failure shapes. |
| `orchestrator/errors.py:1-24` | Docstring: stale voice-smoke-test sentence removed; config documented as built. |
| `orchestrator/agents.py:147` | `_terminal_speech` friendly fallback → `errors.friendly_tool_failure(tool)`. Debug fallback unchanged. |
| `orchestrator/agents.py:585`, `:613` | `run_stream(error_style=None)`: when not given (delegated `ask_*` runs), `errors.current_style()`. Was hard-coded `DEBUG`. |
| `orchestrator/agents.py` (system prompt build, was `:621-624`) | Removed: friendly style no longer appends `FRIENDLY_TOOL_PHRASING` to the system prompt. |
| `orchestrator/agents.py:859` | Friendly style appends `FRIENDLY_TOOL_PHRASING` to a tool result only when `is_failed_tool_result(result)`. |
| `orchestrator/telemetry.py:329`, `:356` | `start_pipeline(..., voice=False)` sets `kronk.voice` on the root span. |
| `docker-compose.yml:213-221` | Comment rewritten (smoke-test line removed); `# - ERROR_STYLE_VOICE=debug` is the documented switch-back. No value change. |
| `.env.dev.example:31-36` | Commented `# ERROR_STYLE_VOICE=debug` with a note. `.env.dev` not edited. |
| `README.md:259-264`, `ROADMAP.md:290-292` | Error-style sentences corrected. |
| `docs/features/verbose-errors.md` | Config table, the friendly/debug sentence table, the failed-result phrasing rule, TraceQL query. Smoke-test paragraph removed. |
| `docs/features/voice-pipeline.md` | New section "The voice signal (sprint M2)"; one blog hook. |
| `tests/test_voice_signal.py` | New, 13 tests. |
| `tests/test_error_style.py` | Rewritten, 60 tests (stale smoke-test note removed). |
| `tests/test_telemetry.py` | +2 (`kronk.voice` true/false). |
| `tests/test_retract.py` | The two `fake_tokens` stand-ins take `voice=False`. No assertion changed. |

## Friendly sentences and their debug twins

| Failure | Speech mount (friendly) | Other mounts (debug, unchanged) |
| --- | --- | --- |
| Routing raised | Sorry — I couldn't work out where to send that request. I've noted the details. | `Error: routing failed: <cause> [rid <rid>]` |
| Pipeline crash | Sorry — something went wrong on my end. I've noted the details. | `Error: the pipeline failed unexpectedly (<Type>: <cause>) [rid <rid>]` |
| Model server unreachable (connect, timeout, 5xx) | Sorry — I couldn't reach my language model. Try again in a moment. | `[coordinator agent error: <cause>]` |
| Model server refused (LiteLLM 4xx) | Sorry — my language model couldn't handle that request. | `[coordinator agent error: LiteLLM returned 4xx: <detail>]` |
| Terminal music tool raised (tool_service down) | That didn't work — I couldn't get through to the music service. | `That didn't work — the tool failed with <cause>.` |
| `update_magicmirror` raised | That didn't work — I couldn't get through to the magic mirror updater. | same debug line |
| Other terminal tool raised | That didn't work — I couldn't get through to the service that handles that. | same debug line |
| Music failures with their own sentence | unchanged, e.g. I couldn't play that. The speaker may be powered off. | identical |
| Non-terminal tool failed; specialist failed | model-written, with the friendly rule on the failed result / in the failure block | model-written, debug instruction |

The pipeline crash is the one generic sentence: the code does not know the
cause of an unexpected exception (criterion 7's exception).

## Criteria and evidence

1. **One request-scoped value.** `origin.voice` (`origin.py:53`), set once
   in `_run_pipeline` (`main.py:315`) from the mount's argument, read with
   `origin.voice.get()`. `test_signal_per_mount[*]`: True only for
   `/voice/api/chat`, seen at routing and in the agent loop, `False` after
   the run.
2. **Set from the mount only.** `test_content_cannot_turn_it_on[*]`,
   `..._for_message`, `test_content_cannot_turn_it_off`: headers
   (`X-Kronk-Voice`, `X-Voice`, `X-Profile`), body fields (`voice`,
   `profile`, `options.voice`, `model: "voice"`) and system/user text do not
   change it in either direction. `test_the_mount_is_the_only_producer`
   (AST): the only literal `voice=True` in `main.py` is in
   `ollama_chat_voice`; no handler takes the raw `Request`.
3. **No leak between concurrent requests.**
   `test_concurrent_requests_keep_their_own_value`: five pipelines (two
   voice, three not) run interleaved inside `_run_pipeline` with the LLM
   lock lifted and a barrier holding all five in routing. Each sees its own
   value at routing, after the barrier, in the agent loop, and the matching
   `current_style()`. Mutation check: with `origin.voice` replaced by a
   plain module global (scratch copy), the test fails
   (`tests/test_voice_signal.py:218: AssertionError: v1`).
4. **Speech profile uses the same value.** `_ollama_chat_impl` takes
   `voice` instead of `profile`; `render.to_speech` runs exactly when that
   same value goes to the pipeline. Behaviour unchanged: the speech path
   still buffers, scrubs and answers a streaming request with one chunk;
   retracts are still honoured (`test_retract.py` unchanged in its
   assertions). `test_speech_profile_and_signal_agree[*]`,
   `test_voice_mount_streaming_request_is_still_spoken`. One source: the
   mount's argument.
5. **On the trace.** `kronk.voice` on the `pipeline.*` root span
   (`telemetry.py:356`); `test_pipeline_root_carries_the_voice_signal[*]`.
   Live in Tempo, trace `3c667109bbc170c3fbfaed5c26408d35`: `pipeline.shim
   {'kronk.transport': 'voice', 'kronk.http_route': '/voice/api/chat',
   'kronk.voice': True, ...}`. Query `{ span.kronk.voice = true }` over the
   live window returned the 4 dev voice traces;
   `{ span.kronk.voice = true } && { status = error }` returned exactly the
   3 failed ones (below), not the successful one.
6. **Speakable on the speech mount.** Unit tests on `/voice/api/chat`
   through the real app: `test_routing_failure_on_voice_is_friendly_and_logged`,
   `test_pipeline_crash_on_voice_is_friendly_and_logged`,
   `test_unreachable_model_on_voice_is_friendly`,
   `test_refusing_model_on_voice_is_friendly`,
   `test_tool_service_outage_on_voice_is_friendly[home|direct]` (pinned, and
   delegated via `ask_home`, where the style comes from `origin.voice`),
   `test_failed_non_terminal_tool_carries_the_rule_only_on_voice`,
   `test_specialist_failure_reaches_the_coordinator_in_the_mounts_style`.
   Code-authored sentences are checked with `assert_speakable` (no
   `*Error`/`*Exception`, URL, port, 3-digit code, `rid`, brackets/braces,
   `Traceback`, model or LiteLLM names; at most 120 characters). Live: see
   criterion 14. Model-written failure sentences are instructed, not
   guaranteed (see "Departures").
7. **Says what kind of thing failed.** Table above;
   `test_friendly_renderings_say_what_kind_of_thing_failed`,
   `test_terminal_speech_friendly_drops_the_cause_and_names_the_service`.
8. **Music failure sentences unchanged.**
   `test_music_failure_sentences_are_unchanged_in_both_styles` (5 shapes ×
   2 styles). `_terminal_speech` mappings not edited.
9. **Debug unchanged elsewhere, byte for byte.**
   `test_routing_failure_elsewhere_is_debug_unchanged[/message|/api/chat|/v1/chat/completions]`,
   `test_pipeline_crash_elsewhere_is_debug_unchanged[*]`,
   `test_unreachable_model_elsewhere_is_debug_unchanged[*]` (full-string
   matches, rid by pattern), `test_tool_service_outage_elsewhere_is_debug_unchanged[*]`.
   `errors.render` debug branches untouched
   (`test_debug_renderings_are_the_pinned_contracts`). Live `/dev/api/chat`
   kept the debug line (criterion 14). `pipeline_bench.sh` not run (brief).
10. **Logs and traces keep full detail.** Unit: the voice routing and crash
    tests assert the cause in `caplog` and in `end_pipeline`'s
    `status_message` while the user gets the friendly sentence; the
    unreachable/refused/specialist tests assert the cause in
    `status_message`. Live: the orchestrator-dev log for the voice music
    failure carries `"event": "tool_error", "tool": "play_music", "error":
    "[Errno -3] Temporary failure in name resolution"` and the trace's
    httpx span carries `STATUS_CODE_ERROR`, `ConnectError: [Errno -3]
    Temporary failure in name resolution`; found with the one query in
    criterion 5.
11. **On by default, one setting to switch back.** Code default
    (`errors.py:57`). Dev container env after deploy: `ERROR_STYLE=debug`
    only (from `.env.dev`), no `ERROR_STYLE_VOICE`, and voice was friendly
    live. Production's `.env` sets neither (`grep ERROR_STYLE .env` empty),
    so production gets the same on promotion. Switch back:
    `ERROR_STYLE_VOICE=debug` (`docker-compose.yml:221` commented line,
    `.env.dev.example:36`), `test_voice_setting_switches_the_speech_mount_back_to_debug`.
    Docs changed to match the new meaning (see "Departures").
12. **Stale comments removed.** `grep -rn "smoke test" orchestrator
    tests/*.py docker-compose.yml docs/features README.md .env.dev.example`:
    no output. The compose comment carried the same sentence and was
    removed too.
13. **Unit tests.** `tests/test_voice_signal.py` (signal per mount, content
    cannot set it, isolation, single producer, speech profile agreement);
    `tests/test_error_style.py` (each criterion-6 kind on the speech mount
    and on at least one other mount); `tests/test_telemetry.py` (span
    attribute). Both new/rewritten files use the `no_network` fixture.
14. **Live on dev.** Table below. Three kinds of real failure with
    `tool_service-dev` stopped, each on both mounts, then started again.
15. **Test suite.** `2276 passed, 2 skipped, 18 xfailed` (output below).
16. **Replay.** Rerun after the television went idle: all 122 rows ran;
    against the M10 baseline, 111 identical on status and method, 8 new M4b
    rows (7 pass, 1 skip by tag), and three changes, all on the brief's
    flapper list (below). First run was cut short by a playing television
    (below).
17. **Production untouched.** Only `kronk-orchestrator-dev-1` has a new id
    (`9ef634451051` → `bb1337f9f7b5`). Every production container, HA, MA,
    telemetry, `wyoming-piper`, `bgutil-pot-provider` and `tool_service-dev`
    (`b1f1a9092de5`, stopped and started, same container) keep theirs. No
    HA/MA restart, nginx not restarted. No sound: the music failures ran
    with `tool_service-dev` down, the replay is dry-run gated, players
    unchanged (17 checked, both runs).
18. **Docs.** `docs/features/voice-pipeline.md` "The voice signal (sprint
    M2)"; `docs/features/verbose-errors.md` config and sentence tables;
    `README.md:259-264`.

## Live on dev (criterion 14)

Deploy: `docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` at 00:15 UTC; again after a docstring-only change in
`telemetry.py`, so the running image matches the commit (md5 of `main.py`,
`errors.py`, `origin.py`, `agents.py`, `telemetry.py`, `tools.py`, `render.py`
in the container equal the working tree's). The live failure runs below
were made on the first image; the only difference is that docstring.

Control, `tool_service-dev` up, "what's the weather tomorrow": voice 200 in
5.1 s, trace `525f9b4d…`, a forecast; api 200 in 2.9 s, trace `0f61cbf1…`,
a forecast.

`docker compose --profile dev stop tool_service-dev` at 00:16:02 UTC. Every
request returned HTTP 200. Each `tool_error` was `[Errno -3] Temporary
failure in name resolution` (Docker DNS has no record for a stopped
container).

| Request | Mount | s | Trace | Route → tool | Reply (verbatim) |
| --- | --- | --- | --- | --- | --- |
| play some jazz | voice | 3.5 | `3c667109bbc170c3fbfaed5c26408d35` | music → home:play_music | That didn't work — I couldn't get through to the music service. |
| play some jazz | api | 2.7 | `daedfe9ef64c0322c21eb4ddf393d244` | music → home:play_music | That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution. |
| what's the weather in Paris tomorrow | voice | 4.2 | `2a16f39d7b35d60278e138c56c843286` | weather → home:get_weather | I'm sorry, I was unable to retrieve the weather information for Paris because there was a temporary network issue. |
| what's the weather in Paris tomorrow | api | 3.2 | `377859e312ee878ba13ac744adbeaef5` | weather → home:get_weather | I am sorry, but I encountered a temporary error while trying to check the weather for Paris. Please try again in a little while. |
| what's on my shopping list | voice | 5.7 | `c4d8904827f14a3004efcfbfa86c8ecf` | direct → coordinator:ask_home → home:shopping_list_view | The shopping list service experienced a temporary failure. |
| what's on my shopping list | api | 5.5 | `367fad474ac3607f6974fb1f90e06172` | direct → coordinator:ask_home → home:shopping_list_view | The shopping list is currently unavailable due to a temporary network issue with the home system. |

`docker compose --profile dev up -d tool_service-dev` at 00:17:08 UTC;
healthy 15 s later.

The music row is the code-authored sentence, friendly on voice and the
debug twin on `/api/chat`. The weather and shopping rows are model-written
in both styles; this model already paraphrased the failure on `/api/chat`,
so the two styles read alike there. The shopping row goes through the
delegated `ask_home` path, where the specialist's style now comes from
`origin.voice`.

Not produced live: routing failure, pipeline crash, unreachable or refusing
model server. There is no safe way inside the dev pair. Routing is
rule-based and does not fail on input. The shims ignore the client's
`model` field (`COORDINATOR_MODEL` is used), so a request cannot name a
missing model. Changing the dev model needs an `.env.dev` edit, and
stopping LiteLLM or a llama unit is forbidden. Those kinds are covered by
the unit tests through the real app.

## Test output

```
$ ./scripts/run_tests.sh -q        # after the code change, before the test updates
FAILED tests/test_error_style.py::test_default_is_debug - AssertionError: ass...
FAILED tests/test_error_style.py::test_voice_override_applies_only_to_shim - ...
FAILED tests/test_error_style.py::test_invalid_value_falls_back_to_debug - As...
FAILED tests/test_error_style.py::test_friendly_style_adds_phrasing_rule_to_system_prompt
FAILED tests/test_error_style.py::test_terminal_speech_friendly_drops_the_cause
FAILED tests/test_retract.py::test_voice_collector_drops_retracted_text - Typ...
FAILED tests/test_retract.py::test_streaming_shims_pass_tokens_and_skip_retracts
7 failed, 2208 passed, 2 skipped, 18 xfailed, 3 warnings in 16.11s
$ ./scripts/run_tests.sh -q        # after the test updates
2276 passed, 2 skipped, 18 xfailed, 3 warnings in 16.18s
$ ./scripts/run_tests.sh -q        # before the commit, after the live runs
2276 passed, 2 skipped, 18 xfailed, 3 warnings in 16.22s
```

The five `test_error_style.py` failures pinned the old behaviour (shim =
voice, voice default debug, friendly rule in the system prompt, the old
friendly fallback); they were rewritten to the new behaviour. The two
`test_retract.py` failures were `TypeError`s from stand-ins that did not
accept `voice`. The pre-change baseline was not rerun; the brief gives it
(`2215 passed, 2 skipped, 18 xfailed`), and `2208 + 7 = 2215`.

## Replay output

First run, 00:18 UTC:

```
$ python3 scripts/voice_replay.py --label m2-build
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  … pass / xfail rows …
voice_replay: {'pass': 87, 'xfail': 16, 'skip': 19}; executed 37, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-pay-the-album-hotel-costes-3: fuzzy local match (HassSetVolume) while a player is active; not executed
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped eval-shuffle-the-anime-bangers-playlist-on-sonos: fuzzy local match (HassTurnOn) while a player is active; not executed
  skipped eval-resume-the-music: fuzzy local match (HassMediaUnpause) while a player is active; not executed
  skipped eval-resume-the-music-2: fuzzy local match (HassMediaUnpause) while a player is active; not executed
  skipped eval-what-song-is-this: fuzzy local match (HassGetCurrentTime) while a player is active; not executed
  skipped eval-what-s-playing: fuzzy local match (HassGetCurrentTime) while a player is active; not executed
  skipped eval-what-song-is-this-2: fuzzy local match (HassGetCurrentTime) while a player is active; not executed
  skipped eval-skip-the-current-song: fuzzy local match (HassMediaNext) while a player is active; not executed
  skipped eval-skip-treat-me-my-wallet-son: fuzzy local match (HassMediaNext) while a player is active; not executed
  skipped eval-volume-50: fuzzy local match (HassSetVolumeRelative) while a player is active; not executed
  skipped eval-what-song-is-this-3: fuzzy local match (HassGetCurrentTime) while a player is active; not executed
  skipped eval-shuffle-it: fuzzy local match (HassGetCurrentTime) while a player is active; not executed
  skipped eval-what-song-is-playing-in-the-kitchen: fuzzy local match (HassClimateGetTemperature) while a player is active; not executed
  skipped eval-what-s-playing-on-the-kitchen: fuzzy local match (HassClimateGetTemperature) while a player is active; not executed
  skipped eval-what-s-playing-on-the-kitchen-2: fuzzy local match (HassClimateGetTemperature) while a player is active; not executed
  skipped m4b-upgrade-the-mirror-latest: fuzzy local match (HassMediaNext) while a player is active; not executed
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_001807_m2-build.json
exit=0
```

The active player was `media_player.upper_shield`, playing in HBO Max since
00:02:13 UTC (HA states, read-only). No row failed; 16 rows did not run.
A read-only poll of HA states every 30 s found no player playing at
00:31:37 UTC, and the replay was rerun once:

```
$ python3 -u scripts/voice_replay.py --label m2-build-rerun
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  X xpass executed   eval-shuffle-it  []
  F fail  executed   eval-search-youtube-music-for-some-deep-chill  [tool []]
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 98, 'xfail': 19, 'skip': 3, 'xpass': 1, 'fail': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_003150_m2-build-rerun.json
exit=1
```

(The 117 `pass`/`xfail` lines are elided; the per-row record is the
committed result file.)

Rerun against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`,
row by row on status and method:

| Row | Baseline | Rerun |
| --- | --- | --- |
| 111 of the 114 baseline rows | | identical |
| `inc-0930-search-deep-house-ytm` | fail | pass (known YouTube Music flapper; also passed in the first run) |
| `eval-search-youtube-music-for-some-deep-chill` | pass | fail (known YouTube Music flapper) |
| `eval-shuffle-it` | xfail | xpass (known flapper) |
| 7 `m4b-*` rows | new | pass |
| `m4b-fetch-and-run-on-the-mirror` | new | skip (typed link) |

`eval-search-youtube-music-for-some-deep-chill` (trace
`9da3b227e6ed13e1c3f410b20ff3b3cd`): route `direct/default`, one coordinator
round, no tool call, no error event; spoken "I can control the music that
is currently playing, but I cannot search YouTube Music for specific genres
or playlists." No failure path ran, so nothing in M2 applies. It passed in
the first run. `eval-shuffle-it` (trace `95898497…`): home escalated, then
asked which playlist; no failure path. Successful answers: the system
prompt of every request is unchanged by this build (test
`test_system_prompt_is_the_same_in_both_styles`), and the friendly rule
only reaches the model on a failed tool result.

## Departures, and why

- **The friendly phrasing rule moved from the system prompt to failed tool
  results.** Before, friendly style appended `FRIENDLY_TOOL_PHRASING` to
  every agent's system prompt. Turned on for voice, that would change the
  prompt of every spoken request, including successful ones, which the
  brief rules out. Now the rule is appended only to a tool result that
  matches the code's own failure shapes (`errors.is_failed_tool_result`),
  next to the failure it is about. Model-written failure sentences (a
  non-terminal tool or a specialist failure) are still instructed, not
  guaranteed: criterion 6 holds for those as far as the model follows the
  rule. In the live runs it did. No structural scrub was added; if the e2e
  pass finds technical text in a spoken model-written failure, that is the
  next step, not more prompt.
- **`ERROR_STYLE_VOICE` changed meaning, and so did its default.** It used
  to override the whole shim transport (`/api/chat` and `/v1` too) and
  inherited `ERROR_STYLE` when unset. It now covers only `/voice/api/chat`
  and defaults to `friendly` on its own, so `ERROR_STYLE=debug` (as
  `.env.dev` sets) no longer reaches voice. Docs are changed to match
  (criterion 11 allows this).
- **Delegated specialists follow the request's style.** `run_stream`'s
  default was hard-coded `DEBUG`, so a specialist the coordinator reached
  via `ask_*` spoke debug fallbacks on voice (the delegated `ask_home →
  play_music` path read "the tool failed with …" aloud). It now reads
  `origin.voice`. On non-voice mounts this is the same as before unless
  `ERROR_STYLE=friendly` is set globally.
- **New and changed friendly sentences.** Routing has its own sentence, the
  `llm` kind splits refused from unreachable, and the terminal-tool fallback
  names the service (was "That didn't work — I couldn't finish that
  request."). Friendly style was never enabled anywhere, so no user heard
  the old wording.
- **`kronk.transport` already read `voice`** on the speech mount (derived
  from `http.route`). `kronk.voice` is added from the signal itself, so it
  stays right if a route is renamed.
- **Three failure kinds were not produced live** (routing, crash, model
  server). There is no safe means inside the dev pair; see "Live on dev".

## What the coordinator must do at promotion

1. `docker compose up -d --build orchestrator`, then `docker compose
   restart nginx` (stale-upstream gotcha). No `tool_service` change in M2.
2. Check `.env` still sets neither `ERROR_STYLE_VOICE` nor `ERROR_STYLE`
   to friendly (checked 2026-11-17: neither set). With it unset,
   production voice turns friendly on deploy.
3. Verify both shims: a normal request on `/api/chat` and on
   `/voice/api/chat`; then in Tempo, `{ span.kronk.voice = true }` should
   list the voice request from production's `kronk-orchestrator` service.
4. HA's Ollama integration already points at `/voice`; no HA or MA change
   or restart is needed.
5. Benches that read debug text on `/api/chat` and `/v1` see no change
   (criterion 9).
6. Ask Drew for an "Okay Nabu" check if wanted; a spoken failure needs a
   real outage, so a normal request is the realistic check.

## Guard rails

- Production: no container recreated; production `tool_service` log since
  00:07 UTC has 0 `/ops/exec`, `/magicmirror/update` or `/http_check` lines.
  No LiteLLM, llama unit or production container touched. No HA or MA
  restart; HA was used only for read-only state reads and the replay's
  `assist_pipeline/run` on `kronk-dev`.
- Dev: only `tool_service-dev` was stopped and started (00:16:02 →
  00:17:08 UTC). The dev pair is running and healthy.
- `.env`, `.env.dev` not edited (`HA_TOKEN` read from `.env` for HA state
  reads). No `down -v`, no `--remove-orphans`, no `pipeline_bench.sh`, no
  LAN scan; the only HTTP targets were `localhost`. No command to the
  MagicMirror Pi or any managed host. `kronk-hottub-monitor.service`
  active, not touched.
- No sound: music requests ran only while `tool_service-dev` was down; the
  replay was dry-run gated and found players unchanged.
- Scratch (session scratchpad): the request driver, the replay logs, the
  TV wait script and a mutation copy of the tree (removed). `rm` guarded
  with `${S:?}`. No symlinks to repo data or secrets. No background job
  left running.
