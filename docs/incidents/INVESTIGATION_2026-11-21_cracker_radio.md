# Investigation 2026-11-21 — "Kronk seems hung on playing 'cracker radio'": Fatboy Slim requests in the kitchen

Read-only investigation. Nothing was changed, played, paused or reloaded.
Times are EST, with UTC in parentheses.

## Symptom (as the operator saw it)

Drew, about 08:55:

> Kronk seems hung on playing 'cracker radio' and I don't know why. Multiple
> requests for fat boy either directly went to cracker radio or played the
> song, and then went directly back to playing cracker radio. I suspect it's
> something in MA. All interactions have happened in the past hour.

"Fat boy" is Fatboy Slim. "Cracker radio" is not the Pandora station
"Cracker Radio" (`library://radio/298`), which did not play today. It is
Music Assistant playing the artist Cracker's own library tracks, shuffled, in
the kitchen.

## Short answer

Two faults, and they compound.

1. **The first request turned into "play music", and "play music" means
   "Cracker".** Whisper heard "Play right here, right now, bud. That boy's
   slow." The messy-transcript gate (M14, two sentences) sent it to the
   coordinator, which delegated `ask_home("play music in the Kitchen")` and
   dropped the song title. The home agent called `play_music(query="music")`.
   tool_service reads a bare "music" as a genre request with no genre, and
   that plays the top of the household taste profile. The top artist is
   Cracker, at 4.60 against 2.78 for the next one, because Kronk's own
   Cracker pick yesterday (20:53 UTC, from "Play back in the Kitchen", the
   same shape) filled Music Assistant's play history with Cracker.
2. **A track play keeps the rest of the queue.** Neither tool_service nor the
   blueprint passes `enqueue` to `music_assistant.play_media`. Music
   Assistant's default enqueue option for a **track** is `play`: insert at
   the current position, start it, and keep everything after it. Albums,
   artists, playlists and radio default to `replace`. So "Right Here, Right
   Now" (Kronk, 08:41) and "The Rockafeller Skank" (blueprint, 08:48) each
   played once, and then the shuffled Cracker queue picked up where it had
   been. Drew's fourth request, at 08:58, resolved to the **album** (single)
   "Praise You", which replaced the queue. That ended the loop by accident.

Music Assistant did what it is configured to do. Its default is upstream
behaviour and by design, not a defect in our derived image. The fault is
Kronk's: both play paths rely on a default they never state. The sprint work
this morning touched no player and no queue (hypothesis 7).

## Setup at the time

- Satellite: kitchen Voice PE, ESPHome device
  `e0949788663255952ce7778975527882`, MA player
  `media_player.home_assistant_voice_0ac919_pe_01` ("kitchen voice pe ma",
  queue `up20f83b0ac919`, area Kitchen). All four requests came from it.
- Pipeline: production `kronk`, HA's Assist, Whisper, then the blueprint fork
  `kronk_mass_voice_device_first`. The **installed** blueprint is version
  `20250404-kronk13`; change 14 (M22) is in the repo only.
- Kronk: `scripts/deploy_tag.sh status` reports production orchestrator and
  tool_service on tag `sprint-M16`, created 43 hours before. tool_service
  `MUSIC_SHADOW=1`, no `MUSIC_DRY_RUN`. `TELEMETRY_RECORD_TEXT` is on, so
  transcripts and Kronk's input and output are on the spans.
- Music Assistant: `kronk/music-assistant:2.11.0b5-kronk.9`, up 2 weeks.
  Kitchen queue now: `dont_stop_the_music_enabled false`,
  `autoplay_enabled false`, `is_dynamic false`, `radio_source []`.
  `voice-music-control.md` says the kitchen queue has Don't stop the music
  **on** (set 2026-09-16). It is off now. Whether MA dropped the setting on a
  restart or someone turned it off is not known. Either way it played no part
  here.
- Before 08:40 the kitchen queue already held the shuffled Cracker artist
  queue from 2026-11-20 20:53 UTC (taste pick 18). HA history shows the
  player `idle` on Cracker "I Could Be Wrong, I Could Be Right"
  (`library://track/4625`, shuffle on) from 10:00 UTC (the start of the
  query) until 13:40:56 UTC.

## Timeline (2026-11-21, EST; UTC in parentheses)

