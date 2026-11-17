# M7 build: direct dispatch for regex-pinned commands

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M7-criteria.md`. Tree before the build: `7c83f36`. No push, no
tag. This report is the last commit.

## Summary

- Routing is an ordered rules table (`routing.RULES`, review item O12),
  pinned before the refactor by 6415 utterances and frozen copies of every
  pattern. No `(route, rule)` changed, and no rule was added, removed,
  reordered or widened.
- Five rules (`playback`, `now_playing`, `rate`, `music_another`, `mm_update`)
  now carry a direct call `(tool, args)` built from named groups. The pipeline
  runs it through `tools.execute`, speaks it with `agents._terminal_speech`,
  makes no model call and does not take `_llm_lock`.
- Dev voice mount, five runs each: "pause" median 2.85 s → 0.02 s, "what
  song is this" 1.38 → 0.02, "thumbs up" 1.96 → 0.02, "not that" 2.88 → 0.02.
  "play another one" stays on the model path (2.94 → 2.82): it is the `music`
  rule, not `music_another` (departure 1).
- Tests: `3341 passed, 2 skipped, 23 xfailed` (baseline `3003 passed, 2
  skipped, 18 xfailed`; +338 passed, +5 strict xfails, see criterion 14).
  Replay: `{'pass': 99, 'xfail': 20, 'skip': 3}`, exit 0. Every baseline row
  has the same status, except one known flapper that went fail → pass.

## Commits

| Commit | Step |
| --- | --- |
| `f168cff` | (1) Routing pin, from the `7c83f36` tree, before any change to `routing.py`. |
| `49389b5` | (2) O12: the if-chain becomes `RULES` + `decide()`. Same outcomes. |
| `044d1e8` | (3) Named groups and the direct call on the five rules. Nothing calls it yet. |
| `77280f0` | (4) The pipeline branch, outside `_llm_lock`. |
| `5c5658a` | (5) Corpus rows expect the direct call; layer one and the replay check it. |
| `d63aa04` | (5) Docs. |
| this one | Report and the replay result `docs/bench/voice_replay_2026-11-17_074950_m7-build.json`. |

## What changed

`orchestrator/routing.py` (line numbers at HEAD):

| Line | Change |
| --- | --- |
| `routing.py:14-16` | Module docstring points at the table and `docs/features/direct-dispatch.md`. |
| `routing.py:139-160` | `_PLAYBACK_RE`: one named group per branch (`pause`, `resume`, `next`, `next_track`, `turn_up`, `turn_down`, `louder`, `quieter`, `volume_up`, `volume_down`); `_PLAYBACK_ACTIONS` maps them to `control_music` actions. |
| `routing.py:194-214` | `_RATE_RE`: named groups (`thumbs_up`, `thumbs_down`, `like`, `dislike`, `never_again`, `upvote`, `downvote`, `more`, `less`); `_RATE_THUMBS` maps them to `up` / `down`. |
| `routing.py:224-226` | `_ANOTHER_RE`: the trailing room is the named group `room`. |
| `routing.py:271-301` | `DirectCall`, `Rule(name, route, match, direct=None)`, `Decision(route, rule, direct=None)`. |
| `routing.py:304-318` | `_group()` (the one action group that matched; raises if not exactly one) and `_another_call()`. |
| `routing.py:321-348` | `RULES`, the 15 rules in order. Matchers are lambdas that read the module patterns at call time, so a test that swaps `_MM_UPDATE_RE` is still followed. |
| `routing.py:351-359` | `decide(text)`: pure, the first matching rule and its direct call. |
| `routing.py:362-379` | `classify()`: same signature and return value; the span metadata gains `direct: <tool>`. |
| `routing.py:382-398` | `_record()` logs the decision (`route_shortcut`, plus `direct: true, tool` for the five; `route` with `text_preview` for the default, as before). `_classify_inner()` still returns `(route, rule)`. |

`orchestrator/main.py`:

| Line | Change |
| --- | --- |
| `main.py:7` | `nullcontext` import. |
| `main.py:330-341` | `_run_pipeline` calls `routing.decide(text)` before the lock. With a direct call it enters `nullcontext()` instead of `_llm_lock`. If `decide` raised, `direct = None` and `classify()` reports the error as before. |
| `main.py:360-369` | Weather, shared-state and file context are fetched only when `direct is None`. |
| `main.py:388-403` | After routing: `if direct is not None`, run `_direct_dispatch`, collect its tokens into `assistant_reply`, return. History, `end_pipeline` and the last-resort guard are the existing `finally` / `except`. |
| `main.py:580-609` | `_direct_dispatch()`: narration (`agents._tool_narration`), `tool_call` event (`agent` = route, `direct=True`), `tool.<name>` span on `telemetry.root()` with `.current()` so tool_service joins, `tools.execute`, `tool_complete`, timing stage, `agents._terminal_speech(result, error_style, tool=name)`, `generating` stage, token, timing payload (`model: "none (direct call)"`), `request_complete`. |

Tests: `tests/test_routing_pin.py` and `tests/fixtures/{routing_pin.jsonl,make_routing_pin.py}`
(new); `tests/test_direct_dispatch.py` (new, 112 tests); `tests/test_routing_shortcuts.py`
(+86 tests: the utterance → (tool, args) table and its negatives);
`tests/test_mm_update_pin.py` (three assertions `llm_log == ["home"]` → `[]`);
`tests/test_voice_corpus.py` (schema check for `expect.direct`, new
`test_corpus_direct_call`); `tests/test_voice_replay.py` (+1).

`scripts/voice_replay.py:281-282, 306-307, 377-380`: `kronk_evidence` records
the tools logged with `direct: true`; `compare_kronk` adds a `direct` check for
rows with `expect.direct`. The new check sits after the `if tool / elif "tool"
in e` pair. My first edit put it between them, and the existing replay tests
caught that.

`tests/voice_corpus.jsonl`: 28 rows gain `expect.direct: true`; six have
`action` `stop` → `pause` (departure 2).

Docs: `docs/features/direct-dispatch.md` (new), `README.md` (routing step 1,
the terminal-tools note, last-updated line), `docs/features/voice-regression-corpus.md`
(the `expect.direct` field and both layers), `docs/features/agents-as-tools-routing.md`
(pointer).

## The rules table

| # | Rule | Route | Matcher | Direct call |
| --- | --- | --- | --- | --- |
| 1 | `talkie_explicit` | talkie | `_TALKIE_PHRASES.search` | — |
| 2 | `direct_override` | direct | `_DIRECT_OVERRIDE.search` | — |
| 3 | `music_url` | home | `_MUSIC_URL_RE.search` | — |
| 4 | `url` | research | `_URL_RE.search` | — |
| 5 | `search_phrase` | research | `_SEARCH_PHRASES.search` | — |
| 6 | `weather` | home | `_WEATHER_RE.search` | — |
| 7 | `playback` | home | `_PLAYBACK_RE.search` (anchored) | `control_music {action}` |
| 8 | `now_playing` | home | `_NOW_PLAYING_RE.search` (anchored) | `now_playing {}` |
| 9 | `rate` | home | `_RATE_RE.search` (anchored) | `rate_music {thumb}` |
| 10 | `music_another` | home | `_ANOTHER_RE.search` (anchored, optional room) | `play_music {query: "another", kind: "another"[, player: room]}` |
| 11 | `taste` | home | `_TASTE_RE.search` | — |
| 12 | `music` | home | `_MUSIC_RE.search` | — |
| 13 | `mm_update` | home | `_MM_RE.search and asks_for_mirror_update` | `update_magicmirror {}` |
| 14 | `mm_ops` | devops | `_MM_RE.search` | — |
| 15 | `default` | direct | always | — |

## Utterance → (tool, args)

Pinned by `tests/test_routing_shortcuts.py::test_direct_call_from_the_match`
(65 rows). "Extra" marks a spelling today's regex already accepted that the
plan's table does not list.

| Words (plan table, then extras) | Tool | Args |
| --- | --- | --- |
| stop, pause, shut up ("Stop!", "stop the music", "pause it please") | `control_music` | `{action: pause}` |
| resume, unpause ("Resume the music.") | `control_music` | `{action: resume}` |
| skip, next ("skip this song"); extra: "next track", "skip the song" | `control_music` | `{action: next}` |
| louder, turn it up, volume up; extra: "turn that up", "turn the music up", "turn the volume up" | `control_music` | `{action: volume_up}` |
| quieter, turn it down, volume down; extra: "turn the volume down", "turn that down", "turn the music down" | `control_music` | `{action: volume_down}` |
| what song is this, what's playing, who is this, who sings this song, what am I listening to, … | `now_playing` | `{}` |
| thumbs up, like / love this song/one/track/tune, upvote, more like this; extra: "thumb up", "thumbsup", "up vote" | `rate_music` | `{thumb: up}` |
| thumbs down, hate / dislike / don't like this song, never play this (song) again, downvote, less like this; extra: "don't play that one again", "down vote" | `rate_music` | `{thumb: down}` |
| not that, no not this one, (play / try / put on) something else / different, a different song/one/station/genre/artist | `play_music` | `{query: another, kind: another}` |
| … in the office / on the Satellite1 speaker | `play_music` | `{query: another, kind: another, player: "office"}` |
| update the magic mirror / magicmirror (anywhere in the utterance) | `update_magicmirror` | `{}` |

Plan words matched only in their regex shape (not changed, stopping rule): "like", "love",
"hate", "dislike" and "don't like" need "this/that" plus "song/track/one/tune"
("I like this" stays with the coordinator, as the regex comment says).

Negatives, pinned with no direct call (`test_no_direct_call_off_the_five_rules`,
and through the pipeline in `test_direct_dispatch.py::test_negatives_go_to_a_model`):
"stop the timer", "pause my subscription", "what is the next holiday", "pause
the music in the office", "skip this song in the kitchen", "what's playing in
the kitchen", "thumbs up in the office" (all `default`), "play another one",
"play some jazz" (`music`), "never play nickelback" (`taste`), "what's the
weather like" (`weather`), "look up the magic mirror" (`search_phrase`),
"update magic mirror" and "what's the uptime of the magic mirror" (`mm_ops`),
"I like this", "hello" (`default`).

## Criteria

1. **Pass.** `tests/test_routing_pin.py` and the fixture are in `f168cff`, whose parent is `7c83f36`. `git log --format=%h -- orchestrator/routing.py` puts the first change at `49389b5`. The pin covers: the 15 outcomes and routes; every utterance landing on the first rule in ORDER whose frozen `7c83f36` matcher accepts it; 20 precedence cases matching two or more rules; `(route, rule)` for 6415 utterances (122 corpus, 491 `routing_eval.jsonl`, 308 strings the suite fed to a routing regex, recorded with a pytest plugin, plus overlaps and generated variants); each regex accepting what its frozen copy accepts. At `f168cff`: `3024 passed, 2 skipped, 18 xfailed`.
2. **Pass.** `routing.RULES` at `routing.py:321` holds 15 `Rule(name, route, match)`. The pin passed unchanged at `49389b5`, `044d1e8` and HEAD. `test_rules_table_is_the_fifteen_outcomes_in_order` and `test_decision_events_keep_their_shape` (event shape, also run against the old `routing.py`: it passed there) were added with the refactor. `test_regex_accepts_what_its_frozen_copy_accepts` (14 patterns × 6415 utterances) shows the named groups widened or narrowed nothing.
3. **Pass.** `test_direct_call_from_the_match` (65 rows), `test_exactly_the_five_rules_carry_a_direct_call`, and `test_every_pinned_utterance_decides_with_a_direct_call_iff_in_scope`: over all 6415 pinned utterances `decide()` never raises, and `direct` is set exactly for the five rules. Mapping as in the table above; extras are listed there. `music_another` passes the room as `player` (departure 3).
4. **Pass.** `test_direct_rule_answers_without_the_model` (5 rules × 4 mounts) uses a model stand-in that fails the request if it is called: the call count is 0, the reply is the expected sentence, exactly one POST goes to the right endpoint with the right args, and `weather_context` is not awaited. On the old `main.py`, 107 of the 112 new pipeline tests fail; the 5 that pass are the negatives.
5. **Pass.** `test_direct_reply_is_the_agent_paths_sentence` (5 rules × 3 tool_service outcomes (200, 4xx/5xx with detail, down) × 4 mounts = 60). The agent path is forced by patching `routing.decide` to drop the direct call, with a scripted home model making the same call; the replies are equal. `test_failure_sentences_by_style`: the music failure sentences are the same on voice and debug, and the mirror gives `FRIENDLY_MIRROR_NOT_STARTED` on voice and tool_service's preflight detail elsewhere. A mutant that passes `errors.DEBUG` instead of the request's style fails 7 tests.
6. **Pass.** `test_origin_reaches_tool_service_as_on_the_agent_path` (4 rules × stamped/unstamped): the payload is identical to the agent path's, carries `origin_device` / `origin_area` when stamped, and neither when unstamped. Live: every voice-mount request with the Office stamp targeted `media_player.satellite1_d8d1bc` (tool_service-dev `music request … -> ['media_player.satellite1_d8d1bc']`; dry-run lines for "resume the music").
7. **Pass.** The direct call goes through `tools.execute` → `_tool_update_magicmirror`, whose `origin.said` guard is unchanged. `test_mm_update_pin.py`'s 18 odd-shape tests (five trailing shapes plus the assistant-ending shape on three shims) now run the direct path (`llm_log == []`). Each is refused with the fixed sentence, with no POST. The 16 trigger tests reach the update once. Also `test_direct_dispatch.py::test_mirror_update_needs_the_users_own_last_message` (3 shims). Live: 3 `POST /magicmirror/update` → 502 on tool_service-dev (one per mount for the pinned sentence), and 0 mirror, `/ops/exec`, `/fetch` or `/http_check` lines on production `tool_service` since 07:25 UTC.
8. **Pass.** `test_failed_tool_ends_the_turn` (5 rules × tool_service failing and down): one POST, no model call, a spoken reply. `test_raising_direct_call_is_spoken_not_retried`: with `tools.execute` raising, voice says "Sorry — something went wrong on my end. I've noted the details." and debug says "Error: the pipeline failed unexpectedly (RuntimeError: handler exploded) …", with no model call.
9. **Pass.** Unit: `test_direct_request_does_not_wait_for_the_model_lock` holds `_llm_lock`; "pause" completes within 2 s, "hello" is still waiting after 0.3 s, and it finishes after release. A mutant that always takes the lock fails it. Live on dev (`lockcheck`): a long `/dev/api/chat` request sent at 0.00 s held the lock until 5.33 s (`c4a9aa1837e80cb504cb63e1f6b52ac2`). "pause" on `/dev/voice/api/chat`, sent at 1.50 s, finished at 1.53 s (0.03 s, `bdf9f71c2637b7e62674b4786c295717`). A non-direct "What is two plus two?" sent at 1.70 s finished at 5.48 s (3.77 s, `f2691aa6033583cae1531f87459198c2`).
10. **Pass.** `test_trace_shape_and_events`: `tool.control_music` and `routing.decide` are children of `pipeline.shim`, with no `agent.*` or `llm.*` span. The root's `kronk.route` is `home`. `route_shortcut` has `direct: true, tool: control_music`. `tool_call` has `agent: home, direct: true`. `tool_complete` and `request_complete` are present; there is no `agent_complete`. `test_webui_history_records_the_exchange`: `/message` "pause" stores the user and assistant messages. Live Tempo trace `445406e6ebcc…` (dev, "pause"): `POST /voice/api/chat` 13.5 ms → `pipeline.shim` 13.0 → `routing.decide` 0.0 and `tool.control_music` 12.7 → tool_service-dev `POST /music/control` 6.8 ms. Before, trace `8974073d…` had `agent.home` 4963 ms → `llm.gemma-4-e4b` 4932 ms → `tool.control_music` 19 ms. All 45 live direct requests logged `route_shortcut` direct=true, `tool_call` direct=true, 0 `agent_round`, and `request_complete`.
11. **Pass.** Unit: listed under the negatives above. Live `/dev/api/chat`: "stop the timer" → `default`, coordinator `ask_home`, 3 model rounds, 9.33 s; "pause my subscription" → `default`, 1 round; "what is the next holiday" → `default`, `ask_research` + `web_search`, 4 rounds; "pause the music in the office" → `default`, `ask_home` → `control_music`, 2 rounds. None was dispatched directly.
12. **Pass.** The 28 rows routed by the five rules carry `expect.direct: true`, and the schema test enforces that `direct` appears exactly on those rules. `test_corpus_direct_call` checks tool, action, thumb and kind against `decide()`, and the replay checks `direct` live (19 executed rows pass it). No row's tier, route, rule or tool changed, and no xfail was added. Six `stop` → `pause` (departure 2).
13. **Pass (under 0.1 s).** Table below. The dev tool_service runs `MUSIC_DRY_RUN=1` and nothing was playing, so tool_service answered from its own state ("already paused", "Nothing is playing", 409s) in 10–30 ms. A real pause waits for HA to report the state change, so in production the tool's own time will be longer. The orchestrator's share is what the table shows.
14. **Pass.** `3341 passed, 2 skipped, 23 xfailed` against the baseline `3003 passed, 2 skipped, 18 xfailed`. The 5 extra xfails are new strict-xfail instances of `test_corpus_direct_call` on corpus rows already `xfail.routing` that expect the direct path (`plan-mishearing-pasta`, `plan-mishearing-whats-up`, `eval-stop-stop`, `eval-skip-the-current-song`, `eval-volume-50`). They pass the day routing reaches them. No test reaches the network: `test_direct_dispatch.py` uses `no_network`, and the rest are regex-only. Temporary DBs live under `tmp_path`.
15. **Pass.** The replay at `5c5658a` matched the baseline row by row. The only status change is `inc-0930-search-deep-house-ytm`, fail → pass, one of the known "YouTube Music" search flappers. 8 rows are new since the baseline (M4b's mirror rows): 7 pass, 1 skipped (typed link). No baseline xfail was caused by the model changing or dropping arguments on the five rules, so none flipped (the five-rule xfails are routing misses or a mishearing). One such case did show up in the before runs: "not that" run 2 came back as a `play_music` call with no query (`2f50fd44…`). After: 0 of 20.
16. **Pass.** All live requests went to `127.0.0.1/dev/…` (dry-run checked before every script) or to the `kronk-dev` pipeline through the replay, which ran only rows HA does not take locally. "pause" (`eval-pause`, strict `HassMediaPause`) was checked read-only and not executed through HA. "what song is this" (fuzzy only) and "thumbs up" (no local match) were executed. "players unchanged (17 checked)". nginx shows no POST to production chat routes from any client between 07:25 UTC and the end of the run. HA and MA kept their uptimes (31 h, 12 days). Only `orchestrator-dev` was recreated (twice), and `tool_service-dev` kept running. Nothing was sent to the Pi.
17. **Pass.** `docs/features/direct-dispatch.md` (table, branch, telemetry, gotchas, blog hooks); README routing step 1 and the terminal-tools note; corpus doc; a pointer in `agents-as-tools-routing.md`.

## Latency (criterion 13)

`/dev/voice/api/chat`, Office stamp, non-streaming, one second apart, from
the host. Before: 07:32:52–07:34:11 UTC, dev image of `7c83f36` (taken before
the first rebuild). After: 07:45:07–07:45:46 UTC, `77280f0`. Seconds are client
wall time; the server's `request_complete` agreed to within 0.01 s on every run.

| Said | Before s (runs 1-5) | Before median | After s (runs 1-5) | After median | After tool_complete s |
|---|---|---|---|---|---|
| pause | 4.98, 2.85, 0.23, 2.37, 3.9 | 2.85 | 0.02, 0.02, 0.02, 0.03, 0.02 | 0.02 | 0.01, 0.02, 0.02, 0.02, 0.02 |
| what song is this | 1.38, 2.35, 2.22, 0.18, 0.18 | 1.38 | 0.02, 0.02, 0.03, 0.02, 0.03 | 0.02 | 0.02, 0.02, 0.02, 0.02, 0.03 |
| thumbs up | 2.21, 1.96, 2.59, 1.4, 1.49 | 1.96 | 0.02, 0.02, 0.02, 0.03, 0.03 | 0.02 | 0.02, 0.02, 0.02, 0.02, 0.03 |
| play another one | 2.96, 2.91, 2.89, 2.94, 3.95 | 2.94 | 5.26, 2.82, 2.45, 0.23, 3.59 | 2.82 | 0.02, 0.02, 0.01, 0.02, 0.02 |
| not that | 3.01, 2.88, 4.59, 1.12, 2.58 | 2.88 | 0.02, 0.03, 0.03, 0.02, 0.02 | 0.02 | 0.02, 0.02, 0.02, 0.02, 0.02 |

"play another one" is the `music` rule both times, so it went to the model both
times. Before, the model called `control_music` 5 of 5 times. After, it called
`play_music` 3 times (once with no query: "That didn't work — I didn't get enough
detail…") and `control_music` twice. "not that" is the `music_another`
representative. The sub-second "before" runs (0.18, 0.23 s) are model rounds
that came back fast; the spread is the model's.

Through HA (`assist_pipeline/run`, `kronk-dev`, from the replay): `plan-stop-bang`
0.02 s (baseline 1.0), `inc-1019-thumbs-up-dot` 0.03 (4.8),
`eval-what-song-is-this` 0.03 (2.35), `eval-resume-the-music` 0.02 (3.18),
`eval-turn-it-down` 0.03 (2.56), `m4b-update-the-magicmirror` 0.02.

| Said | Run | Before trace | After trace |
|---|---|---|---|
| pause | 1 | `8974073d5b3fb96b8080f50dc3a0bfd6` | `c7f86a228083279c0446305122a2881e` |
| pause | 2 | `e0fd24d0cd359e73fd8f77ece585b660` | `2c4ad2a23a32089278ba501a5331b323` |
| pause | 3 | `e75e1fc9f7db0f755bb7f92ba5c5c9d9` | `3046cc955f9cda482fc9084eaf590b32` |
| pause | 4 | `9fb18b738f1ca6e1981526ea3ac6f3fa` | `41fcbb12725a7418399d6ebc622db87a` |
| pause | 5 | `bad6d63867bb3e26b2aa7f75fe7c46dc` | `853248d3d96f88a6a0fe8f29270d9d33` |
| what song is this | 1 | `e7d3b5b48bd41c3d8b3a61162a0a1220` | `eeb9874940f1e20c23ec7449892b00ee` |
| what song is this | 2 | `f744c898a34aae8fb1ef32720acaaacf` | `0fa9076206548e1db80d0d6102060ad4` |
| what song is this | 3 | `8ca1aad37c9a7a2331b074c8246e8b54` | `5a8712227c1680999523a64b3ef3f28b` |
| what song is this | 4 | `a623b6909119408765326fcde466e706` | `5f9c6c842c728f6e9480f2cb6629d8e6` |
| what song is this | 5 | `783faa52aa2e5579f3c43c74189efb9b` | `edf533dd853ed17e9364c64eefbd1fbd` |
| thumbs up | 1 | `30b1b218c60dd4b3abe004aa586d4e2d` | `321ff32b8def5c2d8e250bfa7ba6a79f` |
| thumbs up | 2 | `334fdb6c70d0db57230f5c24251ec028` | `4b6516032c906ee97dc26659b9d176f5` |
| thumbs up | 3 | `9af0b2290eed649f32f1485128f09286` | `acb62dbece1dd115a78592625e81fa4b` |
| thumbs up | 4 | `5cb22ec7c3522764001acf3f772d9db7` | `ddfda1dfd842d6da7ab32f66bf10d9c7` |
| thumbs up | 5 | `f975509012e7a3713bc1855476716a6c` | `9082d0e55ef38fa16c682d926710a8eb` |
| play another one | 1 | `75d2818749f03330ec015e4ae29ea84d` | `658f951c096e3069e4bf9b2ee2ab3b13` |
| play another one | 2 | `0cb40afec12d15a6480de1a47fb2ccd9` | `98207238406f40e763f17e91f116d5cf` |
| play another one | 3 | `09f44435db204917fefaeb10fa970f53` | `234532f5c6f6a2cb9296da7a195b72ef` |
| play another one | 4 | `4e36e05e5c8011e2fb22c47b1eb947d0` | `b0c12de23c892abb8ee02c0ee9dc8437` |
| play another one | 5 | `80e02896bb1902eefa073609c64b38cd` | `882a21e5d68c9cce95a7c77e7c4c2d54` |
| not that | 1 | `d44f7da168349c89b1da699262fcba69` | `9b6e446fa30b812b643ace806d5db8d4` |
| not that | 2 | `2f50fd44b045cfac959a0472ec384c69` | `46a53c065905be5491d57d2c4c26ef22` |
| not that | 3 | `4e816ff8dcbe738838c030ea46aea41c` | `4e02cb2cc590e90ed6145094d6474be6` |
| not that | 4 | `6db3ef1366550ebf2595467c0b8170b6` | `8c97f2ab999032ee6f37e8f10a1bebd5` |
| not that | 5 | `0842877f123674191f7cf8e994535ce0` | `12c58db576fb99a83d4c91554642e19b` |

## Live checks on dev

`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`
at 07:44 UTC (`77280f0`) and again at 07:54 UTC (`d63aa04`; the only code
difference is `routing.py`'s docstring). Both times `tool_service-dev` was
unchanged and kept running, `orchestrator-dev` was recreated and healthy, and
`/dev/api/status` reported `music_dry_run: true`. nginx resolves the dev
upstream at request time and was not restarted.

15 utterances × 3 shims at 07:46:11–07:46:12 UTC: every one HTTP 200 in
0.01–0.02 s, rule as pinned, direct=true, 0 model rounds. Replies:
"pause" / "Stop!" → "The Office speaker is already paused."; "resume the music"
→ "Dry run: would resume on the Office speaker. Nothing was sent."; "skip",
"turn it down", "volume up" → "I couldn't do that. Nothing is playing on the
Office speaker."; "what song is this", "Who is this?" → "Nothing is playing on
the Office speaker."; "thumbs up", "Thumbs down.", "never play this again",
"more like this" → "I couldn't rate that. Nothing is playing on the Office
speaker."; "not that", "not that in the office" → "I couldn't play that. I
didn't pick what's playing, so I've got nothing to swap in. Tell me what
you'd like." (tool_service-dev logged `player='office'` for the second);
"update the magic mirror" → `/api/chat` and `/v1`: "I couldn't update the
magic mirror. Mirror preflight failed: SSH key not found at
/keys/kronk-mm-update — moun…", `/voice/api/chat`: "I couldn't start the
magic mirror update. The details are in the logs."

## Test output

```
$ ./scripts/run_tests.sh
…
=========== 3341 passed, 2 skipped, 23 xfailed, 6 warnings in 23.99s ===========
```

Proof the new pipeline tests test the change: `git stash push orchestrator/main.py`,
then `./scripts/run_tests.sh tests/test_direct_dispatch.py -q -p no:randomly`
gave `107 failed, 5 passed, 3 warnings in 3.25s` (the 5 are the negatives).
Mutants on `main.py` (scratch copy, restored): `_terminal_speech(..., errors.DEBUG)`
→ `7 failed, 105 passed`; always `async with _llm_lock` → `1 failed, 111 passed`;
span named `tools.<name>` → `1 failed, 111 passed`.

## Replay output

```
$ python3 scripts/voice_replay.py --label m7-build
…
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_074950_m7-build.json
exit=0
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows, `{'pass':
91, 'xfail': 20, 'fail': 1, 'skip': 2}`): every one of the 114 rows has the
same status except `inc-0930-search-deep-house-ytm` (fail → pass, known
flapper). The other flappers (`eval-shuffle-it`, the YouTube Music search rows)
kept their baseline status. Nothing was skipped for a busy player, and the
run was not stopped. Before the run, `--check-only` reported `{'pass': 65,
'skip': 56, 'xfail': 1}`, and no media player was playing.

