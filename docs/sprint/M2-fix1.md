# M2 fix 1: one exit for code-written failure text, and a speech backstop

Fix agent, round 1 of at most 3, 2026-11-17 (UTC), branch `refactor`.
Inputs: `docs/sprint/M2-criteria.md`, `docs/sprint/M2-build.md`,
`docs/sprint/M2-e2e.md` (criterion 6 failed: findings 1 to 3; observations
4 to 7). Fix commit: `408b34c`; this report is the commit after it. No push,
no tag.

## Summary

- The class: text the code writes for a failure reached the speaker through
  a path that skipped the error style. Every path where the orchestrator
  writes user-visible failure text now leaves through one function,
  `errors.failure_text(debug, style, friendly)`
  (`orchestrator/errors.py:109`): the friendly sentence in friendly style,
  the debug text unchanged otherwise.
- Findings 1 to 3 fixed. On `/dev/voice/api/chat` the news request with
  `tool_service-dev` stopped now speaks "That didn't work — I couldn't get
  through to the news service." (5 of 5); the mirror preflight failure
  speaks "I couldn't start the magic mirror update. The details are in the
  logs."; an empty model reply speaks "Sorry — my language model didn't come
  up with an answer. Try asking again." (unit test; not producible live).
  `/dev/api/chat` keeps the debug text byte for byte.
- Structural backstop, speech mount only: `errors.speech_backstop`
  (`errors.py:288`, called at `main.py:957`) replaces the final text of a
  *failed* turn that still carries one of the code's debug shapes with the
  generic sentence, logs the original and sets it on the span. A turn with
  no recorded failure is never checked.
- Tests: `2349 passed, 2 skipped, 18 xfailed` (baseline `2276 passed,
  2 skipped, 18 xfailed`; +73). 55 of the new or changed tests fail on the
  pre-fix tree; the 23 that pin unchanged behaviour are shown by mutants
  instead (12 of 12 killed).
- Replay: 122 rows, 3 skipped by design (no history, typed links), none for an active player. Against the M10 baseline 111 of 114 rows are identical on status and method; two changes are known flappers and one, `eval-what-s-playing`, failed once on a model that wrote its tool call as text (no tool ran, no failure path ran). That row passed on an immediate rerun, and its model inputs are byte-identical before and after the fix. The two pinned `m4b` mirror rows now speak the friendly sentence.

## The enumeration: every place the orchestrator writes user-visible failure text

"User-visible" means the text reaches the user without a model rewriting
it. Model-facing text (tool results, the specialist-failure block) is listed
separately, because the model, not the code, writes what the user hears;
there the friendly rule rides on the failed result and the backstop is the
check.

