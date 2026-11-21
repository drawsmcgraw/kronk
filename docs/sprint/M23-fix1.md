# M23 fix round 1: the stripped-request guard turned round, the pick window ends at the next start

Branch `refactor`. Criteria: `docs/sprint/M23-criteria.md`. Build report:
`docs/sprint/M23-build.md`. End-to-end report (FAILS): `docs/sprint/M23-e2e.md`.
Fix round 1, 2026-11-21, by the fix agent, on the coordinator's rulings A to H.

| Commit | What |
|---|---|
| `d64ad4e` | code and tests: rulings A, C, D, F |
| `b3def9a` | docs: rulings A, D, G (music-taste.md, voice-music-control.md, the incident document) |
| this one | the report |

## Summary

- A: a stripped request is refused only when the sentence holds no word
  that means music in general. 341 of the 342 generic sentences (the
  tester's 303 and the builder's 39) pick exactly as with no sentence;
  "Play some muse sick." stays refused. Both captures stay refused.
- B: the lookup is unchanged (room, provider and verb dropped). No hint at
  or above 0.5 on the captures and their neighbours names nonsense; the
  bar stays.
- C: through the orchestrator the refusal is one sentence.
- D: the pick window ends at the first play started on the queue after the
  pick, 24 h at most. A named album of the picked artist after the pick
  counts with its tracks; a request for the picked artist itself still
  cannot be told.
- E: the coordinator's reading of finding 2 does not hold. The 21 station
  rows are 21 named plays of that station through Kronk (the 2026-11-08
  argument bench). Rule D keeps the row.
- F: the three new test files leave no module state; the tester's two
  orderings pass.
- G: report wording corrected below; findings 6, 7, 8 have their lines in
  the feature docs.
- H: recomputed: 14 rows left out (was 15), Cracker 1.90 (third), top ten
  names unchanged.
- Tests: before `5898 passed, 3 skipped, 54 xfailed`; after
  `6545 passed, 3 skipped, 54 xfailed, 6 warnings in 84.62s (0:01:24)`.
- Nothing installed, reloaded or deployed. Only the dev pair was rebuilt
  (once). Production containers, units and timers unchanged.

## A. Fix 2 turned round

**The rule** (`tool_service/taste.py:757-836`):

1. No sentence, or no request verb in it (play, put on, throw on, listen
   to, shuffle, queue up, spin): unknown, today's pick. Unchanged.