## Departures from the criteria and why

1. **"play another one" is not a direct call.** Criterion 13 names it for the
   measurement, but `_ANOTHER_RE` does not match it: it routes `music` (pinned
   at `7c83f36`, `routing_pin.jsonl:5331`). Per the brief and the stopping
   rule, the regex was left alone. Measured anyway, with "not that" added as
   the `music_another` representative. If Drew wants "another one" covered,
   it is a one-branch regex change and a pin update, decided on data.
2. **Corpus `action: stop` → `pause` on six rows** (`plan-stop-bang`,
   `inc-0910-stop-transcript`, `plan-mishearing-whats-up`, `eval-stop`,
   `eval-stop-stop`, `eval-stop-2`). The plan's table maps stop → pause.
   tool_service runs both as `media_pause` with the word "Paused", so the
   effect is the same, but the replay's action check compares the dry-run
   line's action, which now says `pause`. Each row's note says so. Not a
   weakening: the row expects exactly what Kronk now sends. The coordinator
   should confirm.
3. **`music_another`'s room is passed as `player`.** The criterion says the
   trailing room is "handled as it is today". Today the blueprint appends the
   room so that Kronk targets it ("the target rides in the words", KRONK
   change 9), and the home prompt has the model pass a named room as
   `player`. The direct call does the same. A request with the room *and* a
   trailing "please" after it passes "office please" (gotcha in the feature
   doc; no such row in the corpus or eval set).
