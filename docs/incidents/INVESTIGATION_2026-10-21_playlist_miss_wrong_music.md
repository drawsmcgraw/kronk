# Investigation 2026-10-21 — "Old Man Sings" playlist plays unrelated music

**Status:** Kronk-tier bug fixed 2026-10-21 (no literal play after a rejected
search). Playlist made playable by a manual MA library sync. Open: the
12-hour YTM sync interval and the blueprint fast path playing MA's top hit
(see "Not fixed").

## Symptom

Operator asked the kitchen Voice PE for their personal YouTube Music
playlist "Old Man Sings" (shuffled). Five attempts over ~3 minutes, two
through the HA blueprint and three through Kronk, all played the wrong
music: "Late Night with Jimmy Fallon" four times and "Celebration of Life
Funeral Songs" once. Kronk's follow-up answers ("what did you just play?",
"why?") could not explain it. The same session's "anime bangers" played
correctly.

## Timeline (UTC; HA logbook, orchestrator `kronk.events`, tool_service and MA logs)

| Time | Path | What happened |
|---|---|---|
| 22:46:40 | HA blueprint (local tier) | literal `play_media` → MA played playlist "Late Night with Jimmy Fallon" |
| 22:47:11 | HA blueprint | literal `play_media` → "Celebration of Life Funeral Songs" |
| 22:47:37–22:47:52 | Kronk, direct/coordinator | "What the hell was that?", "that was the wrong playlist" → chat only |
| 22:48:19 | Kronk rid 2d41bd66 → home → `play_music` | `resolve 'Old Man Sings' [playlist] … 'Old South African songs' (0.62) BELOW THRESHOLD` → literal play → Jimmy Fallon |
| 22:48:52 | Kronk rid 1af1cbd7 | same, player Kitchen → Jimmy Fallon |
| 22:49:49 | Kronk rid e886d21c ("Can you fix it?") | same → Jimmy Fallon |
| 22:52:57 | (blueprint) | "anime bangers" plays correctly (library playlist) |

## Root causes

1. **The playlist wasn't in MA's library.** MA's YouTube Music playlist
   sync runs every 12 hours (task `music_sync_ytmusic--kGmHoQDn_playlist`,
   schedule `hourly, every: 12`); the playlist was newer than the last
   sync. The library held 3 YTM playlists (anime bangers, Liked Music,
   video game pretty songs). MA's search for "Old Man Sings" only reaches
   public YTM playlists, none of them the operator's.
2. **Kronk's resolver refused correctly, then the fallback played junk
   anyway.** The resolver (MUSIC_TASTE_AGENT_PLAN step 2) scored the best
   public candidate at 0.62, below `PLAY_MIN_SCORE` 0.75. Below the line,
   `/music` kept the pre-resolver literal `play_media("Old Man Sings",
   playlist)`. MA's literal play runs its own search and plays its top
   hit, which was a candidate the resolver had just rejected. Verification
   saw "playing" and reported success.
3. **The blueprint fast path has the same shape.** It plays the literal
   words; MA plays its top hit; there's no failure, so no handoff to Kronk
   (KRONK change 9 hands off only on failure).

Not a regression in the strict sense: before 2026-10-21 the Kronk path
played the same literal search. But the resolver's verdict was available
and ignored. That was a design gap in step 2 ("nothing above threshold →
fail as today", where "today" was the literal play).

## Fix

- `tool_service/main.py`: when the resolver found candidates and rejected
  them all, `/music` fails with "I couldn't find a playlist called Old Man
  Sings in Music Assistant." (plus "The closest I found was …" when one
  scores ≥ 0.5). No literal play. The literal play remains only when the
  search found nothing at all or MA couldn't be searched; MA may resolve
  through a type the resolver doesn't search (radio, podcast), and the
  Pink Floyd / zzqx tests cover those paths.
- Tests: `test_play_playlist_not_in_the_library_plays_nothing`,
  `test_play_no_confident_match_fails_and_names_the_closest` (replaces the
  test that asserted the literal play).
- Ran the MA YTM playlist sync once by hand (`music/sync`, playlist,
  ytmusic). "old man sings" is now `library://playlist/12`.

Verified live, basement Voice PE: "Old Man Snigs Polka Hour" → 502 in
1.2 s, nothing played; "Old Man Sings" → resolved `library://playlist/12`
(score 1.04), playing in 5.3 s.

## Follow-up: "shuffle" opened on track 1

After the sync, "shuffle Old Man Sings" (kitchen, blueprint path) played
shuffled but started on the playlist's first track, "Man About a Dog". The
queue (52 items, `current_index` 0) was shuffled from position 2 on.

Cause: both tiers set shuffle *after* `play_media`. MA shuffles only the
items after the current one, so the first track is always track 1. It
looked fine on basement earlier only because that queue's shuffle was
already on from a previous play.

Measured (basement): shuffle set before the play → 3/3 random first
tracks.

Fix:

- `tool_service`: `_shuffle_before_play()` sets the queue's shuffle before
  the play when it differs (named path, and genre artist picks). The
  after-play set stays as the retry for a queue that refused, e.g. a
  dynamic radio queue.
- Blueprint KRONK change 10 (`20250404-kronk10`): the same order, and the
  after-play set runs only when the player still disagrees. Installed with
  an automation reload (no HA restart).

Live: the blueprint sentence "shuffle the playlist old man sings on the
basement voice pe ma" started on "Zak and Sara", then on "Happy Trails";
Kronk `/music` started on "Semi-True Story". Tests: 767 passed, 2 skipped.

## Not fixed (operator decisions)

- **Sync interval.** A new personal playlist is invisible for up to 12
  hours. Options: shorten the YTM playlist sync in MA's UI (no restart), or
  have `/music` run a YTM playlist sync on a playlist miss and re-resolve
  once (a manual sync took < 10 s).
- **Blueprint fast path.** It still plays MA's top hit for a playlist that
  isn't in the library. Options: route playlist requests to Kronk (adds
  ~7–8 s to every voice playlist request), or leave it.
- **Kronk can't explain its own plays.** "What did you just play?" went to
  the coordinator with no access to the last play result.

## What would have caught it sooner

A test asserting that a below-threshold resolve makes no `play_media` call.
The old test asserted the opposite: it checked that the literal play
happened.
