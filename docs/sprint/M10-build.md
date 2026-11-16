# M10 build report: voice regression corpus

Build agent, 2026-11-15/16 (UTC), branch `refactor`. Criteria:
`docs/sprint/M10-criteria.md`. Commits: `36339e4` (corpus and layer one),
`5c80992` (live replay, its tests, the baseline), `0bd7c02` (docs), and the
commit that adds this report.

## Summary

- `tests/voice_corpus.jsonl`: 114 rows. Every utterance the plan names, 37
  rows from 10 music incident docs, all 73 music/playback/now_playing rows of
  `docs/bench/routing_eval.jsonl`, 5 M11 seed rows, 1 row from the M17 e2e.
- Layer one, `tests/test_voice_corpus.py`: schema, plan-named utterances, and
  every row through `routing._classify_inner`. 213 passed, 18 xfailed, 0.15 s.
  Holds the M11 parser hook.
- Layer two, `scripts/voice_replay.py`: silent live replay through HA's
  `assist_pipeline/run` (intent stage only) on `kronk-dev`. Unit tests for its
  gates in `tests/test_voice_replay.py` (30).
- Baseline: `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`.
  91 pass, 20 xfail, 1 fail, 2 skip. 46 rows executed, 66 checked read-only.
  17 `media_player`s unchanged.
- No tool_service or orchestrator code changed. The existing `MUSIC_DRY_RUN`
  is the only dry-run mechanism. No container was rebuilt or restarted; HA and
  MA were not touched.

## Files changed

| File | Change |
| --- | --- |
| `tests/voice_corpus.jsonl` | new: the corpus |
| `tests/test_voice_corpus.py` | new: layer one, schema, M11 hook |
| `scripts/voice_replay.py` | new: layer two |
| `tests/test_voice_replay.py` | new: the replay's gates and comparisons |
| `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json` | new: the baseline |
| `docs/features/voice-regression-corpus.md` | new: format, the rule, both layers, gotchas, blog hooks |
| `CLAUDE.md` | incident rule adds the utterance to the corpus; voice definition of done points at the replay |
| `docs/features/voice-pipeline.md` | "Testing utterances" points at the replay |
| `docs/sprint/M10-build.md` | this report |

## How the corpus was seeded

Row ids carry their primary source: `eval-` 66, `inc-` 32, `plan-` 10,
`m11-` 5, `m17-` 1. A row cites every source it appears in, so the
per-source counts below overlap.

| Source | Rows citing it |
| --- | --- |
| `docs/bench/routing_eval.jsonl` (every row with `sub` music, playback or now_playing) | 73 |
| Plan, M10 section (named utterances) | 10 |
| Plan, M11 section (implement step 3 seeds) | 5 |
| `INVESTIGATION_2026-10-04_radio_failures.md` | 8 |
| `INVESTIGATION_2026-11-08_anime_pop_radio_hang.md` | 7 |
| `INVESTIGATION_2026-10-19_ytmusic_album_misses.md` | 6 |
| `INVESTIGATION_2026-10-19_thumbs_wrong_song.md` | 5 |
| `INVESTIGATION_2026-09-15_album_played_one_track.md` | 4 |
| `INVESTIGATION_2026-10-21_pandora_station_match.md` | 3 |
| `INVESTIGATION_2026-09-10_voice_stop.md`, `…2026-09-30_music_false_success.md`, `…2026-10-21_playlist_miss_wrong_music.md`, `…2026-10-25_pandora_station_name_miss.md`, `…2026-11-07_ma_pandora_and_ui.md` | 1 each |
| `ROADMAP.md` (the "Pasta" / "What's up?" mishearings) | 2 |
| `docs/features/voice-music-control.md` ("The Girl with the Sun …", "shuffle my YouTube playlist …") | 2 |
| `docs/sprint/M17-e2e.md` (O4) | 1 |

Incident utterances were taken only where the doc records the words. Lines
the doc truncates with "…" were left out, except the YouTube Music link, which
is kept as recorded and skipped live. Follow-up chat ("What the hell was
that?", " up.", " but") was left out.

The plan's named utterances, verbatim: "Play Anime Pop Radio. That's probably
about it. Everything is pretty wet." (its own row; the incident's exact STT
transcript, with leading space and double spaces, is a second row), "Like
massive attack radio", "Start an orbital radio station on YouTube Music."
(the transcript; the plan's text is a prefix), "play The Girl with the Sun in
Her Head by Orbital", "shuffle my YouTube playlist anime bangers", "put on some
jazz", "Stop!", "Play ninety's two thousands and today's hits radio on the
basement voice pe ma" (the incident's bench sentence in full), "Pasta",
"What's up?". `test_corpus_holds_the_plan_named_utterances` pins them.

Expected outcomes were set by hand, row by row, from the incident docs and the
eval labels, under one rule written into the feature doc: a request inside a
pin's designed scope expects the pin; a request outside it by design (names a
player or room for playback or now-playing, composite, needs the previous
turn) expects `direct/default`; a mishearing expects what was meant. The tier
(`ha_local` or `kronk`) was set from what HA's sentence matcher does today
where that matches the design, and marked where it does not ("Who is this?").

