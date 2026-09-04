# Feature: Voice music control

**Shipped:** 2026-07-03 · **Plan:** `../plans/MUSIC_ASSISTANT_PLAN.md` · **Journal entries:** `../VOICE_SETUP.md` timeline (2026-07-03)

## What it does

"Play Pink Floyd on the Sonos Move" — spoken or typed — plays music through
Music Assistant on any mapped player. Two tiers, by design (Option C):

| Tier | Path | Latency | Grammar |
|---|---|---|---|
| 1 | **Kronk fork** of the MA local-assist blueprint (`ha/blueprints/mass_assist_kronk.yaml`) | ~2 s | strict: needs a media-type keyword ("play the **artist** X [in Y]") |
| 2 | Kronk `home` agent → `play_music` tool | ~15–25 s | fuzzy, anything |

Utterances the blueprint's grammar can't parse fall through HA's Assist
pipeline to Kronk automatically — the user just waits longer.

## Tier 1 targeting: the device that asked (2026-09-04)

Plan: `../plans/VOICE_MUSIC_DEVICE_FIRST_PLAN.md`. Upstream's blueprint
resolves by *area*; with two satellites in one room that means both play,
unsynchronized. The fork resolves **named player → named room →
own device → own device's room → default**, and a room with a Music
Assistant sync group (`mass_player_type == group`) targets the group
only. "Own device" is a MAC join inside the blueprint: the ESPHome
satellite's `connections` MAC → the MA player whose device identifier is
`up` + that MAC. The confirmation says "playing in the Office" when the
match was by device or room, so technical device names are never read
aloud. Verified 2026-09-04 by text runs as each satellite (unnamed →
own device only; "in the office" from the kitchen → office; no device →
default); the group rung is dry-run only until a sync group exists.

**This fork is a maintenance line.** MA's blueprint updates do not flow
into it. The pristine upstream is kept at
`ha/blueprints/upstream/mass_assist_blueprint_en.yaml`; when MA ships a
new version, diff it against that copy and re-apply the three marked
changes (`# KRONK change 1/2/3` in the fork). Install = `docker cp` into
`homeassistant:/config/blueprints/automation/kronk/` + automation reload.
The automation built from it is `automation.kronk_music_assistant_voice_device_first`
(default player: kitchen Voice PE); both upstream-blueprint automations
are disabled and kept for rollback.

Kronk's tier still plays on the default for fuzzy requests — the origin
stamp for that tier is the follow-on (ROADMAP, Next).

## How it works (tier 2)

`orchestrator/tools.py:play_music` → `tool_service POST /music` → HA REST
`music_assistant.play_media` → MA resolves the free-text query against its
providers → audio on the player. The route:

1. **Discovers the players from HA** (since 2026-09-04,
   `docs/plans/MUSIC_PLAYERS_FROM_HA_PLAN.md`): one template call lists
   every `media_player` the Music Assistant integration registered — the
   set MA can drive — with friendly name, area and state. Nothing is
   configured in compose except `MUSIC_DEFAULT_PLAYER`.
2. **Resolves in the blueprint's order**, so both tiers agree: exact
   spoken **player name** → exact spoken **area** (every MA player in it)
   → substring on names, then areas (tolerance, deliberately after both
   exact rungs so "kitchen" reaches the Kitchen area rather than a device
   called "kitchen-voice-pe-ma") → the request's **origin area**
   (`origin_area`, filled once the shim knows which satellite spoke —
   follow-on plan) → `MUSIC_DEFAULT_PLAYER`. Ambiguous names and unknown
   rooms are errors that say what exists. Spoken labels come from HA: the
   area when matched by area, else the player's friendly name — so
   technical device names never get read aloud for room requests.
3. Unavailable targets are caught from the same list (503 "may be
   powered off"); a room with two speakers plays on both.
4. Calls `play_media`, then **polls the targets for `playing`** before
   reporting success — MA queues async, so HA's 200 alone proves nothing.
   Expired provider auth (e.g. YouTube Music) surfaces here as a clean
   failure instead of a silent no-play.

**The two tiers must agree.** The blueprint's Jinja
(`player_entity_id_by_player_name` → `_by_area_name` → `_by_assist_area`
→ default) and `resolve_players()` in tool_service cannot share code;
`tests/test_music_players.py` pins the Python order. If the blueprint's
rule ever changes, change both.

## Adding a satellite

Assign an **area** in HA to both of its devices — the ESPHome one that
hears you and the Music Assistant one that plays — and it is playable by
voice with no Kronk change. Each satellite is two HA devices; the join
key is the MAC: the MA device's identifier is `up` + the MAC without
colons, and both original names end in the MAC's last three bytes. List
the pairs from HA's registry:

```bash
docker exec homeassistant cat /config/.storage/core.device_registry \
  | jq -r '.data.devices[] | "\(.name_by_user // .name)\t\(.name)\t\(.connections[]?[1] // .identifiers[][1])"' \
  | grep -i "voice\|satellite" | sort -k2
```

Name devices however you like (`satellite-01-ha` / `-ma` is fine); keep
**area names speakable** — that is what voice addresses. When renaming a
device in HA, decline the offer to rename entity ids.

## The terminal-tool mechanism (the interesting part)

`play_music` is a **terminal tool** (`AgentConfig.terminal_tools` in
`orchestrator/agents.py`): its result is converted to speech verbatim
(`_terminal_speech`) and the agent turn ends immediately. This is a
*structural* guardrail, added after prompt engineering failed three ways:
gemma-4-e4b narrated fake tool scaffolding aloud, hallucinated "Jazz is now
playing" after a 503, and retried the tool to budget exhaustion. With the
mechanism, the model never gets a chance to editorialize about the result.

## Gotchas

- `MUSIC_DEFAULT_PLAYER` must be an **MA entity** (`_2`-suffixed, platform
  `music_assistant`) — native Sonos/Cast entities can't be driven by MA.
  Discovery keys on the integration, not on `mass_player_type`: an
  unavailable player drops its attributes.
- The blueprint matches **exact MA player names**: "sonos move" ≠ "Sonos Move
  Derp" → silently plays on the default player. Fix: rename the player in
  MA's UI (entity_id survives renames).
- Blueprint grammar traps: "play the album X **by Y**" stuffs Y into the
  media name; it needs "by the **artist** Y".
- MA 2.8.8's YT Music provider can 500 transiently (`ytmusicapi has no
  attribute YTMusicError` — upstream bug). tool_service logs the full body,
  speaks one clean sentence.

## Blog hooks

- Terminal tools: when prompt engineering loses to a 4B model, change the
  loop, not the prompt.
- "HTTP 200 means nothing": verifying async playback actually started.
- Two-tier voice design: strict-grammar fast path + LLM fuzzy fallback.
