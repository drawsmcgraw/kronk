# Investigation 2026-11-08 — "Play anime pop radio": 63 s of silence, then Backstreet Boys

**Status:** fixed 2026-11-08 (blueprint change 12 + station-name parser).
Not caused by any of the 2026-11-08 radio changes.

## Symptom (as the operator saw it)

Asked the kitchen Voice PE for "play anime pop radio". "Kronk seems hung":
the satellite sat in its thinking state for about a minute, then answered
"Playing media" and started Backstreet Boys, not the Anime Pop station.

## Timeline (2026-11-08, EST; UTC in parentheses)

- 16:11:57.754 (21:11:57) — wake word on the kitchen satellite
  (`assist_satellite.home_assistant_voice_0ac919_assist_satellite`), pipeline
  "kronk" (`01ksc2pw5bkhtk37dh2d6zwcxp`, conversation engine
  `conversation.ollama_conversation`, `prefer_local_intents: true`), pipeline
  run `01M6YG1W1STYKNT41DF2377DH3`, conversation `01M6YG1W1SEDTY78SCESY5AEMS`.
- 16:11:58.787 — VAD start (1050 ms).
- 16:12:07.536 — VAD end at 9800 ms. Speech kept going after the request,
  so the capture ran ~9.8 s.
- 16:12:07.998 — STT (`wyoming-whisper`, "Processing audio with duration
  00:09.800") returned:
  `" Play Anime Pop Radio.  That's probably about it. Everything is pretty wet.  I just needed to make some space."`
  Satellite → `processing`.
- 16:12:07.998 — intent stage starts. No sentence trigger of
  `automation.kronk_music_assistant_voice_device_first` matched (its
  `last_triggered` is still 20:16:07 UTC, trace `1b579b7ecb99189768e934be17184505`,
  from the afternoon bench). Because `prefer_local_intents` is on, HA's
  built-in `HassMediaSearchAndPlay` intent (`play {search_query}`) took the
  whole transcript as the search query.
- 16:12:09.980 — MA: `Fetching tracks to play for artist Backstreet Boys
  (selection: all_tracks, order: shuffled_tracks)`. The intent had searched,
  taken the first result (YouTube Music artist
  `ytmusic--kGmHoQDn://artist/UCQsJi7L4_zXzYRN2X0Fw3Zg`, "Backstreet Boys")
  and called `media_player.play_media` on
  `media_player.home_assistant_voice_0ac919_pe_01` with `blocking=True`.
- 16:12:10 → 16:13:10 — nothing logged. MA was collecting every track of a
  non-library artist across providers (`all_tracks` is forced for artists not
  in the library — `media_resolver.resolve_artist_tracks`). The queue ended
  up with 157 items.
- 16:13:10.492 — first yt-dlp stream resolution (bgutil PO-token warning).
- 16:13:11.235 — MA: `Start Queue Flow stream for Queue kitchen voice pe ma`.
- 16:13:11.431 — `media_player.home_assistant_voice_0ac919_pe_01` → playing
  ("The Answer to Our Life" — Backstreet Boys).
- 16:13:11.434 — intent-end, `processed_locally: true`, speech "Playing
  media"; TTS (piper); satellite → `responding`, → `idle` at 16:13:12.4.
- 16:14:43.4 — kitchen player → idle (stopped; HA logbook carries no
  user context for the stop).

Intent stage duration: 63.4 s, of which ~61 s was MA's artist track fetch
behind a blocking `play_media`.

## Where it did not go

- **Kronk:** no orchestrator `request` event after 20:46:59 UTC (end of the
  `play_music` bench); nginx shows no `/api/chat` or `/voice/api/chat` after
  20:46:59 UTC; tool_service has no `music request` line for it. Kronk was
  never called. Nothing for Langfuse to show.
- **Blueprint (change 11):** the automation did not trigger, so the
  `"<name> Radio"` media_id and the radio-miss handoff to Kronk never ran.
- **Pandora:** no Pandora lines in the MA log in the window (last is the
  15:54:49 provider reload). No station created: the library still has 50
  radios and exactly one Anime entry, "Anime Pop Radio"
  (`library://radio/253`, pandora `208345360983142440`).

## Hypotheses tried

1. *Change 11 sends "Anime Pop Radio", which no longer matches a station
   named "Anime Pop", pushing MA into a Pandora seed search.* Wrong on both
   counts: the automation never ran, and the library station is named
   "Anime Pop Radio" — change 11 would have hit it exactly.
2. *`offer_new_stations` (turned back on today) created a station or made
   search slow.* No Pandora activity in the window, no new station.
3. *Kronk hung: `_llm_lock`, HA `conversation.process` timeout, blueprint
   `mode: single`.* Kronk received nothing; the automation was idle
   (`current: 0`).
4. *HA's built-in search-and-play intent took a transcript the blueprint
   could not match, and blocked on a slow MA play.* Confirmed by the
   pipeline debug run above.

## Root cause

Two things stacked, both pre-existing:

1. **The transcript carried trailing speech.** VAD ("Finished speaking
   detection: default" on the kitchen satellite) kept listening through
   conversation after the request. Every blueprint sentence is anchored at
   the end ("… {media_name} (radio|station) [on pandora][ (in|on) the
   {area}]"), so "Play Anime Pop Radio. That's probably about it. …" matched
   none of them. With `prefer_local_intents: true`, the next in line is HA's
   built-in `HassMediaSearchAndPlay` ("Searches for media and plays the first
   result"), which accepts any "play …". The blueprint's change-9 comment
   already names this intent as a hazard ("takes 'play some chill music' and
   plays an album literally named 'Some Chill Music'"); the loose triggers
   only cover "play some/something …".
2. **That intent blocks on `play_media`, and MA's artist play is slow.**
   The search's first hit was a YouTube Music artist. For an artist not in
   the library MA fetches all tracks across providers before it returns;
   that took ~61 s. HA has no timeout on the intent stage, so the satellite
   stayed in `processing` for 63 s.

The 2026-11-08 changes neither caused nor were exercised by this request.

## Recommended fixes (ranked; none applied)

1. **Route unmatched "play …" to Kronk instead of HA's search-and-play.**
   Options, each an operator call:
   a. Pipeline "kronk": turn `prefer_local_intents` off. Sentence triggers
      (the blueprint) still run first in the pipeline; everything else goes
      to Kronk. Cost: device control ("turn on the lights") loses the ~2 s
      local path and goes through Kronk (tenet 12). Pipeline setting, no
      restart.
   b. Blueprint: add a last-resort loose sentence
      `(play|put on|listen to) {media_name}` handed to Kronk. Needs a test of
      how HA ranks it against the specific sentences (it must not steal
      "play artist X" etc.). Blueprint edit + `automation.reload` (not an HA
      restart).
   Either way Kronk's `origin.said` / `radio_name()` path would extract
   "Anime Pop Radio" from the noisy sentence — still to be verified with
   that exact transcript.
2. **Bound the wait.** Kronk's path already bounds play time; HA's built-in
   intent does not. If HA's intent stays reachable, nothing caps it.
   Upstream MA: non-library artists always get `all_tracks`; a cap or a
   top-tracks-first start would cut the 61 s. Not a local change
   (operator directive: no new local MA code).
3. **VAD.** "Finished speaking detection" is `default`. A more aggressive
   setting would have ended capture sooner but trades off clipping slow
   speakers. Low priority; a device setting, no restart.

## Fix (2026-11-08)

Operator decision: option 1 (a catch-all that hands to Kronk); not turning
off `prefer_local_intents`, which would also send timers and pause/stop to
Kronk, which has no timer tool since 2026-07-12.

- Blueprint change 12 (`20250404-kronk12`, docker cp + `automation.reload`,
  no HA restart): `(play|put on|listen to) {media_name}` → `conversation.process`
  to Kronk, with the same room/player suffix as the loose trigger.
- `music_resolve.radio_name` accepts other speech after a full stop
  ("Play Anime Pop Radio. That's probably about it…" → "Anime Pop"), so the
  sentence still forces a radio request. Without it the first live test
  reached Kronk via the catch-all, the model marked the request `kind=genre`,
  and the taste picker played Sublime "for anime pop".

Live tests (`conversation/process`, basement Voice PE):

| Utterance | Trigger | Result |
|---|---|---|
| Play Anime Pop Radio. That's probably about it. Everything is pretty wet, put it on the basement voice pe ma. | catchall | 7.7 s, Kronk → "Playing your Pandora station, Anime Pop Radio" |
| Play Mushroom Jazz radio on the basement voice pe ma | radio | 1.0 s, blueprint fast path (specific sentence still wins) |
| Play the artist Portishead on the basement voice pe ma | artist | blueprint fast path |
| Pause the basement voice pe ma | (none) | HA local intent, "Paused"; automation not triggered |
| Set a timer for 2 minutes | (none) | HA local intent ("not supported on this device" — no satellite in a REST test); automation not triggered |

Found while testing:

- A spoken player/room the blueprint cannot resolve falls back to the
  default player (the kitchen) instead of failing: "Play Anime Pop Radio on
  the basement voice pe ma. That's probably about it…" matched the radio
  sentence with the whole tail as `area_or_player_name`, matched no player,
  and played in the kitchen (paused within a minute; inherited from the
  upstream blueprint's resolution chain).
- "Play the artist Portishead …" reported success in 0.1 s while the
  previous test's station was still starting: the verify step's "something
  new is playing" check can be satisfied by the prior request's track change.
- One `run_tests.sh` run reported 3 errors that two immediate reruns did not
  reproduce (811 passed); error text was not captured.

## What would have caught it sooner

- Any trace that a voice request was handled by HA's built-in media intent
  rather than the blueprint or Kronk. Today it is only visible in the
  assist pipeline debug (`assist_pipeline/pipeline_debug/get`), which keeps
  a handful of runs.
- A voice check with a noisy-tail transcript ("play X radio. and some
  chatter") alongside the clean bench sentences.