## Expected-failure rows (23 rows; 18 routing, 11 live, 3 parser markers)

| Row | Layers | Why |
| --- | --- | --- |
| `plan-like-massive-attack-radio` "Like massive attack radio" | routing, live | STT turned "play" into "like"; `_MUSIC_RE` needs a verb at the start (documented miss in `routing.py`). The coordinator answers without a tool (radio_failures cause 3). |
| `inc-1004-well-play-orbital` "We'll play Orbital Radio." | routing | Prefixed verb. Same class. |
| `inc-1017-pay-the-album-hotel-costes-3` | routing | Typed "Pay" for "Play". |
| `eval-oh-play-portishead-radio`, `eval-i-have-to-play-the-radio-station`, `eval-late-evening-from-radio-on-pandora`, `eval-i-hope-you-play-anime-pop-radio`, `eval-we-play-justin-johnson-radio-on-pandora` | routing | Filler or STT before the verb ("Oh,", "I have to", "Late evening from" for "Play …", "I hope you", "We"). |
| `eval-played-the-album-toys-in-the-attic`, `eval-i-shuffled-my-youtube-playlist-old-man`, `eval-j-envoie-my-youtube-playlist-video-game`, `eval-clay-thief-recreperation-radio-on-pandora` | routing, live | STT-mangled verb or name; on the coordinator no tool runs. |
| `inc-1021-ninetys-no-verb` "90s, 2000s, and today's hits radio" | routing, live | No verb at all; the coordinator answers without a tool. |
| `plan-mishearing-pasta`, `plan-mishearing-whats-up` | routing, live | STT mishearing; belongs to M12/M13, not to a rule. |
| `eval-stop-stop` "Stop.  Stop." | routing | The same command twice; the anchored pattern takes one. |
| `eval-skip-the-current-song` | routing | "the current song" is not in `_PLAYBACK_RE`. One capture. |
| `eval-volume-50` "volume 50%" | routing, live | Absolute volume: not in the pin, and `control_music` has no absolute-volume action. |
| `inc-0915-who-is-this` "Who is this?" | live | HA's built-in `HassGetState` takes it locally ("Not any"). |
| `eval-shuffle-it` "Shuffle it." | live | No tool shuffles the current queue. |
| `plan-girl-with-the-sun` | parser | A greedy room slot cuts titles containing "in"/"on". M11 matches rooms from the right. |
| `inc-0915-daisies-album-eels-ytm` | parser | The album sentence takes "YouTube Music" as the room and keeps "by Eels" in the name. |
| `eval-play-the-albo-daisies-of-the-galaxy` | parser | "album" heard as "Albo"; the type word is gone. |

None of these is fixed here, and no routing rule was added (stopping rule).

## Baseline replay

`python3 scripts/voice_replay.py --label m10-baseline`, 01:28:24 to 01:32:53
UTC, pipeline `kronk-dev` → `http://localhost/dev/voice`, dry-run on. File:
`docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json` (its
`corpus_sha256` matches the committed corpus; its `git_commit` field reads
`36339e4` because the script was committed right after the run, unchanged, in
`5c80992`).

| Status | Rows |
| --- | --- |
| pass | 91 |
| xfail | 20 |
| fail | 1 (`inc-0930-search-deep-house-ytm`, finding 2) |
| skip | 2 (`inc-1017-try-playing-it-ytm`: needs history; `inc-1019-ytm-link`: typed link) |
| xpass | 0 |

- Executed through Kronk dev: 46. Latency p50 3.5 s, p95 7.5 s, max 13.1 s.
- Checked read-only, not executed: 66 (every trigger and strict built-in match).
- Players: 17 `media_player`s, state, title, artist and content id identical
  before, after each executed row, and at the end.
- Exit status 1, because of the one fail.

Console output, summary lines verbatim:

```
voice_replay: {'pass': 91, 'xfail': 20, 'fail': 1, 'skip': 2}; executed 46, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json
```

An earlier full run (01:23) was discarded: the replay read tool_service's log
with `docker logs --since`, which is second-granular, so two rows picked up
the previous row's `music dry run:` line ("Stop!" scored as action `play`
on the kitchen). The script now filters on the nanosecond timestamp
(`_log_lines`), with a test for that exact case. The discarded run's
Kronk-side records are still valid and feed finding 3.

### Safety evidence (criteria 11, 12, 16, 19, 20)

- Refusals, run with `--check-only` and one row so nothing could execute:

  ```
  voice_replay: REFUSED: pipeline kronk is production (Kronk at http://localhost/voice); pass --allow-production to replay against it (dry-run is still required)
  voice_replay: REFUSED: music dry-run is not on behind kronk (http://localhost/api/status → tool_service {'status': 'ok', 'music_dry_run': False}); refusing
  voice_replay: REFUSED: pipeline Full local assistant uses conversation.home_assistant: everything would run locally in HA
  ```

  Exit 2 each time, no result file written. Dry-run off on dev was not
  provoked (the brief forbids switching it off); `tests/test_voice_replay.py`
  pins that gate (false, missing key, non-boolean, unreachable), the gate
  before every row (`test_dry_run_lost_mid_run_stops_before_executing`), and
  that trigger and strict built-in rows are never sent
  (`test_local_rows_are_checked_not_executed`).
- Production files, mtimes after the run: `data/sessions.db` 2026-11-11
  21:49, `data/ops_audit.log` 2026-10-22, `data/music_taste.yaml` 2026-10-21,
  `data/music_taste.db` 2026-11-15 08:30. None written in the replay window.
