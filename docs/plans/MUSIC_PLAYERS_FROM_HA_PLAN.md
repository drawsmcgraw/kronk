# Music players from HA, not from compose — Plan

Status: **shipped 2026-09-04 (same day).** Live-verified through the tool
and the pipeline: "the office" → satellite 1 playing, kitchen idle;
`origin_area=Kitchen` with nothing spoken → Voice PE playing, office
idle; unknown room / unavailable player / garage each speak their own
sentence. 22 new tests (the `/music` route's first); suite 442 green.
Distilled into `docs/features/voice-music-control.md`. The origin-area
slot is live but unfilled — the shim side ("play from the device that
asked") is the follow-on plan.

## Why

`MUSIC_PLAYERS` in `docker-compose.yml` was a spoken-alias → MA-entity map
that did three jobs at once for two speakers: filter to players Music
Assistant can drive, attach speakable names to ugly ones, name a default.
More satellites are coming, and each one would mean editing compose — a
fact maintained in two places (tenet 8). HA already knows every MA player,
its name, its area and whether it is up, and HA is the broker by design.

## Design

- **One HA template call per play request** (tool_service →
  `POST /api/template`): `integration_entities('music_assistant')` →
  JSON list of `{entity_id, name, area, state}`. Membership is by
  integration, not by attribute — an unavailable player loses its
  `mass_player_type` attribute (observed on the Sonos, 2026-09-04).
  `area_name(entity)` resolves through the device when the entity has
  no area of its own, which is how the satellites are set up. No cache:
  tens of ms on a 15 s path, and a newly assigned area is live at once.
- **Resolution order = the MA blueprint's** (`mass_assist_blueprint_en`
  variables `player_entity_id_by_player_name` → `_by_area_name` →
  `_by_assist_area` → default), so the two tiers behave identically:
  1. spoken text matches a player **name** exactly
  2. spoken text matches an **area** name exactly → every MA player in it
  3. substring on player names, then substring on area names — tolerance
     for "the sonos" / "sonos move speaker", placed *after* both exact
     rungs so a room name inside a device name ("kitchen" in
     "kitchen-voice-pe-ma") never steals a room request from the area
     (found by the tests, 2026-09-04)
  4. the request's **origin area** (`origin_area` field; unused until the
     shim learns HA's prompt stamp — next plan)
  5. `MUSIC_DEFAULT_PLAYER` — the one preference that cannot be
     discovered (where the web UI's music goes). Stays in compose.
  Two candidates for one spoken name is an error that names both.
- **Spoken labels come from HA**: the area when matched by area/origin,
  else the player's friendly name. With technical device names
  (`satellite-01-ma`), confirmations name rooms.
- **Failures stay loud and specific** (tenet 7), one sentence each, spoken
  verbatim by the terminal-tool mechanism: HA unreachable; no MA players
  registered; unknown speaker/room (lists what exists); ambiguous name;
  room whose players are all unavailable; playback did not start.
- The Jinja rule (blueprint) and the Python rule (tool) cannot share
  code. The feature doc states they must agree; the tests pin the order.

## Changes

- `tool_service/main.py`: `_PLAYERS_TEMPLATE`, `parse_players()`,
  `resolve_players()` (pure, tested), `_fetch_players()`; `MusicRequest`
  gains `origin_area`; play + verify across a target list (any target
  reaching `playing` is success). `MUSIC_PLAYERS` removed.
- `orchestrator/tools.py`: `player` param = "speaker or room, as the user
  said it". `orchestrator/agents.py` home prompt: "speaker or room".
- `docker-compose.yml`: drop `MUSIC_PLAYERS`; comment explains the one
  remaining preference.
- Tests (`tests/test_music_players.py`, the `/music` route's first):
  parser; order (name beats area beats origin beats default); substring;
  ambiguity; "the office speaker" phrasing; area with two players →
  both; origin area with no player → default; unavailable wording; HA
  down / empty list; route happy path with a fake HA.
- Docs: feature doc (resolution rule + "must agree" note + the
  MAC-pairing recipe for new satellites), `docs/VOICE_SETUP.md` env
  line, memory note, ROADMAP Shipped line.

## Verification (live)

"Play the artist Aerosmith in the office" → satellite 1 reaches
`playing`, kitchen stays idle. "…on the kitchen" → Voice PE. Web UI, no
name → default. "…in the bedroom" (no player) → the spoken sentence says
so. Then `./scripts/run_tests.sh`, `up -d --build tool_service
orchestrator`, nginx restart, shim check.

## Rollback

`git revert` + compose redeploy. Nothing stateful.
