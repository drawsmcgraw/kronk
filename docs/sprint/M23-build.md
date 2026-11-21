# M23 build report: the queue incident (enqueue replace, stripped requests, Kronk's picks out of the taste profile)

Branch `refactor`. Criteria: `docs/sprint/M23-criteria.md`. Source:
`docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md`, fixes 1 to 3.
Built 2026-11-21 by the build agent.

| Commit | What |
|---|---|
| `1d6dadc` | fix 1: `enqueue: replace` on every play (tool_service, blueprint change 15) |
| `1d1861a` | fix 2: a stripped request is refused; corpus row; snapshot and pin for it |
| `5eb728b` | fix 3: Kronk's own picks are left out of what the taste profile learns |
| `e864798` | fix 3 follow-up: the `picks.queue` migration leaves a read-only taste DB as it is (a regression of `5eb728b`, found on dev) |
| this one | the report |

## Summary

- Fix 1: every `music_assistant.play_media` that tool_service or the blueprint
  sends says `enqueue: replace`. Only track plays change: every other type
  already defaulted to `replace`, and no queue overrides a default.
- Fix 2: built. A query with no phrase plus a spoken sentence that names
  something answers 422 "I didn't catch what to play." with the closest
  match; nothing plays. "Play some music" picks exactly as before (39
  generic sentences tested against today's pick).
- Fix 3: the nightly build drops the history rows Kronk's picks played.
  Recomputed on a copy of the real data: Cracker 4.10 (1st) → 1.90 (3rd).
  The real taste DB and file are unchanged (sha256 checked at the end).
- Tests: before `5738 passed, 3 skipped, 53 xfailed`; after
  `5898 passed, 3 skipped, 54 xfailed, 6 warnings in 81.71s (0:01:21)`.
- Nothing installed, deployed or reloaded. Only the dev pair was rebuilt
  (twice). Production containers, units and timers unchanged.

## Criterion 1: every play says what happens to the queue

**Checked against the running images (read-only).**

- HA 2026.5.4, `components/music_assistant/services.py`: the `play_media`
  schema has `vol.Optional(ATTR_MEDIA_ENQUEUE): vol.Coerce(QueueOption)`, and
  `ATTR_MEDIA_ENQUEUE = "enqueue"`. `services.yaml` offers `play`, `replace`,
  `next`, `replace_next`, `add`. `media_player.py` passes the option
  straight to MA's `player_queues.play_media(option=…)`.
- MA (`kronk/music-assistant:2.11.0b5-kronk.9`),
  `controllers/player_queues/queue_loader.py`:
  - with `option is None`, MA reads the queue's
    `default_enqueue_option_<type>` (`:876-891`);
  - the defaults (`controllers/player_queues/config.py:245-250`) are artist,
    album, genre, live sources and playlist `replace`; track `play`;
  - an explicit `replace` takes the same branch the defaults take
    (`_enqueue_with_option`, `:138-170`), and the same reset of enqueued
    items (`:820` instead of `:887`).

  So `replace` changes nothing for a radio station, a playlist, an album or
  an artist.
- `radio_mode` (the blueprint's "with radio mode") is translated to a
  `radio_playlist://` URI before the option is read (`:800-815`), and a
  playlist defaults to `replace`. No change there either.
- MA's `/data/settings.json` holds no `default_enqueue_option_*` override.
  The only enqueue key is `core/player_queues/values/default_enqueue_select_artist
  = prefer_library`, which picks the tracks of an artist play, not the
  option. So the defaults above are what every queue uses.
- **No path adds to a queue.** No code passes `next` or `add`, and no
  trigger or rule means "play X next" or "add X to the queue". "Queue up X"
  is a play verb (`routing._MUSIC_RE`, `music_resolve._REQUEST_VERB_RE`) and
  has always played X.

**Every call site.** One `ha_call_service("music_assistant", "play_media", …)`
in tool_service (`_play_verified_inner`). No `play_media` in the
orchestrator, the scripts, `voice_tracer` (it only reads traces) or
`ha/rest_command.yaml`. One `music_assistant.play_media` in the blueprint.
The builders of the data:

| Path | Where | Change |
|---|---|---|
| shared value | `tool_service/music_resolve.py:41` | `PLAY_ENQUEUE = "replace"`, with the reason |
| named play: resolved URI, literal fallback, station, link | `tool_service/main.py:1664`, `:1666` | `_named_play_data` (`:1706`) |
| `/music/say` (M11) named play | `tool_service/main.py:2488` | `_named_play_data` |
| every pick: artist, Pandora-only artist, station, playlist, "not that" | `tool_service/taste.py:816` | `Pick.play_data()` adds it |
| blueprint | `ha/blueprints/mass_assist_kronk.yaml:54-58` (header 15), `:318` (version `20250404-kronk15`), `:338-342` (`action_data.enqueue: replace`) | the play's `data` is `dict(action_data.items() \| selectattr('1'))`, so it is sent; change 13's `dict(action_data, media_id=…)` keeps it |

The dry-run answer's `would_play` is the same dict, so the dev path shows
it.

**Test.** `tests/test_music_enqueue.py`, 22 tests:
- named plays: track (the incident's shape), album with a provider hint,
  artist, radio, untyped, a link, a resolved station URI;
- the genre pick, the no-phrase pick, "not that";
- `Pick.play_data()` for four kinds of pick;
- the dry-run answer;
- the blueprint: one `play_media`, `action_data.enqueue == replace`, every
  later `action_data` assignment derives from it, and the play's data
  template rendered with Jinja keeps `enqueue` (with and without change
  13's URI swap);
- call-site counts: tool_service 1, orchestrator 0.

On the old code (a `git archive` of `ff8a7a5` with only the new test file):
`20 failed, 2 passed`. The two that pass are the call-site counts, which
were already one.

The other existing tests that assert service data exactly gained
`"enqueue": "replace"`:
- `test_error_surfacing.py:199`, `:333`;
- `test_music_dry_run.py:61`, `:73`, `:98`;
- `test_music_resolve.py:62`, `:72`, `:173`, `:218`, `:298`, `:335`, `:412`, `:438`, `:465`;
- `test_music_say.py:43`, `:126`;
- `test_music_taste.py:331`, `:364`, `:448`;
- `test_music_live_pin.py:93`;
- `test_blueprint_handoff.py`: the version test is now `…change_15`.

**Live pin.** Regenerated on purpose
(`KRONK_WRITE_MUSIC_PIN=1 … -k test_write_pin`). 99 rows: 96 gained
`"enqueue": "replace"` in `would_play`; the 3 others have no play. With the
key removed again, every row is identical to the old pin. Nothing else
changed: no target, URI, type, reading or MA read.

**Dev, dry run** (after confirming `MUSIC_DRY_RUN=1`, the startup warning,
the new code in `/app`, and 17 media players with none playing, buffering
or paused):

```
> POST /music {"query":"Right Here, Right Now","artist":"Fatboy Slim","media_type":"track","player":"Kitchen"}
{"detail": "Dry run: would play the track Right Here, Right Now by Jesus Jones on the Kitchen speaker. Nothing was sent to the speaker.", "would_play": {"media_id": "library://track/14086", "media_type": "track", "enqueue": "replace"}, ...}  [HTTP 200]
```

`enqueue: replace` is there. The item is not the right one, though:
- The artist field went in separately, and the resolver preferred the NAS
  track by Jesus Jones.
- This was my stand-in request, not a household one. The incident's
  run 2 sent the artist inside the query and resolved to Fatboy Slim.
- The live pin's stand-in, on the recorded snapshot, also plays Fatboy
  Slim.
- Pre-existing resolver behaviour, not touched by M23. Listed under "Not
  done".

## Criterion 2: a stripped request is not a generic request

**Where the sentence is, per path** (read in the code, and pinned where noted):

| Path | `said` at `/music` |
|---|---|
| Kronk direct, web UI `/message` | `req.text` (`orchestrator/main.py:762`) |
| shims `/api/chat`, `/voice/api/chat`, `/v1/chat/completions` | the last user message (`_turn_said`, `main.py:843`); `None` when the turn has no words of its own |
| blueprint hand-off | `conversation.process` reaches `/api/chat`; `said` is the hand-off text, room suffix included ("… in the Kitchen", "… on the kitchen voice pe ma") |
| messy gate → coordinator → `ask_home` → home agent | `origin.said` is set once in `_run_pipeline` (`main.py:387`); the agents-as-tools chain runs inside it, so the coordinator's rewrite does not touch it. Pinned on all four mounts: `test_said_reaches_tool_service_through_the_coordinator_rewrite` |
| lead-in ("ask Kronk to …") | the request without the lead-in (`main.py:375`) |
| dev path | the same code (orchestrator-dev, tool_service-dev) |
| `/music/voice` (unused rest_command) | none: today's pick |
| `/music/say` (M11, not live) | reads the sentence itself; not guarded |

The two captures were confirmed in the production shadow log: both reached
`/music` as `{"query": "music", "player": "Kitchen"}` with the sentence
present.

**The guard.** `tool_service/main.py:1629-1636`, inside the genre branch:
- it runs only when the query cleans to no phrase (`taste.genre_phrase` is
  empty), and the request is not "not that";
- when `taste.said_request(req.said)` is `False`, `/music` raises
  `_stripped_request` (`:1677`).

No routing rule was added.

**How "generic" is decided** (`tool_service/taste.py:696-757`):

1. No sentence, or no request verb in it → unknown → today's pick.
2. The sentence is split at `.`, `!`, `?` and `;`. In each segment, every
   request verb starts a clause. The verbs: play, put on, throw on, listen
   to, shuffle, queue up, spin. "start" and "turn on" are left out, so
   "turn on the lights and play some music" reads only the play clause.
3. A clause is the words after the verb, up to the end of its segment or
   the first stop word: `in, on, at, from, for, to, with, through, into,
   onto`. Rooms, players, providers and "for dinner" are never read.
4. The sentence is generic if **any** clause is all generic words. It is
   "names something" only when every clause holds a word outside the list.

The generic word list (`SAID_GENERIC_WORDS`):
- request words: play, playing, put, throw, listen, shuffle, queue, spin,
  start, turn, up, hear
- amounts and articles: a, an, the, some, any, more, few, couple, of, bit,
  little, lot, whatever, kind, sort, all
- what music is called: music, something, anything, stuff, song, songs,
  tune, tunes, track, tracks, jams, vibe, vibes, mix, playlist, radio,
  station, album, noise, sounds
- how it should be: good, nice, great, cool, fun, new, different, else,
  other, random, usual, favorite, favourite, favorites, favourites,
  background
- who, please, when, filler: me, us, our, my, your, it, please, thanks,
  now, here, again, just, also, then, and, or, so, maybe, kronk, okay, ok,
  hey, um, uh, yeah, alright, can, could, would, will, you, go, ahead, let,
  let's, lets, i, we, want, wanna, like, how, about

A test checks that no built-in genre is made only of these words.

**How "play some music" is protected.**
- Every leaning goes toward generic: any one generic clause is enough;
  trailing chatter after a full stop is not read; a room or provider after
  a stop word is not read; no sentence or no verb keeps the pick.
- The guard never runs when the query has a phrase.
- `test_generic_sentences_pick_exactly_as_before` posts each of 39 generic
  sentences with `query: "music"`. It asserts the answer and the play data
  equal the same request with no sentence: today's pick from the top of the
  profile.
- The corpus holds no bare generic sentence of the household's own. Every
  `play_music` row with no name carries a genre. So the table starts from
  the blueprint's own loose sentences, `(play|put on) [some ](music|something)
  [(in|on) [the ]{area}]`, with both hand-off suffixes, then everyday forms:

  > play some music · play music · play something · put on some music ·
  > put on something · play some music in the Kitchen · play something in
  > the Office · put on some music in the Basement Theater · play some music
  > on the kitchen voice pe ma · Play music. in the Kitchen · Play some
  > music. · put on some tunes · Put on some tunes in the kitchen. · throw
  > on some tunes · listen to some music · let's listen to something · Can
  > you play some music? · Kronk, play some music please. · Hey Kronk, could
  > you put on some music for dinner? · play me something · play us
  > something nice · Play some good music · play some songs · play a song ·
  > play anything · play some random music · play some background music ·
  > play some music on the Sonos Move · play some music from YouTube Music ·
  > shuffle some music · queue up some music · play something again · I'm
  > cooking dinner, play some music · turn on the lights and play some
  > music · Play some music. That's probably about it. · Play some music for
  > us, now · Okay, um, play some music · " Play some music in the kitchen!"
  > · Let’s play some music

- Real data, counts only: the production shadow log (12 records, from
  2026-11-17) holds two requests whose query had no phrase. They are the
  two captures, and both read as "names something". The incident found the
  same two in journald back to 2026-10-19. So no past request other than
  the two captures would have been refused.

**The refusal** (`_stripped_request`, `main.py:1677-1703`):
- it runs the M11 parser on the clause's words (room and provider
  stripped), then the resolver;
- 422 "I didn't catch what to play.", plus "The closest I found was <name>
  by <artist>." when the best match reaches `resolver.hint_min_score`, the
  bar `_not_found_sentence` already uses;
- nothing is sent to a player and no pick is recorded;
- span `music.stripped_request` (outcome, hint score) and one log line.

Through the orchestrator it is a terminal failure: "Could not play music:
…", and the model is told not to call `play_music` again.

**Tests.** `tests/test_music_stripped.py`, 117 tests:
- the classifier: 39 generic, 7 that name something (both captures among
  them), 8 unknown;
- the word lists are well formed;
- `/music`:
  - run 1 refused, naming Right Here, Right Now by Fatboy Slim;
  - capture 2 refused with no hint;
  - a weak hint is not named;
  - MA unreachable still refuses plainly;
  - undeclared and declared genre are both guarded;
  - the 39 generic and 8 unknown sentences pick exactly as before;
  - a phrase is never guarded, and neither is "not that";
  - `/music/voice` picks as before;
- the coordinator path on four mounts.

On the fix-1 tree, the six `/music` refusal tests fail (the old code
picks). The generic, unknown and orchestrator tests pass there too, as
they should.

**Dev, dry run, real MA reads:**

```
> POST /music {"query":"music","player":"Kitchen","said":"Play right here, right now, bud.  That boy's slow. in the Kitchen"}
{"detail": "I didn't catch what to play. The closest I found was Right Here, Right Now (Rework) by Fatboy Slim and Topic and Marco Generani."}  [HTTP 422]
> POST /music {"query":"music","player":"Kitchen","said":"Play back in the Kitchen"}
{"detail": "I didn't catch what to play. The closest I found was Back In Black by AC/DC."}  [HTTP 422]
> POST /music {"query":"music","player":"Kitchen","said":"play some music in the Kitchen"}
{"detail": "Dry run: would play Cracker on the Kitchen speaker. ...", "would_play": {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}, "pick": {"label": "Cracker", "phrase": "", "kind": "artist"}}  [HTTP 200]
> POST /music {"query":"music","player":"Kitchen"}
{"detail": "Dry run: would play Pink Floyd on the Kitchen speaker. ...", "would_play": {"media_id": "library://artist/81", "media_type": "artist", "enqueue": "replace"}, "pick": {"label": "Pink Floyd", "phrase": "", "kind": "artist"}}  [HTTP 200]
```

The last two run the same code path and the same ranked list (log:
`1559 picks, first [Cracker 4.10, Pink Floyd 2.78, Orbital 1.80, …]`).
Which of the top three plays is the existing weighted random choice, as
before. Dev reads production's taste DB through a read-only mount, so its
profile is the real one, not yet rebuilt.

Run 1's hint on live MA is the "Rework" album at 0.74. The shadow's track
match came from the whole two-sentence text; the hint is true, and it is
the closest MA has to what was said.

**Corpus, pin, shadow.**
- Row `inc-1121-fatboy-slim-heard-as-two-sentences` is the last line of
  `tests/voice_corpus.jsonl` (`:162`). It is the incident's row, with:
  - `xfail.live` brought up to date (the refusal);
  - a new `xfail.parser`: the rules' reading is not_found at 0.74, so the
    parser layer is a strict xfail.
- Routing passes (`direct/messy`). The xfail count went from 53 to 54.
- Snapshot: recorded for this row only with
  `scripts/music_snapshot_record.py --path both --id …` (reads only, MA
  commands allowlisted, the taste data its own temp copy, `TMPDIR` in
  scratch). 11 reads added, 0 changed; `players`, `genres`, `taste`
  identical.
- Live pin: gained exactly that row. It is a dry run of the track
  `ytmusic--kGmHoQDn://track/vqNIeInr9kA` (Right Here, Right Now by Fatboy
  Slim) on the kitchen player, from the stand-in arguments. No other row
  changed.
- Existing corpus, pin and shadow tests: unchanged and passing, except the
  96 pin rows of fix 1 (the `enqueue` key) and this new row.

## Criterion 3: Kronk's own picks do not feed the profile

**What the history is.** `music/recently_played_items` reads MA's
`media_progress`, not a log:
- one row per item per user, holding its last play (`_upsert_playlog`);
- the answer gives item, provider, type and name: no time, queue or flag.

MA does filter on three columns (`controllers/music/controller.py:959-1065`):
- `queue_id`;
- `played_after_timestamp`;
- `user_initiated_only`. MA sets `user_initiated` on the item handed to
  `play_media`, and the flag is sticky once set. Tracks played out of an
  artist, album or station are not user-initiated (`playback_tracker.py`,
  `_is_user_initiated_play`), and neither are the artist credits each track
  adds.

MA cannot tell Kronk's play from the household's. Both arrive through HA's
`play_media` as the same user.

**How a pick is told from a named play.** `taste.kronk_pick_rows`
(`tool_service/taste.py:573`), called by `build_profile` (`:474`). For each
of the last 100 picks (`PICK_LOOKBACK`), MA is asked three reads, each
filtered to the pick's queue:
- the rows played from 60 s before the pick (`PICK_LEAD_S`, `:300`);
- the rows played after 12 h (`PICK_WINDOW_S`, `:299`);
- the user-initiated rows since the pick.

A row in the window is Kronk's when:
- the household did not start it (the pick's own item is Kronk's either way);
- and it is the picked artist's (the artist row, or a track or album row
  credited to it, as the build credits it), or it is the picked station or
  playlist itself.

Kronk's rows are dropped. The rest keep their order, so recency weights
are as if Kronk's plays had not happened.

The queue comes from the player's `active_queue`, the HA state attribute
the integration sets. `_play_genre` already reads that state after the
start and now passes it (`main.py:2085`) to `record_pick` (`taste.py:916`),
which stores it in the new `picks.queue` column. An existing DB gains the
column on open (`taste.py:326-336`); a read-only DB is left as it is
(`e864798`). Picks recorded before M23 have no queue and are read on every
queue. That covers ids 18 and 19.

Why the 60 s lead: a pick is recorded after its play is verified, and MA
marks the picked item when the play starts. Pick 19's artist row is
13:40:56 against the pick's 13:40:57, and its tracks never played through,
so no later credit moved the row. Without the lead, pick 19's artist row
was missed on the real data.

**What it cannot tell:**
- tracks of an album or artist the household asked for by name, by the
  picked artist, on the same speaker, within 12 h of the pick (only the
  item asked for is user-initiated, not its tracks);
- a request by name for the very artist Kronk picked (one row for both);
- the tracks a picked station or playlist played: their artists could be
  anyone's, so only the station or playlist row is dropped;
- a row played again after the window counts again (MA keeps the last play
  only);
- a pick older than the last 100.

Feedback is not touched: thumbs, skips, stops, "not that", listened.

**What happens to ids 18 and 19.** They have no queue, so they are read on
every queue. On the real data:
- pick 18 drops the ten Cracker tracks played that evening;
- pick 19 drops the Cracker artist row.

Their feedback (+0.3 listened, −0.5 stopped) stays.

**Tests.** `tests/test_music_taste_picks.py`, 16 tests, on a fake MA that
keeps `media_progress` as MA does and answers with MA's three filters:
- a pick of 20 Cracker tracks leaves the profile identical to one built
  with no such plays;
- contrast: without the pick recorded, Cracker tops the profile;
- a named play raises its artist's weight;
- a named play inside the window still counts;
- other artists after the pick, on the same queue, count;
- the same artist on another queue counts;
- plays after the window count;
- the pick's own item marked before the pick is recorded is Kronk's;
- a pick with no queue is read on every queue;
- a station pick drops the station row;
- feedback still moves the score;
- no picks means no extra reads;
- `record_pick` keeps the queue; `/music` passes `active_queue`;
- an old DB gains the column; a read-only old DB still opens and picks.

On the pre-fix-3 tree: `12 failed, 2 passed`. The two that pass are the
named-play test and the contrast test, which hold either way. The
read-only test fails on `5eb728b` with
`sqlite3.OperationalError: attempt to write a readonly database`.

**The profile, recomputed in scratch.**
- Inputs: a read-only `sqlite3` backup of `data/music_taste.db` and a copy
  of `data/music_taste.yaml`, in `/tmp/kronk-m23-build` (0700).
- MA's answers were recorded once, through an allowlist of four read
  commands (`music/artists/library_items`, `music/item_by_uri`,
  `music/recently_played_items`, `music/radios/library_items`).
- Both builds replayed those answers: "before" with the code before fix 3
  (a `git archive` of `1d6dadc`), "after" with this branch.
- The tagger was a stub: no model call, the cached tags only. A production
  rebuild may tag a few new artists; that moves genres, not scores.

Top ten, score including feedback (`GET /music/taste/profile` order):

| # | Real DB now (built 03:30) | Before (old rule, MA now) | After (M23) |
|---|---|---|---|
| 1 | Cracker 4.10 | Cracker 4.10 | Pink Floyd 4.30 |
| 2 | Pink Floyd 2.78 | Pink Floyd 3.14 | Fatboy Slim 2.55 |
| 3 | Orbital 1.80 | Orbital 1.80 | Cracker 1.90 |
| 4 | Eels 1.44 | Fatboy Slim 1.75 | Orbital 1.80 |
| 5 | Wilco 1.43 | Eels 1.48 | Eels 1.60 |
| 6 | The Avalanches 1.39 | Wilco 1.46 | Wilco 1.56 |
| 7 | Weezer 1.34 | The Avalanches 1.42 | The Avalanches 1.49 |
| 8 | Kruder & Dorfmeister 1.30 | Weezer 1.35 | Aerosmith 1.44 |
| 9 | Massive Attack 1.30 | Kruder & Dorfmeister 1.30 | Weezer 1.38 |
| 10 | Moby 1.30 | Massive Attack 1.30 | Portishead 1.31 |

- 15 history rows were left out of 500:
  - pick 18: ten Cracker tracks;
  - pick 19: the Cracker artist row;
  - picks 13 to 16: one station row and three playlist rows.
- Picks 10, 11, 12 and 17 left nothing in the window that was still in the
  history.
- Summary: `history_items 485, kronk_pick_items 15`; 30 extra reads.
- Cracker keeps 1.90 from three household Cracker tracks older than pick
  18 (history positions 42, 45, 49), its library entry and its feedback.
- Fatboy Slim's 2.55 is this morning's named plays.
- Pink Floyd is now the most-played artist, so it scales to the top.

## Criterion 4: requests whose result changes

| Request | Before | After | Fix |
|---|---|---|---|
| any track play (Kronk named, blueprint `track` trigger, `/music/say`) onto a queue with items after the current one | the track, then the rest of the old queue | the track, then whatever it holds, then silence | 1 |
| any other play (album, artist, playlist, station, genre pick, artist pick, "not that") | `replace` by default | `replace`, stated | 1: no effect |
| `/music` with a no-phrase query and a sentence that names something (seen twice, the two captures) | top-of-profile pick | 422 refusal with the closest match | 2 |
| `/music` with a no-phrase query and a generic sentence, no sentence, or no verb | pick | the same pick | 2: no effect |
| no-phrase and genre picks after the next rebuild | ranked by a profile that counts Kronk's own picks | ranked without them (Cracker 4.10 → 1.90, see above) | 3 |

No request changes its target. The pin shows no item or reading changed
except the `enqueue` key.

## Criterion 5: nothing installed; steps for the coordinator

Nothing was copied into HA, nothing reloaded, nothing deployed. The real
taste DB and taste file were only read, and only through a read-only
`sqlite3` backup and a file copy. sha256 matched at the start and at the
end.

### Blueprint change 15

Installed today: `version: 20250404-kronk14` (`:313`). It is
byte-identical to the repo's file before M23 (sha256 `13aa0728d8c2be70…`).
Owner 1000:1000, mode 664. New file: sha256 `8ded30f9745b67a0…`. No HA
restart is needed: an automation reload picks it up, as with changes 8 to
14. Pick a moment when no puck is mid-request. The reload re-creates every
automation, and the music automation is `mode: single`.

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

### tool_service (fixes 1 to 3)

They reach production only through the next tagged deploy
(`scripts/deploy_tag.sh plan <tag>`, then `deploy <tag>`), from a tag at
or after `e864798`. Do not deploy `5eb728b` alone: its migration breaks
dev's read of the taste DB. Production's own DB is writable and would
take the migration, but nothing gains from that commit on its own. The
blueprint and tool_service changes are independent: each works with the
other's old version.

### Apply fix 3 to the real profile (after the deploy)

The deploy alone changes nothing in the DB until the next build, at 03:30.
The first open after the deploy adds the `picks.queue` column. To apply
the new rule now:

```bash
cd ~/git-repos/drawsmcgraw/kronk
# 0. a consistent copy of the taste DB (read-only open), kept for the undo
python3 -c "import sqlite3; s=sqlite3.connect('file:data/music_taste.db?mode=ro', uri=True); d=sqlite3.connect('data/backups/music_taste.pre-m23.db'); s.backup(d); d.close()"
curl -s 'http://127.0.0.1:8003/music/taste/profile?limit=10' | jq -c '[.artists[] | [.name, .score]]'   # before
# 1. rebuild with the new rule (rewrites the artists and stations tables only; tags, feedback, picks kept)
curl -s -X POST http://127.0.0.1:8003/music/taste/rebuild | jq .
#    expect kronk_pick_items > 0 (15 on 2026-11-21; it moves as the history does) and history_items = 500 - that
# 2. read back
curl -s 'http://127.0.0.1:8003/music/taste/profile?limit=10' | jq -c '[.artists[] | [.name, .score]]'
#    expect Cracker no longer first (1.90, third, in the scratch recompute)
```

The rebuild reads MA only: library items, items, history and stations,
plus the local tagging model for untagged artists. It takes ~20 s with
tags cached.

**Undo:**
- **(a) the rule:** deploy the previous tag (`sprint-M22`), then
  `curl -s -X POST http://127.0.0.1:8003/music/taste/rebuild`. That rebuilds
  the profile under the old rule from MA as it is then. The `queue` column
  stays. The old code names its columns when it inserts and reads by name,
  so it ignores it.
- **(b) the data:** copy `data/backups/music_taste.pre-m23.db` back over
  `data/music_taste.db`. Do it while tool_service is stopped, or between
  writes. This loses any pick or feedback recorded after the copy, so
  prefer (a).

## Criterion 6: docs

- `docs/features/voice-music-control.md`:
  - "How it works (tier 2)" step 4 and a new paragraph, "Every play
    replaces the queue": the rule, why, where it is sent, what does not
    change, no add path, the test;
  - the "Don't stop the music" line: it was on for the kitchen in 2026-09,
    every queue reads off on 2026-11-21, cause unknown, turning it back on
    is an MA setting.
- `docs/features/music-taste.md`:
  - genre requests step 1: the stripped-request guard, its word list, its
    leanings, what it cannot tell;
  - "Learned": Kronk's picks left out, how, what it cannot tell, the
    recompute;
  - the history gotcha: MA filters by queue, time and user-initiated.
- `tool_service/taste.py` module docstring: one line on the same.
- The incident document:
  - a "Fixes (sprint M23)" section with dated lines for fixes 1, 2 and 3;
  - the corpus-row section now says the row was added, and with which
    `xfail`s.
- `tests/voice_corpus.jsonl`: the row (criterion 2).
- `ROADMAP.md`: not touched. The queue incident's line (`cee634d`) moves to
  Shipped when the coordinator has installed and deployed.

## Criterion 7: tests

`./scripts/run_tests.sh`:
- before: `5738 passed, 3 skipped, 53 xfailed, 6 warnings in 81.12s`;
- after: `5898 passed, 3 skipped, 54 xfailed, 6 warnings in 81.71s (0:01:21)`.

160 more passing tests and one more xfail:
- 22 in `test_music_enqueue.py`;
- 117 in `test_music_stripped.py`;
- 16 in `test_music_taste_picks.py`;
- 6 parametrizations of the new corpus row: 4 in `test_voice_corpus.py`
  (one is the strict `xfail.parser`), 1 pin, 1 shadow.
The new tests open no sockets: `no_network` is used, and MA and HA are
fakes.

## Criterion 8: production unchanged

Containers (name, id, image), start and end of the build:

| Container | Before | After |
|---|---|---|
| bgutil-pot-provider | 0e8f74842089 | same |
| homeassistant | 98f56e2549d0 (Up 3 days) | same |
| kronk-litellm-1 | f93873e3549c | same |
| kronk-nginx-1 | fc84987c3cbd | same |
| kronk-orchestrator-1 | 8715b86b10f4 | same |
| kronk-orchestrator-dev-1 | ce8d0be88033 | b9a4fe8ac137 (rebuilt) |
| kronk-searxng-1 | 06e462a11968 | same |
| kronk-telemetry-grafana-1 | 6f78f54c5348 | same |
| kronk-telemetry-tempo-1 | a44fa0d3f460 | same |
| kronk-tool_service-1 | 9f2e0c572204 | same |
| kronk-tool_service-dev-1 | 71509fa06f84 | 05a4f500af0a (rebuilt twice; ad2eba1d2751 between) |
| kronk-voice_tracer-1 | a7bc70aec2d4 | same |
| music-assistant-server | cf6b980f6630 (Up 2 weeks) | same |
| wyoming-piper | 5b97cc70b9ce | same |

Production runs tag `sprint-M22` (`deploy_tag.sh status`), as before.

Units: `systemctl --user list-units 'llama-*' 'wyoming-*' 'kronk-*' --all`
is identical before and after. The hot tub monitor is active. Timers: the
same 14, none started.

The dev pair was rebuilt twice with
`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`:
for criteria 1 and 2, and after the follow-up fix. The second run
recreated only tool_service-dev. Before every request each time:
- `printenv MUSIC_DRY_RUN` gave `1` (from `.env.dev`);
- the startup line `WARNING:tool_service:MUSIC_DRY_RUN is on: music requests resolve but nothing is sent to a player or MA`
  was logged;
- the new code was in `/app`;
- an HA state read showed 17 media players, none playing, buffering or paused.

Seven requests in all, to `127.0.0.1:8103/music`, all shown above.

**Against HA, all reads:**
- `/api/states` reads: every player's state, 3 times; one player's
  attributes, once (`active_queue`). The snapshot recorder asked HA for
  nothing, since the snapshot already holds the players;
- `docker exec` reads of HA's music_assistant integration source and the
  installed blueprint.

**Against MA, all reads:**
- `docker exec` reads of its source and `settings.json`;
- the snapshot recorder's allowlisted reads;
- the recompute's allowlisted reads;
- the dev tool_service's search reads.

**Not done:**
- no HA or MA write, reload or restart;
- no pipeline run;
- no sound, no notification;
- no request to production's chat or music routes;
- no backup, upgrade or deploy script; no `pipeline_bench.sh`.

## Not done, and why

- **Install of change 15 and the deploy:** the coordinator's steps
  (criterion 5).
- **Applying fix 3 to the real profile:** the coordinator's step, after
  the deploy (criterion 5).
- **Live replay (`scripts/voice_replay.py`):** not run.
  - The full replay and `--check-only` use HA's pipeline and conversation
    debug, and this item allows no pipeline run.
  - `--answers` would be allowed, but it sends stand-in "perfect model"
    arguments. Those never carry a bare "music", so fix 2 never runs, and
    fix 1 shows only in the offline pin.
  - Left for the e2e agent or the coordinator.
- **Fix 5 (a wake scored as a stop):** does not fall out of fix 3. The
  watcher scores outcomes; fix 3 changes what the build reads. Left as the
  incident says.
- **Station and playlist picks' own tracks:** still counted. Their artists
  are anyone's, and attributing them needs the end of the pick's session,
  which MA's API cannot give without a search over times. The loop the
  incident found is the artist pick, which is covered.
- **Found in passing, not fixed:**
  - `{"query": "Right Here, Right Now", "artist": "Fatboy Slim",
    "media_type": "track"}` resolves on live MA to the NAS track by Jesus
    Jones, not Fatboy Slim. This is the resolver with a separate artist
    field, untouched by M23. The household's own run 2 put the artist in
    the query and resolved right.
  - Run 1's hint on live MA names the "Rework" album at 0.74, not the
    original track.
  - For the coordinator to judge; not added to the roadmap or the incident
    document.
- **The blueprint header** still says "Three changes" above a list of 15.
  Pre-existing; left.

## Scratch

`/tmp/kronk-m23-build` (0700) held:
- state lists and test logs;
- the old pin and snapshot;
- three `git archive` trees (`ff8a7a5`, `1d6dadc`, `5eb728b`) for the
  old-code runs;
- the taste DB and file copies, mode 0600;
- the recompute script and its recorded MA reads.

No symlinks. The token was read inside the scripts and never printed or
written. The snapshot recorder's temporary directory was inside scratch
(`TMPDIR`) and removed by the recorder. Removed at the end with
`rm -rf /tmp/kronk-m23-build`.
