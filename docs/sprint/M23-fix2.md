# M23 fix round 2: the stripped-request guard ships off

Branch `refactor`. Criteria: `docs/sprint/M23-criteria.md`. Reports before
this one: `M23-build.md`, `M23-e2e.md` (FAILS), `M23-fix1.md`, `M23-e2e2.md`
(FAILS). Fix round 2, 2026-11-21, by the fix agent, on the coordinator's
rulings A to F.

| Commit | What |
|---|---|
| `b68faf4` | code, tests, docs: rulings A to F |
| this one | the report |

## Summary

- **A.** Fix 2 ships off. Setting `stripped_request.refuse` in
  `music.yaml`: false in the shipped defaults, false when the key is
  absent. Read at `tool_service/main.py:1637`, before the classifier.
  Off, the sentence is not read. All 659 test sentences pick exactly as
  with no sentence:
  - the 342 of the first fixture;
  - the two captures, plus capture 1 without its suffix;
  - the second tester's 314, now in their own fixture.
  Two repairs for when it is on: a listed word with an apostrophe suffix
  counts, and the room suffix is dropped first.
- **B.** The corpus row's `xfail.live` says what is true now. Still a
  strict xfail.
- **C.** The orchestrator's one-sentence refusal is unchanged.
- **D.** A start counts from 5 s after the pick (`PICK_START_MARGIN_S`).
  On the real data no row falls inside the margin, so no recompute.
- **E.** The five readings each have a test that fails without them.
  Each list word, pronoun, filler word and "some" word has its own test.
- **F.** Docs updated. ROADMAP untouched.
- Tests: before `6545 passed, 3 skipped, 54 xfailed`; after
  `7311 passed, 3 skipped, 54 xfailed, 6 warnings in 99.73s (0:01:39)`.
- Nothing installed, reloaded or deployed. Live config directories not
  edited. Only the dev pair was rebuilt, once. Production containers,
  units and timers are unchanged, and so is the real taste data.

## A. Fix 2 ships off

**The setting.** `stripped_request.refuse` in `music.yaml`, a YAML
boolean.

- Schema: `tool_service/config_loader.py:245` (`StrippedRequest`, default
  `False`), added to `Music` at `:258`. The model is strict and forbids
  unknown keys, so `yes`, `1` and `"true"` are refused with their line,
  as for every other key.
- Shipped default: `config/music.yaml:143`, `refuse: false`, with its
  comment.
- Absent means off: the schema's default is false.
- Read at `tool_service/main.py:1637`, at use (a reload applies to the
  next request):

  ```python
  if config_loader.current().music.stripped_request.refuse and not taste.genre_phrase(nq.text):
      generic, what = taste.said_request(req.said, players)
  ```

  Off, `taste.said_request` is not called and the sentence is not read
  for this. `/music` takes the path it took before M23 for every
  no-phrase request.

**Live directories without the key.** Neither live `music.yaml` has the
key (`grep -c stripped_request`: 0 and 0). `deploy_tag.sh plan`'s check
(`scripts/config_check.py --code <tree> --dir <live dir>`), run read-only
with the working tree's loaders:

```
$ tests/.venv/bin/python scripts/config_check.py --code "$PWD" --dir ../kronk-config/prod
valid
prod exit 0
$ tests/.venv/bin/python scripts/config_check.py --code "$PWD" --dir ../kronk-config/dev
valid
dev exit 0
```

`git -C ../kronk-config/prod status` was clean before and after.

**On `/settings`.** The page shows the comment above a key in the live
file. Production's live file has no comment for a key it does not carry,
so:

- `describe_keys` (`tool_service/config_files.py:558` and its
  byte-identical copy in `orchestrator/`) now returns the schema's
  `Field(description=...)`;
- `orchestrator/settings_view.py:257` shows it when the file has no
  comment for the key;
- the description is `config_loader.STRIPPED_REFUSE_DESCRIPTION`
  (`:239`): "Off: the guard refused ordinary requests for music in both
  end-to-end tests (docs/sprint/M23-e2e.md, M23-e2e2.md); true refuses a
  bare 'music' query whose sentence names something else."
- The shipped file's comment is the same text, pinned equal by a test.

