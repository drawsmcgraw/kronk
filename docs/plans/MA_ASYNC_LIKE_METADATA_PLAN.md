# MA upstream: a like should not wait on the metadata scan

Status: **proposed 2026-10-21, not started.** Operator decision: file an
upstream PR that takes the full metadata scan off the like path. Discussion
first (`MA_UPSTREAM_LESSONS.md` lesson 2). Interim mitigation in place since
2026-10-21: the LRCLIB metadata provider is disabled in Kronk's MA.
Evidence: `docs/incidents/INVESTIGATION_2026-10-16_ma_like_latency.md`
(root cause and the 2026-10-21 addendum). ROADMAP item 16, follow-up (a).

## Problem

A like (`music/favorites/set_item`, favorite=True) on a track that is not
yet in the library does not reach the provider until MA has finished a full
metadata scan of the track. On Pandora stations almost every track is new,
so almost every thumbs up waits on it.

Measured on Kronk:

| Date | Track | Like → provider call |
|---|---|---|
| 2026-10-16 | Kisses in the Wind | 31.1 s |
| 2026-10-16 | Chrysalis | 24.6 s |
| 2026-10-21 | Datte Atashino Hero | 55–70 s (LRCLIB retried twice) |
| 2026-10-21 | Cold Sun | about 9 s |

A dislike or a clear does not take this path and returns in about a second.

Kronk's voice reply no longer waits (thumbs up has been fire-and-forget
since 2026-10-16), so the cost is the delay before Pandora records the
thumb and before a failure alert reaches the operator. Any other MA client
that awaits the like (the MA web UI's heart button included) waits the full
time.

## Where the time goes

Code references are against fork base `f7aec9c38` (upstream `dev`,
2026-10-16). Re-check current `dev` before writing code.

1. `controllers/music/controller.py`, `set_item_favorite`: for an item not
   in the library and favorite=True, it awaits `add_item_to_library` before
   storing the favorite or calling the provider's `set_favorite`.
2. `add_item_to_library` ends with
   `await self.mass.metadata.update_metadata(library_item, overwrite_existing)`.
3. `controllers/metadata/controller.py`, `update_metadata`: enters
   `self._throttler = Throttler(1, 30)`, one scan start per 30 s across all
   of MA, shared with background metadata refreshes. A second like on a new
   track within 30 s of any other scan waits for the slot.
4. `controllers/metadata/enrichment.py`, `_update_track_metadata`, in
   series:
   - fetch the track from each mapped music provider;
   - MusicBrainz linking (`_link_track_to_musicbrainz`, bounded by
     `MUSICBRAINZ_LINK_ITEM_TIMEOUT = 60`);
   - every loaded metadata provider with `TRACK_METADATA`, one after
     another. On Kronk: LRCLIB, MusicBrainz, TheAudioDB.
5. `providers/lrclib/__init__.py`: on the public API it throttles itself to
   one request per 30 s, and `throttle_with_retries` makes every retry take
   a slot. Five attempts with 5/10/20/40 s backoffs cost about 130 s in the
   worst case. The public server returned 503 intermittently on 2026-10-21.

Only step 1's ordering is specific to likes; the rest is ordinary metadata
work that has no reason to block a rating.

## Proposed upstream change

Keep the library row and the favorite write synchronous, and run the
metadata scan in the background on the like path:

- In `add_item_to_library`, replace the awaited `update_metadata` with a
  background task, either `schedule_update_metadata` (already exists:
  background task, deduplicated per item uri, skips items refreshed within
  `REFRESH_INTERVAL`) or a `create_task` that keeps `overwrite_existing`.
- Narrower alternative if maintainers want `music/library/add_item` to stay
  synchronous for other callers: a parameter on `add_item_to_library` (for
  example `metadata_in_background: bool = False`) that only
  `set_item_favorite` sets.

Callers of the music controller's `add_item_to_library` to check for an
assumption that metadata is complete on return:
`set_item_favorite` (two calls), `music/library/add_item` (API command),
`controllers/music/media/radio.py:472`, and the `mammamiradio` provider.

Expected result: a like on a new Pandora track reaches the provider in
about 1 s, the same as a dislike. Metadata arrives a few seconds to minutes
later, as it already does for items added by a library sync.

## Open questions for the Discussion

- Is a synchronous scan on `music/library/add_item` deliberate (the UI
  showing artwork and details as soon as the add returns)? That decides
  between the broad change and the parameter.
- `schedule_update_metadata` drops `overwrite_existing`; is a forced
  refresh needed on this path?
- Should the like path also skip the metadata throttler entirely, or is a
  background task in the queue enough?

## Steps

1. Re-check upstream `dev`: is the await still there; has anyone changed
   the like path or opened an issue?
2. Open a GitHub Discussion with the numbers above and both options. Ask
   which shape the maintainers want. Operator writes the text (upstream AI
   policy); Claude drafts.
3. Branch from current `dev` in the fork. One change, with a test: a like
   on a new item calls the provider's `set_favorite` before the metadata
   scan finishes (a fake metadata provider that blocks until released).
4. Build a derived image, swap it in with a named MA restart, and time
   likes on fresh Pandora tracks with LRCLIB re-enabled (worst case).
5. Open the PR; link the Discussion.

## Interim mitigation (done 2026-10-21)

LRCLIB disabled through `config/providers/save` (`enabled: false`); no MA
restart. LRCLIB's manifest allows disabling, and MA skips disabled builtin
providers at startup, so it stays off. Cost: no synced lyrics in MA. To
restore, enable LRCLIB in MA's Settings → Providers, or point it at a
self-hosted LRCLIB (a custom API URL gets a 1 request per second throttle
instead of 1 per 30 s, and keeps lyric lookups on the box).
