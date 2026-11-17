# M11 build report: the music decision in tool_service, built and shadowed

Build agent, 2026-11-17 (UTC), branch `refactor`, on top of `bbe54ee`.
Inputs: `docs/sprint/M11-criteria.md`; `Kronk evaluation findings and
implementation plan.md` (M11, C5, C6, the M11 line of "Verified on Kronk,
2026-11-15"). Scope as the criteria narrow it: build and shadow only. No
cutover, no change to Home Assistant, the blueprint stays, the live `/music`
path and its literal-play fallback are unchanged.

## Steps done

All eight numbered steps are done and committed, in order:

| Step | Commit | What |
|---|---|---|
| 1 | `d4c4fa6` | Corpus rows before the parser (criterion 6) |
| 2 | `85883d4` | Pin of today's live `/music` for the corpus rows, MA stubbed (criterion 22) |
| 3 | `f5d5ef9` | The parser (section A) |
| 4 | `021ac4d` | The decision order and the policy in `config/music.yaml` (section B) |
| 5 | `5bcc4ce` | `POST /music/say` (section C) |
| 6 | `a33790f` | Shadow mode and the summary script (section D) |
| 6, fix | `9b10179` | The two new modules were missing from the image's COPY line; dev crash-looped on import. New test: every module `main.py` imports is copied |
| 4, fixes | `bb42288`, `f6c91ee` | Decision fixes found on dev (below, "Found on dev") |
| 7 | `26c6701` | The corpus parser layer and the replay's answer check (section E) |
| 8 | `ee29381` | Docs |
| report | this commit | This file |

## Check first (plan M11), as found

- **`rest_command`.** Loaded since M17 (`configuration.yaml:25`,
  `rest_command: !include rest_command.yaml`; C6 is done).
  `rest_command.kronk_music` points at `http://127.0.0.1:8003/music/voice`.
  The installed blueprint is byte-identical to `ha/blueprints/mass_assist_kronk.yaml`
  and hands off with `conversation.process` (3 calls), never `rest_command`
  (0). So `/music/voice` is not called by anything today.
- **One-slot catch-all against built-in intents.** HA's read-only sentence
  match (`voice_replay.py --check-only`) puts every "play …" corpus row,
  including the 14 M11 rows, on a blueprint trigger, never a built-in
  intent. The non-music rows ("play a game with me", "play the movie
  Inception …", "play the news") match the catch-all trigger too.
- **Blueprint latency per media type** (Tempo, `blueprint.run` spans of
  `kronk-voice-tracer`, 2026-11-08 to 2026-11-17, end to end including the
  verify wait): radio played p50 1.54 s (n=2), artist played 0.32 s (n=2),
  album played 5.87 s (n=2), playlist played 6.37 s (n=3); handed to Kronk:
  catch-all p50 8.97 s, p95 17.37 s (n=10), playlist 6.22 s (n=1), radio
  6.56 s (n=2). Samples are small; Tempo starts 2026-11-08.
- **The model call.** tool_service has no model client; it already calls
  LiteLLM directly for the news brief and genre tagging (operator decision
  2026-08-24). LiteLLM passes both `response_format` (a JSON schema with an
  enum of `style`, `named`) and `chat_template_kwargs` through to
  llama.cpp. Measured from the host against `gemma-4-e4b`: with reasoning
  1.50 to 2.53 s (20 prompts), with `enable_thinking: false` 0.27 to 0.36 s,
  the same answer on 20 of 20.
- **`score()` and providers.** Confirmed: the provider counts only as
  `provider_bonus` when the sentence names it, plus `library_bonus` for a
  library item; there was no default provider preference.
- **`_resolve_named` and cross-type retry.** Confirmed absent: it only adds
  the "X by Y" stage 2 (the artist's works).
- **The literal-play fallback** is in `/music` as described; no corpus row
  reaches it against the real library (search always finds candidates), so
  it is pinned with two synthetic cases (no candidates, MA unreachable).

## The parser

`tool_service/music_resolve.py`, pure, beside `radio_name()` (`:116`) and
`normalize()`:

- `parse_sentence()` `:597` returns a `Parsed` (`:487`): verb, words, type
  word and type, style marker, "X by Y" (`by_generic` for "songs by Y"),
  shuffle, named provider, room (real name, kind, words said), and
  `radio_name()`'s station.
- Rooms from the right-hand end: `_room_clause()` `:543` tries each
  "in"/"on" from the right, each candidate running to the end of the
  sentence; `match_room()` `:511` takes an exact player name, an exact
  area, the clause as part of a name, then a near match (`room_min_score`
  0.85), never a name inside the clause. A clause ending in a device noun
  that matches nothing is an unknown target.
- Exclusions: `NOT_MUSIC_ALWAYS` `:463` and the next two tuples, the same
  words as `routing._MUSIC_RE`; `tests/test_music_parse.py` reads both lists
  out of the routing pattern and also compares the two on 12 sentences.
- `room_key()` `:444` is `main._norm`'s body, moved; `_norm` delegates.

## The decision order

`tool_service/music_decide.py`, one function per step, run in the order of
`decision.order` by `decide()` `:469` (dispatch table `STEPS` `:465`):

| Step | Function | Notes |
|---|---|---|
| 1 markers | `step_markers` `:317` | type word or "songs by Y" means named; style marker means style |
| 2 genre list | `step_genre_list` `:328` | taste DB vocabulary, read-only; a type word skips it |
| 3 named | `step_named` `:350` | `_exact` `:205` (library name, no search) then `_resolve` `:221`; `weak_title` `:339`; `title_track_album` `:270` |
| 4 cross type | `step_cross_type` `:365` | other types, strong only, `cross_type: true` |
| 5 other reading | `step_other_reading` `:379` | unknown style retried as a name, artist only; name to style if the genre list knows it |
| 6 model | `step_model` `:424`, `unclear` `:410` | one constrained question; shadow never asks |
| 7 rule 7 | `decide` `:486` | a lone title never beats a style reading |
| 8 not found | `finish_not_found` `:456` | names the closest match above `hint_min_score` |

The search, scoring and choice are shared with `/music`:
`music_resolve.resolve_named()` `:379` is the body of the old
`main._resolve_named_inner`, with its I/O passed in;
`main._resolve_named_inner` `:2115` calls it with today's arguments. The new
path adds `types` (cross-type retry), `provider_order`, `strong_min` and
`prefer_min`. The provider policy is `music_resolve.prefer_provider()`
`:355`. The reads the decision may make are `main.ReadOnlyMusic` `:2192`;
every Music Assistant command goes through `main.ma_read` `:2452`, which
refuses anything outside `MA_READ_COMMANDS` `:2445` (search, name lookup,
library listings); `/music`'s own search, library listing and station
lookup now go through it too.

`POST /music/say` is `main.music_say` `:2310`; `main.decide_sentence`
`:2258` is the decision without the play; the play is `_play_named` `:1574`
(the tail of `/music`, extracted) or `_play_genre`.

## The policy file

`config/music.yaml`, through the M21 loader (`tool_service/config_loader.py`:
`Parser` `:153`, `ProviderOrder` `:196`, `ModelStep` `:206`, `Decision`
`:214`), schema strict, every key commented, defaults equal to the file
(`test_shipped_music_yaml_loads_and_equals_the_builtin_defaults` passes):

- `parser` (`:62`): `style_markers`, `type_words`, `fillers`, `room_min_score`.
- `decision` (`:82`): `order` (the five steps in fixed order, `model` placed
  anywhere once; anything else is refused), `strong_min_score` 0.75 (the
  live `play_min_score`), `title_min_score` 0.9, `provider_min_score` 0.95,
  `provider_order` per media type (artist, album, track, playlist:
  `filesystem_local`, `ytmusic`; radio: `pandora`), `model` (`enabled`,
  `model` gemma-4-e4b, `thinking` false, `timeout_s` 4).

Thresholds from the corpus: on the snapshot, with the provider preference
applied, the item asked for scored 0.98 or more and near misses 0.77 to
0.95, and no corpus row changes winner because of the preference (Music
Assistant merges library copies of an item into one candidate). So
`provider_min_score` is 0.95 and `strong_min_score` stays at the live 0.75.
`tests/test_music_decide.py::test_the_model_step_moves_earlier_by_configuration`
shows the order change: late, `play italian` searches first and then asks;
with `model` after `genre_list` it asks before any lookup.

## The model question

`main.ask_style_or_named` `:2159`: a direct call to LiteLLM
(`MUSIC_MODEL_URL`, default `http://host.docker.internal:8002/v1`; set in
`tool_service-dev`'s compose environment), `temperature` 0, a JSON schema
whose one property is an enum of `style` and `named`, `max_tokens` 32 with
thinking off (`chat_template_kwargs: {enable_thinking: false}`), and a
timeout of `decision.model.timeout_s` (4 s). The answer only picks the
branch; the words always come from the sentence. On a timeout, an HTTP
error, unparsable JSON or a value outside the two, it returns no answer and
the rules' own reading stands: for a style request with a lone title match
the style (rule 7); for an unmarked name with a weak title the named
reading (what `/music` does); for an unmarked name with no match, a plain
"not found".

Why direct, and why it does not break the lock's purpose: there is no
orchestrator endpoint for it and tool_service already calls LiteLLM
directly for two jobs. The orchestrator's `_llm_lock` comment ("llama.cpp
handles one request at a time") is out of date (plan M9 check: each
llama.cpp server runs 4 slots, each with the full context), so a question
arriving during another generation is served in another slot; the cost is
shared GPU time for about 0.3 s. The one structure documented as relying on
the lock, `sessions._cache`, is in the orchestrator and tool_service never
touches it. In this item no live request can ask: shadow mode never asks,
and nothing live calls `/music/say`. At cutover the orchestrator's `music`
route becomes a direct dispatch, which skips the lock by design (M7).

Measured on dev, through `/music/say`: the question itself 233 to 278 ms
(n=6, warm), 316 and 346 ms (cold); whole requests that asked it 257 to
342 ms warm, 2.50 and 2.66 s cold (both also ran searches and a genre plan).

## Shadow mode, and why it cannot play

`MUSIC_SHADOW` (env). Off in code (`music_shadow.ENABLED` `:39` is false
unless set), off in production compose (a commented `# - MUSIC_SHADOW=1`
line in `tool_service`'s environment), on for `tool_service-dev`. `main.play_music`
`:1451` answers through `_play_music_live` `:1477` (the old body) and, with
shadow on, schedules the shadow after the answer exists, also on failure
(`music_shadow.schedule` `:170`). What the live path sent to Music Assistant
is captured passively by a context variable written in `_play_verified`
(`_LIVE_SENT` `:1447`).

Playing is impossible by construction: `tool_service/music_shadow.py`
imports no HA or MA client (only `asyncio`, `json`, `logging`, `os`, `time`,
`pathlib`, `music_resolve`, `telemetry`). It is handed one function,
`main._shadow_decide` `:1472`, which is `decide_sentence(…, ask_model=False)`:
HA's players template read and `ReadOnlyMusic`. Neither reaches
`play_media`, a queue, a transport or a rating call; every MA command is
checked by `ma_read`. The taste DB is opened read-only
(`taste.db_readonly`, `mode=ro`) and the taste file is never created
(`TasteFile.load(create=False)`). Tests:
`test_shadow_and_decision_modules_cannot_reach_a_write` (imports and
names), `test_live_answer_is_the_pinned_one_with_shadow_on` (every pinned
row, shadow on, HA service calls refused, MA replayed: the live answer is
the pinned one, no service call, only recorded reads),
`test_ma_read_refuses_anything_but_a_read`.

It runs after the answer, one at a time (a semaphore per event loop), with
a timeout (`MUSIC_SHADOW_TIMEOUT_S`, 30 s); every failure goes into its own
record and never surfaces (`test_a_shadow_failure_never_surfaces`,
`test_a_slow_shadow_times_out_and_is_logged`,
`test_the_shadow_starts_after_the_live_answer`). It never asks the model;
where it would, the record says `"would_ask": true`.

## Found on dev, and fixed

1. **"Destiny Waving by Ulrich Schnauss" played a stranger's "DESTINY WAVE".**
   The decision scored the title alone; `/music` scores the model's query,
   which keeps "X by Y" whole, so the artist pulls a stranger under the line
   and stage 2 finds the right album. The decision now builds the same
   query text (`bb42288`).
2. **"play italian" played "Italian Lesson No. 3" on a prefix match.** A
   match of part of a name on an unmarked request is now a weak title: it
   goes to the model question; without an answer the named reading stands,
   as on `/music` (`decision.title_min_score`, `bb42288`). On dev the model
   says "style" and the path plays an Italian genre pick.
3. **The provider preference at 0.75 let a poor NAS match win** (a
   synthetic "Blue" by Massive Attack over "Blue Lines" on YouTube Music).
   It now has its own bar, `provider_min_score` 0.95 (`bb42288`).
4. **"Play toys in the attic by aerosmith" played the NAS title track**
   where `/music` plays the album (the dev shadow log's one real
   disagreement at that point). Untyped exact lookups skip tracks, and an
   untyped track winner yields to a strong album of the same name and artist
   (`f6c91ee`).
5. **The image COPY line** lacked the two new modules (`9b10179`).

## Criteria, one line each

1. Pure parser in `music_resolve.py` beside `radio_name()`/`normalize()`: `parse_sentence` `:597`, all fields; `tests/test_music_parse.py` (no network fixture), 99 tests.
2. Right-hand rooms against real names: `test_title_with_in_keeps_its_title_and_targets_the_origin`, `test_title_with_in_and_a_room_at_the_end`, `test_room_words_inside_a_title_stay_in_the_title` ("Hell's Kitchen", "Lost in the Kitchen Tapes"), `test_the_basement_player_with_no_area_is_reached_by_its_name`; corpus rows `plan-girl-with-the-sun` (its M10 `xfail.parser` now passes and was removed), `m11-girl-with-the-sun-in-the-office`, `m11-handcuffed-in-mississippi-radio`.
3. Non-music "play": `test_not_music` (10 sentences), `test_exclusion_lists_agree_with_routing` (lists read from `routing._MUSIC_RE.pattern`), `test_parser_and_routing_agree_on_what_is_music`; corpus `test_corpus_parser_says_not_music` on the 3 `non-music` rows.
4. `radio_name()` unchanged and still tested (`tests/test_music_resolve.py`, `test_transcript_gate.py::test_radio_name_still_stops_at_the_full_stop`, plus `test_music_parse.py::test_radio_name_still_stops_at_the_full_stop`); `test_the_parser_does_not_trim_a_messy_transcript`.
5. The order, one tested function per step: the table above; `tests/test_music_decide.py` (45 tests) has a group per step, rule 7 and rule 8; `test_an_outcome_that_plays_always_carries_a_uri_never_the_words` and `test_music_say.py::test_every_corpus_play_is_a_uri_never_the_words` (every corpus row on `/music/say`).
6. Drew's examples, offline and live: corpus rows `m11-some-deep-house-chill`, `m11-italian-music`, `m11-deep-house`, `m11-portishead` (added in M10, commit history), `m11-jazz-music-style`, `m11-the-song-jazz-music`, `m11-burning-down-the-house` (step 1 commit `d4c4fa6`, before the parser `f5d5ef9`); all pass layer one except `m11-burning-down-the-house` (xfail.parser: a tie between two library tracks, below); live on dev `/music/say` dry run: "play some deep house chill music" style, "play italian music" style (via the model, the genre list does not know "italian"), "play deep house" style, "play Portishead" artist, "play jazz music" style, "play the song Jazz Music" track by De-Phazz.
7. Provider policy as a preference over one search's candidates: `prefer_provider` `:355`; tests `test_provider_policy_prefers_the_nas_among_strong_candidates` (searches counted: one per query variant for the one type), `test_a_poor_nas_match_never_beats_a_good_youtube_match`, `test_a_provider_named_in_the_sentence_goes_first`, `test_radio_prefers_pandora`, `test_prefer_provider_unit`, `test_the_provider_order_is_read_from_config`.
8. Fast case: `_exact` `:205` before any search; `test_the_fast_case_is_an_exact_library_name_before_any_search` (0 searches), dev: owned stations 19 to 46 ms.
9. Policy in `config/music.yaml` through the M21 loader: sections `parser` and `decision`, strict schema, comments on every key (`test_every_yaml_key_has_a_comment`), defaults equal to the file, validation tests; `test_the_model_step_moves_earlier_by_configuration`.
10. Model question: direct to LiteLLM, reason and lock safety above; timeout and the defined no-answer result (`test_music_reads.py`: constrained schema, thinking switch, four failure shapes, timeout); measured on dev 233 to 346 ms.
11. `music.decided_by`, reading, candidates with scores: `music.decide` span and `music decision` log line, `test_the_decision_is_a_span_and_a_log_event`.
12. `POST /music/say`: `music_say` `:2310`, builds `MusicRequest`, plays through `_play_named`/`_play_genre`; `MUSIC_DRY_RUN` as `/music`; `dry_run` field only turns it on (`test_dry_run_field_forces_a_dry_run_when_the_setting_is_off`, `test_dry_run_field_false_cannot_turn_the_setting_off`); real play path tested with a stubbed verify (`test_off_dry_run_it_plays_the_decided_uri_through_the_existing_path`).
13. Not on the LAN, nothing live calls it: `test_nginx_proxies_nothing_but_solar_and_health_to_tool_service`, `test_tool_service_ports_are_loopback_only`, `test_nothing_in_the_orchestrator_or_ha_calls_music_say`; no orchestrator file changed in this item (`git diff --stat bbe54ee..HEAD -- orchestrator/` is empty).
14. Shadow on every live music request, playing impossible by construction, one record each: above; `test_live_answer_is_the_pinned_one_with_shadow_on` (98 rows), `test_the_record_carries_both_outcomes`, `test_a_live_failure_is_shadowed_too`, construction tests.
15. No observable change: answers identical in 48 of 48 on/off pairs on dev; latency (24 requests each way): spaced 1.5 s apart, on p50 32 / p95 43 ms, off 30 / 44 ms; back to back, on 37 / 60 ms, off 21 / 37 ms (the previous request's shadow still running on the event loop). Shadow starts after the answer; failures and timeouts are logged, never surfaced; it never asks the model (records `would_ask`).
16. A setting, off in code, on for dev, on in production only at promotion; log under `./data` (`./data/music_shadow.jsonl`, dev `./data/dev/music_shadow.jsonl`) and a `music.shadow` span; format in `docs/features/voice-music-control.md`; `scripts/music_shadow_report.py` (tests in `test_music_shadow.py`).
17. What shadow cannot see is stated in the feature doc and below (blueprint plays never reach Kronk; what does reach Kronk is mostly what has no request verb at the start); the corpus answer check is the main evidence (C5).
18. Layer one runs every music row through parser and decision offline: `tests/test_voice_corpus.py::test_corpus_parser` (100 play rows: 70 pass, 30 strict xfail with reasons), none dropped.
19. The replay targets the new endpoint on dev in dry run and compares what would play with the row and with today's path: `scripts/voice_replay.py --answers`; result committed (`docs/bench/voice_replay_2026-11-17_123112_m11-answers.json`); disagreement tables below.
20. Corpus agreement tables: below.
21. Latency table: below.
22. Live paths unchanged: `tests/test_music_live_pin.py` (98 rows plus two literal-fallback cases) written at step 2 from the pre-M11 code and holding at every later commit; one entry regenerated after an expectation correction, from the pre-M11 code in a worktree at `85883d4`, where the other 97 rows matched the committed pin. `/music/voice` and `play_music` are covered by the existing suites, unchanged; the blueprint and HA were not touched.
23. `./scripts/run_tests.sh`: 4235 passed, 3 skipped, 53 xfailed (baseline 3725 / 2 / 23; the new skip is the pin writer, the 30 new xfails are `xfail.parser`). New tests use the `no_network` fixture and temporary files.
24. Voice replay on dev against the M10 baseline: no row regressed; details below.
25. No sound (dry run on throughout, players unchanged in every replay), dev only (only `tool_service-dev` and `orchestrator-dev` rebuilt), no HA or MA change or restart, no MA settings touched, no MA image code, no MagicMirror command; MA reads only (search, library listings, name lookup) through an allowlist.
26. Docs: `docs/features/voice-music-control.md` section "One decision-maker in tool_service: built and shadowed", with the remaining cutover steps listed as not done; `voice-regression-corpus.md`, `config-directory.md`, `lead-in.md` updated.

## Criterion 20: the corpus

Two comparisons. **Against today's path with a perfect model**: the answer
check sends dev's `/music` stand-in model arguments built from each row's
expectation (`tests/music_snapshot.live_args`), so "today's path matches"
means the model path can get it right. **Against today's path with the real
model**: the shadow records written while the HA replay ran the Kronk-tier
rows through `kronk-dev` (gemma-4-e4b choosing the arguments).

Source: `docs/bench/voice_replay_2026-11-17_123112_m11-answers.json`. Compared with today's live path (dev `/music`, stand-in model arguments): 98 rows; agree 72, disagree 26.

**New path better (matches the row; today's path does not): 1**

| Row | Sentence | Today's path | New path | Why |
|---|---|---|---|---|
| `inc-1021-woodstock-from-pandora` | 'Play Woodstock Radio from Pandora.' | radio 'Sounds of Woodstock Radio' | radio 'WOODSTOCK Radio' | different item |

**New path worse (today's path matches the row; the new path does not): 22**

| Row | Sentence | Today's path | New path | Why |
|---|---|---|---|---|
| `plan-like-massive-attack-radio` | 'Like massive attack radio' | radio 'Massive Attack Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `inc-1004-well-play-orbital` | "We'll play Orbital Radio." | radio 'Orbital Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `inc-0930-search-deep-house-ytm` | 'Search for some deep house chill music on YouTube Music' | style 'deep house chill' | fails: That doesn't sound like music I can play. | only the live path plays |
| `inc-1017-hotel-costes-albums-atlas` | 'Play hotel costes albums from Atlas, on the kitchen' | album 'Hôtel Costes by Various Artists' | album 'Hôtel Costes: 1999–2007, The Anniversary Boxset by Aphrodelics' | different item |
| `inc-1019-ytm-link` | "It's this album on YouTube music https://music.youtube.com/p" | album 'Destiny Waiving by Ulrich Schnauss and Mark Peters' | fails: That doesn't sound like music I can play. | only the live path plays |
| `inc-1021-ninetys-no-verb` | "90s, 2000s, and today's hits radio" | radio '90s, 2000s, Today's Hits Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-put-on-some-mellow-pink-floyd-in` | 'put on some mellow pink floyd in the kitchen' | artist 'Pink Floyd' | playlist 'Mellow Pink Floyd' | different item |
| `eval-played-the-album-toys-in-the-attic` | 'Played the album Toys in the Attic.' | album 'Toys In The Attic by Aerosmith' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-oh-play-portishead-radio` | 'Oh, play Portishead Radio.' | radio 'Portishead Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-i-have-to-play-the-radio-station` | 'I have to play the radio station, German Soundscape Radio.' | radio 'German Soundscape Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-play-the-albo-daisies-of-the-galaxy` | 'Play the Albo Daisies of the Galaxy.' | album 'Daisies of the Galaxy by Eels' | fails: I couldn't find anything called the Albo Daisies of the Galaxy in Music Assistant.  | only the live path plays |
| `eval-i-shuffled-my-youtube-playlist-old-man` | 'I shuffled my YouTube playlist, Old Man Video Games.' | playlist 'Old man video games' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-j-envoie-my-youtube-playlist-video-game` | "J'envoie my YouTube playlist, Video Game Pretty Songs." | playlist 'video game pretty songs' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-clay-thief-recreperation-radio-on-pandora` | 'Clay Thief Recreperation Radio on Pandora.' | radio 'Thievery Corporation Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-search-youtube-music-for-some-deep-chill` | 'Search YouTube Music for some deep, chill house music and pl' | style 'deep chill house' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-play-lunchlady-land-by-adam-sandler-in-2` | 'play "lunchlady land" by adam sandler in the kitchen, use th' | track 'Lunchlady Land by Adam Sandler' | track 'Lunchlady Land by Adam Sandler' | different speaker |
| `eval-i-hope-you-play-anime-pop-radio` | 'I hope you play Anime Pop Radio from Pandora.' | radio 'Anime Pop Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-we-play-justin-johnson-radio-on-pandora` | 'We play Justin Johnson Radio on Pandora.' | radio 'Justin Johnson Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `inc-1107-ask-kronk-ask-home-french-dinner` | ' Ask Kronk to ask the home agent to play French Dinner Radio' | radio 'French Dinner Radio' | fails: That doesn't sound like music I can play. | only the live path plays |
| `m19-find-deep-house-and-play-it` | 'ask Kronk to find some deep house music and play it' | style 'deep house' | fails: That doesn't sound like music I can play. | only the live path plays |
| `m14-over-word-limit` | "Play Anime Pop Radio that's probably about it everything is " | radio 'Anime Pop Radio' | style 'anime pop radio that s probably about it everything is pretty wet i just needed to make some space okay so yeah' | live read named, new read genre |
| `m14-lookalike-mr-brightside` | 'Play Mr. Brightside' | track 'Mr. Brightside by The Killers' | artist 'Mr. Brightside' | different item |

**Neither or both match the row: 3**

| Row | Sentence | Today's path | New path | Why |
|---|---|---|---|---|
| `inc-1017-pay-the-album-hotel-costes-3` | 'Pay the album "hotel costes 3" from Atlas, on the kitchen' | album 'Hôtel Costes 3 - Etage 3 by Hôtel Costes' | fails: That doesn't sound like music I can play. | only the live path plays |
| `eval-look-for-some-good-deep-house-chill` | 'Look for some good deep house chill music on YouTube music a' | style 'deep house chill' | fails: That doesn't sound like music I can play. | only the live path plays |
| `m14-lookalike-mambo-no-5` | 'Play Mambo No. 5 by Lou Bega' | track 'Mambo No. 5 (a Little Bit of...) by Lou Bega' | album 'Mambo No.5 by Lou Bega' | different item |

Reading it: 16 of the 22 "worse" rows are sentences with no request verb
at the start (STT-mangled verbs, searches, two nested lead-ins, the link),
which the parser declines by design, as routing's pin does; at cutover they
stay on the model path, because HA's thin automation triggers on request
verbs. The other six, each with an `xfail.parser` reason: the Hôtel Costes
series "from Atlas" (the new path honours "from Atlas" and plays the NAS
boxset; the album named exactly "Hôtel Costes" is on YouTube Music only,
which `/music` plays: a policy question for Drew), "mellow pink floyd" (a
mood word; the model question sends it to a YouTube Music playlist called
"Mellow Pink Floyd", the stand-in passes the artist), "the Albo Daisies of
the Galaxy" (STT turned "album" into "Albo"; no strong match for the words
as heard), the lunchlady row with an instruction after the room (the
default speaker, not the kitchen), `m14-over-word-limit` (a run-on
transcript with no full stop, so `radio_name()` cannot cut the station; the
M14 gate sends it to the coordinator), and "Play Mr. Brightside" (an artist
of that name on YouTube Music outranks the track when untyped; the stand-in
passes `media_type: track`). Of the three in the last group, Mambo No. 5 is
the single "Mambo No.5" (an album in Music Assistant) against the stand-in's
track, and the other two are declined no-verb rows whose expectation the
stand-in also misses.

**Against the real model** (shadow records, HA replay window 12:23 to
12:31 UTC, 19 requests reached `/music` on dev):

| Sentence (as said) | Today's path, real model | New path | Why |
|---|---|---|---|
| 'Start an orbital radio station on YouTube Music.' | fails: "I couldn't find a station called orbital radio station" | radio 'Orbital Radio' | new better (the model passed "orbital radio station") |
| 'Shuffle the anime bangers playlist on Sonos Move Derp.' | fails: "I couldn't find anything called anime bangers playlist" | playlist 'anime bangers' | new better (the model kept "playlist" in the name) |
| 'Late evening from Radio on Pandora.' | album 'Late Evening by Sharmk' (YouTube Music) | declined (no request verb) | new better: today plays an unrelated album |
| "We'll play Orbital Radio." | radio 'Orbital Radio' | declined | stays on the model path |
| 'Search for some deep house chill music on YouTube Music' | style 'deep house chill' | declined | stays on the model path |
| 'Pay the album "hotel costes 3" from Atlas, on the kitchen' | album 'Hôtel Costes 3 - Etage 3' | declined | stays on the model path |
| 'Oh, play Portishead Radio.' | radio 'Portishead Radio' | declined | stays on the model path |
| 'I have to play the radio station, German Soundscape Radio.' | radio 'German Soundscape Radio' | declined | stays on the model path |
| 'Look for some good deep house chill music on YouTube music and start a radio based on that.' | style 'deep house chill' | declined | stays on the model path |
| 'I hope you play Anime Pop Radio from Pandora.' | radio 'Anime Pop Radio' | declined | stays on the model path |
| 'ask the home agent to play French Dinner Radio from Pandora.' | radio 'French Dinner Radio' | declined | stays on the model path |
| 'find some deep house music and play it' | style 'deep house' | declined | stays on the model path |

The other 7 agreed (2 same item, 2 same style, 3 both fail). Shadow over
the whole dev log since the last tool_service deploy (12:22:46 UTC, 215
records, replay, answer check and latency runs together):

```
data/dev/music_shadow.jsonl: 215 requests shadowed, 164 compared, 0 skipped, 0 shadow errors, 0 unreadable lines
agreement: 146/164 (89%) of the requests the parser read as music
declined (no request verb; these stay on the model path): 51, of which the live path played 49
decided_by: {'named': 127, 'genre_list': 18, 'not_found': 9, 'other_reading': 6, 'rule7': 4}
model question would have fired: 8
live ms p50/p95: 16/45; new ms p50/p95: 39/85
```

**Rows HA handles locally today**, which the new path would take over at
cutover, and what the new path does with each (answer check, dev, dry run;
"Row check" is the layer comparison against the row):

| Row | Sentence | HA today | New path (dry run) | Row check |
|---|---|---|---|---|
| `plan-anime-pop-radio-wet` | "Play Anime Pop Radio. That's probably about it. Everyth" | trigger catchall | would play the radio Anime Pop Radio on the Kitchen speaker. | pass |
| `inc-1108-anime-pop-transcript` | " Play Anime Pop Radio.  That's probably about it. Every" | trigger catchall | would play the radio Anime Pop Radio on the Kitchen speaker. | pass |
| `inc-1108-anime-pop-wet-basement` | "Play Anime Pop Radio. That's probably about it. Everyth" | trigger catchall | would play the radio Anime Pop Radio on the basement voice pe ma speaker. | pass |
| `inc-1108-mushroom-jazz-basement` | 'Play Mushroom Jazz radio on the basement voice pe ma' | trigger radio | would play the radio Mushroom Jazz Radio on the basement voice pe ma speaker. | pass |
| `inc-1108-artist-portishead-basement` | 'Play the artist Portishead on the basement voice pe ma' | trigger artist | would play the artist Portishead on the basement voice pe ma speaker. | pass |
| `inc-1108-timer-2min` | 'Set a timer for 2 minutes' | builtin HassStartTimer | That doesn't sound like music I can play. | pass |
| `inc-1004-play-orbital-radio` | 'Play orbital radio.' | trigger  | would play the radio Orbital Radio on the Kitchen speaker. | pass |
| `inc-1004-french-dinner-pandora` | 'Play French Dinner Radio on Pandora.' | trigger radio | would play the radio French Dinner Radio on the Kitchen speaker. | pass |
| `inc-1004-french-dinner` | 'Play French Dinner Radio.' | trigger  | would play the radio French Dinner Radio on the Kitchen speaker. | pass |
| `inc-1004-french-cafe-pandora` | 'Play French Cafe Radio on Pandora.' | trigger radio | would play the radio French Cafe Radio on the Kitchen speaker. | pass |
| `plan-girl-with-the-sun` | 'play The Girl with the Sun in Her Head by Orbital' | trigger  | would play the track The Girl With The Sun In Her Head by Orbital on the Office speaker. | pass |
| `plan-shuffle-anime-bangers` | 'shuffle my YouTube playlist anime bangers' | trigger playlist | would play the playlist anime bangers on the Office speaker, shuffled. | pass |
| `plan-put-on-some-jazz` | 'put on some jazz' | trigger  | would play Mushroom Jazz Radio, for jazz, on the Office speaker. | pass |
| `plan-ninetys-two-thousands` | "Play ninety's two thousands and today's hits radio on t" | trigger radio | would play the radio 90s, 2000s, Today's Hits Radio on the basement voice pe ma speaker. | pass |
| `inc-0915-daisies-album-eels-ytm` | 'Play the album Daisies of the Galaxy by Eels on YouTube' | trigger album | would play the album Daisies of the Galaxy by Eels on the Kitchen speaker. | pass |
| `inc-1019-destiny-waving` | 'Play the album Destiny Waving by Ulrich Schnauss' | trigger  | would play the album Destiny Waiving by Ulrich Schnauss and Mark Peters on the Office speaker. | pass |
| `inc-1019-destiny-waving-ytm-kitchen` | 'Play the album Destiny Waving by Ulrich Schnauss from Y' | trigger album | would play the album Destiny Waiving by Ulrich Schnauss and Mark Peters on the Kitchen speaker. | pass |
| `inc-1017-hotel-costes-albums-atlas` | 'Play hotel costes albums from Atlas, on the kitchen' | trigger  | would play the album Hôtel Costes: 1999–2007, The Anniversary Boxset by Aphrodelics on the Kitc | xfail |
| `inc-1021-ninetys-from-pandora` | "Play 90s, 2000s, Today's Hits, radio from Pandora." | trigger  | would play the radio 90s, 2000s, Today's Hits Radio on the Office speaker. | pass |
| `inc-1021-woodstock-from-pandora` | 'Play Woodstock Radio from Pandora.' | trigger  | would play the radio WOODSTOCK Radio on the Office speaker. | pass |
| `inc-1021-shuffle-old-man-sings-basement` | 'shuffle the playlist old man sings on the basement voic' | trigger playlist | would play the playlist old man sings on the basement voice pe ma speaker, shuffled. | pass |
| `inc-1025-ninetys-quoted-kitchen` | 'Play "90s, 2000\'s and today\'s hits" radio from Pandora,' | trigger radio | would play the radio 90s, 2000s, Today's Hits Radio on the Kitchen speaker. | pass |
| `m17-play-some-jazz-office` | 'play some jazz in the office' | trigger loose | would play Mushroom Jazz Radio, for jazz, on the Office speaker. | pass |
| `m11-some-deep-house-chill` | 'play some deep house chill music' | trigger  | would play Thievery Corporation, for deep house chill, on the Office speaker. | pass |
| `m11-italian-music` | 'play italian music' | trigger  | would play ITALIAN GREATEST HITS, for italian, on the Office speaker. | pass |
| `m11-deep-house` | 'play deep house' | trigger  | would play Groove Armada, for deep house, on the Office speaker. | pass |
| `m11-portishead` | 'play Portishead' | trigger  | would play the artist Portishead on the Office speaker. | pass |
| `m11-play-the-news` | 'play the news' | trigger  | That doesn't sound like music I can play. | pass |
| `eval-play-top-pink-floyd-songs-on-the` | 'Play top Pink Floyd songs on the Sonos Move Derp speake' | trigger  | would play the artist Pink Floyd on the Sonos Move Derp speaker. | pass |
| `eval-play-k-pop-radio-on-sonos-move` | 'Play K-pop radio on Sonos Move Dirt.' | trigger  | would play the radio K-Pop Radio on the Sonos Move Derp speaker. | pass |
| `eval-play-music-by-pink-floyd` | 'play music by pink floyd' | trigger  | would play the artist Pink Floyd on the Office speaker. | pass |
| `eval-play-pink-floyd-on-sonos-move` | 'play pink floyd on sonos move' | trigger  | would play the artist Pink Floyd on the Sonos Move Derp speaker. | pass |
| `eval-play-some-jazz-on-the-sonos-move` | 'play some jazz on the sonos move' | trigger  | would play Mushroom Jazz Radio, for jazz, on the Sonos Move Derp speaker. | pass |
| `eval-play-the-beatles-in-the-kitchen` | 'play the beatles in the kitchen' | trigger  | would play the artist The Beatles on the Kitchen speaker. | pass |
| `eval-put-on-some-mellow-pink-floyd-in` | 'put on some mellow pink floyd in the kitchen' | trigger  | would play the playlist Mellow Pink Floyd on the Kitchen speaker. | xfail |
| `eval-play-albuquerque-by-weird-al-yankovic` | 'Play Albuquerque by Weird Al Yankovic.' | trigger  | would play the track Albuquerque by Weird Al Yankovic on the Office speaker. | pass |
| `eval-play-top-songs-by-weird-al-yankovic` | 'Play Top Songs by Weird Al Yankovic on Sonos Move Dirt.' | trigger  | would play the artist Weird Al Yankovic on the Sonos Move Derp speaker. | pass |
| `eval-play-journey-on-the-sonos` | 'play journey on the sonos' | trigger  | would play the artist Journey on the Sonos Move Derp speaker. | pass |
| `eval-play-pink-floyd-on-the-blue-group` | 'play pink floyd on the blue group' | trigger  | I couldn't play that. I don't know a speaker or room called 'blue group'. Speakers: Cameron’s M | pass |
| `eval-play-pink-floyd-on-the-sonos-move` | 'play pink floyd on the sonos move' | trigger  | would play the artist Pink Floyd on the Sonos Move Derp speaker. | pass |
| `eval-play-don-t-stop-me-now-by` | 'play "Don\'t stop me now" by queen on the Satellite 1' | trigger  | would play the track Don't Stop Me Now by Queen on the satellite 1 ma speaker. | pass |
| `eval-play-the-artist-aerosmith-in-the-office` | 'Play the artist Aerosmith in the office' | trigger artist | would play the artist Aerosmith on the Office speaker. | pass |
| `eval-play-toys-in-the-attic-by-aerosmith` | 'Play toys in the attic by aerosmith' | trigger  | would play the album Toys In The Attic by Aerosmith on the Office speaker. | pass |
| `eval-shuffle-my-youtube-music-playlist-anime-bankers` | 'Shuffle my YouTube music playlist, Anime Bankers.' | trigger playlist | would play the playlist anime bangers on the Office speaker, shuffled. | pass |
| `eval-shuffle-my-youtube-playlist-old-man-video` | 'Shuffle my YouTube playlist, Old Man Video Games.' | trigger playlist | would play the playlist Old man video games on the Office speaker, shuffled. | pass |
| `eval-shuffle-my-youtube-playlist-anime-bangers` | 'Shuffle my YouTube playlist, anime bangers.' | trigger playlist | would play the playlist anime bangers on the Office speaker, shuffled. | pass |
| `eval-shuffle-the-youtube-music-playlist-anime-bangers` | 'Shuffle the YouTube music playlist, anime bangers.' | trigger playlist | would play the playlist anime bangers on the Office speaker, shuffled. | pass |
| `eval-play-some-aerosmith-please` | 'play some aerosmith please' | trigger  | would play the artist Aerosmith on the Office speaker. | pass |
| `eval-put-on-some-aerosmith` | 'put on some aerosmith' | trigger  | would play the artist Aerosmith on the Office speaker. | pass |
| `eval-put-on-the-radio-station-zorblax-fnordwave` | 'put on the radio station Zorblax Fnordwave Nonexistent' | trigger  | I couldn't find a station called Zorblax Fnordwave Nonexistent in Music Assistant. The closest  | pass |
| `eval-play-groove-armada-radio-from-pandora-in` | 'play "groove armada" radio from pandora in the kitchen' | trigger radio | would play the radio Groove Armada Radio on the Kitchen speaker. | pass |
| `eval-play-the-albo-daisies-of-the-galaxy` | 'Play the Albo Daisies of the Galaxy.' | trigger  | I couldn't find anything called the Albo Daisies of the Galaxy in Music Assistant. The closest  | xfail |
| `eval-shuffle-my-youtube-playlist-video-game-pretty` | 'Shuffle my YouTube playlist, video game pretty songs.' | trigger playlist | would play the playlist video game pretty songs on the Office speaker, shuffled. | pass |
| `eval-shuffle-the-youtube-playlist-old-man-video` | 'Shuffle the YouTube Playlist Old Man Video Games.' | trigger playlist | would play the playlist Old man video games on the Office speaker, shuffled. | pass |
| `eval-play-deep-chill-house-radio-from-pandora` | 'Play deep chill house radio from Pandora in the kitchen' | trigger radio | would play the radio Deep Chill House Radio on the Kitchen speaker. | pass |
| `eval-play-lunchlady-land-by-adam-sandler-in` | 'play "lunchlady land" by adam sandler in the kitchen' | trigger  | would play the track Lunchlady Land by Adam Sandler on the Kitchen speaker. | pass |
| `eval-play-lunchlady-land-by-adam-sandler-in-2` | 'play "lunchlady land" by adam sandler in the kitchen, u' | trigger  | would play the track Lunchlady Land by Adam Sandler on the Office speaker. | xfail |
| `eval-play-the-song-lunchlady-land-by-adam` | 'play the song  "lunchlady land" by adam sandler in the ' | trigger track | would play the track Lunchlady Land by Adam Sandler on the Kitchen speaker. | pass |
| `m14-over-word-limit` | "Play Anime Pop Radio that's probably about it everythin" | trigger catchall | would play Andy Grammer, for anime pop radio that s probably about it everything is pretty wet  | xfail |
| `m14-lookalike-mr-brightside` | 'Play Mr. Brightside' | trigger catchall | would play the artist Mr. Brightside on the Office speaker. | xfail |
| `m14-lookalike-st-vincent-radio` | 'Play St. Vincent radio' | trigger catchall | would play the radio St. Vincent Radio on the Office speaker. | pass |
| `m14-lookalike-rem` | 'Play R.E.M.' | trigger catchall | would play the artist R.E.M. on the Office speaker. | pass |
| `m14-lookalike-dr-dre` | 'play Dr. Dre' | trigger catchall | would play the artist Dr. Dre on the Office speaker. | pass |
| `m14-lookalike-u2` | 'Play U2.' | trigger catchall | would play the artist U2 on the Office speaker. | pass |
| `m14-lookalike-mambo-no-5` | 'Play Mambo No. 5 by Lou Bega' | trigger catchall | would play the album Mambo No.5 by Lou Bega on the Office speaker. | xfail |
| `m14-lookalike-blueprint-room-suffix` | 'Play Hotel Costas Radio. in the Kitchen' | trigger radio | would play the radio Hotel Costes Radio on the Kitchen speaker. | pass |
| `m14-lookalike-ellipsis` | 'Play... Anime Pop Radio' | trigger catchall | would play the radio Anime Pop Radio on the Office speaker. | pass |
| `m14-lookalike-panic-at-the-disco` | 'Play Panic! at the Disco' | trigger catchall | would play the artist Panic! At The Disco on the Office speaker. | pass |
| `m11-jazz-music-style` | 'play jazz music' | trigger  | would play Parov Stelar, for jazz, on the Office speaker. | pass |
| `m11-the-song-jazz-music` | 'play the song Jazz Music' | trigger  | would play the track Jazz Music by De-Phazz on the Office speaker. | pass |
| `m11-burning-down-the-house` | 'play Burning Down the House' | trigger  | would play the track Burning Down the House by [unknown] on the Office speaker. | xfail |
| `m11-girl-with-the-sun-in-the-office` | 'play The Girl with the Sun in Her Head by Orbital in th' | trigger  | would play the track The Girl With The Sun In Her Head by Orbital on the Office speaker. | pass |
| `m11-toys-in-the-attic-in-the-kitchen` | 'play Toys in the Attic by Aerosmith in the kitchen' | trigger  | would play the album Toys In The Attic by Aerosmith on the Kitchen speaker. | pass |
| `m11-handcuffed-in-mississippi-radio` | 'Play Handcuffed To A Fence In Mississippi Radio' | trigger  | would play the radio Handcuffed To A Fence In Mississippi Radio on the Office speaker. | pass |
| `m11-some-jazz-on-the-basement-player` | 'play some jazz on the basement voice pe ma' | trigger  | would play Parov Stelar, for jazz, on the basement voice pe ma speaker. | pass |
| `m11-play-a-game-with-me` | 'play a game with me' | trigger  | That doesn't sound like music I can play. | pass |
| `m11-play-the-movie-inception` | 'play the movie Inception in the living room' | trigger  | That doesn't sound like music I can play. | pass |

## Criterion 21: latency

New path: `POST /music/say` on dev, dry run, office satellite as origin.
Dry run stops before `play_media`, so these are the decision's cost (players
template read, parse, lookups, model question, genre plan); a real play adds
the same `play_media` and verify poll `/music` has. "Warm" repeats each
sentence 3 times (Music Assistant caches searches for 7 days, so most are
cache hits); "cold" is one run of sentences not asked before. Today's Kronk
path: the plan's baseline (8 days, voice, `music` rule through the `home`
agent). Blueprint direct path: Tempo, as in "Check first", end to end with
its verify wait.

| Media type | New path, warm p50 / p95 (n) | New path, cold (each) | Blueprint direct, played (Tempo, p50, n) | Today's Kronk path (plan) |
|---|---|---|---|---|
| Owned station | 21 / 46 ms (9) | 32, 22, 21 ms (exact library name, no search) | radio 1.54 s (2) | p50 6.6 s, p95 11.9 s, max 17.4 s (13), all types |
| Named album | 36 / 95 ms (9) | 19 ms (exact), 992, 931 ms (search) | album 5.87 s (2) | as above |
| Artist | 35 / 42 ms (9) | 31 ms (exact), 999, 1074 ms (search) | artist 0.32 s (2) | as above |
| Style | 52 / 360 ms (12) | 1264, 1204 ms (genre list and genre plan) | not played by the blueprint; catch-all hand-off to Kronk p50 8.97 s, p95 17.37 s (10) | as above |
| Model question | 316 / 342 ms (6) | 2502, 2661 ms (search, the question, genre plan) | n/a | as above |

The model question itself: 233 to 278 ms warm, 316 and 346 ms cold. Against
the blueprint's direct path the new path adds the decision (tens of
milliseconds on a library hit, about 1 s on a fresh search) and one local
HTTP hop; the blueprint's own numbers are dominated by Music Assistant
starting playback and its verify wait. Samples are small on both sides.

## Test and replay output

`./scripts/run_tests.sh` (final, at `ee29381`), last lines verbatim:

```
.xxx.xxx.x.xx..xx..xx....x.....x........................................ [ 98%]
...........................................                              [100%]
4235 passed, 3 skipped, 53 xfailed in 28.99s
```

`python3 scripts/voice_replay.py --label m11-build` (kronk-dev, dry run,
tool_service-dev at the final code), last lines verbatim:

```
voice_replay: {'pass': 134, 'xfail': 21, 'skip': 3, 'xpass': 2}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_122318_m11-build.json
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row: of the 114 baseline rows, two changed status, both known flappers:
`eval-shuffle-it` xfail to xpass (`xfail.live`), and
`inc-0930-search-deep-house-ytm` fail to pass (one of the three "YouTube
Music" search rows; it was the baseline's one failure). The 46 rows added
since the baseline: 42 pass, 2 xfail, 1 xpass
(`m19-what-happens-if-i-ask-kronk-to-pause`, `xfail.live`), 1 skip. No
media player changed state. Nothing was skipped for a player in use and the
replay did not stop.

`tests/.venv/bin/python scripts/voice_replay.py --answers --label m11-answers`,
verbatim:

```
voice_replay --answers: http://127.0.0.1:8103, dry-run on, 107 rows
voice_replay --answers: {'pass': 77, 'xfail': 30}; agree with live 72/98; players unchanged
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_123112_m11-answers.json
```

## Departures from the criteria, and why

- **The corpus hook's call shape.** M10 left `PARSE(text, origin_area)`; the
  layer now calls `PARSE(row, tmp_path)` because the target is checked
  against the origin device's speakers, not the area name, and the decision
  needs the snapshot environment. The comparisons fold names, accept one of
  several credited artists, read `provider: null` as "the sentence names
  none", and compare players by the speakers they resolve to. Documented in
  the test file and `voice-regression-corpus.md`.
- **One expectation corrected.** `m14-lookalike-blueprint-room-suffix`
  expected the name as misheard ("Hotel Costas Radio"); the corpus rule is
  that a mishearing expects what was meant, so it now expects "Hotel Costes
  Radio", which both paths play. Its live-pin entry (whose stand-in input
  comes from the expectation) was regenerated from the pre-M11 code.
- **Two M10 `xfail.parser` markers removed** (the rows now pass), 30 added
  with reasons. Nothing was dropped.
- **Three settings beyond the plan's list**, all in `decision`:
  `title_min_score`, `provider_min_score`, and the title-track rule (in
  code). Each came from a dev finding above; the plan's own words ("a weak
  title match never beats a style reading", "prefer the NAS only among
  candidates that pass the match threshold", the resolver's "album over its
  title track") are what they implement.
- **The parser wants a request verb at the start**, as routing's pin does
  (stopping rule: no new special case for mangled verbs). Sentences without
  one are declined and stay on the model path.
- **Shadow never asks the model** (the criteria allow it); it records
  `would_ask`.
- **"Today's path" in the answer check is a perfect model** (stand-in
  arguments from the expectation). The comparison with the real model is
  the shadow log from the HA replay.
- **Live-path code was moved, not changed**: the resolver body into
  `music_resolve.resolve_named`, the play tail into `_play_named`, `_norm`
  into `music_resolve.room_key`, the three MA reads through `ma_read`. The
  pin holds across each move.
- **Not done**: charting `music.decided_by` on the Music dashboard (the plan
  asks for it; the attribute is on every `music.decide` and `music.shadow`
  span, but no Grafana provisioning was changed). Production compose does
  not set `MUSIC_MODEL_URL`; the code default is the same URL, and shadow
  never asks.
- **The dev shadow log** (`data/dev/music_shadow.jsonl`, not committed)
  holds records from before the last fixes; the summaries above use
  `--since 2026-11-17T12:22`, the last tool_service-dev deploy.

## What the coordinator does at promotion

1. Deploy tool_service from the tag (production compose, `up -d --build
   tool_service`; it no longer starts without `music_decide.py` and
   `music_shadow.py` in the image, which the Dockerfile now copies).
2. Turn shadow on: uncomment `# - MUSIC_SHADOW=1` in the production
   `tool_service` environment in `docker-compose.yml`, then `docker compose
   up -d tool_service`. Check the log line `MUSIC_SHADOW is on: every /music
   request also runs the M11 decision and logs a comparison to
   /data/music_shadow.jsonl; nothing it decides is played`, `/health` (still
   `{"status":"ok","music_dry_run":false}`), and restart nginx as for any
   container recreate (nginx proxies `/solar/` and `/probe/tools` to
   tool_service). MUSIC_DRY_RUN stays off in production; shadow does not
   need it.
3. The log is `./data/music_shadow.jsonl` on the host, one JSON line per
   music request (about 1.5 KB each; dev wrote 71 KB for 48). Read it with
   `python3 scripts/music_shadow_report.py data/music_shadow.jsonl --since
   <promotion time>`.
4. Watch: `shadow errors` (expect 0), new-path ms p95 (dev: 85 ms; it runs
   after the answer but shares the event loop), live `/music` latency in the
   Music dashboard (unchanged), the agreement rate over music-parsed
   requests and each disagreement, the declined count (sentences that stay
   on the model path), `decided_by` counts, and how often the model question
   would have fired. Each shadow decision makes 0 to about 8 Music
   Assistant reads (exact library lookups and searches, mostly cache hits).
5. Rollback: comment the line again and `up -d tool_service`.
6. After about a week, review with Drew: the shadow report and, as the main
   evidence (C5), `--answers` on the corpus; the open policy point is the
   Hôtel Costes case ("from Atlas" plays a NAS album that is not the one
   named exactly). Cutover stays with Drew.