On dev after the rebuild (dev's live file lacks the key), `GET
/api/settings` from inside `orchestrator-dev`:

```
{"key": "stripped_request.refuse", "value": false, "source": "code default", "default": false, "type": "bool",
 "comment": "Off: the guard refused ordinary requests for music in both end-to-end tests (docs/sprint/M23-e2e.md, M23-e2e2.md); true refuses a bare 'music' query whose sentence names something else."}
music keys: 29 with comment: 29
```

**Proof that off is pre-M23.** Before M23 `/music` did not read the
sentence for a no-phrase request. So "as before M23" is: the request with
any `said` gets what the same request gets with no `said`. That is the
comparison fix round 1 used: status, body, play data. Tests
(`tests/test_music_stripped.py`), setting off (its default):

| Test | Cases | Result |
|---|---|---|
| `test_with_the_guard_off_every_sentence_picks_as_with_none`: `music` + genre | 659 | all equal to the no-sentence pick |
| `test_with_the_guard_off_the_captures_pick_in_every_shape`: 3 capture forms × 4 shapes | 12 | all equal; a pick recorded each time |
| `test_with_the_guard_off_the_sentence_is_not_read` | 1 | `said_request` replaced by one that raises; 3 requests, 0 calls |

The 659:

| Source | Count |
|---|---|
| `tests/fixtures/music_generic_sentences.txt` | 342 (the muse sick one included) |
| the captures | 2, plus capture 1 without " in the Kitchen" |
| `tests/fixtures/music_generic_sentences_e2e2.txt` (new) | 314 |

The 314 file holds the second tester's appendix sentences in its order,
taken from `docs/sprint/M23-e2e2.md` by a script. No overlap with the
first fixture. Nothing was asked of that agent. The four shapes are the
tester's: `music`; `music` + genre; `some music` + genre; `music` + genre
+ player.

Also:

- `test_with_the_guard_on_the_sentence_is_read`: on, one call, and 422;
- `test_the_guard_is_off_in_the_shipped_defaults_and_in_the_code`;
- `test_a_music_yaml_without_the_key_is_valid_and_off`: the shipped file
  cut before the section loads and validates (`settings_info.validate`,
  the save path's check), means off; `refuse: true` means on; `yes`, `1`,
  `"true"` are refused;
- `test_settings_shows_the_key_with_its_one_line_description` and
  `test_the_settings_page_falls_back_to_the_schema_description`.

Live, dev, dry run, guard off by absence (requests verbatim; each answer
shortened to its first sentence, `...` for "Nothing was sent to the
speaker.", and its `would_play`):

```
> POST /music {"query": "music", "kind": "genre", "player": "Kitchen"}
  200 Dry run: would play Cracker on the Kitchen speaker. ... would_play {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}
> POST /music {"query": "music", "player": "Kitchen", "said": "Play right here, right now, bud.  That boy's slow. in the Kitchen"}
  200 Dry run: would play Cracker on the Kitchen speaker. ... would_play {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}
> POST /music {"query": "music", "player": "Kitchen", "said": "Play back in the Kitchen"}
  200 Dry run: would play Orbital on the Kitchen speaker. ... would_play {"media_id": "library://artist/4", "media_type": "artist", "enqueue": "replace"}
> POST /music {"query": "good music", "kind": "genre", "player": "Kitchen", "said": "play whatever's good in the Kitchen"}
  200 Dry run: would play Cracker on the Kitchen speaker. ... would_play {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}
> POST /music {"query": "music", "kind": "genre", "player": "Kitchen", "said": "Play some music, thank you."}
  200 Dry run: would play Cracker on the Kitchen speaker. ... would_play {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}
dev log, the same 3 minutes: 0 lines with "refused"
```

Each is a dry-run pick from the top three of the profile, chosen at
random and weighted (Orbital once), with `pick.phrase` "".

Before M23 every one of these picked. With the guard on, the second and
third would be refused. Dev reads production's taste DB, which still has
the pre-M23 profile (Cracker on top): the fix-3 rebuild is the
coordinator's step below.

**Tests of the guard run with it on.** The `guard_on` fixture
(`patch.object(config_loader.current().music.stripped_request, "refuse",
True)`). Every round-1 `/music` guard test uses it: the refusals, the 341
generic and 9 unknown sentences picking as before, the exception, query
with terms, "not that". The classifier's own tests call
`taste.said_request` directly and need no setting.

**Repair 1: a list word with an apostrophe suffix counts**
(`taste._said_music_word`, `tool_service/taste.py:813`).

- The word before the apostrophe is looked up too: "whatever's",
  "whatever’s" (curly, folded by `_said_words`), "something's",
  "anything'll".
- Only for `SAID_MUSIC_WORDS`. The pronoun rule still reads the whole
  token: "play it's my life", "Play it’s a beautiful day", "play that's
  life" stay refused.
- Tests: `test_a_list_word_with_an_apostrophe_suffix_counts` (6),
  `test_a_pronoun_with_an_apostrophe_suffix_still_does_not_count` (3),
  `test_whatevers_good_picks_with_the_guard_on` (the blocker, with the
  model's arguments `good music`).
- Cost, as the tester said: a stripped "play Something's Gotta Give" now
  picks, as before M23.

**Repair 2: the room suffix is dropped before the bare-verb and filler
rules** (`taste._said_without_room`, `:821`; used in `_said_generic`,
`:832`).

- The segment's right-hand room clause is cut as the M11 parser cuts it:
  `music_resolve._room_clause` with `/music`'s player list (now passed:
  `said_request(said, players)`, `:853`) and `parser.room_min_score`.
- The music-word rule still reads the whole segment.
- Generic with the Kitchen player: "Play in the Kitchen", "Play on the
  Kitchen speaker", "Play some in the Kitchen", "play some in the
  kitchen, please", "play now in the kitchen".
- Still refused: "Play back in the Kitchen", capture 1, "Play back on
  the Kitchen speaker", "play in the air tonight" (not a room).
- With no players known, "in the Kitchen" is words; "on the Kitchen
  speaker" is still a speaker, as in the parser.
- Tests: `test_a_bare_verb_with_a_room_is_generic` (7),
  `test_dropping_the_room_keeps_the_captures_refused` (4),
  `test_a_room_that_is_not_a_player_is_not_dropped`,
  `test_the_guard_drops_the_room_on_music`.

No new list words.

**The two fixtures with the guard on**, classifier with the Kitchen
player:

| Fixture | Generic | Unknown | Refused |
|---|---|---|---|
| first, 342 | 305 | 36 | 1 ("Play some muse sick.") |
| second tester's, 314 | 205 | 5 | 104 (107 at `248ac98`) |

The three no longer refused: "play whatever's good", "play some in the
kitchen", "play in the kitchen". The 104 hold no listed word ("play what
I like", "play some beats", "play some hip hop"). That tail is why the
guard is off.

## B. The corpus row

`tests/voice_corpus.jsonl`, `inc-1121-fatboy-slim-heard-as-two-sentences`:

- `xfail.live`: "The coordinator delegated 'play music in the Kitchen' and
  /music played the top of the taste profile (artist Cracker). It still
  does: the M23 stripped-request guard (fix 2) is built but off by
  default (music.yaml stripped_request.refuse, false when absent), so the
  live answer is a pick from the profile. Since M23 that profile is the
  household's taste (fix 3 leaves Kronk's own picks out) and the pick
  replaces the queue (fix 1). Remove when the parser or cutover plays the
  track."
- `note`: its last sentence now says fix 2 ships off and the query is
  picked.
- `xfail.parser` unchanged. Both layers stay strict xfails
  (`tests/test_voice_corpus.py:152`, `:174`, `strict=True`). The suite's
  54 xfailed is unchanged.

## C. The orchestrator's wording

Unchanged: `orchestrator/agents.py` `STRIPPED_REFUSAL` and `_terminal_speech`
are not touched. They act only on tool_service's 422, which only comes
with the guard on. Their tests (`test_the_orchestrator_knows_the_refusal_sentence`,
`test_the_refusal_is_said_once_through_the_orchestrator`,
`test_the_refusal_reaches_the_user_once_on_every_mount`) pass unchanged.

## D. The start margin

**The change.** `PICK_START_MARGIN_S = 5` (`tool_service/taste.py:319`).
`_window_end` (`:643`) reads starts from `int(pick.ts) + 5`, and the
bisection's lower bound is that second. The own-item check (60 s) and the
cap (24 h) are unchanged.

**Why 5 s.** From MA's source (`2.11.0b5-kronk.9`, read in the container):

- MA writes a track's `media_progress` row only when its play ends or is
  cut short. `mark_item_played` writes the playlog only when
  `is_playing` is false, at `utc_timestamp()`
  (`controllers/music/controller.py:1745`, `:1827`).
- The tracker reports the previous item when the queue's current item
  changes (`controllers/player_queues/playback_tracker.py:662`). So the
  track the pick replaced is stamped when MA sees the pick's first item
  play.
- tool_service's verify loop sees the same change through HA, polling
  each second (`main.py:1936`). The pick is recorded after that, and for
  an artist pick after a `shuffle_set` and one more second (`:2085`).
- So the replaced track's stamp falls about at the record: normally
  before it, but the order of MA's tracker and HA's state is not fixed,
  and the record's second is truncated (`int(ts)`). That is the race of
  finding 4.
- MA records no item played under 5 s (`playback_tracker.py:716`). A
  track the household starts after the pick cannot be stamped sooner
  than 5 s after its start, which is after the pick.
- A station or playlist is stamped when it starts (`queue_loader.py:964`).
  That is the one start a margin can miss.

**On the real data.** A scratch copy of MA's `library.db` and WAL, and a
read-only backup of the taste DB, the ten picks:

| Table | User-initiated rows in `[int(ts), int(ts)+5)` | Nearest after a pick |
|---|---|---|
| `media_progress` (what the build reads) | 0 | 61.6 s (pick 14) |
| `play_history` (append-only) | 0 | 5.6 s (pick 16), 6.6 s (pick 13) |

The nearest is 5.6 s after pick 16 was recorded, not 6.2 s as M23-e2e2.md
has it. It is a 2026-11-08 bench station start. From the truncated second
it is 6.1 s, so the 5 s margin still counts it. A margin of 7 would not:
the mutant below fails.

5 s covers the truncation and the ordering race. It sits on MA's own 5 s
floor for tracks. It is under the nearest real start.

**Not recomputed.** No user-initiated row falls inside the margin for any
pick. So every window ends where it did, the same 14 rows are left out,
and the profile is the one in `M23-fix1.md` section H: Pink Floyd 4.30,
Fatboy Slim 2.55, Cracker 1.90.

**Tests** (`tests/test_music_taste_picks.py`):

- `test_a_track_cut_short_by_the_pick_itself_does_not_end_its_window`: a
  household track (user-initiated, not played through) stamped at T+0,
  T+1 and T+4. The whole pick stays out: 6 rows, Cracker 0. On
  `248ac98`'s rule all three fail: the window ends at the stamp and the
  five Cracker tracks count.
- The fake MA now keeps whether a row's last play was played through and
  honours `fully_played_only` (MA's default true). Existing tests are
  unchanged by it: every play they make is a full one.

**Feature doc corrected** (`docs/features/music-taste.md`, "Kronk's own
picks"). In MA's records "started" means stamped. A track is stamped when
its play ends or is cut short; a station or playlist when it starts. The
window ends when the first item the household started after the pick
finishes its first play or is cut short (a station or playlist: starts),
counted from 5 s after the pick.

## E. Tests for the readings and the words

**The readings of `M23-fix1.md` section D**, each with a test that fails
without it. Mutants run on a scratch copy of the tree, each against its
test file:

| Reading (mutant) | Killed by |
|---|---|
| margin 0: starts from the pick's own second (`248ac98`'s rule) | `test_a_track_cut_short_by_the_pick_itself_does_not_end_its_window[0]` |
| margin 7: past the nearest real start | `test_the_window_ends_at_the_first_start_to_the_second[6-6]` |
| artist rows count as starts | `test_an_artist_row_moved_by_the_picks_tracks_is_not_a_start` |
| the pick's own station or playlist never a start | `test_the_picked_station_started_again_is_a_start` |
| the pick's own item always a start | `test_the_picked_station_marked_at_its_own_start_is_not_a_start` |
| starts read on every queue | `test_a_start_on_another_queue_does_not_end_the_window` |
| starts need a full play | `test_a_start_cut_short_still_ends_the_window` |
| bisection stops a minute early | `test_the_window_ends_at_the_first_start_to_the_second[6-6]` |
| bisection returns one second late | `test_an_album_of_the_picked_artist_asked_for_after_the_pick_counts_with_its_tracks` |

In `M23-e2e2.md` finding 5, five of these survived:

- artist rows as starts;
- own item never a start;
- starts need a full play;
- starts on every queue;
- the bisection a minute early.

Each now has its own test. The other mutants of this round, all killed:

| Mutant | Killed by |
|---|---|
| the guard ignores the switch (always on) | `test_with_the_guard_off_the_sentence_is_not_read` |
| the sentence read with the switch off, result dropped | `test_with_the_guard_off_the_sentence_is_not_read` |
| the room not dropped | `test_a_bare_verb_with_a_room_is_generic` |
| `/music` passes no players | `test_the_guard_drops_the_room_on_music` |
| apostrophe suffix not read | `test_a_list_word_with_an_apostrophe_suffix_counts` |
| apostrophe suffix read for pronouns too | `test_sentences_that_name_something_are_not` ("play it's my life") |

New tests:

- `test_an_artist_row_moved_by_the_picks_tracks_is_not_a_start`: a
  Cracker track credits Orbital, whose artist row is user-initiated
  (sticky from the household's Orbital); 6 rows out, not 0.
- `test_the_picked_station_started_again_is_a_start`: the station Kronk
  picked, asked for again 2 h later on the same speaker; it counts (0
  left out).
- `test_the_picked_station_marked_at_its_own_start_is_not_a_start`: the
  row at T+30 is the pick's (1 left out).
- `test_a_start_on_another_queue_does_not_end_the_window`: the
  household's track in the basement at T+600 s; the kitchen pick's 21
  rows stay out.
- `test_a_start_cut_short_still_ends_the_window`: a request skipped
  after 20 s ends the window; the album after it counts.
- `test_the_window_ends_at_the_first_start_to_the_second`: `_window_end`
  directly, a pick with a fractional time. A start at T+d ends it at
  exactly T+d for d = 5, 6, 61, 3,601, 43,199, 86,399. At d = 0 and 4
  (inside the margin) and 86,400 it is the cap.

**The guard's lists** (run with `taste.said_request` directly; the guard
reads them only when on). The test file carries the lists as ruled
(`MUSIC_WORDS` 31, `PRONOUNS` 4, `FILLER` 10, `SOME` 2), and
`test_the_word_lists_are_the_ones_tested` pins them equal to taste.py's.
So a word added without a test fails too. One generated test per word:

| Test | Per word | Generic only because of the word |
|---|---|---|
| `test_each_word_for_music_counts[w]` (31) | "play back w", "play back w's", "play back w’s" | "play back zzz" is refused |
| `test_each_pronoun_counts_right_after_the_verb_only[w]` (4) | "play w back" generic; "play back, w is slow" and "play w's back" refused | "play zzz back" is refused |
| `test_each_filler_word_counts_after_a_bare_verb_and_after_some[w]` (10) | "play w", "play some w" | "play zzz", "play some zzz" refused |
| `test_each_some_word_counts_with_nothing_after_it[w]` (2) | "play w" generic, "play w zzz" refused | "play zzz" refused |

`test_the_controls_are_refused` pins the controls.

Mutants removing each word in turn (47: 31 + 4 + 10 + 2), two passes:

- **Whole file, `-x`.** 47 killed. The first failing test was the
  word's own case or, for 24 words, the equality test, which runs first.
- **Only the per-word tests (`-k each_`).** 47 killed, each by that
  word's own generated test.

The first test's finding ("thanks" removed fails no test) and the second
test's 11 surviving words, 9 filler words, "that" and "sum" are all
closed.

The verbs ("spin", "shuffle") have no per-word test. They are not in the
ruling's list; not added.

## F. Docs

- `docs/features/music-taste.md`, "Genre requests" step 1:
  - fix 2 is built and off, and why (both reports named; the long tail;
    a wrong refusal costs more than a wrong pick after fixes 1 and 3);
  - the setting, false when absent, and how to turn it on (`/settings`,
    dev first);
  - the two repairs, and the second tester's 104 still refused with it
    on.
- `docs/features/music-taste.md`, "Kronk's own picks": what "started"
  means in MA's records, and the margin.
- `docs/features/voice-music-control.md`: "A stripped request still picks
  (sprint M23 fix 2: built, off)", after the hand-off paragraph.
- `docs/features/config-directory.md`: the new key in the file table; the
  page shows the schema's description for a key the file lacks.
- `docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md`: dated lines
  for the second end-to-end test and fix round 2.
- `ROADMAP.md`: not touched (the coordinator's).
- Code comments: `taste.py` (the margin, the repairs, `said_request`),
  `main.py` (the guard), `config_loader.py` (the model).

## Coordinator steps (in full; they replace the earlier reports' steps)

Three independent changes. Each works with the others' old versions:

- the blueprint (fix 1, change 15);
- tool_service (fixes 1 to 3, both fix rounds);
- the profile rebuild (fix 3's data).

Fix 2 needs no step: it ships off, and production's live `music.yaml`
does not carry the key.

### 1. Blueprint change 15

- Installed: `version: 20250404-kronk14` (`:313`), sha256
  `13aa0728d8c2be70…`, equal to `ff8a7a5`'s file.
- Repo: `version: 20250404-kronk15` (`:318`), `enqueue: replace` at
  `:342`, sha256 `8ded30f9745b67a0…`. Unchanged since `1d6dadc`; this
  round does not touch it.
- No HA restart: an automation reload picks it up, as with changes 8 to
  14. Pick a moment when no puck is mid-request and nothing is playing.
  The reload re-creates every automation, and the music automation is
  `mode: single`.

```bash
cd ~/git-repos/drawsmcgraw/kronk
B=/config/blueprints/automation/kronk/mass_assist_kronk.yaml
# 0. back up what is installed (expect version 20250404-kronk14)
docker cp homeassistant:$B /tmp/mass_assist_kronk.kronk14.yaml
grep -n 'version: 20250404-kronk' /tmp/mass_assist_kronk.kronk14.yaml
# 1. install (-a keeps owner 1000:1000)
docker cp -a ha/blueprints/mass_assist_kronk.yaml homeassistant:$B
# 2. reload automations (no restart)
HA_TOKEN=$(grep '^HA_TOKEN=' .env | cut -d= -f2-)
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services/automation/reload
# 3. read back
docker exec homeassistant grep -n 'version: 20250404-kronk' $B          # expect :318 ... kronk15
docker exec homeassistant grep -n 'enqueue: replace' $B                  # expect :342
docker exec homeassistant cat $B | sha256sum; sha256sum < ha/blueprints/mass_assist_kronk.yaml   # equal
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://localhost:8123/api/states/automation.kronk_music_assistant_voice_device_first | jq -r .state   # expect "on"
docker logs --since 5m homeassistant 2>&1 | grep -i 'blueprint\|kronk_mass_voice' || echo "no blueprint errors"
```

The next blueprint trace shows `version: 20250404-kronk15`, and its
`play_media` step's data carries `enqueue: replace`.

**Undo:**

```bash
docker cp -a /tmp/mass_assist_kronk.kronk14.yaml homeassistant:$B
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services/automation/reload
```

The same file is `git show ff8a7a5:ha/blueprints/mass_assist_kronk.yaml`.

### 2. tool_service and the orchestrator

- They reach production only through the next tagged deploy, from a tag
  at or after `b68faf4`.
- No tag between `e864798` and `b68faf4`: those ship the guard on, and
  the refusals of both end-to-end tests.
- Do not deploy `5eb728b` alone: its migration breaks dev's read of the
  taste DB.

```bash
scripts/deploy_tag.sh plan <tag>     # the live-config check must say valid (production's music.yaml has no stripped_request key: off)
scripts/deploy_tag.sh deploy <tag>
```

After the deploy, verify through the shims as usual. For this change:

```bash
curl -s http://127.0.0.1:8003/config/status | jq '.source, .problems'      # files, []
```

`/settings` shows `stripped_request.refuse`: false, "code default", with
the description. Do not set it to true in production. It refuses
ordinary requests (`M23-e2e.md`, `M23-e2e2.md`).

The orchestrator image changes only in `settings_view.py` and
`config_files.py`, the `/settings` description fallback.

### 3. Apply fix 3 to the real profile (after the deploy)

The deploy alone changes nothing in the DB until the next build, at 03:30.
To apply the rule now:

```bash
cd ~/git-repos/drawsmcgraw/kronk
# 0. a consistent copy of the taste DB (read-only open), kept for the undo
python3 -c "import sqlite3; s=sqlite3.connect('file:data/music_taste.db?mode=ro', uri=True); d=sqlite3.connect('data/backups/music_taste.pre-m23.db'); s.backup(d); d.close()"
curl -s 'http://127.0.0.1:8003/music/taste/profile?limit=10' | jq -c '[.artists[] | [.name, .score]]'   # before
# 1. rebuild with the new rule
curl -s -X POST http://127.0.0.1:8003/music/taste/rebuild | jq .
#    expect kronk_pick_items > 0 (14 on 2026-11-21; it moves as the history does) and history_items = 500 - that
# 2. read back
curl -s 'http://127.0.0.1:8003/music/taste/profile?limit=10' | jq -c '[.artists[] | [.name, .score]]'
#    expect Cracker no longer first (1.90, third, in the scratch recompute)
```

What the rebuild writes:

- the `artists` and `stations` tables (rewritten);
- `tags` rows for artists it tags (one local model call per 25 untagged
  artists);
- `meta.last_build`.

The first open after the deploy adds `picks.queue`. Feedback and picks are
kept. MA gets four read commands: `music/artists/library_items`,
`music/item_by_uri`, `music/recently_played_items`,
`music/radios/library_items`. That is about 2,300 reads on 2026-11-21, a
few seconds, plus the tagging model for untagged artists (~20 s with tags
cached). The margin adds no read.

**Undo:**

- **(a) the rule:** deploy the previous tag (`sprint-M22`), then
  `curl -s -X POST http://127.0.0.1:8003/music/taste/rebuild`. The `queue`
  column stays; the old code ignores it. The old tag has no
  `stripped_request` key in its schema. Production's live file does not
  carry it either, so the old tag's plan still validates.
- **(b) the data:** copy `data/backups/music_taste.pre-m23.db` back over
  `data/music_taste.db`, with tool_service stopped or between writes.
  This loses any pick or feedback recorded after the copy, so prefer (a).

### Turning the guard on later (not part of this item)

On `/dev/settings` first: set `stripped_request.refuse` to true and save
(the save validates, runs the corpus check, reloads, commits). Or edit
`../kronk-config/dev/music.yaml`, commit it there, and run
`scripts/config_reload.sh dev`. Then production the same way.

Undo: set it back to false, or Revert that commit on the page.

What it refuses is in `music-taste.md`, "Genre requests": 104 of the
second tester's 314, for a start.

## Tests

`./scripts/run_tests.sh`:

- before: `6545 passed, 3 skipped, 54 xfailed`;
- after: `7311 passed, 3 skipped, 54 xfailed, 6 warnings in 99.73s (0:01:39)`.

766 more tests:

- 749 in `tests/test_music_stripped.py` (755 → 1,504):
  - 659 off-equals-no-sentence and 12 capture shapes;
  - 47 per-word tests and their pins;
  - 31 for the repairs, the setting, the page.
- 17 in `tests/test_music_taste_picks.py` (25 → 42): the margin (3 + 9
  boundary cases) and the five readings.

On `248ac98`'s code (scratch copy; the margin's name added to `taste.py`
so the file collects, its logic not):