2. Otherwise the sentence is generic when any of these holds, anywhere in
   it (`_said_generic`, `:795`):
   - a word starting with "music" (musics, music's, musical);
   - a word in `SAID_MUSIC_WORDS` (`:774`): something, anything,
     everything, whatever, stuff, song, songs, tune, tunes, track, tracks,
     jam, jams, mix, playlist, playlists, radio, station, album, albums,
     record, records, library, sounds, vibes, noise, favorites, favourites,
     usual, more, again;
   - that, it, this or one as the first word after a request verb
     (`SAID_MUSIC_PRONOUNS`, `:783`);
   - "some" or "sum" followed only by filler in its sentence
     (`SAID_FILLER`, `:787`: please, thanks, thank, you, now, kronk, okay,
     ok, um, uh);
   - a request verb followed only by filler in its sentence ("Play.");
   - "surprise me".
3. Otherwise `/music` refuses (`main.py:1635`, unchanged). `what` for the
   lookup is the words after the first request verb to the end of its
   sentence, as before.

**Two departures from the ruling's list, both forced by the ruling's own
constraints:**

- **Pronouns count only right after the verb.** The ruling lists "that",
  "it", "this", "one" as words that count anywhere, and also says capture
  1 stays refused. Capture 1 holds "That" ("That boy's slow."), its only
  listed word. Both cannot hold. The narrowest change that keeps both: a
  pronoun counts as the thing played ("play it", "play that again"),
  not in chatter. Applied to all four, since each is common in chatter
  ("It's by …", "This is …"). No sentence of the 342 needs a pronoun
  elsewhere.
- **A bare request verb is generic.** "Play." and "play" (fixture lines)
  hold no listed word, so the ruling's rule refuses them; before M23 they
  picked. A verb followed only by filler asks for nothing in particular;
  the same reading the ruling gives "play some".

**The 342** (`tests/fixtures/music_generic_sentences.txt`: the tester's 303
first, then the builder's 39 under a `#` line):

| Result | Count |
|---|---|
| classified generic | 305 |
| classified unknown (no sentence or no request verb) | 36 |
| refused | 1 |

Each of the 341 is posted to `/music` with `query: "music", kind: "genre"`
and asserted equal (status, body, play data) to the same request with no
sentence: `test_generic_sentences_pick_exactly_as_before`, 341 cases.

The exception, refused: **"Play some muse sick."** Whisper's form of "music"
left no word for music in the sentence. Saving it takes a rule for that one
mishearing, and "muse" alone is a band ("play Muse" with a stripped query
should refuse). That is the word-list problem again, one mishearing at a
time. Pinned by `test_the_generic_sentence_that_stays_refused`. On dev it
answers "I didn't catch what to play. The closest I found was muse sick! by
WILLITO." (0.74): a real track by that name.

**The captures** (`NOT_GENERIC`, `tests/test_music_stripped.py`):
"Play right here, right now, bud.  That boy's slow." with and without
" in the Kitchen", and "Play back in the Kitchen", refused. Also "Play
back", "play some jazz", "play Praise You by Fatboy Slim", "play it's my
life" ("it's" is not "it").

**The known cost** (`MISSED`, `test_the_known_cost_a_named_request_with_a_word_for_music`).
A stripped request whose sentence also holds a word for music is read as
generic and gets the pre-M23 pick, which is no worse than before M23:

- play the song right here right now
- play something by Orbital
- play music by pink floyd
- Play the song Praise You by Fatboy Slim.
- play some 80s music in the kitchen
- play Low by Cracker again
- play this is america

The M23 build refused all seven; the end-to-end test's finding 11
listed "Play Something by the Beatles" with query `Something` as refused by
the build: it now picks, as before M23.

**Tests on HEAD's code** (`83f0d72`, the new test file and fixture copied
over): `123 failed, 623 passed`.

## B. The closest match

The lookup is as before: `_stripped_request` (`main.py:1678`) parses
"play " + `what` with the M11 parser, which drops the room and provider,
then resolves; a hint is named at or above `resolver.hint_min_score` (0.5).
Finding 3's nonsense hints came from generic sentences ("To music, thank
you by Treason"); those no longer reach the refusal.

On dev, dry run (preconditions under "State"):

```
> POST /music {"query": "music", "player": "Kitchen", "said": "Play right here, right now, bud.  That boy's slow. in the Kitchen"}
{"detail": "I didn't catch what to play. The closest I found was Right Here, Right Now (Rework) by Fatboy Slim and Topic and Marco Generani."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play right here, right now, bud.  That boy's slow."}
{"detail": "I didn't catch what to play. The closest I found was Right Here, Right Now (Rework) by Fatboy Slim and Topic and Marco Generani."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play back in the Kitchen"}
{"detail": "I didn't catch what to play. The closest I found was Back In Black by AC/DC."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play back"}
{"detail": "I didn't catch what to play. The closest I found was Back In Black by AC/DC."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play right here, right now"}
{"detail": "I didn't catch what to play. The closest I found was Right Here, Right Now by Jesus Jones."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play some muse sick."}
{"detail": "I didn't catch what to play. The closest I found was muse sick! by WILLITO."}  [HTTP 422]
> POST /music {"query": "music", "player": "Kitchen", "said": "Play some music, thank you."}
{"detail": "Dry run: would play Pink Floyd on the Kitchen speaker. Nothing was sent to the speaker.", "would_play": {"media_id": "library://artist/81", "media_type": "artist", "enqueue": "replace"}, "pick": {"label": "Pink Floyd", "phrase": "", "kind": "artist"}}  [HTTP 200]
> POST /music {"query": "music", "player": "Kitchen", "said": "play some music tonight"}
{"detail": "Dry run: would play Cracker on the Kitchen speaker. Nothing was sent to the speaker.", "would_play": {"media_id": "library://artist/639", "media_type": "artist", "enqueue": "replace"}, "pick": {"label": "Cracker", "phrase": "", "kind": "artist"}}  [HTTP 200]
```

Hint scores from dev's log: Rework album 0.74 (both forms of capture 1),
Back In Black 0.97 (both), Jesus Jones' track 1.03, "muse sick!" 0.74.

Bar kept at 0.5. Every hint is a real title matching what was said: the
right song in another version; a title made of the word heard; a song with
exactly that title; a track with that name. The one questionable hint,
Back In Black for "back", scores 0.97, so no bar between 0.5 and 0.97 would
remove it; that is the resolver's scoring of a one-word query, untouched by
M23. The last two requests were refused by the build (finding 1) and now
pick.

## C. The refusal said once

`orchestrator/agents.py:128` `STRIPPED_REFUSAL = "I didn't catch what to
play."`; `:152`: a "Could not play music" detail that starts with it is
spoken as it is, in both styles. Every other music failure keeps "I
couldn't play that. …". tool_service's wording is unchanged.

Tests (`tests/test_music_stripped.py`):
- `test_the_orchestrator_knows_the_refusal_sentence`: the two strings are
  equal (the orchestrator cannot import tool_service);
- `test_the_refusal_is_said_once_through_the_orchestrator`: with and
  without a hint, debug and friendly; another failure keeps its lead-in;
- `test_the_refusal_reaches_the_user_once_on_every_mount`: run 1's path
  with tool_service's 422, all four mounts.

On HEAD's orchestrator: `9 failed`.

Live, one request to dev's orchestrator (`/api/chat` on
`orchestrator-dev:8100`, from inside its container):