**Run 1**: voice run `01M7Z5D047ZRJCE3NHSS64PFXA`, voice trace
`237631ec3c5aa97df950cf077d0e46ba`, Kronk trace
`dbe42d6a5dbb100aa95438fafd5ebaad` (rid `bf1720a0`). Its blueprint trace has
rotated out of HA's store of five; voice_tracer logged it as
`81e9a022f97bcf5051982ad8b7a5eb00: trigger=catchall outcome=handed_to_kronk 8.59s`.

- 08:40:44 (13:40:44): run start.
- 08:40:49.38: STT `Play right here, right now, bud.  That boy's slow.`.
  Only the catch-all matched (no type word). Hand-off text:
  `Play right here, right now, bud.  That boy's slow. in the Kitchen`.
- 08:40:49.39: rule `messy` (`reason sentences`, 2 sentences, limit 1), so
  the request went to the coordinator.
- 08:40:51.62: coordinator called `ask_home` `{"query": "play music in the Kitchen"}`.
  The title was gone.
- 08:40:56.59: home agent called `play_music` `{"player": "Kitchen", "query": "music"}`.
- 08:40:56.61 (tool_service): `genre request 'music' -> terms [] rule None:
  1559 picks, first [Pick(artist 'Cracker' 4.60), Pick(artist 'Pink Floyd'
  2.78), Pick(artist 'Orbital' 1.80), Pick(artist 'Eels' 1.44)]`. Shuffle was
  set before the play. `play_media` `{"media_id": "library://artist/639",
  "media_type": "artist"}` went out, with no enqueue, so the artist default
  `replace` applied.
- 08:40:56.63 (MA): `Fetching tracks to play for artist Cracker (selection:
  prefer_library, order: shuffled_tracks)`. Kitchen `playing` "Hey Bret (You
  Know What Time It Is)", Cracker, at 08:40:56.95.
- 08:40:57.96: spoken `Now playing Cracker on the Kitchen speaker.`
- 08:40:57 → 08:40:59: the shadow read it as named `right here, right now,
  bud. That boy's slow`, `not_found`, closest "Right Here, Right Now" by
  Fatboy Slim (0.74), `model_would_ask true`. MA reads only (library_items
  and search).

**Run 2**: voice run `01M7Z5DTHQKDGDCHEVEKKJC5ZK`, voice trace
`a6afe724faf71595e83c7c8f6f1d9a55`, Kronk trace
`a2ec88f8bc872bbf9ab16b1fd1147967` (rid `20618548`). Blueprint:
`f9b895ec…` ran the catch-all (handed to Kronk, 10.12 s). Run
`70d6637b66ba1278757f1265b40c4b24` was the `track` trigger (`trigger/1`) for
the same sentence, dropped with `failed_single` ("Already running").

- 08:41:09.03 (13:41:09): the satellite's ESPHome media player went `idle`,
  and the MA player 0.1 s later. MA logged `Clearing active output protocol`.
  The satellite went `listening` at 08:41:11.35. This is the wake for run 2.
  No HA service call and no user context. At 08:41:17.97 the taste watcher
  scored it as a stop: `taste pick 19 stopped → artist:cracker -0.50`.
- 08:41:16.88: STT `Play the song right here, right now by Fat Boys Lynn.`
  Hand-off `… Fat Boys Lynn. in the Kitchen`, rule `music`, home agent.
- 08:41:21.64: `play_music` with `kind, media_type, player, query`. tool_service:
  `query='Right Here, Right Now by Fat Boys' media_type='track' player='the Kitchen'`,
  resolved to `ytmusic--kGmHoQDn://track/vqNIeInr9kA` "Right Here, Right
  Now" by Fatboy Slim (0.96). `play_media` `{"media_id": <uri>, "media_type":
  "track"}` went out with no enqueue, so the track default `play` applied.
- 08:41:25.99: kitchen `playing` "Right Here, Right Now", Fatboy Slim. Shuffle
  went off at 08:41:22 (tool_service's `shuffle_set false` before a named
  play). Spoken `Now playing Right Here, Right Now by Fatboy Slim on the
  Kitchen speaker.` The shadow agreed (same item).
- **08:47:55 (13:47:55)**: the track ended and the queue went on to Cracker
  "Show Me How This Thing Works" (`library://track/4616`, the same album as
  the Cracker tracks before). No request and no service call: the rest of
  the queue.