4. **Five more strict xfails** (criterion 14): new parametrizations of an
   existing marker, not new known failures.
5. **`mm_update` is unanchored**, unlike the other four: "Okay, update the
   magic mirror please" dispatches directly and the rest of the utterance is
   not acted on. This is unchanged from before (terminal tool).
6. **Not done here:** no ROADMAP change (the sprint items are not in
   ROADMAP.md), no CLAUDE.md change (its "Routing / agent loop internals"
   list could gain `docs/features/direct-dispatch.md`, the operator's file),
   no `pipeline_bench.sh` (forbidden by the brief).

## For the coordinator at promotion

- Deploy: `docker compose up -d --build orchestrator` (production); then
  `docker compose restart nginx` per CLAUDE.md. tool_service is unchanged by
  M7. No HA or MA restart; no config/ change.
- Verify through the shim with nothing playing (or in an agreed quiet
  moment): `/api/chat` "what song is this" (read-only) should answer in well
  under a second, with a `route_shortcut … "direct": true` and a
  `tool_call … "direct": true` line in `docker logs kronk-orchestrator-1`.
  "pause" and "thumbs up" on production act on real speakers and the taste
  file; do not send them as a check.
- Grafana: direct requests show `tool.<name>` directly under `pipeline.*` and
  no `agent.home`. "Agent loops by agent" will count fewer home runs. "Tools
  by name" and "Routes over time" are unchanged in kind.
- Ask Drew for a real "Okay Nabu" test on "what song is this" and "thumbs up"
  (both reach Kronk; "pause" and "skip" are usually answered by HA itself).
- Dev pair left running and healthy at `d63aa04` code.