- `test_music_stripped.py`: `725 failed, 417 passed, 362 errors`. The
  errors are the `guard_on` fixture: there is no switch to patch.
- `test_music_taste_picks.py`: `5 failed, 37 passed`: the margin cases (3
  cut-short, d = 0 and 4). The ruling-E tests pass there too: they pin
  readings `248ac98` already had, and fail only on the mutants above.

They open no sockets (`no_network`; MA and HA are fakes).

## State

Before any request to dev:

- `MUSIC_DRY_RUN=1` in `kronk-tool_service-dev-1`, before and after the
  rebuild;
- the new container's startup line `WARNING:tool_service:MUSIC_DRY_RUN is
  on: music requests resolve but nothing is sent to a player or MA`;
- `/app/taste.py`, `main.py`, `config_loader.py`, `config_files.py`, and
  orchestrator-dev's `settings_view.py`, `config_files.py`: sha256 equal
  to the working tree;
- HA states, read twice (before the rebuild and right before the
  requests): 17 media players, 7 idle, 6 unavailable, 4 off, none
  playing, paused or buffering.

After those requests nothing in the code changed. Dev was not rebuilt
again.

| Container | Before | After |
|---|---|---|
| bgutil-pot-provider | 0e8f74842089 (Up 2 weeks) | same |
| homeassistant | 98f56e2549d0 (Up 3 days) | same |
| kronk-litellm-1 | f93873e3549c | same |
| kronk-nginx-1 | fc84987c3cbd | same |
| kronk-orchestrator-1 | 8715b86b10f4 | same |
| kronk-orchestrator-dev-1 | c42873907aa4 | cfff2d50b445 (rebuilt once) |
| kronk-searxng-1 | 06e462a11968 | same |
| kronk-telemetry-grafana-1 | 6f78f54c5348 | same |
| kronk-telemetry-tempo-1 | a44fa0d3f460 | same |
| kronk-tool_service-1 | 9f2e0c572204 | same |
| kronk-tool_service-dev-1 | 8baec689d972 | 6c9bbca9d71b (rebuilt once) |
| kronk-voice_tracer-1 | a7bc70aec2d4 | same |
| music-assistant-server | cf6b980f6630 (Up 2 weeks) | same |
| wyoming-piper | 5b97cc70b9ce | same |

- **Rebuild.** One `docker compose --profile dev up -d --build
  tool_service-dev orchestrator-dev`. Only those two were recreated.
  Reason: to show live that a live file without the key means off, and
  that `/dev/settings` shows the description.
- **Production.** Tag `sprint-M22` before and after (`deploy_tag.sh
  status`; wyoming-piper `sprint-M17`).
- **Units and timers.** `systemctl --user list-units 'llama-*' 'wyoming-*'
  'kronk-*' --all`: identical before and after. The same 14 timers. None
  started by me; `kronk-upgrade-notices` last ran on its own schedule at
  11:02.
- **Taste data.** `data/music_taste.db` sha256 `0484fed3700c06cf…` and
  `data/music_taste.yaml` `bfc9b44786a8d86f…`, before and after.
- **Live config directories.** `git status` clean in both, before and
  after. Neither was edited.

**Requests made:**

- dev: five dry-run `/music` requests (above); one `GET /api/settings`
  inside `orchestrator-dev`.
- HA: two `/api/states` reads. The token was read inside the command,
  never printed, and unset after. One `docker exec sha256sum`/`grep` of
  the installed blueprint.
- MA:
  - `docker exec` reads of its source (`controllers/music/controller.py`,
    `controllers/player_queues/playback_tracker.py`, `queue_loader.py`);
  - `ls` of `/data`;
  - `docker cp` of `library.db` and its WAL into scratch;
  - no API request.
- Live config: `scripts/config_check.py` on both live directories, read
  only.

**Not done:**

- no HA or MA write, reload or restart; no pipeline run;
- no sound; no notification;
- no production chat or music route; no request to production's
  `/settings`;
- no backup, upgrade or deploy script; no `pipeline_bench.sh`;
- no root;
- `voice_replay.py` not run: the guard is off, and with it off the voice
  path is the pre-M23 one (the corpus's offline layer runs inside
  `run_tests.sh`).

## Scratch

`/tmp/kronk-m23-fix2` (0700) held:

- state lists;
- a read-only backup of the taste DB (0600);
- MA's `library.db` and WAL copy (0600);
- a copy of the tree's tracked files (no `data/`, no ignored files) for
  the mutants;
- the mutant scripts and outputs.

No symlinks (`find -type l`: 0). Removed at the end with
`rm -rf /tmp/kronk-m23-fix2`.