**Run 3**: voice run `01M7Z5V1FP6FJBH8HKMP9GXHRZ`, voice trace
`ce7377fa67e7b3570937c0a43cdc62ab`, blueprint run
`76af1149acd149ac28ff5bd8829d87f5` (Tempo trace
`6fcd4a86bc7863b2d42dfb7b238f6990`). The catch-all
`cfe28cd137e12bf2afec45b959aea44f` (`trigger/7`) was dropped. Kronk was not
involved.

- 08:48:29: STT `Play the song Rockefeller Skank by Fatboy Sled.`
- 08:48:30.26: `track` trigger won. Target by device: the kitchen player,
  which was then `playing` Cracker "Show Me How This Thing Works". Blueprint
  `play_media` `{"media_id": "Rockefeller Skank by Fatboy Sled", "media_type":
  "track"}`, no enqueue, so `play`.
- 08:48:34.31 (MA): `Start Queue Flow stream`. Kitchen `playing` "The
  Rockafeller Skank", Fatboy Slim (`ytmusic--kGmHoQDn://track/wD6jCMn5nq0`).
  Spoken `Rockefeller Skank by Fatboy Sled playing in the Kitchen`.
- **08:55:29 (13:55:29)**: the track ended and the queue went on to Cracker
  "We All Shine A Light" (`library://track/4617`).
- 08:55:47.54: the ESPHome media player went `idle`, then the MA player
  (08:55:47.66). MA logged `Clearing active output protocol on kitchen voice
  pe ma`. HA's logbook shows no context user, no service call and no voice
  run near it. This was a stop from outside HA and Kronk: the puck's button
  or a Music Assistant client are the candidates. Not determined.
- 08:56:25 → 08:56:30: the sprint test agent's dry-run requests on dev,
  after it had waited for the kitchen to stop (below). Nothing was sent.

**Run 4**: voice run `01M7Z6CTDHFY3NPVX4Q55EGX15`, voice trace
`ba098dbfe6998311a81cccb3dbe9e26d`, Kronk trace
`cecbc50b069230a637fa768a6cd3426c` (rid `98064d74`). Blueprint run
`bb32ca9a512c7421dab806b2d448eafd` ran the catch-all. The `track` run
`8cab90d93d61a1bffe393f0d108a0fae` was dropped. This came after Drew's
message.

- 08:58:10: STT `Play the song Praise You by Fatboy Slim.` Hand-off
  `… Fatboy Slim. in the Kitchen`, rule `music`.
- 08:58:14.41: `play_music` `{"player", "query"}` with no media type.
  tool_service: `query='Praise You by Fatboy Slim' media_type=None`, which
  resolved to the **album** `ytmusic--kGmHoQDn://album/MPREb_dnX556Is13h`
  "Praise You" (1.01). `play_media` with `media_type album`, no enqueue, so
  `replace`. The shadow would have played the track (`…/track/wXaRyNgIMCw`),
  so `agree false, different item`.
- 08:58:21.06: kitchen `playing` "Praise You". Spoken `Now playing Praise You
  by Fatboy Slim on the Kitchen speaker.`

