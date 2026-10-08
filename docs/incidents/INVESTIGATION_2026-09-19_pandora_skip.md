# Investigation 2026-09-19 — "skip" on a Pandora station did nothing

Status: root-caused and resolved the same day by moving MA to 2.11.0b2 in
place (recipe and the two surprises in the ROADMAP chore). Pandora skip
verified from the kitchen.

## Symptom

Kitchen Voice PE, Enigma station on Pandora playing. "Skip" → chime, no
change of song.

## Evidence

- Pipeline log 10:55:40 UTC: STT `skip`, handled **locally** by HA's
  built-in next-track intent, reply "Playing next", target
  `media_player.home_assistant_voice_0ac919_pe_01`. No HA error.
- Music Assistant logged nothing for the command.
- MA `player_queues.next()`: computes `_get_next_index()`; for a queue
  whose current item is the **last** item and repeat is off it returns
  `None` and `next()` returns silently. A Pandora station in MA 2.8.8 is
  **one** queue item of type radio: `get_stream_details()` builds a
  1,000-part HTTP stream (`MultiPartPath`, one part per song fragment,
  `fragmentRequestReason: "Normal"`) with metadata updates every 5 s. The
  songs live inside that one stream; the queue never sees them. Next
  track has nowhere to go.
- HA's intent reports success on any service call that doesn't raise.
  Kronk's `control_music next` would have said "Skipped" too: its check is
  the player state staying `playing`/`buffering`, which a station
  satisfies without changing song (`_CONTROL_ACTIONS["next"]`).

## Upstream

Known: music-assistant/support #4793 (Jan 2026), discussion #4916. The
fix is "dynamic track-based stations" (`Playlist.is_dynamic`, queue
controller support merged 2026-04-02, shipped in 2.9). The Pandora
provider on **2.10.4 (latest stable, 2026-09-18) is still the multipart
stream** — no skip. On the **dev branch** (2.11.0.dev) the provider is
rewritten on `is_dynamic` playlists: real tracks in the queue, skip works.

## What to do

1. Nothing on 2.8.8. Say so to the user instead of "Playing next":
   Kronk's `next` should verify a title change (or position reset) and,
   when the queue item is a radio, answer "I can't skip inside a Pandora
   station on this version". HA's built-in intent can't be taught that;
   a sentence trigger for "skip"/"next" routed to Kronk would (trade:
   ~1 s → ~3 s for every skip). Decide with the playback fast path
   (ROADMAP 18).
2. MA 2.11: Pandora becomes track-based, skip works, and station starts
   stop repeating the same opening songs (the other half of #4793). The
   operator chose to take the 2.11 beta in place rather than wait for
   stable; #6424 (Voice PE idle after skip) was checked and does not
   apply — it is a "Home Assistant MediaPlayers" output-protocol
   misconfiguration, and our PEs run Sendspin. Recipe in the ROADMAP chore.

## What would have caught it sooner

A playback-control probe on a Pandora station in the voice smoke test
(ROADMAP 8): "skip" must change `media_title`.

## Outcome (2026-09-19, later)

MA 2.11.0b2 in place. Backup `~/backups/ma/ma-config-2.8.8-20260919-134757-quiesced.tgz`.
Needed on the way: PO-token helper 1.3.1 → 2.0.0 (plugin/server major
mismatch on 2.11; the container had drifted to `:latest`), a Pandora
provider reload after a transient login 403, and a Pandora library resync
so the stations carry `is_dynamic` (pre-migration rows failed with
"Unsupported media type: radio" / "no more tracks available"). After that:
Enigma station plays as tracks (`pandora://track/…`), two skips changed
the song within 1 s, speaker stayed playing. YouTube album and local album
play and skip. HA integration loaded, no repair issues.
