# Investigation 2026-09-30 — "now playing" reported for music that never started

Status: fixed (verify-the-change) and deployed; media_type-miss follow-up in
ROADMAP chores.

## Symptom

Kitchen, music already playing (Fountains of Wayne, "All Kinds Of Time").
"Search for some deep house chill music on YouTube Music" → the puck
answered with an affirmative and named an artist; the old track kept
playing. A retry ("try that again, it's not playing") did the same.

## Evidence

- tool_service, both requests: `HA music_assistant.play_media failed:
  code=home_assistant_error message=Could not resolve ['deep house chill
  music'] to playable media item` followed by `play_media reported failure
  but …_pe_01 is playing` and `POST /music 200`.
- Kronk events: coordinator → `ask_home` → `play_music` with args
  `["media_type", "query"]` (values were not logged).
- MA search for "deep house chill music": tracks, playlists, artists and
  albums on YouTube Music; nothing as radio or genre.

## Root cause

1. `/music` treated "any target in `playing`" as success, including after
   a failed call (rule added 2026-09-04 because MA once failed the call and
   played anyway). With music already on, a failed call left the old item
   playing, the check passed, and the old track's title/artist were spoken
   as the new one. Tenet 6 violation — state, not effect. Live since
   2026-09-04 for any request made while something was playing.
2. The home agent's `media_type` guess (most likely `radio`, unconfirmed)
   made MA's lookup fail for a query it can otherwise find.

## Fix (deployed 2026-09-30)

- `/music` snapshots each target's (state, content id, title) before the
  call; success requires a target to be `playing` something different. A
  call that did not fail and a player still on the same item (asked for
  what's already playing) is accepted at the verify deadline.
- `/music` logs the request arguments (query, media_type, player, shuffle).
- Tests: failed call with old music playing → 502 with MA's message;
  success reports the new track; same-item replay succeeds at the
  deadline; no blind retry.

## Wrong turn, recorded

First version also retried once without `media_type` on "could not
resolve". Live test with a nonsense query ("zzqx florbent nonexistent",
media_type album) — chosen because it "could not" play anything —
**replaced the operator's kitchen music with Akon, "Lonely"**: MA's untyped
search always returns a hit. Retry removed within minutes, redeployed,
re-tested: honest 502 in 8.06 s, kitchen music untouched. Lesson: an
untyped music lookup is never a safe no-op, and a live test on a player
someone is listening to needs the operator's OK.

## Latency

- Before-snapshot: one HA state read per target, median 0.2 ms.
- Success: reported when the new item shows up (first 1 s poll in the
  cases seen) — same as before, but now names the new track.
- Failure while music plays: previously a false success at ~1 s; now an
  honest failure after the full verify window, 8 s (`MUSIC_VERIFY_TIMEOUT_S`).
- Asking for exactly what is already playing: 8 s (waits for a change that
  never comes).

## What would have caught it sooner

A test for "failed call, player already playing" when the plays-anyway
rule went in on 2026-09-04.
