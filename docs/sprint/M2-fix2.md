# M2 fix 2: failure is a flag set by the code that wrote the result

Fix agent, round 2 of at most 3, 2026-11-17 (UTC), branch `refactor`.
Inputs: `docs/sprint/M2-criteria.md` (including "Coordinator rulings after
the retest"), `docs/sprint/M2-fix1.md`, `docs/sprint/M2-e2e2.md` (findings
1 to 4, observations 1, 2, 4, 5) and observation 5 of `docs/sprint/M2-e2e.md`.
Tree before the fix: `cb844ac` (code identical to `a090f92`). Fix commit:
`2129483`; this report is the commit after it. No push, no tag.

## Summary

- The speech backstop is gone: `errors.speech_backstop`, `debug_shape`,
  `_DEBUG_SHAPES`, the turn-failure record (`begin_turn`, `end_turn`,
  `note_failure`, `turn_failures`, the `_turn` ContextVar), the closing
  `outcome` event, `_ollama_collect_turn`, the `kronk.speech_backstop*`
  span attributes and `speech_backstop` log event, their tests and their
  docs. No switch was left. `orchestrator/main.py` is back to its pre-fix-1
  text (`9d27f2e`) apart from one line in the test alias `_execute_tool`.
- `errors._FAILED_RESULT` and `errors.is_failed_tool_result` are deleted.
  Nothing in `orchestrator/` decides failure by matching result text.
  Every tool handler returns its text for a success and `tools.failure(text)`
  for a failure; `tools.execute` returns `ToolResult(text, failed)`;
  `agents.run_delegated` returns `(text, terminal, failed)`. The agent loop
  appends `errors.FRIENDLY_TOOL_PHRASING`, in friendly style only, when
  `failed` is set.
- Kept from fix 1: `errors.failure_text` as the one exit for code-written
  user-visible failure text, and the three fixed sentences (news down,
  mirror preflight, empty model reply). `failure_text` no longer records
  anything; it only chooses the text and logs the substitution.
- Findings 1, 2 and 4 of the retest are gone, live and in unit tests that
  fail on `a090f92`. Music failure sentences are unchanged in both styles,
  tool_service's detail included (ruling: criterion 8 wins).
- Handler text and the debug-style model input are byte-identical to
  `9b6c435` in 188 of 188 scenarios (every handler, success with six
  failure-looking strings, and every failure branch). A successful result's
  model input is identical in both styles in 132 of 132 success scenarios
  (on `a090f92`: 102 of 132).
- Tests: `2694 passed, 2 skipped, 18 xfailed` (baseline `2349 passed,
  2 skipped, 18 xfailed`; 41 tests removed, 386 added).

## What was removed

