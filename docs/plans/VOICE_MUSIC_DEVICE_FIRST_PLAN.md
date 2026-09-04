# Voice music: play on the device that asked (HA tier) — Plan

Status: **shipped 2026-09-04 (same day), HA tier.** Fork installed as
`blueprints/automation/kronk/mass_assist_kronk.yaml`, automation
`kronk_music_assistant_voice_device_first` on, both upstream copies off
(backup `automations.yaml.bak-20260904_162955` in HA's config). Verified
by text runs through the Assist pipeline as each satellite: office
unnamed → satellite 1 only ("playing in the Office"); kitchen unnamed →
Voice PE only; "in the office" from the kitchen → office; no device →
default. **Group rung dry-run only** — no sync group exists yet; verify
when the second kitchen satellite and its group land. Distilled into
`docs/features/voice-music-control.md`. The Kronk tier (origin stamp for
fuzzy requests) is the follow-on (ROADMAP, Next); tool_service already
carries the `origin_area` slot (`MUSIC_PLAYERS_FROM_HA_PLAN.md`).

## Why

The operator's ideal: "the music plays FROM THE DEVICE that initiated the
ask." The upstream MA blueprint resolves by **area**, not device: with a
second satellite in the Kitchen, a request from either kitchen satellite
plays on both kitchen players, unsynchronized. Operator decision
(2026-09-04): **device first, then groups** — an unnamed request plays on
the satellite that heard it; a named room plays on that room's Music
Assistant sync group when one exists, else on every player in the room.

## Mechanics (verified 2026-09-04)

- Each satellite is two HA devices: the ESPHome one that hears (carries
  the MAC under `connections`) and the MA player (identifier
  `('music_assistant', 'up' + MAC without colons)`). The join is one
  template: `device_attr(trigger.device_id, 'connections')` → MAC → the
  MA player whose device identifiers contain that key. Proven live for
  satellite 1 → `media_player.satellite1_d8d1bc`.
- Step 1 (areas on both devices, 2026-09-04) already made the upstream
  area rung work — satellite 1 played in the office at ~2 s with Kronk
  never involved. This plan adds the device rung ahead of it.

## Design: a Kronk fork of the MA blueprint

`ha/blueprints/mass_assist_kronk.yaml` in this repo (source of truth),
installed into HA as `blueprints/automation/kronk/mass_assist_kronk.yaml`
— a new file; upstream's stays untouched. Forked from upstream
`version: 20250404` (sha256 `d57d3880862e0df7…` of the installed copy).
Three changes, everything else verbatim:

1. **Device rung** `player_entity_id_by_assist_device` (MAC join), placed
   between the named rungs and the area rung.
2. **Group preference** in both room rungs (`_by_area_name`,
   `_by_assist_area`): if any player in the room is a sync group
   (`mass_player_type == group`), target the group only.
3. **Confirmation names the room** when the match was by device or room
   (`mass_player_where`: "in the Office" / "on Sonos Move"), so technical
   device names are never read aloud.

Resolution order: named player → named room (group-preferred) → **own
device** → own device's room (group-preferred) → default.

**Maintenance line:** MA's blueprint updates do not flow into the fork.
When upstream changes, diff `ha/blueprints/upstream/` (kept alongside)
against the new file and re-apply the three changes.

## Steps

1. Plan doc (this). Fork file in the repo + pristine upstream copy.
2. Dry-run every new expression through HA's template API with the
   office and kitchen satellite device ids (expect each one's own player)
   and with no device (expect `[]`).
3. `docker cp` the fork into HA. Back up `automations.yaml`, append one
   automation built from the fork (default = kitchen Voice PE), reload
   automations, disable both upstream copies (operator authorized me to
   do step 5 this once, 2026-09-04).
4. Verify by text through `assist_pipeline/run` with each satellite's
   `device_id`: unnamed from the office → satellite 1 only; unnamed from
   the kitchen → Voice PE only; "in the office" from the kitchen → office.
   Read the automation trace for the rung that fired. Stop playback after
   each. Operator does the real "Okay Nabu" runs.
5. Docs: feature doc (order, fork maintenance note), ROADMAP (Shipped +
   Kronk tier under Next), memory.
6. Group rung: verified by dry-run only until the second satellite and
   its sync group exist — status here says so until then.

## Out of scope

The Kronk-tier origin stamp; tool changes; the Whisper tense drift
("Played the album…" transcripts), which sits in front of both tiers.
