# Investigation 2026-09-15 — "play the album Daisies of the Galaxy" played one track

Status: root-caused. Un-exposure experiment run and reverted (below); no change kept.

## Symptom

Kitchen Voice PE, 06:35 local. Operator asked for the album *Daisies of
the Galaxy* (Eels). What played sounded like the album on shuffle. Asked
again ~20 s later; the album played from track 1.

## Timeline (UTC, from HA history, pipeline debug runs, automation traces, MA log)

| Time | Source | Event |
|---|---|---|
| 10:35:44 | pipeline debug | STT: `Play the Albo Daisies of the Galaxy.` — `processed_locally: true`, response `Playing media`. No blueprint trace. |
| 10:35:54 | HA history / MA log | MA player starts **track** `Daisies Of The Galaxy` (`ytmusic://track/FlVyo0hCAW8`), no album attribute; MA log: "Start Queue Flow stream", no "Fetching tracks to play for album". |
| 10:36:01 | HA history | ESPHome media player → idle. Operator pressed the PE's button to stop the wrong track. |
| 10:36:04 | pipeline debug | STT: `Play the album Daisies of the Galaxy by Eels on YouTube Music.` |
| 10:36:11 | automation trace | Kronk blueprint fork matched: `media_name="Daisies of the Galaxy by Eels"`, `area_or_player_name="YouTube Music"` (fell through to the origin device). `music_assistant.play_media media_type=album`, `shuffle_set false`. |
| 10:36:12 | MA log | "Fetching tracks to play for album Daisies Of The Galaxy". |
| 10:36:16 | HA history | Playing `Grace Kelly Blues` (track 1). |

## Root cause

Two things stacked:

1. **STT**: Whisper transcribed "album" as "Albo". The blueprint's album
   sentence needs the literal word, so it did not match.
2. **The fallback tier**: HA's built-in `HassMediaSearchAndPlay` intent
   matches `play {query}` and searched Music Assistant for the whole
   string "the Albo Daisies of the Galaxy". MA's best fuzzy hit was the
   *title track* (track 5 of the album), so a single track from the
   middle of the album started. Heard as "shuffle". The response
   "Playing media" is that intent's stock reply.

Same class as the 2026-09-04 "toys in the attic" miss (STT dropped
"album" → HA-tier search-and-play played something else). It is the
third time the built-in intent has taken a sentence the blueprint or
Kronk should have had (see `docs/plans/MUSIC_ACCURACY_PLAN.md` for the
station-first case).

Tier order, for the record (HA local pass, then Kronk): sentence
triggers (our blueprint) → built-in intents (`HassMediaSearchAndPlay`,
timers, lights…) → only if neither matches, the pipeline's conversation
agent (Kronk). "Blueprint missed" therefore does not mean "Kronk got
it"; the built-in media intent's `play {query}` is a wildcard that
catches almost any sentence starting with "play" while an exposed media
player exists.

## Fix options (not applied)

- **Remove the third tier.** The built-in intent can only target media
  players exposed to Assist. Un-exposing the MA player entities makes an
  STT miss fall through to the blueprint (conversation trigger, no
  exposure needed) or to Kronk's `play_music` (tool_service → HA
  websocket, no exposure needed), both of which handle "albo" better
  than a raw search. Trade-off: HA's local pause/resume/volume intents
  also need exposure, and ROADMAP item 18 (the ~1 s playback fast path)
  may want them. Decide together.
- **STT accuracy** — ROADMAP item 13. "Album" → "Albo" is the kind of
  miss a bench would catch or a different model would not make.
- Not recommended: teaching the blueprint "albo". Prompt/sentence
  tuning against STT noise (tenet 5).

## What would have caught it sooner

The voice smoke test (item 8) with an "album" utterance asserting
`media_type=album` in the blueprint trace, run after STT model changes.

## Experiment 2026-09-15 13:14 UTC — send the miss to Kronk instead

Un-exposed the seven Music Assistant players from Assist (websocket
`homeassistant/expose_entity`), then ran the exact first transcript
through the kitchen pipeline (`assist_pipeline/run`, intent stage only,
kitchen device id).

| Path | Intent stage | Result |
|---|---|---|
| HA built-in intent (10:35, as it happened) | 4.5 s | track "Daisies Of The Galaxy" |
| Kronk (13:14, players un-exposed) | 10.1 s | track "Daisies Of The Galaxy" |
| Blueprint (10:36, with "album") | 5.2 s | album, from track 1 |

Kronk's chain: coordinator 2.4 s → `ask_home` 2.9 s → `play_music`
4.7 s. The home agent passed `query` only — no `media_type` — so
tool_service's MA search picked the same title track. "Albo" fooled the
model as much as the sentence matcher. Kronk was not better, only slower.

Two things the retained pipeline runs showed while checking this:

- **HA's local pause/skip is live and fast.** "Pause." and "Skip." from
  the kitchen (2026-09-14) were handled locally in 0.01–0.02 s intent
  time by HA's built-in media intents against the exposed MA players.
  Un-exposing the players would have sent those to Kronk (3–5 s). That
  is the ~1 s fast path ROADMAP item 18 asks for — already there for
  pause/skip, not for "stop" or volume phrasing.
- **"Who is this?" was also grabbed locally** (2026-09-14 19:00 UTC):
  processed locally, spoken reply `Not any`. Kronk's now-playing route
  never saw it. Same catch-all class, different intent.

Decision: exposure restored as it was (7 of 7). The trade is not worth
it until Kronk's own path infers `media_type` from the sentence and
runs faster than the built-in intent. The fix for this incident is
upstream of both tiers: STT (item 13).