**State at 09:02 (14:02), read-only**: kitchen queue `playing`, 3 items, the
single's three tracks (Praise You, Sho Nuff, The Rockafeller Skank
(Mulder's Urban Takeover Remix)). No Cracker items left. Shuffle off,
autoplay off, Don't stop the music off, not dynamic. When the single ends
the kitchen stops. Basement (Aerosmith queue, 14 items), satellite 1 (4
items) and every other queue were `idle`. Nothing was touched. The defect
is still live: the next "play the song X" on a player with a queue inserts X
and resumes that queue.

## The path

1. **HA**: wake on the satellite, then Whisper, then the blueprint's sentence
   triggers. "Play the song X by Y." matches both the `track` trigger
   (`trigger/1`) and the catch-all (`trigger/7`, change 12). Both fire. The
   automation is `mode: single`, so whichever starts first runs and the other
   is dropped with "Already running". The catch-all won runs 2 and 4, and the
   `track` trigger won run 3.
2. **Blueprint, typed play** (run 3): `music_assistant.play_media` with
   `media_id`, `media_type` and the target, and no `enqueue`.
3. **Blueprint, hand-off** (runs 1, 2, 4): `conversation.process` with
   `<sentence> in the Kitchen`.
4. **Kronk**: the `music` rule goes to the home agent and `play_music`. A
   transcript of more than one sentence (run 1) hits rule `messy`, which goes
   to the coordinator, then `ask_home(query)`, the home agent and
   `play_music`. The coordinator's rewrite is free text, and on run 1 it
   kept only "play music".
5. **tool_service `/music`**:
   - Generic query (`GENERIC_WORDS` in `tool_service/taste.py:583`:
     `"", "music", "something", …`): the phrase cleans to `''`, so a genre
     request with no terms. `taste.py:709`, `if not terms:` gives "the top of
     the profile". One of the top three plays, weighted by score, and Cracker
     led by 1.8.
   - Named: the resolver, then `_play_verified` and `play_media`
     `{"media_id", "media_type"}` (`tool_service/main.py`, `service_data`
     built at `:1587` in the release, the genre picks in
     `taste.Pick.play_data`). No `enqueue` anywhere in tool_service, the
     orchestrator or the blueprint (`grep -rn enqueue` finds only a comment).
6. **Music Assistant**: `queue_loader.py`. With `option is None` it reads the
   queue's `default_enqueue_option_<media_type>`. The defaults
   (`controllers/player_queues/config.py:245-250` in the running image) are
   artist `replace`, album `replace`, **track `play`**, genre `replace`, live
   sources `replace`, playlist `replace`. `QueueOption.PLAY` "Insert new
   item(s) in queue at the current position and start playing". The items
   after the current one stay. The derived image patches only Pandora
   (thumbs, create-station: `docker-compose.ma.yml:53`), not this.

## Hypotheses tried

1. **(a) A Pandora station, radio mode or Don't stop the music refilling the
   queue.** Rejected. The kitchen queue is not dynamic, `radio_source` is
   empty, autoplay and Don't stop the music are off, and no `radio_mode` was
   sent on any of the four plays. The Cracker tracks that came back are
   `library://track/4616` and `4617`, from the same album as the Cracker
   tracks queued at 08:40 by `Fetching tracks to play for artist Cracker
   (… shuffled_tracks)`. They are the rest of the queue, not new picks. MA
   loaded nothing into the queue between the four plays.
2. **(b) The track was put into the existing queue instead of replacing
   it.** **Confirmed.** It happened twice (08:41 by Kronk and 08:48 by the
   blueprint). Each time the track played to its end and Cracker followed
   with no request in between (08:47:55, 08:55:29). The fourth request
   replaced the queue only because it resolved to an album.
3. **(c) The request resolved to the station ("X radio", change 11, the
   Pandora upsert, the resolver, genre picks).** Partly. No radio was played
   or looked up: the station "Cracker Radio" (`library://radio/298`) exists
   in the library and did not play. But the **genre pick** chose Cracker on
   run 1, because the request reached tool_service as the bare word "music".
4. **(d) An STT mishearing.** True, and it set this off, but it is not the
   cause. "Fatboy Slim" was heard as "bud. That boy's slow", "Fat Boys Lynn"
   and "Fatboy Sled". The resolver fixed the last two. The first produced a
   second sentence, so the messy gate fired, and the coordinator dropped the
   title. The resolver would have found it (the shadow's closest match was
   the right track at 0.74).
5. **(e) A different player from the one playing the station.** Rejected.
   All four plays targeted `media_player.home_assistant_voice_0ac919_pe_01`,
   the player playing Cracker.
6. **(f) One Pandora account, one stream.** Rejected. Nothing played from
   Pandora today. Cracker came from the NAS library, Fatboy Slim from
   YouTube Music.
7. **(g) This morning's sprint work.** Rejected, with evidence:
   - The dev pair now running was created at 06:55:04 and 06:56:11
     (11:55–11:56 UTC). `kronk-tool_service-dev-1` has `MUSIC_DRY_RUN=1` in
     its environment and logged `MUSIC_DRY_RUN is on: music requests resolve
     but nothing is sent to a player or MA` at 06:56:14. Commits:
     `f381a2d` 06:42, `eb68da3` 06:49, `7d18c68` 06:59, `aa94a12` 07:02,
     `754b72e`/`f7b169d` 08:37, `daf75d3` 08:57, `f7d26a4` 09:00.
   - Every outbound HA request in the dev tool_service log for the last four
     hours: 16 `POST /api/template` (renders) and 1 `GET /api/states/…` (a
     now-playing read). No service call. Its `/music` requests ran at 06:56:23
     (2) and 08:56:25–08:56:30 (13). Each played answer reads `Dry run: would
     play … Nothing was sent to the speaker.` The orchestrator-dev logged no
     request in the window.
   - Music Assistant logged playback only at 08:40:56 (artist Cracker),
     08:41:25, 08:48:34 and 08:58:16–20, one for each production play above.
     Its other lines in the window are websocket logins (`authenticated as
     drew`) at 06:52–07:01, 08:40:57, 08:41:21–27, 08:56:25–30 and 08:58:14–22,
     which match tool_service and the dev reads, plus the daily YouTube Music
     provider reload at 07:01:33 (also at 07:02:19 on 11-20).
   - HA history: the kitchen player sat `idle` on the same Cracker track from
     at least 05:00 (the start of the query) until 08:40:56. Nothing moved it during the 06:5x work.
   - The test agent's dev requests came at 08:56:25, 38 seconds after the
     kitchen stopped at 08:55:47, which fits its report of waiting for the
     player to stop. Its 30 s polls were state reads. The stop itself carries
     no HA context and no service call, and no dev log line or MA command
     lines up with it.
   - HA logged rejected loopback logins in the window (`/api/websocket` at
     06:45, 06:49, 06:54, 06:58, 08:36, 08:40, 08:59; `/api/template` from
     `Python-urllib` at 08:44). They were refused, so they could do nothing.
     Their source was not traced.
   - Production shadow (`data/music_shadow.jsonl`, 3 records today, one per
     Kronk request): its spans show only MA `music/search` and
     `…/library_items`, and `music_shadow.py` holds only `ma_read`, which
     refuses any other command. It played nothing.
8. **Music Assistant itself ("something in MA").** Rejected as a defect. MA
   followed its configured default. This is a Kronk fault: it relies on a
   default it never states.

## Root cause

1. **Kronk and the blueprint never say how to enqueue.** Every
   `music_assistant.play_media` from tool_service and the blueprint leaves
   `enqueue` unset. Music Assistant's per-queue default for tracks is `play`,
   which keeps the rest of the queue. A spoken "play the song X" therefore
   means "play X, then go back to whatever was there". Everything else
   defaults to `replace`, which is why albums, artists, playlists and
   stations never showed it.
2. **A request with its subject stripped out plays the household's top
   artist.** When `play_music` arrives with `query="music"` (or any
   `GENERIC_WORDS` term) and no genre, tool_service treats it as "play some
   music" and picks from the top of the taste profile. It does not check the
   user's own sentence (`origin.said`, already on the request), which here
   held a recoverable title. On run 1 the coordinator's rewrite stripped it.
3. **The pick feeds itself.** Kronk's own Cracker pick on 2026-11-20 played
   for hours. MA's play history (500 items, recency-weighted) is the
   strongest input to the profile, rebuilt at 03:30. Cracker's history
   weight is 15.1 against 7.4 for Pink Floyd. The next empty pick was Cracker
   again (3.40 on 11-20, 4.60 today).

(1) made the problem stick: every fix attempt by name played once and gave
the room back to Cracker. (2) and (3) are why the room was on Cracker at all.

## Why each request did what it did

| Run | Heard | Tier | What was asked of MA | Result |
|---|---|---|---|---|
| 1 08:40 | "…right here, right now, bud. That boy's slow." | catch-all, then Kronk (messy gate, coordinator) | artist Cracker, shuffled, replace | Cracker; "directly went to cracker radio" |
| 2 08:41 | "…right here, right now by Fat Boys Lynn." | catch-all (won the race), then Kronk | track Right Here, Right Now, `play` | played, then Cracker at 08:47:55 |
| 3 08:48 | "…Rockefeller Skank by Fatboy Sled." | blueprint `track` | track by name, `play` | played, then Cracker at 08:55:29 |
| 4 08:58 | "Play the song Praise You by Fatboy Slim." | catch-all (won the race), then Kronk | album Praise You, `replace` | Cracker gone; single plays out |

## How often (the stopping rule)

Sources: Tempo (since 2026-11-08), HA recorder history (kitchen from
2026-11-11, satellites from 2026-11-16), journald for tool_service and HA
(since 2026-10-19 and 2026-10-01), the taste DB's `picks`, and the shadow
log.

- **Track play into a live queue, then resume.** Started track plays through
  tool_service in Tempo: 2 (2026-11-11 11:38 UTC, trace
  `44fd6389d76adaf5bc5b1bf20f9dbf4e`, onto an **empty** kitchen queue, idle
  after it; and today's run 2). Blueprint `track` plays that played: 1
  (today's run 3). So 2 of 2 track plays onto a non-empty queue resumed the
  old queue, both today. The defect is in every track play. It only shows
  when something else is already queued, and that is rare because most
  household plays are albums, artists and stations, which replace.
- **Empty genre request: top of profile.** journald since 2026-10-19 has
  `genre request 'music' -> terms []` twice: 2026-11-20 20:53 UTC ("Play
  back in the Kitchen", Cracker 3.40) and today's run 1 (Cracker 4.60). The
  taste DB has two empty-phrase picks, ids 18 and 19, both Cracker.
- **Double trigger race (track vs catch-all).** HA logged the blueprint's
  "Already running" 21 times since 2026-10-01 (10 since HA's start on
  2026-11-18: 3 on 11-19, 4 on 11-20, 3 today). The traces kept today show
  the catch-all winning 2 of 3.

The stopping rule is about new special-case routing rules. None of the fixes
below is one.

## What would have caught it sooner

- **Asserting the queue, not just the state.** The play verifier (tenet 6)
  checks that the player starts something new, and it did. It cannot see
  that the old queue is still behind the new item. A test that pins
  `play_media`'s service data (it has a `_LIVE_SENT` hook) would have shown
  `enqueue` missing.
- **The corpus cannot see it.** `voice_replay.py` and the live pin check what
  would play, not what follows, and the dry run sends nothing to a queue.
- **A spoken pick that names an artist nobody asked for.** "Now playing
  Cracker" for a sentence holding "right here, right now" is a mismatch
  between the reply and the request. M12's wrong-action marks did not fire:
  the next request came 19 s later (inside the 60 s window) but was not a
  near repeat of the first.

## Proposed fixes, ranked

1. **Say `enqueue: replace` on every play (Kronk and blueprint).** tool_service:
   add `"enqueue": "replace"` to the named play's service data and to
   `Pick.play_data()`, and pin it in `tests/test_music_players.py` (or
   wherever `_LIVE_SENT` is checked). Blueprint: change 15, `enqueue:
   replace` on its `play_media`. That needs a `docker cp` and an automation
   reload, but **no HA restart**. Effect: "play X" means X, then silence (or
   whatever X's own queue holds), in both tiers, on every player including
   ones added later. Cost: a few lines and a test, plus the blueprint
   install. A Kronk change and a blueprint change. Admitted (a bug fix, not
   a routing rule).
   *Alternative, no code:* set "Default enqueue option for tracks" to
   `replace` in Music Assistant's per-player settings. It covers HA's
   built-in media intents too, but it is a Music Assistant settings change
   (operator only), per player, invisible in the repo, and every new player
   starts on `play` again. Not recommended as the fix. Acceptable as a
   stopgap for the kitchen until 1 ships. Not upstream: MA's default is a
   deliberate choice (append-and-play suits its UI), not a bug.
2. **Refuse a generic query when the sentence says more.** In `/music`,
   when the model's query cleans to no terms but `origin.said` holds words
   beyond a request verb and a generic word ("play", "some", "music"), do
   not pick from the profile. Answer "I didn't catch what to play" and name
   the closest match the resolver finds, which the shadow already computes
   ("Right Here, Right Now" by Fatboy Slim). Structural (tenet 5): the model
   cannot strip the subject and get a random artist for it. Cost: a small
   tool_service change and tests, with the two captures as cases. A Kronk
   change. Admitted (2 captures, a guard, not a routing rule). M11 cutover
   retires it, since `/music/say` reads the sentence and answered
   `not_found` with the right closest match on run 1.
3. **Damp the taste loop.** Kronk's own genre picks feed MA's play history
   and then the profile, so the empty pick converges on one artist. Options:
   exclude history from Kronk-picked sessions, or flatten the weighting for
   the no-phrase pick. Cost: taste.py and tests. One artist, two picks: an
   observation, not yet a roadmap item.
4. **The track/catch-all race.** A typed sentence that both triggers match
   runs whichever HA fires first. Today 2 of 3 "Play the song …" went to
   Kronk (about 10 s) instead of the 4 s blueprint path. It is not part of
   this incident's cause, since both paths sent `play`. The fix is in the
   blueprint (the catch-all's action steps aside when a typed trigger also
   matched the sentence, or the catch-all moves to its own automation so
   both don't share `mode: single`). Needs its own test; 21 occurrences in
   journald. Blueprint change. For Drew to decide whether it is worth a
   roadmap line.
5. **The taste watcher scored a wake as a stop.** At 08:41:09 the satellite
   cut its stream for the wake, and the watcher recorded `stopped -0.5` for
   Cracker. That was right by accident here. A stop inside a voice run on
   the same satellite is not feedback. Small, and kept in this doc only.

Not proposed: a vocabulary prompt for "Fatboy Slim" (three different
mishearings, two fixed by the resolver); a routing rule for the messy gate
(the gate did its job; the rewrite dropped the title, and fix 2 makes that
harmless).

Doc drift found in passing: `voice-music-control.md` says the kitchen queue
has Don't stop the music on. The live queue says off. The feature-doc line
should be corrected with fix 1.

## Corpus row (added with fix 2, sprint M23)

Added as `tests/voice_corpus.jsonl` row `inc-1121-fatboy-slim-heard-as-two-sentences`
with `xfail.live` (the refusal, until the parser or cutover plays the
track) and `xfail.parser` (the rules' reading is not_found at 0.74). As
first proposed:

```json
{"id": "inc-1121-fatboy-slim-heard-as-two-sentences", "text": "Play right here, right now, bud.  That boy's slow.", "origin": {"area": "Kitchen", "device": "kitchen_pe"}, "expect": {"tier": "ha_local", "ha": {"source": "trigger", "trigger": "catchall"}, "route": "direct", "rule": "messy", "tool": "play_music", "kind": "named", "media_type": "track", "name": "Right Here, Right Now", "artist": "Fatboy Slim", "player": "origin"}, "source": ["docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md"], "tags": ["music", "track", "mishearing", "stt-mangled", "incident", "messy", "handoff"], "xfail": {"live": "The coordinator delegates 'play music in the Kitchen' and /music plays the top of the taste profile (artist Cracker); fix 2 turns that into a refusal naming the closest match. Remove when the parser or cutover plays the track."}, "note": "STT for 'Play Right Here, Right Now by Fatboy Slim'. Two sentences, so the messy gate sends it to the coordinator, which dropped the title. The rules' reading (M11) is not_found with the closest 'Right Here, Right Now' by Fatboy Slim at 0.74.", "ref": {"trace": "dbe42d6a5dbb100aa95438fafd5ebaad", "first": "2026-11-21"}}
```

Fix 1 needs a unit test on the service data, not a corpus row. The corpus
checks what plays, not what is left in the queue.

## Fixes (sprint M23)

Criteria: `docs/sprint/M23-criteria.md`; build report: `docs/sprint/M23-build.md`.

- **2026-11-21, fix 1 built (repo only).** tool_service sends `enqueue:
  replace` on every play (`music_resolve.PLAY_ENQUEUE`: the named play, the
  literal fallback, stations, links, every pick, "not that"); blueprint fork
  change 15 (`20250404-kronk15`) puts it in `action_data`. Checked against
  the running images: HA's `play_media` schema takes `enqueue` and coerces
  it to MA's `QueueOption`; MA's `replace` path is the one every non-track
  type already took by default, and no queue overrides a default. Pinned by
  `tests/test_music_enqueue.py` (20 of its 22 tests fail on the old code).
  Not yet deployed; the blueprint is not installed.
- **2026-11-21, fix 2 built (repo only).** `/music` reads the user's
  sentence when the query has no phrase: a sentence whose every request
  clause names something is refused with "I didn't catch what to play."
  and the closest match, and nothing plays (`taste.said_request`,
  `main._stripped_request`). The sentence is there on every Kronk path:
  `origin.said` is set once per turn in `_run_pipeline` and the coordinator
  → `ask_home` → home agent chain runs inside it, so the rewrite on run 1
  did not lose it (`test_said_reaches_tool_service_through_the_coordinator_rewrite`,
  every mount). The blueprint's hand-off arrives on `/api/chat` with its
  room suffix, which the check does not read. `/music/voice` and a shim
  turn with no words of its own carry no sentence and pick as before.
  "Play some music" and 38 other generic forms pick exactly as before
  (Drew's condition). Both captures (run 1 and "Play back in the Kitchen",
  2026-11-20) are refused in the tests.
- **2026-11-21, fix 3 built (repo only).** The nightly build leaves Kronk's
  own picks out of the history it learns from (`taste.kronk_pick_rows`):
  per pick, MA is asked for the rows last played on the pick's queue in
  the 12 h after it and for the ones the household started; the picked
  artist's rows (or the picked station or playlist) that the household did
  not start are dropped. Picks now record their queue (`picks.queue`);
  ids 18 and 19 have none and are read on every queue. Recomputed in
  scratch on a copy of the real taste DB and MA's history: Cracker 4.10
  (top) → 1.90 (third), Pink Floyd 2nd → top. The real DB is not rewritten
  until the coordinator deploys and rebuilds (`docs/sprint/M23-build.md`).
  Fix 5 (a wake scored as a stop) does not fall out of this; left as is.
- **2026-11-21, end-to-end test failed** (`docs/sprint/M23-e2e.md`): fix 2
  refused 55 of the tester's 305 generic sentences ("play some music,
  thank you", "… tonight", "… buddy"); fix 3 dropped a station row that
  was asked for by name after its pick.
- **2026-11-21, fix round 1 (repo only)** (`docs/sprint/M23-fix1.md`).
  Fix 2 turned round: a sentence is refused only when it holds no word
  meaning music in general (music…, something, songs, tunes, playlist,
  radio, again, …; "it" or "that" right after the verb). 341 of 342
  generic sentences pick exactly as before ("Play some muse sick." stays
  refused); both captures stay refused. Cost: a stripped request whose
  sentence also holds such a word ("play the song right here right now")
  gets the pre-M23 pick. Through the orchestrator the refusal is said
  once. Fix 3's window now ends at the first play started on the queue
  after the pick (24 h at most) instead of a fixed 12 h; a named album of
  the picked artist after the pick counts with its tracks, and the
  station of pick 13 counts: its 21 plays on 2026-11-08 match, second for
  second, Kronk named plays of that station logged by tool_service.
  Recomputed: 14 rows left out instead of 15; Cracker 1.90 (third), as
  before.
- **2026-11-21, second end-to-end test failed** (`docs/sprint/M23-e2e2.md`):
  fix 2 refused "play whatever's good" (a listed word with `'s`) and, of
  the tester's 314 generic sentences, 107 in all ("play what I like").
- **2026-11-21, fix round 2 (repo only)** (`docs/sprint/M23-fix2.md`).
  Fix 2 ships off, per Drew's condition ("If we need to NOT implement Fix
  2, then so be it"): `music.yaml` `stripped_request.refuse`, false in the
  shipped defaults and when the key is absent. Off, `/music` does not read
  the sentence: all 659 test sentences (342, the captures, the second
  tester's 314) pick exactly as with none. So the incident's sentence is
  picked from the profile again; since fix 3 that is the household's
  taste, and since fix 1 the pick replaces the queue. Two repairs for when
  it is turned on: a listed word with an apostrophe suffix counts, and a
  room at the end is dropped before the bare-verb and filler rules (104 of
  the 314 still refused with it on). Fix 3: a start counts from 5 s after
  the pick (`PICK_START_MARGIN_S`), so the household track the pick cut
  short no longer closes the window at once; no such row in the real
  data, so the recomputed profile is unchanged. The corpus row's
  `xfail.live` now says the live answer is a pick.

## Blog hooks

- "Play the song" means "play the song, then go back": a default nobody set,
  in a system with three callers, none of which says what it wants.
- The verifier said yes. Tenet 6 checked that the right thing started and
  had no way to see what was still waiting behind it.
- A household DJ that learns from itself: one bad pick, hours of listening,
  and the profile votes for the same artist next time.