```
> {"model": "kronk", "stream": false, "messages": [{"role": "user", "content": "Play right here, right now, bud.  That boy's slow. in the Kitchen"}]}
200 I didn't catch what to play. The closest I found was Right Here, Right Now (Rework) by Fatboy Slim and Topic and Marco Generani.
```

The model sent `query='music'` again (dev's log); tool_service refused.

## D. The pick window

**The rule** (`tool_service/taste.py:290-306`, `_window_end` `:578`,
`kronk_pick_rows` `:639`):

- The window opens 60 s before the pick (`PICK_LEAD_S`, unchanged).
- It ends at the first play started on the pick's queue after the pick:
  the household's or another pick's. A start is a user-initiated row last
  played at or after the pick (MA's `user_initiated_only`, with
  `fully_played_only=False` so a start that was skipped still counts).
- It never runs past `PICK_WINDOW_S`, now 24 h.
- Rows of the pick (the picked artist's rows, or the picked station or
  playlist) last played inside the window are Kronk's, unless the household
  started them; rows after the window are the household's, tracks
  included. The pick's own item is Kronk's only while its last play is
  inside the window.

MA's answer carries no time. The first start's time is found by bisecting
`played_after_timestamp` between the pick and the cap, to the second: about
17 reads, and only for a pick with a start after it.

**What is not a start, and why.** "Other than the pick's own item at the
pick's own time" needs reading against MA's source. Read literally, an
artist pick ends its own window: MA's `_credit_artist_plays`
(`controllers/music/controller.py:3632`) moves the artist row to each
track's play while the sticky flag stays set (`_upsert_playlog`, `:3537`).
So:

- **Artist rows are not starts.** Every track's credit moves its artists'
  rows: the picked artist's own row, and a co-artist's.
- **Tracks credited to the picked artist are not starts.** A track the
  household once asked for keeps the flag, and the pick replays it
  (finding 5). Such a track counts itself, as in the build, but does not
  end the window. Otherwise one sticky track would hand the rest of the
  pick to the profile.
- **The pick's own station or playlist is a start only when started
  again.** MA marks a dynamic station only when a play starts it
  (`queue_loader.py:964`) and a playlist or finite station when its tracks
  resolve (`media_resolver.py:1083`, `:1163`, `:1359`). The pool's tracks
  are tracks (`managed_pool.py`), and the tracker reports the track
  (`playback_tracker.py:768`). A station or playlist row last played more
  than 60 s after the pick was therefore started again.

These are readings of the ruling, not a third design. The window is the
ruling's: next start, else the cap.

**The cap: 24 h.** It covers a pick left playing all night (finding 5:
200 tracks over 13.3 h). It also bounds the damage when MA saw no start (a
play from a speaker's own app, or the case below). The cost is in the
table: the household asking for the very artist Kronk picked, 12 to 24 h
later on the same speaker with nothing started between, was counted by the
12 h window and is not now.

**The tester's finding 4 and 5 cases, as tests** (`tests/test_music_taste_picks.py`;
a Cracker pick on the kitchen queue at T, five Kronk tracks unless said):

| Case | Build (12 h, finding 4/5) | Fix round 1 | Test |
|---|---|---|---|
| picked artist asked for by name 2 h after, same queue, 5 tracks | dropped (6 rows) | dropped (11 rows, Kronk's 5 included): cannot tell | `test_a_request_for_the_picked_artist_itself_cannot_be_told` |
| the same, 13 h after | kept (5.85) | dropped: inside the 24 h cap | same test |
| the same, 25 h after | kept | kept | same test |
| album of the picked artist 2 h after, same queue, 8 tracks | 9 dropped, 1.0 | album, 8 tracks and the artist row kept; only Kronk's 5 dropped | `test_an_album_of_the_picked_artist_asked_for_after_the_pick_counts_with_its_tracks` |
| the album on another queue, pick has a queue | kept | kept | `test_the_album_on_another_queue_counts_whether_or_not_the_pick_has_a_queue` |
| the album on another queue, pick's queue NULL | 9 dropped, 1.0 | kept | same test |
| a track of the picked artist asked for in the window | 1 dropped (the artist credit), 1.0 | same: the track counts, the artist row stays Kronk's | `test_a_track_of_the_picked_artist_asked_for_in_the_window_counts` |
| 200 tracks over 13.3 h | 179 dropped, Cracker tops the profile | all 201 dropped, Cracker not on top | `test_a_pick_playing_all_night_is_left_out_whole` |
| a sticky track replayed by the pick | that row kept | that row kept; the rest of the pick dropped | `test_a_track_once_asked_for_by_name_and_replayed_by_the_pick` |
| another start 1 h in, then the household's Cracker tracks | Cracker tracks dropped (inside 12 h) | kept | `test_a_start_after_the_pick_ends_its_window` |
| finding 2's shape: station pick, a playlist pick 6 min later, the station asked for 21 times | station row dropped | station row kept, playlist dropped | `test_a_station_asked_for_by_name_after_its_pick_counts` |

Also `test_the_window_end_costs_a_bounded_number_of_reads` (at most 23 reads
for a pick with a start). The 16 build tests pass unchanged. On HEAD's
code: `6 failed, 19 passed`. The three that pass hold under both rules.

**What still cannot be told:**
- a request by name for the very artist Kronk picked, on the same queue,
  with nothing else started between, inside 24 h. One artist row serves
  both, moved by every track. The coordinator expected this case kept; MA's
  records cannot give it. Only the album case works. (Kronk could log its
  own named plays and use them as starts, but most voice plays go through
  the blueprint, not Kronk. A design change, not made.);
- the sticky flag: a track the household once named, replayed by the pick,
  counts as the household's (one row; it no longer ends anything);
- a household track request made just before the pick, inside the 60 s
  lead: a user-initiated row there is kept, but album or artist tracks by
  the picked artist that ended in the lead are dropped (finding 6);
- a pick whose play took over 60 s to verify: its artist row falls before
  the lead;
- for a pick with no queue (ids 10 to 19), a start on any queue ends its
  window, so Kronk's tracks still playing on its own queue after a start
  elsewhere count;
- the household's start that MA does not mark (a skipped album's first
  track never earns the album credit);
- a row played again after the window counts again (MA keeps the last play
  only); picks older than the last 100.

**Cost.** On the real data: about 2,300 MA reads per build in 4.0 s
(the build's rule: about 2,100). Each reads `music/recently_played_items`
or `music/item_by_uri`; no new command.

## E. Finding 2 checked

The coordinator's reading was that the 21 plays are the picked Pandora
station logging each track or skip. MA's source and the records say no.

**MA's source** (`2.11.0b5-kronk.9`, read inside the container):
- `play_history` rows are written only by `_record_completed_play`
  (`controllers/music/controller.py:3569`), called from `mark_item_played`;
  artist credits do not write it.
- A station row is marked only:
  - when a play starts it (`queue_loader.py:964`, a dynamic source,
    `user_initiated=True`);
  - when a finite station resolves its tracks (`media_resolver.py:1163` →
    `:1359`);
  - when the tracker reports a queue item that is the station itself.
- A Pandora station plays through the managed pool (`managed_pool.py`),
  whose queue items are tracks. The tracker reports the track, flagged
  user-initiated only if the track itself was enqueued
  (`playback_tracker.py:888`). A track or skip does not write the station.

**The records** (a scratch copy of `library.db` and its WAL; counts and
times only):
- 21 `play_history` rows of `library://radio/263`, all `user_initiated =
  1`, on the queue of pick 13's speaker, between +417.6 s and +760.6 s after
  pick 13;
- three bursts, between Kronk's three playlist picks: 5 rows at
  +418..+439 s, 5 at +614..+649 s, 11 at +667..+761 s; 4 to 50 s apart.

**What started them.** tool_service's journal (journald, container
`kronk-tool_service-1`) holds, in the same span, 21 `/music` requests
answered `200 OK` that resolved to `library://radio/263`. Each falls
within 2 s of one MA row. Each comes with MA's own log of a flow-stream
restart and a refused `shuffle_set`. Each is a named play of the station
from the orchestrator (172.18.0.9). The sentence and the cadence are those
of the 2026-11-08 `play_music` argument bench
(`docs/incidents/INVESTIGATION_2026-11-07_ma_pandora_and_ui.md`, "play_music
argument bench"): ten runs of the same radio sentence before and after a
description change. Picks 13 to 16 came from the same run (genre picks of
the model's shortened queries).

So the rows are named plays: not Kronk picks, and not the household's
either. Fix 3 is about picks. A named play counts as every named play
does; the build cannot tell a bench run from a listener. Under rule D the
row is kept: the playlist pick at +383 s is a start, so pick 13's window
ends at +378 s, and the station's last play (+761 s) is after it. In the
recompute, pick 13 drops 0 rows; the station's `plays` is 0.015 again, as
before M23.

## F. Test order

Each new file (`test_music_enqueue.py:39`, `test_music_stripped.py:81`,
`test_music_taste_picks.py:26`) has an autouse fixture that clears
`ts._LAST_PICKS` and resets `FakeHA.playing`, `shuffle` and `title` after
each test.

| Order | HEAD (`83f0d72`) | Fix round 1 |
|---|---|---|
| `test_music_enqueue.py` then `test_music_resolve.py` | `1 failed, 57 passed` (`-x`) | `92 passed in 0.70s` |
| `test_music_stripped.py` then `test_music_resolve.py` | hangs (killed at 60 s) | `816 passed, 3 warnings in 2.86s` |
| `test_music_taste_picks.py` then `test_music_resolve.py` | not run | `95 passed in 0.56s` |

The older leak through `test_music_taste.py`'s `genre_env` (no cleanup on
teardown, finding 10's last sentence) is not touched: not a new file.

## G. Report wording; findings 6, 7, 8

**Finding 9, corrected.** The build report said the rebuild "rewrites the
artists and stations tables only". It writes:
- the `artists` and `stations` tables (deleted and rewritten);
- `tags` rows for artists it tags (the real tagger makes one local model
  call per 25 untagged artists);
- `meta.last_build`.

The first open of the DB after the deploy also adds `picks.queue`
(`ALTER TABLE`). It sends MA four read commands, nothing else:
`music/artists/library_items`, `music/item_by_uri`,
`music/recently_played_items`, `music/radios/library_items`. The coordinator
steps below carry this wording.

**Findings 6, 7 and 8:**
- 6 (the 60 s lead) and 7 (an empty answer read as no rows,
  `kronk_pick_items` the only sign): `docs/features/music-taste.md`, the
  "Kronk's own picks" entry;
- 8 (an explicit `replace` clears MA's enqueued-items list before the item
  resolves): `docs/features/voice-music-control.md`, "Every play replaces
  the queue".

No code for them: rule D does not make any of them free.

## H. The profile, recomputed

**Inputs:**
- a read-only `sqlite3` backup of `data/music_taste.db` and a copy of the
  taste file, in `/tmp/kronk-m23-fix1` (0700, files 0600);
- three trees: `ff8a7a5` (before M23), `83f0d72` (the build), the working
  tree (fix round 1);
- one script built each profile on its own copy of the DB, the tagger a
  stub (no model call).

**MA reads:**
- MA was read through an allowlist of the four read commands above, with a
  shared cache of the answers;
- the first fix-round-1 run read live (2,293 reads, build 4.0 s);
- the other runs replayed the cache and read live what it lacked (221
  each);
- the fix-round-1 result was the same on both of its runs.

Top ten (score with feedback, `taste.profile` order):

| # | Before M23 | M23 build | Fix round 1 |
|---|---|---|---|
| 1 | Cracker 4.10 | Pink Floyd 4.30 | Pink Floyd 4.30 |
| 2 | Pink Floyd 3.14 | Fatboy Slim 2.55 | Fatboy Slim 2.55 |
| 3 | Orbital 1.80 | Cracker 1.90 | Cracker 1.90 |
| 4 | Fatboy Slim 1.75 | Orbital 1.80 | Orbital 1.80 |
| 5 | Eels 1.48 | Eels 1.60 | Eels 1.59 |
| 6 | Wilco 1.46 | Wilco 1.56 | Wilco 1.56 |
| 7 | The Avalanches 1.42 | The Avalanches 1.49 | The Avalanches 1.49 |
| 8 | Weezer 1.35 | Aerosmith 1.44 | Aerosmith 1.44 |
| 9 | Kruder & Dorfmeister 1.30 | Weezer 1.38 | Weezer 1.38 |
| 10 | Massive Attack 1.30 | Portishead 1.31 | Portishead 1.31 |

`history_items 486, kronk_pick_items 14` (build: 485 and 15; before M23:
500).

Per pick (fix round 1; the window's end after the pick, and the rows left
out):

| Pick | What | Window ends | Rows left out |
|---|---|---|---|
| 19 | artist Cracker | +418 s | 1: the Cracker artist row |
| 18 | artist Cracker | +60,868 s (16.9 h) | 11: ten Cracker tracks and the artist row (also pick 19's) |
| 17 | artist | +4,592 s | 0 |
| 16 | playlist | +99 s | 1: the playlist |
| 15 | playlist | +204 s | 1: the playlist |
| 14 | playlist | +62 s | 1: the playlist |
| 13 | station | +378 s | 0: the station counts (E) |
| 12 | artist | +9,669 s | 0 |
| 11 | station | +2,340 s | 0 |
| 10 | artist | +16,158 s | 0 |

The set left out is the build's 15 rows minus pick 13's station row. Pick
18's window runs past the old 12 h, to the first start on any queue (ids
18 and 19 have no queue), but adds no row. Eels moves 1.60 → 1.59 because
the kept station row sits ahead of its plays in the decay order.

## Coordinator steps (replace the build report's "tool_service" and "Apply fix 3" steps)

Blueprint change 15: unchanged from `docs/sprint/M23-build.md` (fix round 1
does not touch the blueprint).

**tool_service (fixes 1 to 3 and this round).** They reach production only
through the next tagged deploy, from a tag at or after `d64ad4e`
(`scripts/deploy_tag.sh plan <tag>`, then `deploy <tag>`). Do not deploy
`5eb728b` alone: its migration breaks dev's read of the taste DB. A tag
between `e864798` and `d64ad4e` ships the refusals of finding 1.

**Apply fix 3 to the real profile (after the deploy).** The deploy alone
changes nothing in the DB until the next build at 03:30. To apply the
rule now:

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
kept. MA gets four read commands (`music/artists/library_items`,
`music/item_by_uri`, `music/recently_played_items`,
`music/radios/library_items`): about 2,300 reads on 2026-11-21, a few
seconds, plus the tagging model for untagged artists (~20 s with tags
cached).

**Undo:**
- **(a) the rule:** deploy the previous tag (`sprint-M22`), then
  `curl -s -X POST http://127.0.0.1:8003/music/taste/rebuild`. The `queue`
  column stays; the old code ignores it.
- **(b) the data:** copy `data/backups/music_taste.pre-m23.db` back over
  `data/music_taste.db`, with tool_service stopped or between writes. This
  loses any pick or feedback recorded after the copy, so prefer (a).

Unchanged from the build report.

## Docs

- `docs/features/music-taste.md`: genre requests step 1 (the turned-round
  guard, its list, the departures, the cost, the orchestrator wording);
  "Kronk's own picks" (the window, what is not a start, the cap, what it
  cannot tell, findings 6 and 7, the recompute).
- `docs/features/voice-music-control.md`: finding 8.
- The incident document: the failed end-to-end test and fix round 1, dated.
- `tests/voice_corpus.jsonl`: the corpus row's `xfail` text still holds
  (capture 1 is still refused); unchanged.
- `tool_service/main.py:1630-1633`: the guard's comment.

## Tests

`./scripts/run_tests.sh`:
- before: `5898 passed, 3 skipped, 54 xfailed`;
- after: `6545 passed, 3 skipped, 54 xfailed, 6 warnings in 84.62s (0:01:24)`.

647 more:
- 638 in `test_music_stripped.py`: 341 pick-as-before and 341 classifier
  cases replace 39 and 39; captures, cost, edges, wording, the exception;
- 9 in `test_music_taste_picks.py`.

They open no sockets (`no_network`; MA and HA are fakes).

## State

Before any request to dev: `MUSIC_DRY_RUN=1` in `kronk-tool_service-dev-1`;
its startup line `WARNING:tool_service:MUSIC_DRY_RUN is on: music requests
resolve but nothing is sent to a player or MA`; `/app/taste.py` and
`/app/main.py` sha256 equal to the working tree; `STRIPPED_REFUSAL` in
dev's `/app/agents.py`; HA states: 17 media players, 7 idle, 6
unavailable, 4 off, none playing, paused or buffering. After those
requests, `main.py` changed by one comment (the guard's); dev was not
rebuilt again.

| Container | Before | After |
|---|---|---|
| bgutil-pot-provider | 0e8f74842089 (Up 2 weeks) | same |
| homeassistant | 98f56e2549d0 (Up 3 days) | same |
| kronk-litellm-1 | f93873e3549c | same |
| kronk-nginx-1 | fc84987c3cbd | same |
| kronk-orchestrator-1 | 8715b86b10f4 | same |
| kronk-orchestrator-dev-1 | b9a4fe8ac137 | c42873907aa4 (rebuilt once) |
| kronk-searxng-1 | 06e462a11968 | same |
| kronk-telemetry-grafana-1 | 6f78f54c5348 | same |
| kronk-telemetry-tempo-1 | a44fa0d3f460 | same |
| kronk-tool_service-1 | 9f2e0c572204 | same |
| kronk-tool_service-dev-1 | 05a4f500af0a | 8baec689d972 (rebuilt once) |
| kronk-voice_tracer-1 | a7bc70aec2d4 | same |
| music-assistant-server | cf6b980f6630 (Up 2 weeks) | same |
| wyoming-piper | 5b97cc70b9ce | same |

- **Rebuild.** One `docker compose --profile dev up -d --build
  tool_service-dev orchestrator-dev`; neither service depends on a
  production one.
- **Production.** Tag `sprint-M22` before and after (`deploy_tag.sh
  status`).
- **Units and timers.** `systemctl --user list-units 'llama-*' 'wyoming-*'
  'kronk-*' --all`: identical. The same 14 timers. None started by me;
  `kronk-upgrade-notices` ran on its own hourly schedule at 11:02.
- **Taste data.** `data/music_taste.db` sha256 `0484fed3700c06cf…` and
  `data/music_taste.yaml` `bfc9b44786a8d86f…`, before and after.

**Requests made:**
- dev: eight dry-run `/music` requests (above); one `/api/chat` to dev's
  orchestrator. Each appended to dev's own shadow log, as a dev request
  does;
- HA: one `/api/states` read (token read inside the command, not printed);
- MA:
  - `docker exec` reads of its source;
  - `docker logs` for 2026-11-08 20:33–20:48 UTC;
  - `docker cp` of `library.db` and its WAL into scratch;
  - the recompute's allowlisted reads;
  - dev's search reads;
- journald: `kronk-tool_service-1` for the same span.

**Not done:** no HA or MA write, reload or restart; no pipeline run; no
sound; no notification; no production chat or music route; no backup,
upgrade or deploy script; no `pipeline_bench.sh`; no root.

## Scratch

`/tmp/kronk-m23-fix1` (0700) held:
- state lists;
- copies of the taste DB and file (0600);
- MA's `library.db` and WAL copy;
- `git archive` trees of `83f0d72` and `ff8a7a5`;
- the recompute script, its cache and outputs.

No symlinks. Removed at the end with `rm -rf /tmp/kronk-m23-fix1`.