| # | Path | Where | Before (speech mount) | Now |
| --- | --- | --- | --- | --- |
| 1 | Routing raised | `main.py:344` → `errors.render("routing")` | friendly (M2 build) | `render` builds the debug text and returns through `failure_text` (`errors.py:85-106`) |
| 2 | Coordinator stream error (model server unreachable or refusing) | `main.py:482` → `render("llm")` | friendly | through `failure_text` |
| 3 | Pipeline crash | `main.py:504` → `render("pipeline")` | friendly | through `failure_text` |
| 4 | Empty model reply, normal round | `agents.py:771` | `[coordinator agent returned no response]` (finding 3) | `_empty_reply` (`agents.py:620`) → `failure_text`, friendly `FRIENDLY_EMPTY_REPLY` |
| 5 | Empty model reply, forced synthesis | `agents.py:997` | same bracketed line | same as 4 |
| 6 | Empty reply of a *delegated* specialist | `agents.py:771`/`997` inside `run_delegated` | model-facing (coordinator reads it) | stays the debug marker on every mount (`user_style`, `agents.py:664`, `delegated=True` at `agents.py:1013`); now a recognised failed result, so the friendly rule rides on it on voice |
| 7 | Terminal tool raised (tools.execute's `[Tool X error: …]`) | `_terminal_speech`, `agents.py:183-186` | friendly for music and mirror; news spoke it raw (finding 1) | through `failure_text`, friendly `friendly_tool_failure(tool)`; `news_brief` added to the service map ("the news service") |
| 8 | Terminal tool refused its arguments (`[play_music error: query is required]`) | `agents.py:187-188` | "couldn't get through to the music service" (observation 4) | through `failure_text`, friendly `friendly_tool_bad_request(tool)` |
| 9 | Unmapped terminal result | `agents.py:189` | `friendly_tool_failure` | unchanged sentence, now through `failure_text` |
| 10 | Mirror update failed (`Could not update the magic mirror: <detail>`) | `agents.py:170-177` | the preflight detail (finding 2) | through `failure_text`, friendly `FRIENDLY_MIRROR_NOT_STARTED`; the detail is logged and on the span |
| 11 | Music failures with their own sentence (`Could not play music:` / `control` / `rate` / `save music taste` / `read the player`) | `agents.py:140-165` | own sentence, both styles | through `failure_text(…, None)`: identical in both styles (criterion 8); the call marks the turn failed, which arms the backstop |
| 12 | `news_brief` raised | `_news_speech`, `agents.py:98-114` | `[Tool news_brief error: …]` (finding 1) | as 7 |
| 13 | `news_brief` unavailable (tool_service answered an error; e2e F16) | `agents.py:107-108`, prefix `tools.NEWS_UNAVAILABLE` (`tools.py:969`) | tool_service's detail spoken | through `failure_text`, friendly `FRIENDLY_NEWS_UNAVAILABLE` |
| 14 | `news_brief` refresh failed, cached brief follows | `agents.py:109-113`, prefix `tools.NEWS_REFRESH_FAILED` (`tools.py:968`) | the refresh cause (exception text) spoken | the note goes through `failure_text` (friendly `FRIENDLY_NEWS_REFRESH_FAILED`); the brief after it is untouched |
| 15 | Delegated terminal passthrough | `agents.py:879` | relays the specialist's text | unchanged: the specialist rendered it through 7 to 11 in the request's style |
| 16 | Mirror refusal (`Magic mirror update refused:`) | `agents.py:166-169` | the fixed M4b sentence | unchanged in both styles: a refusal, not a failure (coordinator ruling) |
| 17 | Forced-synthesis tool-syntax scrub ("I ran out of research steps …") | `agents.py:966` | a plain sentence, both styles | unchanged: it is a guardrail sentence on a partial answer, not failure text in a debug shape; it does not arm the backstop |
| 18 | Shim protocol errors (no user message → 400 JSON; bad body → 422) | `main.py:759`, `:942` | JSON | unchanged (observation 7, coordinator ruling) |
| 19 | Clear-history confirmation, web-UI-only endpoints (`/files`, `/api/shopping_list`) | `main.py:678` and below | not failures, or not a speech path | unchanged |

Model-facing failure text (the model phrases it for the user):

| Path | Where | What it does now |
| --- | --- | --- |
| Failed non-terminal tool result | `agents.py:855-905` | `errors.is_failed_tool_result` now also matches `[<tool> error: …]`, `[Unknown tool: …]`, `[no such specialist: …]`, `[<agent> agent returned no response]`, `[Hot tub status unknown: …]`, `[Hot tub status unavailable]`, `[Solar energy — <period>] no figure available…` (observation 5, `errors.py:226`). On voice the friendly rule is appended to those results; every failed result also marks the turn failed (`agents.py:861`). |
| Specialist failed, coordinator falls back | `main.py:441` | unchanged block; the pipeline error now marks the turn failed (`main.py:510`). |
| Tool rejected / duplicate call | `agents.py` rejection and dedup lines | unchanged: instructions to the model, not failures to report. |

### The turn's failures, and the backstop

- `errors.begin_turn()` / `note_failure()` / `turn_failures()`
  (`errors.py:248-266`): a request-scoped `ContextVar` holding a list.
  `_kronk_pipeline_tokens` opens it for every shim turn and closes with one
  `{"type": "outcome", "failures": [...]}` event (`main.py:684-695`); the
  OpenAI and streaming Ollama framings already skip non-token events, so
  nothing else sees it. A failure is noted by every `failure_text` call,
  every failed tool result the loop sees, and the pipeline's own error.
- `_ollama_collect_turn` (`main.py:881`) returns `(reply, failures)`;
  `_ollama_collect` keeps its signature for the display path.
- On the speech mount (`main.py:953-957`) the reply is rendered for speech
  and then passed to `errors.speech_backstop`. With failures recorded and
  `ERROR_STYLE_VOICE` friendly, a match of `_DEBUG_SHAPES` (`errors.py:271`:
  bracketed tool/agent markers and the code's bracketed failure prefixes,
  `Errno`, exception class names, `Traceback (most recent call last)`, URLs
  with a port, paths under `/keys/` or `/data/`) replaces the text with
  "Sorry — something went wrong on my end. I've noted the details." The
  original goes to the log (`speech_backstop` event, `logger.warning`) and
  to the current span, which by then is the request's HTTP server span
  (`kronk.speech_backstop`, `.matched`, `.original`); the pipeline root has
  already ended.
- When `failure_text` substitutes a friendly sentence it emits
  `failure_spoken_friendly` with the debug text and sets
  `kronk.failure.debug_text` / `kronk.failure.spoken` on the current span
  (the pipeline root), so "the details are in the logs" is true.

## What changed

| File:line | Change |
| --- | --- |
| `orchestrator/errors.py:1-40` | Docstring: the one exit and the backstop. Imports `telemetry`, `events.emit`, `ContextVar`, a logger. |
| `orchestrator/errors.py:85-106` | `render` builds the debug text and returns through `failure_text`. Debug renderings unchanged. |
| `orchestrator/errors.py:109-130` | New `failure_text`. |
| `orchestrator/errors.py:142-180` | `news_brief` in the service map; `_service`; `friendly_tool_bad_request`; `FRIENDLY_MIRROR_NOT_STARTED`, `FRIENDLY_NEWS_UNAVAILABLE`, `FRIENDLY_NEWS_REFRESH_FAILED`, `FRIENDLY_EMPTY_REPLY`. |
| `orchestrator/errors.py:226-232` | `_FAILED_RESULT` widened (observation 5). |
| `orchestrator/errors.py:238-310` | Turn failure record; `_DEBUG_SHAPES`, `debug_shape`, `speech_backstop`. |
| `orchestrator/agents.py:98-119` | New `_news_speech`, `_TOOL_BAD_REQUEST`. |
| `orchestrator/agents.py:122-189` | `_terminal_speech`: every failure branch returns through `failure_text`; success branches unchanged. |
| `orchestrator/agents.py:620-625` | New `_empty_reply`. |
| `orchestrator/agents.py:632`, `:660-664` | `run_stream(delegated=False)`; `user_style` for model-facing text in delegated runs. |
| `orchestrator/agents.py:771`, `:997` | Empty-reply tokens through `_empty_reply`. |
| `orchestrator/agents.py:858-861` | A failed tool result marks the turn failed. |
| `orchestrator/agents.py:1013` | `run_delegated` passes `delegated=True`. |
| `orchestrator/tools.py:966-969`, `:988-1001` | `NEWS_REFRESH_FAILED`, `NEWS_UNAVAILABLE` constants, used by the handler; output byte-identical. |
| `orchestrator/main.py:509-510` | The pipeline error marks the turn failed. |
| `orchestrator/main.py:681-695` | `_kronk_pipeline_tokens` opens the turn record and closes with the `outcome` event. |
| `orchestrator/main.py:881-906` | `_ollama_collect_turn`; `_ollama_collect` wraps it. |
| `orchestrator/main.py:951-957` | Speech mount: `speech_backstop(render.to_speech(reply), failures)`. |
| `tests/test_spoken_failures.py` | New, 73 tests (below). |
| `tests/test_mm_update_pin.py:345-356`, `:485` | The five voice rows pinned the preflight detail on the speech mount (finding 2); they now expect `FRIENDLY_MIRROR_NOT_STARTED` on voice and today's text on the other transports. |
| `docs/features/verbose-errors.md` | Sentence table extended; `failure_text`, the two enumeration tests, the delegated empty reply, the backstop; one blog hook. |
| `docs/features/voice-pipeline.md:62-66` | Two sentences pointing at the one exit and the backstop. |

`tests/voice_corpus.jsonl` was not edited: no row pins spoken text (the
two pinned `m4b` rows expect route, rule and tool only).

## New sentences beside their debug twins

| Failure | Speech mount (friendly) | Other mounts (debug, unchanged) |
| --- | --- | --- |
| `news_brief` raised (tool_service down) | That didn't work — I couldn't get through to the news service. | `[Tool news_brief error: <cause>]` |
| News brief unavailable | The news brief isn't available right now. Ask me to research the news live if you need it sooner. | `The news brief isn't available right now — <detail>. Ask me to research the news live if you need it sooner.` |
| News refresh failed, cached brief follows | I couldn't fetch fresh news, so here's the latest brief I have. ⏎⏎ <brief> | `I couldn't fetch fresh news (<cause>) — here's the latest brief I have.` ⏎⏎ <brief> |
| Mirror update did not start | I couldn't start the magic mirror update. The details are in the logs. | `I couldn't update the magic mirror. <tool_service detail>` |
| Model returned nothing | Sorry — my language model didn't come up with an answer. Try asking again. | `[<agent> agent returned no response]` |
| Terminal tool refused its arguments | That didn't work — I didn't get enough detail to ask the music service. Try saying it again. | `play_music error: query is required` |
| Backstop (failed turn, debug shape in the final text) | Sorry — something went wrong on my end. I've noted the details. | not applied |

Unchanged: the routing, crash, model-server and terminal-tool sentences of
the M2 build, the music failure sentences (criterion 8) and the M4b
refusal.

## Tests

`tests/test_spoken_failures.py` (all behind `no_network`; tool_service and
the model are stubbed):

- Finding 1: news with tool_service down on `/voice/api/chat` (friendly) and
  `/api/chat` (exact debug line); news unavailable and refresh failed, both
  mounts; a successful brief spoken unchanged.
- Finding 2: preflight failure on voice (friendly, and the debug text in the
  `failure_spoken_friendly` log line) and on `/api/chat` (exact text); the
  refusal unchanged on both.
- Finding 3: empty reply on voice (friendly) and on `/api/chat`,
  `/message`, `/v1/chat/completions` (exact marker); delegated empty reply
  reaches the coordinator as today on both mounts.
- Observation 4: argument refusal sentence, both styles. Observation 5: the
  seven new failed shapes, three successes that must not match.
- Survivor M12: a successful `get_weather` result reaches the model
  byte-identical on `/voice/api/chat` and `/api/chat`.
- Survivor M03: `_kronk_pipeline_tokens` and `_run_pipeline` driven in the
  test's own context with `voice=True`; afterwards `origin.voice` is False
  and the turn record is gone.
- Enumeration, two tests. (a) AST of `main.py` and `agents.py`: every
  `{"type": "token", "text": X}` has `X` a variable or a call into
  `errors.*` / `_empty_reply`; a literal or f-string fails unless it is
  "Done — fresh start.". (b) Every terminal tool of every agent (7 today,
  read from `AGENTS`), driven through `tools.execute` with tool_service
  down, with an HTTP 502 whose detail carries every debug shape, and with no
  arguments; its friendly speech must carry no debug shape (music
  own-sentence failures must instead be identical in both styles). A new
  terminal tool is picked up automatically.
- Backstop: each debug shape recognised; plain sentences not; replacement
  logged and on the span; never applied to a turn without a failure; off
  with `ERROR_STYLE_VOICE=debug`; a model that echoes `[Errno -3] …` after a
  failed tool is replaced on voice and untouched on `/api/chat`; a model
  that reports the failure in plain words is kept; a successful voice answer
  containing `http://tool_service:8103` and `/data/state.json` is spoken as
  written and the backstop saw no failures.

### The new tests fail before the fix

The new file and the changed `tests/test_mm_update_pin.py` were copied into
a `git archive` of `9d27f2e` (the tree before the fix) and run there with
`tests/.venv`:

```
$ tests/.venv/bin/python -m pytest tests/test_spoken_failures.py tests/test_mm_update_pin.py -q
FAILED tests/test_spoken_failures.py::test_news_with_tool_service_down_on_voice_is_friendly
FAILED tests/test_spoken_failures.py::test_news_unavailable_drops_the_detail_only_on_voice
FAILED tests/test_spoken_failures.py::test_news_refresh_failure_keeps_the_brief_and_drops_the_cause_only_on_voice
FAILED tests/test_spoken_failures.py::test_mirror_preflight_failure_on_voice_is_not_spoken
FAILED tests/test_spoken_failures.py::test_empty_model_reply_on_voice_is_friendly
FAILED tests/test_spoken_failures.py::test_delegated_empty_reply_reaches_the_coordinator_as_today[/voice/api/chat-True]
FAILED tests/test_spoken_failures.py::test_terminal_tool_argument_refusal_says_what_failed
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[Hot tub status unknown: no data]]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[Hot tub status unavailable]]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[Solar energy \u2014 today] no figure available: inverter offline. Tell the user plainly.]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[Unknown tool: frobnicate]]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[play_music error: query is required]]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[home agent returned no response]]
FAILED tests/test_spoken_failures.py::test_more_failed_result_shapes_are_recognised[[no such specialist: plumbing]]
FAILED tests/test_spoken_failures.py::test_voice_signal_and_turn_record_are_reset_after_the_run
FAILED tests/test_spoken_failures.py::test_no_literal_text_reaches_the_user_without_the_error_style[agents.py]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-control_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-news_brief]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-now_playing]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-play_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-rate_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-remember_music_preference]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[down-update_magicmirror]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[http_error-news_brief]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[http_error-update_magicmirror]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[bad_args-control_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[bad_args-news_brief]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[bad_args-play_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[bad_args-rate_music]
FAILED tests/test_spoken_failures.py::test_every_terminal_tool_failure_is_speakable_on_voice[bad_args-update_magicmirror]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[[Tool get_weather error: boom]-[Tool get_weather error]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[[home agent error: LiteLLM returned 500]-[home agent error]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[[coordinator agent returned no response].-[coordinator agent returned no response]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[I got [Errno -3] Temporary failure in name resolution.-Errno]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[Traceback (most recent call last): File x-Traceback (most recent call last)]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[It raised ConnectError.-ConnectError]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[Try http://tool_service-dev:8103/news.-http://tool_service-dev:8103]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[The key at /keys/kronk-mm-update is missing.-/keys/]
FAILED tests/test_spoken_failures.py::test_debug_shapes_are_recognised[Check /data/hottub/state.json.-/data/]
FAILED tests/test_spoken_failures.py::test_plain_sentences_have_no_debug_shape[Paris.]
FAILED tests/test_spoken_failures.py::test_plain_sentences_have_no_debug_shape[It's 5:30 PM and 61 degrees.]
FAILED tests/test_spoken_failures.py::test_plain_sentences_have_no_debug_shape[Now playing Glory Box by Portishead on the Office speaker.]
FAILED tests/test_spoken_failures.py::test_plain_sentences_have_no_debug_shape[The weather service isn't answering right now.]
FAILED tests/test_spoken_failures.py::test_plain_sentences_have_no_debug_shape[See example.com for details.]
FAILED tests/test_spoken_failures.py::test_backstop_replaces_a_failed_turns_debug_text_and_records_it
FAILED tests/test_spoken_failures.py::test_backstop_never_touches_a_turn_without_a_failure[[Tool x error: [Errno -3]] at http://h:8080 /keys/k Traceback (most recent call last)]
FAILED tests/test_spoken_failures.py::test_backstop_never_touches_a_turn_without_a_failure[Paris.]
FAILED tests/test_spoken_failures.py::test_backstop_is_off_when_the_operator_asks_for_debug_on_voice
FAILED tests/test_spoken_failures.py::test_model_echoing_debug_text_on_a_failed_voice_turn_is_replaced
FAILED tests/test_spoken_failures.py::test_backstop_never_touches_a_successful_voice_answer
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Update the magicmirror-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Update the magic mirror-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[update the MagicMirror.-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Okay, update the magic mirror please-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_pinned_sentence_as_last_message_after_history_still_runs[voice_api_chat]
55 failed, 109 passed, 3 warnings in 0.90s
```

The 23 new tests that pass on the pre-fix tree pin behaviour that must not
change (criteria 8, 9, 16): debug text on the other mounts, the refusal, a
successful news brief, music own-sentence failures, successful results,
`main.py` having no literal token today. They cannot fail on correct
pre-fix code, so each is shown by a mutant of the fixed tree instead.
Scratch copy of the tracked tree, one mutant at a time, whole suite with
`-x`, file restored after each (all `orchestrator/*.py` compared equal to
the tree afterwards); unmutated: `2349 passed, 2 skipped, 18 xfailed`.

| Mutant | Result | First failing test |
| --- | --- | --- |
| M12 friendly rule appended to every tool result | killed | `tests/test_spoken_failures.py::test_successful_tool_result_reaches_the_model_unchanged[/voice/api/chat]` |
| M03 origin.voice never reset | killed | `tests/test_spoken_failures.py::test_voice_signal_and_turn_record_are_reset_after_the_run` |
| B1 backstop ignores the failed-turn check | killed | `tests/test_spoken_failures.py::test_backstop_never_touches_a_turn_without_a_failure[…]` |
| B2 backstop ignores `ERROR_STYLE_VOICE=debug` | killed | `tests/test_spoken_failures.py::test_backstop_is_off_when_the_operator_asks_for_debug_on_voice` |
| B3 backstop removed | killed | `tests/test_spoken_failures.py::test_model_echoing_debug_text_on_a_failed_voice_turn_is_replaced` |
| F1 `failure_text` friendly on every mount | killed | `tests/test_agentic_loop.py::test_router_failure_message_is_specific_not_speculative` |
| F2 delegated empty reply follows the request style | killed | `tests/test_spoken_failures.py::test_delegated_empty_reply_reaches_the_coordinator_as_today[/voice/api/chat-True]` |
| F3 music own sentence gets a friendly replacement | killed | `tests/test_error_style.py::test_music_failure_sentences_are_unchanged_in_both_styles[friendly-…]` |
| F4 failed tool results not noted for the backstop | killed | `tests/test_spoken_failures.py::test_model_echoing_debug_text_on_a_failed_voice_turn_is_replaced` |
| F5 news success passthrough rewritten | killed | `tests/test_spoken_failures.py::test_successful_news_brief_is_spoken_unchanged` |
| E1 a literal failure token added to `main.py` | killed | `tests/test_spoken_failures.py::test_no_literal_text_reaches_the_user_without_the_error_style[main.py]` |
| E2 a terminal failure branch bypasses `failure_text` | killed | `tests/test_magicmirror.py::test_terminal_speech_mappings_for_magicmirror` (first under `-x`) |

### Test output

```
$ ./scripts/run_tests.sh -q        # after the code change, before the test updates
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Update the magicmirror-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Update the magic mirror-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[update the MagicMirror.-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_trigger_runs_the_update_on_every_transport[Okay, update the magic mirror please-voice_api_chat]
FAILED tests/test_mm_update_pin.py::test_pinned_sentence_as_last_message_after_history_still_runs[voice_api_chat]
5 failed, 2271 passed, 2 skipped, 18 xfailed, 3 warnings in 16.27s
$ ./scripts/run_tests.sh -q        # after the test updates
2349 passed, 2 skipped, 18 xfailed, 3 warnings in 16.34s
$ ./scripts/run_tests.sh -q        # before this report's commit
2349 passed, 2 skipped, 18 xfailed, 3 warnings in 16.41s
```

The five failures are the M4b tests that asserted the preflight detail on
the speech mount, which is finding 2; they were changed to expect the
friendly sentence on voice only.

## Live on dev

Deploy: `docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` at 01:26:59 UTC. `orchestrator-dev` recreated
(`bb1337f9f7b5` → `8ad5f039592f`); `tool_service-dev` image unchanged, same
container (`b1f1a9092de5`). md5 of `errors`, `agents`, `main`, `tools`,
`origin`, `render`, `telemetry` in the container equal the tree's. All
requests to `http://127.0.0.1/dev/…`, one user message, every reply HTTP
200.

Controls, `tool_service-dev` up:

| UTC | Mount | Said | s | Reply |
| --- | --- | --- | --- | --- |
| 01:27:23 | voice | what's the weather tomorrow | 3.7 | Tomorrow in Laurel, MD, will be Sunny with a high near 64°F and a West wind around 3 mph. |
| 01:27:26 | api | what's the weather tomorrow | 3.1 | Tomorrow in Laurel, MD, will be Sunny with a high near 64°F. Winds are expected to be from the West at 3 mph. |
| 01:27:28 | voice | what is the capital of France | 1.6 | Paris. |
| 01:27:28 | api | what is the capital of France | 0.6 | Paris. |
| 01:27:29 | voice | what's the news today | 0.8 | the full evening brief, speech-rendered ("Evening news brief, generated Mon 06:00 PM: ⏎⏎ World: ⏎ California Sues OpenAI Over Hacking Risks. …", 2,390 characters) |
| 01:27:30 | api | what's the news today | 0.8 | the same brief with its markdown ("### World", "**…**", 2,411 characters) |

Finding 2, `tool_service-dev` up (no `/keys` mount: its log shows two
`POST /magicmirror/update` → `502 Bad Gateway`; nothing left the host):

| UTC | Mount | Said | s | Reply (verbatim) | Trace |
| --- | --- | --- | --- | --- | --- |
| 01:27:37 | voice | update the magic mirror | 1.4 | I couldn't start the magic mirror update. The details are in the logs. | `63c7e1db179a78a028d94742ec32c7c6` |
| 01:27:39 | api | update the magic mirror | 2.2 | I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm | |
| 01:27:46 | voice | update the mirror | 6.7 | To update the magic mirror, say "update the magic mirror". | |

Finding 1: `docker compose --profile dev stop tool_service-dev` at 01:27:52,
`docker compose --profile dev up -d tool_service-dev` at 01:28:15, healthy
at 01:28:22.

| UTC | Mount | Said | s | Reply (verbatim) | Trace |
| --- | --- | --- | --- | --- | --- |
| 01:27:53 | voice | what's the news today | 0.7 | That didn't work — I couldn't get through to the news service. | `db63ff73887fe1b40936c0dd1232b413` |
| 01:27:54 | api | what's the news today | 1.0 | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | |
| 01:27:56 | voice, `stream:true` | give me the news briefing | 2.2 | That didn't work — I couldn't get through to the news service. | `9612b61273eb283344f72c980a4f426a` |
| 01:27:57 | voice | update the news feed | 0.9 | That didn't work — I couldn't get through to the news service. | `d003074072f093ca34734b2f88f4e1cd` |
| 01:27:58 | api | update the news feed | 1.5 | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | |
| 01:28:00 | voice | any headlines this morning | 1.9 | That didn't work — I couldn't get through to the news service. | `21fffd14f0aa00e87c997601a829b283` |
| 01:28:06 | voice | what's the weather in Paris tomorrow | 5.9 | I was unable to get the weather for Paris tomorrow because of a temporary issue resolving the location. | |
| 01:28:11 | api | what's the weather in Paris tomorrow | 4.4 | I'm sorry, I'm having trouble reaching the weather service right now and can't retrieve the forecast for Paris. | |
| 01:28:13 | voice | update the magic mirror | 2.3 | That didn't work — I couldn't get through to the magic mirror updater. | `0bd0d8da67ea821518559f2c5a74757e` |
| 01:28:15 | api | update the magic mirror | 2.1 | That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution. | |

`/dev/api/chat` replies match the e2e report's debug text for the same
requests byte for byte. The weather rows are model-written; the backstop
did not fire on them (no debug shape). Finding 3 was not produced live:
there is no safe way inside the dev pair to make the model return nothing
(the same limit the build report gives for the model-server kinds); the unit
tests cover it through the real app.

Logged (`orchestrator-dev`, `failure_spoken_friendly` events, one per
friendly substitution, each with its trace id): the six voice failures above
with their debug text, e.g.
`I couldn't update the magic mirror. Mirror preflight failed: SSH key not
found at /keys/kronk-mm-update — moun…` for trace `63c7e1db…`. In Tempo,
trace `63c7e1db179a78a028d94742ec32c7c6`'s `pipeline.shim` span carries
`kronk.voice: true`, `kronk.failure.debug_text: "I couldn't update the magic
mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update
— mount ./secrets/mm"`, `kronk.failure.spoken: "I couldn't start the magic
mirror update. The details are in the logs."`.

`{ span.kronk.voice = true && span.kronk.failure.spoken != nil &&
resource.service.name = "kronk-orchestrator-dev" }` over the last 25
minutes returned 8 traces: the six voice failures above plus the two replay
mirror rows (`5320c4351e3b…`, `31b4e12f3497…`). A query a few seconds after
the live run returned 5 of the 6; the sixth (`0bd0d8da…`) had not been
exported yet. The backstop fired 0 times across the live runs and the replay
(`speech_backstop` events in the `orchestrator-dev` log: 0).

## Replay

Run 01:28:40 to 01:34:17 UTC, `tool_service-dev` up, no player playing
(the script checked 17 players before and after). The 117 `pass`/`xfail`
lines are left out; the per-row record is
`docs/bench/voice_replay_2026-11-17_012840_m2-fix1.json`.

```
$ python3 -u scripts/voice_replay.py --label m2-fix1
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  F fail  executed   eval-what-s-playing  [tool []]
  X xpass executed   eval-shuffle-it  []
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 98, 'xfail': 19, 'skip': 3, 'fail': 1, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_012840_m2-fix1.json
exit=1
01:34:17
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row on status and method:

| Row | Baseline | This run |
| --- | --- | --- |
| 111 of the 114 baseline rows | | identical |
| `inc-0930-search-deep-house-ytm` | fail | pass (known YouTube Music flapper; also passed in the M2 e2e run) |
| `eval-shuffle-it` | xfail | xpass (known flapper) |
| `eval-what-s-playing` | pass | **fail** (below) |
| 7 `m4b-*` rows (not in the baseline) | | pass |
| `m4b-fetch-and-run-on-the-mirror` (not in the baseline) | | skip (typed link) |

`eval-what-s-playing` ("What's playing?", trace
`923948751c930b833c9ea546649ca6ce`): route `home/now_playing`, one home
round, no tool call, spoken
`{.\n  "tool_name": "now_playing",\n  "parameters": {}.\n}.` The model
wrote the call as text instead of calling the tool. No tool ran and no
failure was recorded, so neither `failure_text` nor the backstop applies
(and braces are not one of the code's debug shapes). It is not on the
brief's flapper list; it passed in all 30 earlier replay files that executed it.
Two checks that the fix did not cause it:

1. Rerun of the row alone at 01:34:49 UTC:
   ```
   $ python3 -u scripts/voice_replay.py --id eval-what-s-playing --label m2-fix1-whats-playing
   voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 1 rows (121 not selected)
     . pass  executed   eval-what-s-playing
   voice_replay: {'pass': 1}; executed 1, checked read-only 0; players unchanged (17 checked)
   voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_013449_m2-fix1-whats-playing.json
   ```
2. The same request ("What's playing?", routed home, `now_playing`
   answering "Nothing is playing on the Office speaker.") through TestClient
   on `/voice/api/chat`, in the pre-fix archive and in the fixed tree, model
   stubbed and every model call captured (messages, tool menu), clock fixed:
   the two captures are byte-identical (5,287 bytes), reply included. The
   fix does not touch round-1 model inputs.

Spoken text against the M2 e2e run (`…_010247_m2-e2e.json`), executed rows:
36 identical, 17 different. The two pinned mirror rows
(`m4b-update-the-magicmirror`, `m4b-okay-update-the-magic-mirror-please`)
changed by design, from the preflight detail to "I couldn't start the magic
mirror update. The details are in the logs." `tests/voice_corpus.jsonl` pins
route, rule and tool for them, not text, so it was not edited. The other 15
are model or music-search variation on rows that passed or failed the same
way before (10 of them `xfail`; `m4b-update-magic-mirror` (devops, refused
by routing, model-written) reworded "not configured in the system" to "not
configured or available"; `eval-shuffle-the-anime-bangers-playlist-on-sonos`
and `eval-look-for-some-good-deep-house-chill` swapped between a dry-run play
and a "couldn't find" music sentence in opposite directions; `eval-shuffle-it`
reworded its question; `eval-what-s-playing` above). None of them went
through a path this fix changed except the music "couldn't find" sentence,
which is criterion 8's own sentence and is identical in both styles.

## Observations from the e2e report

- **4** (friendly fallback says "couldn't get through" for any unmapped
  result): fixed for argument refusals, which now say the request lacked
  detail. A terminal handler that raises anything (transport or not) still
  says "couldn't get through": `tools.execute` catches every exception and
  its line does not carry the exception type, and changing that line would
  change the debug text (criterion 9).
- **5** (`is_failed_tool_result` misses shapes): fixed, all five named shapes
  plus the empty-reply and no-such-specialist markers.
- **6** (survivors M03, M12): both have a killing test now.
- **7** (protocol errors as JSON): unchanged, per the coordinator's ruling.
- **1** (root span of a failed tool turn not ERROR): not changed. The trace
  levels and status of the other mounts are pinned byte-identical
  (criterion 9). On the speech mount a friendly substitution now sets
  `kronk.failure.debug_text` on the root span, so
  `{ span.kronk.voice = true && span.kronk.failure.spoken != nil }` finds
  those turns on one span.

## Departures, and anything not done

- **The backstop's span attribute is on the HTTP server span, not the
  pipeline root.** The root ends inside `_run_pipeline`; the backstop runs in
  the handler after it, where the current span is the server span of the
  same trace.
- **The backstop puts the original text on the span regardless of
  `TELEMETRY_RECORD_TEXT`**, as the brief asks; it is a failed turn's reply,
  truncated to 300 characters, like the error status messages already on
  spans.
- **An empty final reply on the speech mount is not backstopped.** Every
  pipeline path now yields text for an empty model reply, so none was found
  that hands the speaker nothing; a successful answer that renders to empty
  speech is not a failure and is left alone.
- **`ERROR_STYLE=friendly` on the other mounts** now also gets the new
  friendly sentences (consistent with the M2 build's meaning of that
  setting); the backstop stays voice-only.
- **Finding 3 not produced live** (see "Live on dev").
- `pipeline_bench.sh` not run (brief).

## Guard rails

- Only `orchestrator-dev` has a new container id; every production
  container, HA, MA, telemetry, `wyoming-piper`, `bgutil-pot-provider` and
  `tool_service-dev` keep theirs (compared `docker ps` before and after).
  `tool_service-dev` was stopped and started once (01:27:52 → 01:28:15) with
  the two commands in the brief. No HA or MA restart; nginx not restarted.
  No llama unit, LiteLLM or production container touched;
  `kronk-hottub-monitor.service` active.
- Requests went only to `127.0.0.1` (the `/dev/` paths, Tempo on 3200) and,
  from the replay script, to HA on localhost. The driver refuses any path
  outside `/dev/`. No LAN scan. No command to the MagicMirror Pi or any
  managed host; the two `POST /magicmirror/update` calls were on
  `tool_service-dev`, which stops at the missing key before any connection.
  No call to production's `/ops/exec`, `/magicmirror/update`, `/fetch` or
  `/http_check` (production `tool_service` log, last 30 minutes: 0 such
  lines).
- No sound: no music request was sent by the driver; the replay is dry-run
  gated and checks players.
- `.env`, `.env.dev` not edited. No `down -v`, no `--remove-orphans`.
- Scratch (session scratchpad): the pre-fix archive, the mutation copy, the
  driver and its outputs, the replay log; no symlinks, no secrets; removed at
  the end with `rm -rf "${S:?}/…"`. No background job left running.