| Where (before the fix) | What |
| --- | --- |
| `orchestrator/errors.py:27-33` | Docstring paragraph describing the backstop (rewritten: model-written text is never matched or replaced; failure is a flag). |
| `orchestrator/errors.py:36,39,43-45` | `logging`, `ContextVar`, the module logger (only the backstop used them). |
| `orchestrator/errors.py:118-123` | `failure_text` calling `note_failure` (the arming of the backstop). |
| `orchestrator/errors.py:220-236` | `_FAILED_RESULT`, `is_failed_tool_result`. |
| `orchestrator/errors.py:239-308` | The turn-failure record (`_turn`, `begin_turn`, `end_turn`, `note_failure`, `turn_failures`), `_DEBUG_SHAPES`, `debug_shape`, `speech_backstop` with its `speech_backstop` event, warning log and `kronk.speech_backstop`, `.matched`, `.original` span attributes. |
| `orchestrator/main.py:509-510` | `errors.note_failure(pipeline_error)` in `_run_pipeline`'s `finally`. |
| `orchestrator/main.py:681-695` | `begin_turn`/`end_turn` around the shim pipeline and the closing `{"type": "outcome", "failures": …}` event. |
| `orchestrator/main.py:881-899` | `_ollama_collect_turn` (returned the reply and the turn's failures); `_ollama_collect` is its old self again. |
| `orchestrator/main.py:952-957` | The speech mount's `errors.speech_backstop(render.to_speech(reply), failures)`; it is `render.to_speech(await _ollama_collect(…))` again. |
| `orchestrator/agents.py:858-861` | `note_failure` on a failed tool result. |
| `orchestrator/agents.py:914` | `errors.is_failed_tool_result(result)` as the condition for the phrasing rule. |
| `tests/test_spoken_failures.py` | 29 tests: `test_debug_shapes_are_recognised` (9), `test_plain_sentences_have_no_debug_shape` (5), `test_backstop_replaces_a_failed_turns_debug_text_and_records_it`, `test_backstop_never_touches_a_turn_without_a_failure` (2), `test_backstop_is_off_when_the_operator_asks_for_debug_on_voice`, `test_model_echoing_debug_text_on_a_failed_voice_turn_is_replaced`, `test_more_failed_result_shapes_are_recognised` (7), `test_more_successful_results_are_not_failures` (3). `test_backstop_never_touches_a_successful_voice_answer` became `test_a_successful_voice_answer_is_spoken_as_written` (no spy); `test_voice_signal_and_turn_record_are_reset_after_the_run` became `test_voice_signal_is_reset_after_the_run` (the turn-record and `outcome` asserts dropped; it now asserts the shim yields only the token). The terminal-tool enumeration test checks the spoken sentence with the suite's `_TECHNICAL` pattern instead of `errors.debug_shape`. |
| `tests/test_error_style.py` | 12 tests: `test_failed_result_shapes_are_recognised` (6), `test_successful_results_are_not_failures` (6). |
| `docs/features/verbose-errors.md` | The "Speech backstop" paragraph and the `is_failed_tool_result` reference; the blog hook that named the backstop. |
| `docs/features/voice-pipeline.md:66-69` | The sentence pointing at `errors.speech_backstop`. |

`grep -rn "speech_backstop\|is_failed_tool_result\|_FAILED_RESULT\|debug_shape\|note_failure\|turn_failures\|begin_turn" orchestrator/ tests/ README.md docs/features/`
returns nothing. `docs/features/verbose-errors.md` mentions the backstop
once, as history, with a pointer to this report.

## How failure is signalled now

- `orchestrator/tools.py:496-506`: `ToolResult(text, failed=False)`, a
  frozen dataclass, and `failure(text)`, which returns
  `ToolResult(text, failed=True)`. The comment above it (`tools.py:489-493`)
  states the rule: a handler returns its text when it succeeded and
  `failure(text)` when it did not; nothing reads the text afterwards.
- `orchestrator/tools.py:526-537`: `_fail()` returns `failure(…)`.
- `orchestrator/tools.py:1056-1071`: `execute()` returns `ToolResult`: an
  unknown tool and an exception are failures; a handler's `ToolResult` is
  passed through; a handler's plain `str` becomes `ToolResult(text)`.
- `orchestrator/agents.py:833`: `failed = False` for each call;
  `agents.py:856` `failed = True` for `[no such specialist: …]`;
  `agents.py:858` `result, delegated_terminal, failed = await run_delegated(…)`;
  `agents.py:862` `result, failed = outcome.text, outcome.failed`.
- `orchestrator/agents.py:920`: `if error_style == errors.FRIENDLY and failed:`
  appends the phrasing rule. The text the model receives is otherwise the
  handler's text, unchanged.
- `orchestrator/agents.py:1013-1043`: `run_delegated` returns
  `(text, terminal, failed)`. Failed when the specialist errored before any
  text (`:1040`), returned nothing (`:1042`), or ended on its own empty-reply
  marker, which `run_stream` now signals with `"empty": True` on its `done`
  event (`agents.py:773`, `:1004`; the `done` event of every other turn is
  unchanged). `run()` takes the text as before.
- `orchestrator/main.py:84-87`: the test alias `_execute_tool` returns
  `(await tools.execute(…)).text`, so it still returns the text.

`agents._terminal_speech` is unchanged: it maps a terminal tool's result to
its sentence by the tool's own fixed prefixes ("Music playing: ", "Could not
play music: ", …), which a successful result cannot start with, and every
failure branch still leaves through `errors.failure_text`. It decides the
spoken sentence, not whether the result failed, and the flag does not reach
it; nothing it does changed.

### Every tool path and its flag

`tools.py` line numbers are in the fix commit.

| Tool | Path | Result (start) | Flag |
| --- | --- | --- | --- |
| any | handler raised (tool_service down, timeout, bad JSON) | `[Tool <name> error: …]` (`:1070`) | failed |
| any | not in `_HANDLERS` | `[Unknown tool: <name>]` (`:1062`) | failed |
| `get_weather` | 200 | `[Weather for …]` | success |
| `get_weather` | other status | `_fail` `[Weather lookup for … failed (HTTP n): …]` (`:546`) | failed |
| `web_search` | 200 | `[Web search results for '<query>' …]` | success |
| `web_search` | other status | `_fail` (`:566`) | failed |
| `fetch_url` | 200, `ok` | `[Page content from …]` | success |
| `fetch_url` | 200, not `ok` (blocked address, HTTP 403 upstream) | `[Could not fetch …: <reason>. Try a different URL …]` (`:577`) | failed |
| `fetch_url` | other status | `[Could not fetch …: tool service returned n]` (`:581`) | failed |
| `shopping_list_view` | 200 | `[Shopping list: …]` / `[Shopping list is empty]` | success |
| `shopping_list_view`, `_add`, `_clear` | other status | `_fail` (`:591`, `:602`, `:620`) | failed |
| `shopping_list_add`, `_clear` | 200 | `[Added to shopping list: …]` / `[Shopping list cleared]` | success |
| `shopping_list_remove` | 200 / 404 | `[Removed '…' …]` / `['…' was not found on the shopping list]` | success (404 is a fact, not a failure) |
| `shopping_list_remove` | other status | `_fail` (`:612`) | failed |
| `get_kronk_context` | file read | `[Kronk system context] …` | success |
| `get_kronk_context` | read raised | `[Could not read context file: …]` (`:636`) | failed |
| `generate_diagram` | 200 / other | `[Diagram generated: …]` / `[Diagram generation failed: …]` (`:644`) | success / failed |
| `solar_status`, `solar_detail` | 200 / other | `[Solar HEALTHY\|ISSUES] …`, `[Solar detail — …]` / `_fail` (`:650`, `:671`) | success / failed |
| `solar_energy` | other status | `_fail` (`:705`) | failed |
| `solar_energy` | 200 with `error` | `[Solar energy — <period>] no figure available: …` (`:708`) | failed |
| `solar_energy` | 200 | `[Solar energy — LIFETIME\|<PERIOD>] …` | success |
| `query_hottub` | 200, online / offline | `[Hot tub ONLINE]` / `[Hot tub OFFLINE — breaker may have tripped]` | success (the tub answered) |
| `query_hottub` | 200, `online` null | `[Hot tub status unknown: …]` (`:734`) | failed |
| `query_hottub` | other status | `[Hot tub status unavailable]` (`:749`) | failed |
| `play_music` | no query | `[play_music error: query is required]` (`:755`) | failed |
| `play_music` | 200 | `[Music dry run: …]` / `[Music playing: …]` / `[Station playing: …]` | success |
| `play_music` | other status | `[Could not play music: <detail>] …` (`:808`) | failed |
| `control_music` | no action / 200 / other | `[control_music error: action is required]` (`:818`) / `[Music control: …]` / `[Could not control music: …]` (`:835`) | failed / success / failed |
| `rate_music` | bad thumb / 200 / other | `[rate_music error: thumb must be 'up' or 'down']` (`:845`) / `[Music rated: …]` / `[Could not rate music: …]` (`:862`) | failed / success / failed |
| `remember_music_preference` | 200 / other | `[Music taste: …]` / `[Could not save music taste: …]` (`:881`) | success / failed |
| `now_playing` | 200 / other | `[Now playing: …]` / `[Could not read the player: …]` (`:904`) | success / failed |
| `remote_exec` | no command | `[remote_exec error: command is required]` (`:913`) | failed |
| `remote_exec` | 200 | `[remote_exec on …: \`…\` exit=n] …` | success |
| `remote_exec` | 422 (refused) | `_fail` text + read-only guidance (`:926`) | failed (as the old regex had it) |
| `remote_exec` | other status | `_fail` (`:929`) | failed |
| `http_check` | no URL | `[http_check error: url is required]` (`:937`) | failed |
| `http_check` | non-200 from tool_service | `_fail` (`:940`) | failed |
| `http_check` | 200, not `ok` | `[http_check <url>: FAILED (<cause>) after n ms: …]` (`:945`) | failed (new: retest observation 1) |
| `http_check` | 200, `ok` | `[http_check <url>: HTTP n in n ms; …]` | success |
| `update_magicmirror` | not the pinned sentence | `[Magic mirror update refused: …]` | success (the M4b refusal; spoken unchanged) |
| `update_magicmirror` | 200 / other | `[Magic mirror update started: …]` / `[Could not update the magic mirror: …]` (`:980`) | success / failed |
| `news_brief` | 200 | `<Edition> news brief, generated …` | success |
| `news_brief` | refresh failed, brief delivered | `I couldn't fetch fresh news (…) — here's the latest brief I have.` + the brief | success (the brief was delivered) |
| `news_brief` | brief not available | `The news brief isn't available right now — …` (`:1022`) | failed (new: the old regex missed it; `news_brief` is terminal everywhere, so the flag never reaches a model) |
| `ask_<agent>` | no such agent | `[no such specialist: …]` (`agents.py:855`) | failed |
| `ask_<agent>` | specialist errored before any text | `[<agent> agent error: …]` (`agents.py:1040`) | failed |
| `ask_<agent>` | specialist returned nothing, or ended on its empty-reply marker | `[<agent> agent returned no response]` (`agents.py:1042`, `:1043`) | failed |
| `ask_<agent>` | specialist answered | its text | success (whatever the text says) |
| (loop) | exact repeat of a call | `[<tool> was already called …]` | success (unchanged: no rule before either) |
| (loop) | tool not offered to the agent | `[TOOL: … — REJECTED …]` | not a tool result; no rule (unchanged) |

`tests/test_tool_failure_flag.py::test_every_failure_path_is_flagged`
drives each failed row above through `tools.execute` (53 cases: every
tool_service-backed tool down and answering HTTP 502, plus each in-band and
argument failure); `test_unknown_tool_is_flagged`,
`test_a_delegated_result_is_flagged_by_the_specialists_outcome` and
`test_no_such_specialist_is_flagged` cover the rest.
`test_outcomes_that_are_not_failures_are_not_flagged` and
`test_a_failed_refresh_with_the_cached_brief_is_not_a_failure` pin the four
"success" rows that sound negative.

## Byte identity against `9b6c435` (criterion 9)

Scratch harness (not committed; scratch copies of `9b6c435`, `a090f92` and
the fix tree, same venv, `no_network` on): 188 scenarios: every handler
with a 200 success body carrying each of six strings (`error`, `failed:`,
`[Tool x error: y]`, `Errno 13`, `pip build failed: ModuleNotFoundError`,
`connection failed (HTTP 502): bad gateway`) in every field it copies; every
tool_service-backed handler down and answering 502; the in-band, argument
and refusal branches; an unknown tool. For each: the result text from
`tools.execute`, and the tool message the model reads in debug and in
friendly style through `agents.run_stream`.

```
scenarios: 188 same keys: True
text != 9b6c435: []
debug model input != 9b6c435: []
success rows: 132 friendly==debug (new): 132 | friendly==debug (a090f92): 102
non-success rows: 55
  http_check|not_ok: rule new=True a090f92=False
  news_brief|502: rule new=True a090f92=False
  query_hottub|offline: rule new=False a090f92=False
  shopping_list_remove|404: rule new=False a090f92=False
```

Every other failure row gets the rule on friendly in both trees, and in the
fix tree the friendly input is exactly the debug input plus
`"\n\n" + FRIENDLY_TOOL_PHRASING` (asserted by
`test_a_failed_result_gets_the_phrasing_rule_only_on_voice`, 53 cases).
The 30 success rows that differed on `a090f92` are the ones where the
handler copies the string into the first line of its result (weather
location, search query and engines, fetch, http_check URL, music detail,
remote_exec output, shopping items, mirror message).

## Tests

New file `tests/test_tool_failure_flag.py` (386 tests). Unit tests only:
tool_service and the model are stubbed, the suite's `no_network` fixture is
on, `builtins.open` is stubbed for `get_kronk_context`.

| Test | Cases | What it pins |
| --- | --- | --- |
| `test_successful_search_with_failure_words_in_the_query_is_spoken_unchanged` | 2 | Retest finding 1: a search for `pip build failed: ModuleNotFoundError …` and for `docker compose up failed (HTTP 502): bad gateway` succeeds; on `/voice/api/chat` the answer (which names `ModuleNotFoundError` and `Errno 13`) is spoken as `render.to_speech(answer)`, `/api/chat` returns it unchanged, the model input is byte-identical on both mounts and carries no phrasing rule. |
| `test_a_turn_with_one_failed_tool_and_a_correct_answer_is_spoken_unchanged` | 2 | Retest finding 2: `fetch_url` refused (in-band HTTP 403) or the first `web_search` raised, then a search succeeds and the model answers "Errno 13 is EACCES, permission denied; ConnectionRefusedError is errno 111."; spoken unchanged; the rule is on the failed result only. |
| `test_music_failure_sentences_are_spoken_unchanged_on_voice` | 3 | Retest finding 4: `Home Assistant is unreachable (ConnectError).`, an `HA service … failed (HTTP 500): … at http://homeassistant:8123/…` detail and `I couldn't find TypeError by Null on YouTube Music.` are spoken as `I couldn't play that. <detail>` on voice, same as `/api/chat`. |
| `test_successful_result_reaches_the_model_byte_identical_in_both_styles` | 132 | Every handler × six strings: no rule, friendly input == debug input. |
| `test_successful_result_embedding_failure_text_is_not_flagged` | 132 | Every handler × six strings: `ToolResult.failed` is False. |
| `test_every_failure_path_is_flagged` | 53 | Each failure branch: `failed` is True. |
| `test_a_failed_result_gets_the_phrasing_rule_only_on_voice` | 53 | Each failure branch: debug input is the handler's text; friendly is that plus the rule. |
| `test_a_delegated_result_is_flagged_by_the_specialists_outcome` | 3 | A specialist that answers with failure-looking text: no rule; one that says nothing or raises: rule. |
| `test_no_such_specialist_is_flagged`, `test_unknown_tool_is_flagged` | 2 | |
| `test_outcomes_that_are_not_failures_are_not_flagged`, `test_a_failed_refresh_with_the_cached_brief_is_not_a_failure` | 4 | Item not on the list, tub offline, mirror refusal, stale brief delivered. |

Changed existing tests: test fakes that patch `agents.tools.execute` return
`tools.ToolResult(...)` (or `tools.failure(...)` for the music failure in
`test_error_surfacing.py`); tests that read `tools.execute` or a handler's
failure result read `.text` and now also assert `.failed`
(`test_agentic_loop`, `test_solar`, `test_magicmirror`, `test_mm_update_pin`,
`test_music_rate`, `test_now_playing`, `test_news`, `test_playback_control`,
`test_fetch_address_block`, `test_http_check`); the two `run_delegated`
callers unpack three values and assert `failed is False`. No expected text
changed.

### The regression tests fail on `a090f92`

`git archive a090f92` into a scratch directory, the new test file copied in,
run with the repo's venv:

```
$ python -m pytest tests/test_tool_failure_flag.py -q -p no:randomly
229 failed, 157 passed, 3 warnings in 1.65s
```

The tests written against observable behaviour fail for the reason they
were written:

```
tests/test_tool_failure_flag.py:420: AssertionError: assert 'Sorry — some... the details.' == 'That ModuleN...ols with pip.'
tests/test_tool_failure_flag.py:420: AssertionError: assert 'Sorry — some... the details.' == 'That ModuleN...ols with pip.'
tests/test_tool_failure_flag.py:446: AssertionError: assert 'Sorry — some... the details.' == 'Errno 13 is ...is errno 111.'
tests/test_tool_failure_flag.py:446: AssertionError: assert 'Sorry — some... the details.' == 'Errno 13 is ...is errno 111.'
tests/test_tool_failure_flag.py:477: assert 'Sorry — some... the details.' == "I couldn't p...onnectError)."
tests/test_tool_failure_flag.py:477: assert 'Sorry — some... the details.' == "I couldn't p.../media_pause."
tests/test_tool_failure_flag.py:477: assert 'Sorry — some... the details.' == "I couldn't p...ouTube Music."
tests/test_tool_failure_flag.py:271: AssertionError: assert '[http_check ...tion refused]' == '[http_check ...identifiers.]'
tests/test_tool_failure_flag.py:271: AssertionError: assert 'The news bri...ed it sooner.' == 'The news bri...identifiers.]'
tests/test_tool_failure_flag.py:211: AssertionError: assert '[This tool ...dentifiers.]' not in '[Web search...dentifiers.]'
FAILED tests/test_tool_failure_flag.py::test_successful_search_with_failure_words_in_the_query_is_spoken_unchanged[pip build failed: ModuleNotFoundError no module named setuptools]
FAILED tests/test_tool_failure_flag.py::test_successful_search_with_failure_words_in_the_query_is_spoken_unchanged[docker compose up failed (HTTP 502): bad gateway]
FAILED tests/test_tool_failure_flag.py::test_a_turn_with_one_failed_tool_and_a_correct_answer_is_spoken_unchanged[first0-client0]
FAILED tests/test_tool_failure_flag.py::test_a_turn_with_one_failed_tool_and_a_correct_answer_is_spoken_unchanged[first1-None]
FAILED tests/test_tool_failure_flag.py::test_music_failure_sentences_are_spoken_unchanged_on_voice[HA service media_player.media_pause failed (HTTP 500): 500 Internal Server Error at http://homeassistant:8123/api/services/media_player/media_pause]
FAILED tests/test_tool_failure_flag.py::test_a_failed_result_gets_the_phrasing_rule_only_on_voice[http_check-not_ok]
FAILED tests/test_tool_failure_flag.py::test_a_failed_result_gets_the_phrasing_rule_only_on_voice[news_brief-http_error]
FAILED tests/test_tool_failure_flag.py::test_successful_result_reaches_the_model_byte_identical_in_both_styles[web_search-pip build failed: ModuleNotFoundError]
```

(One targeted run of 11 tests, `--tb=line`, ended `11 failed, 3 warnings
in 0.33s`; the last one, `test_unknown_tool_is_flagged`, failed with
`AttributeError: module 'tools' has no attribute 'failure'`. The output was
filtered with `grep -E "^/|^E |passed|failed"`, which drops `FAILED` summary
lines without a lowercase "failed" in them: two music cases are shown by
their assertion lines at `:477` only. All are in the 229.) Per test:

| Test | Failed on `a090f92` | Why |
| --- | --- | --- |
| finding 1 search (2) | 2 of 2 | answer replaced by the generic sentence |
| finding 2 turn (2) | 2 of 2 | answer replaced by the generic sentence |
| finding 4 music (3) | 3 of 3 | sentence replaced by the generic sentence |
| byte-identical success input (132) | 30 of 132 | rule appended to a successful result (the 30 rows above); the other 102 already held |
| rule only on a failed result (53) | 2 of 53 | `http_check` FAILED and the news "not available" line got no rule; the other 51 already held |
| `failed` flag tests (132 + 53 + 4 + 1) | all | `AttributeError`: `tools.execute` returned a `str` and `tools.failure` did not exist. These pin the new API, not old behaviour. |
| delegation, no such specialist (4) | 0 | behaviour that already held; they pin it under the new signal |

### Mutation survivors from the retest

M03 (`failure_text` does not mark the turn failed), M29 (the pipeline's own
error not noted) and M33 (backstop before the speech render) were all about
the backstop and the turn record, which no longer exist; none applies. The
first report's M12 (rule on every tool result) is killed by
`test_successful_result_reaches_the_model_byte_identical_in_both_styles`
and by the existing `test_successful_tool_result_reaches_the_model_unchanged`.
No new mutation run was made in this round.

## Live on dev

Rebuilt only the dev pair at 02:33 UTC:
`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`
(`tool_service-dev` was unchanged and kept running; `orchestrator-dev` was
recreated and healthy within 30 s; `/app/tools.py` in the container has
`class ToolResult`, and `errors.py`, `main.py`, `agents.py` have no
`speech_backstop` or `is_failed_tool_result`). nginx resolves
`orchestrator-dev` at request time (`resolver 127.0.0.11 valid=10s`), so no
nginx restart was needed or made. The driver posted to
`http://127.0.0.1/dev/…` only, non-streaming, with a `traceparent` it chose,
and recorded the reply verbatim. Long replies are cut at 400 characters here;
`⏎` is a newline.

### Retest finding 1: the two replaced searches, `tool_service-dev` up

| UTC | Mount | Said | HTTP | s | Reply | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| 02:33:45 | `/dev/voice/api/chat` | search the web for pip build failed: ModuleNotFoundError no module named setuptools | 200 | 7.5 | The search results provide several common solutions for the pip build failed: ModuleNotFoundError: No module named setuptools error. Here are the primary ways to resolve this: ⏎  ⏎ 1.  Install or Upgrade setuptools: The most direct fix is to ensure the setuptools package is installed in your Python environment. ⏎ You can typically install it using: ⏎         `bash. ⏎         pip install setuptools | `284464355187` |
| 02:33:53 | `/dev/api/chat` | search the web for pip build failed: ModuleNotFoundError no module named setuptools | 200 | 5.65 | Based on current information, the error `pip build failed: ModuleNotFoundError: No module named setuptools` typically means that the `setuptools` package is missing or not accessible in your Python environment. ⏎  ⏎ Here are the common solutions found online: ⏎  ⏎ 1.  **Install Setuptools:** The most direct fix is often to explicitly install the package using pip: ⏎     ```bash ⏎     pip install s | `4503257308e2` |
| 02:33:59 | `/dev/voice/api/chat` | search the web for docker compose up failed: Errno 13 permission denied, and tell me the fix | 200 | 8.62 | The error docker compose up failed: Errno 13 permission denied usually occurs when the user running the docker compose command does not have the necessary permissions to access the files or to interact with the Docker daemon correctly. ⏎  ⏎ Based on the search results, here are the common causes and potential fixes: ⏎  ⏎ 1. Docker User Permissions (Most Common Fix): ⏎ By default, the Docker daemon | `7e1399dc22f5` |
| 02:34:07 | `/dev/api/chat` | search the web for docker compose up failed: Errno 13 permission denied, and tell me the fix | 200 | 9.29 | The error `docker compose up failed: Errno 13 permission denied` typically occurs when the user running the command does not have the necessary permissions to access files, directories, or the Docker daemon socket. ⏎  ⏎ Here are the common fixes based on the search results: ⏎  ⏎ *   **Add your user to the `docker` group:** Often, the issue is that your user doesn's allowed to access the Docker soc | `1cf21e804772` |

A real answer on both mounts, 2 of 2 each (the retest had the generic
failure sentence on voice, 2 of 2). Each request made one `web_search`
(dev log `tool_call` / `tool_complete`, no `tool_error`), and
`tool_service-dev` answered 200 to queries that contain ` failed: `:

```
"GET /search?q=pip%20build%20failed%3A%20ModuleNotFoundError%3A%20No%20module%20named%20setuptools&count=5 HTTP/1.1" 200 OK
"GET /search?q=pip%20build%20failed%3A%20ModuleNotFoundError%20no%20module%20named%20setuptools&count=5 HTTP/1.1" 200 OK
"GET /search?q=docker%20compose%20up%20failed%3A%20Errno%2013%20permission%20denied%20fix&count=5 HTTP/1.1" 200 OK
"GET /search?q=docker%20compose%20up%20failed%3A%20Errno%2013%20permission%20denied%20fix&count=5 HTTP/1.1" 200 OK
```

The four traces in Tempo carry `kronk.voice` true / false as expected, but
the exported spans do not include the tool message text, so whether the
model input on voice carried the phrasing rule is shown by the unit tests
(`test_successful_search_with_failure_words_in_the_query_is_spoken_unchanged`,
model input byte-identical on both mounts), not by the trace. The voice
reply's "`bash." is the speech render's handling of a fenced code block
the model wrote; `render.py` is unchanged by this fix.

### `tool_service-dev` stopped (02:34:51), then started (healthy 02:35:25)

`docker compose --profile dev stop tool_service-dev`, then
`docker compose --profile dev up -d tool_service-dev`.

| UTC | Mount | Said | HTTP | s | Reply | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| 02:34:56 | `/dev/voice/api/chat` | what's the news today | 200 | 1.6 | That didn't work — I couldn't get through to the news service. | `fa8b34b49bc0` |
| 02:34:57 | `/dev/api/chat` | what's the news today | 200 | 0.75 | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `fdd22eb5fb22` |
| 02:34:58 | `/dev/voice/api/chat` | what's the weather this weekend | 200 | 5.79 | I'm sorry, but the weather service failed to find a connection to provide that information for you. | `e43acda1976a` |
| 02:35:04 | `/dev/api/chat` | what's the weather this weekend | 200 | 4.85 | I’m sorry, but I’m unable to retrieve the weather forecast right now due to a temporary connectivity issue. | `72ad690e3824` |
| 02:35:09 | `/dev/voice/api/chat` | play some jazz | 200 | 2.44 | That didn't work — I couldn't get through to the music service. | `a0a5afb7d69d` |
| 02:35:11 | `/dev/api/chat` | play some jazz | 200 | 0.84 | That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution. | `95221a1d79f9` |

The `/dev/api/chat` news and music texts are the debug texts recorded in the
first test and the retest, byte for byte. Weather is model-written on both
mounts. Dev log events for these requests:

```
{"ts": 1794882897.882, "rid": "bb3c3d3a", "event": "tool_error", "tool": "news_brief", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "fa8b34b49bc09b0fbae3e4ecb20e2e80"}
{"ts": 1794882897.882, "rid": "bb3c3d3a", "event": "failure_spoken_friendly", "debug": "[Tool news_brief error: [Errno -3] Temporary failure in name resolution]", "spoken": "That didn't work — I couldn't get through to the news service.", "trace_id": "fa8b34b49bc09b0fbae3e4ecb20e2e80"}
{"ts": 1794882898.629, "rid": "f48d1efd", "event": "tool_error", "tool": "news_brief", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "fdd22eb5fb2280ef5b2770a8eb627073"}
{"ts": 1794882903.151, "rid": "89c172af", "event": "tool_error", "tool": "get_weather", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "e43acda1976a936e827b1a89f07d0962"}
{"ts": 1794882907.972, "rid": "fed7de7d", "event": "tool_error", "tool": "get_weather", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "72ad690e3824574b45363841ea3b468a"}
{"ts": 1794882911.712, "rid": "a48bed98", "event": "tool_error", "tool": "play_music", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "a0a5afb7d69de1be4eb9668ddcc5be98"}
{"ts": 1794882911.712, "rid": "a48bed98", "event": "failure_spoken_friendly", "debug": "That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution.", "spoken": "That didn't work — I couldn't get through to the music service.", "trace_id": "a0a5afb7d69de1be4eb9668ddcc5be98"}
{"ts": 1794882912.556, "rid": "f3e595c6", "event": "tool_error", "tool": "play_music", "error": "[Errno -3] Temporary failure in name resolution", "trace_id": "95221a1d79f9f647f69238eace50d53e"}
```

Nothing could play: `tool_service-dev` was stopped, and the dev path runs
with `MUSIC_DRY_RUN` in any case.

### Mirror on voice, `tool_service-dev` up

| UTC | Mount | Said | HTTP | s | Reply | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| 02:35:32 | `/dev/voice/api/chat` | update the magic mirror | 200 | 3.48 | I couldn't start the magic mirror update. The details are in the logs. | `5156a402dd14` |

```
tool_service-dev-1  | INFO:     172.18.0.6:36782 - "POST /magicmirror/update HTTP/1.1" 502 Bad Gateway
{"ts": 1794882935.475, "rid": "2b0a04f6", "event": "tool_call", "agent": "home", "tool": "update_magicmirror", "args": [], "trace_id": "5156a402dd14f0ccaa904d9a81b6d3ca"}
{"ts": 1794882935.485, "rid": "2b0a04f6", "event": "failure_spoken_friendly", "debug": "I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm", "spoken": "I couldn't start the magic mirror update. The details are in the logs.", …}
```

The preflight stopped on the missing key 10 ms after the call; nothing was
sent to the Pi. `speech_backstop` events in the `orchestrator-dev` log since
the rebuild: 0 (the code no longer exists).

## Test output

```
$ ./scripts/run_tests.sh -q
2694 passed, 2 skipped, 18 xfailed, 3 warnings in 16.93s
```

Baseline before the fix (same command, 02:20 UTC):
`2349 passed, 2 skipped, 18 xfailed, 3 warnings in 16.32s`.
2349 − 41 removed (29 in `test_spoken_failures.py`, 12 in
`test_error_style.py`, listed above) + 386 new (`test_tool_failure_flag.py`)
= 2694.

## Replay output

`python3 scripts/voice_replay.py --label m2-fix2`, 02:35:55 to 02:41 UTC,
against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`. One
run, all 122 rows; players unchanged, so no rerun. Pass and xfail lines left
out; per-row records in `docs/bench/voice_replay_2026-11-17_023555_m2-fix2.json`.

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  F fail  executed   inc-0930-search-deep-house-ytm  [tool ['coordinator:ask_research', 'research:web_search']]
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  X xpass executed   eval-shuffle-it  []
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 98, 'xfail': 19, 'fail': 1, 'skip': 3, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_023555_m2-fix2.json
exit=1
```

Against the baseline, by status and method: 113 of the 114 baseline rows are
identical; the 8 `m4b-*` rows are newer than the baseline. The two rows that
are not plain passes are both named flappers:

| Row | Baseline | This run | Earlier runs |
| --- | --- | --- | --- |
| `inc-0930-search-deep-house-ytm` | fail (`coordinator:ask_research`, `research:web_search`) | fail, same tools | pass in `m2-fix1` and `m2-e2e2-rerun` |
| `eval-shuffle-it` | xfail | xpass | xpass in `m2-fix1`, xfail in `m2-e2e2-rerun` |

The `inc-0930` failure is a routing choice (the coordinator sent "Search for
some deep house chill music on YouTube Music" to research, not home), the
same as the baseline; it is not a failure message, and no tool failed. The
other two YouTube Music search rows passed. The exit status 1 comes from
that row.

The `--check-only` run used to confirm the gates before the replay also
wrote a result file (`docs/bench/voice_replay_2026-11-17_023545_replay.json`);
it was mine, untracked, and was deleted.

## Production and guard rails

- Production containers have the ids and start times recorded at 02:20 UTC,
  before any change (`kronk-orchestrator-1`, `kronk-tool_service-1`,
  `kronk-nginx-1`, `kronk-litellm-1`, `kronk-searxng-1`,
  `kronk-voice_tracer-1` started 00:02 to 00:03 UTC; `homeassistant`
  2026-11-16 00:28 UTC; `music-assistant-server` 2026-11-04 13:45 UTC).
- Production `tool_service` log since 02:20 UTC: 0 lines for `/ops/exec`,
  `/magicmirror/update`, `/http_check`, `/fetch`. Production orchestrator:
  0 `request` events since 02:20 UTC.
- No `llama-*` unit, LiteLLM, HA, MA or production container was stopped,
  restarted or reconfigured; `kronk-hottub-monitor.service` active. No
  sound: music requests ran only with `tool_service-dev` stopped, and the
  replay is dry-run gated. No LAN address other than 127.0.0.1 was
  contacted; `http_check` and `remote_exec` were not called live.
- `.env` and `.env.dev` not edited. `scripts/pipeline_bench.sh` not run.
  No push, no tag. The dev pair is running and healthy
  (`orchestrator-dev` up since 02:33 UTC, `tool_service-dev` since 02:35 UTC).
- Scratch work (exports of `9b6c435`, `a090f92` and the fix tree, the
  comparison harness, the driver) lives in the session scratch directory,
  holds no symlinks, and was not committed.

## Docs

- `docs/features/verbose-errors.md`: the speech backstop paragraph replaced
  by a note that it was removed and why; a paragraph on how failure is
  flagged (`ToolResult`, `tools.failure`, `run_delegated`) and which
  negative-sounding outcomes are not failures; the blog hook rewritten.
- `docs/features/voice-pipeline.md`: the backstop sentence replaced by the
  flag.
- README: no mention of the backstop or of `is_failed_tool_result`; no change.

## Not done, and why

- No new mutation run. The three retest survivors were about removed code;
  the regression tests above were shown to fail on `a090f92` instead.
- `agents._terminal_speech` still chooses a terminal tool's sentence by the
  tool's own result prefixes and its fallback treats an unrecognised result
  as a failure. That decides wording, not whether a tool failed, and was
  left alone (no new behaviour asked for). Passing the flag to it would be
  the obvious next step if the coordinator wants terminal speech keyed on
  the flag too.
- Retest finding 3 (music sentences carry tool_service detail such as
  `(HTTP 502)` or `HA_TOKEN not configured`) is unchanged, per the ruling
  that criterion 8 wins. tool_service was not touched.
- Retest observation 2 (model-written failures are instructed, not
  guaranteed) stands: with the backstop gone, nothing checks what the model
  says. That is the ruling.
- Retest observation 5 / first test observation 1 (the root span of a
  failed tool turn is not marked ERROR) is unchanged; out of this fix's
  scope.
- Live evidence that the voice model input of a successful search carries no
  phrasing rule is not available from Tempo (tool text is not exported); the
  unit tests carry that claim.