- Containers: every production container last started 01:05 UTC (the
  coordinator's sprint-M17 deploy, before this build began); the dev pair
  00:56 and 00:58; HA 00:28; MA 2026-11-04. Nothing was restarted or
  recreated by this build.
- Tempo, replay window: 78 traces (44 `kronk-orchestrator-dev`, 34
  `kronk-voice-tracer-dev`). `{ trace:rootService !~ ".*-dev" }` returns 0.
  Production voice_tracer now emits dev runs as `kronk-voice-tracer-dev`, so
  the M17 single filter is enough.

## Test output, verbatim

`./scripts/run_tests.sh`, last line:

```
========== 1111 passed, 2 skipped, 18 xfailed, 14 warnings in 13.15s ===========
```

Baseline before M10: 868 passed, 2 skipped. New: 213 (corpus) + 30
(replay) = 243 passed. The 18 xfailed are the corpus rows with
`xfail.routing`, listed above. The 2 skips and 14 warnings are the
baseline's.

Criterion 8, one expectation broken on purpose (`plan-stop-bang` rule set to
`now_playing`), then the file restored and compared byte for byte:

```
E       AssertionError: corpus row plan-stop-bang: 'Stop!' routed home/playback, expected home/now_playing
E       assert ('home', 'playback') == ('home', 'now_playing')
FAILED tests/test_voice_corpus.py::test_corpus_routing[plan-stop-bang] - Asse...
1 failed, 212 passed, 18 xfailed in 0.15s
```

Criterion 7, an expected-failure row made to pass (`eval-stop-stop` expected
set to today's route), then restored:

```
[XPASS(strict)] [eval-stop-stop] The same command twice; _PLAYBACK_RE takes one command, anchored. One capture; stopping rule.
1 failed, 213 passed, 17 xfailed in 0.14s
```

## How to run both layers

- Layer one: `./scripts/run_tests.sh` (or `./scripts/run_tests.sh
  tests/test_voice_corpus.py`). No network.
- Layer two: `python3 scripts/voice_replay.py --label <name>` from the repo
  root. Subsets: `--id <row>` and `--tag <tag>`, both repeatable. Read-only:
  `--check-only`. Rows without a recorded origin run as the office Satellite1
  (`--device` to change). Needs HA_TOKEN (read from `.env`) and docker.
  About 5 minutes for the full corpus.

## Deviations from the plan

1. Plan step 5's dry-run switch was built in M17 (`MUSIC_DRY_RUN`); per the
   coordinator it is reused. No per-request dry-run field was added to
   `/music`, so criterion 17 holds by no change.
2. Plan step 4 asks for the M11 parser in layer one. It does not exist; the
   hook is in place and the rows carry kind, media type, name, artist,
   provider, player and shuffle.
3. The live layer executes 46 of 114 rows. The other 66 are rows HA handles
   itself with `prefer_local_intents` on; they are checked with HA's
   sentence-match debug instead (coordinator decision). If Drew turns
   `prefer_local_intents` off on `kronk-dev`, most of them become executable
   without a script change. Trigger rows would still match the blueprint
   first, though, and stay read-only.
4. M11 seed rows: four of the plan's five are in. "A title that contains a
   genre word" is left for M11, which needs a real library title.
5. Expected names are a judgment where a doc does not say what was meant
   (`eval-late-evening-from-radio-on-pandora` has name `null`).

## Findings about today's behaviour (not fixed here)

1. **The blueprint hands off to production Kronk, whatever pipeline the
   request came in on.** `kronk_agent: conversation.ollama_conversation`
   (`ha/blueprints/mass_assist_kronk.yaml:372`). On Hey Jarvis, a request
   matching the catch-all or loose trigger, or a named play MA rejects, is
   answered by production Kronk and plays for real. This extends M17 O1: the
   dev wake word is only dev for sentences no trigger matches.
2. **The coordinator sometimes sends a YouTube Music search to research.**
   "Search for some deep house chill music on YouTube Music" went coordinator
   → `ask_research` → `web_search` and spoke a list of links (trace
   `91e35ba8565c83139c4b6d7c50227140`). In the discarded 01:23 run the same
   row went `ask_home` → `play_music`. Model variance on a route-by-design
   row. Not marked xfail: no fixed cause.
3. **The home agent wrote a tool call as text and it was spoken.** 01:23 run,
   "Thumbs up.": rule `rate` → home agent, no tool call, reply `{.\n
   "action": "thumbs up".\n}.` (trace `6f73a5a7c38daf943ae6e06397a2e410`). The
   01:28 run of the same row passed. Same class as M17 O4. The loop's
   `tool_syntax_leak` guard did not fire for this shape.
4. **"Shuffle it." does something wrong both runs.** 01:23: `control_music`,
   "The Office speaker is already paused." 01:28: `play_music` with the
   query "Shuffle": "Dry run: would play the artist Shuffle on the Office
   speaker, shuffled." That is the play-the-literal-words class M11 removes.
5. **The coordinator invents capability limits or asks back on music
   misses.** "90s, 2000s, and today's hits radio" → "The available tools only
   allow for general music playback commands … not specific genre or playlist
   selection." "Like massive attack radio" → "Please clarify. Are you asking
   for news about a massive attack, a specific radio station, or something
   else?" A question keeps the satellite's mic open
   (`INVESTIGATION_2026-09-10_voice_stop.md`, finding 3).
6. **HA's sentence-match debug is not the pipeline's matcher, and M17 O1
   overstates the local tier.** The debug call is non-strict; the pipeline's
   local pass is strict (`strict_intents_only=True`). "pause the music" from
   the office is a fuzzy `HassMediaPause` match in debug (`fuzzy_match: true`),
   so in the pipeline it reaches Kronk. O1 listed it as built-in. Bare
   "Pause.", "skip", "Skip!", "skip this song" and "Pause the basement voice pe
   ma" are strict matches and local. The replay reads the `fuzzy_match` flag
   for exactly this reason.
7. **HA runs every matching sentence trigger, and debug shows one.**
   `async_handle_sentence_triggers` collects every trigger whose sentences
   match and runs all their callbacks; the automation is `mode: single`.
   For "Play orbital radio." and "Play French Dinner Radio." the debug template
   is the catch-all, not the radio sentence ("…on Pandora." shows the radio
   sentence). Whether change 11's radio fast path or the change 12 catch-all
   runs for a station without a suffix is decided by order. Not checked
   further (it needs automation traces of real requests); M11's check-first
   list already asks how HA ranks the catch-all.
8. **`routing_eval.jsonl` labels "Shuffle it." playback; the router pins it as
   `music`** (the shuffle-verb branch of `_MUSIC_RE`). The corpus expects
   `music`, the designed rule, with the live xfail above.

## Left for Drew

- `prefer_local_intents` on `kronk-dev` (pending). It decides how much of
  the corpus the replay can execute. Finding 1 decides whether Hey Jarvis is
  a safe wake word for music at all.
- At promotion: a ROADMAP "Shipped" line and a `docs/BLOG_TOPICS.md` entry
  (the feature doc has the blog hooks). M10 adds nothing that runs in
  production, so promotion is just merging the files.
- Whether model-variance rows (findings 2 and 3) get a tag or a repeat count
  in the replay. Today a single run can fail on them.
