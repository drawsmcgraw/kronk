# Pandora thumbs by voice

Shipped 2026-10-06; reworked 2026-10-16 onto Music Assistant's own
like/dislike. "Thumbs up" / "thumbs down" said to a satellite while a
Pandora station plays rates the playing track on the Kronk Pandora account;
thumbs down also skips. Plans: `docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md`
(original build), `docs/plans/MA_FORK_STATUS.md` (current fork/PR state).

## What it does

- "thumbs up", "I love this song", "more like this" → spoken at once:
  "Thumbs up for *song* by *artist*." The like is sent in the background;
  if it fails, the operator gets a phone push ("Kronk: thumbs up failed",
  with MA's error text). The household never hears about it.
- "thumbs down", "never play this again", "downvote" → disliked, then
  `media_next_track`, verified by the title changing; spoken: "Thumbs down."
  (kept to two words on purpose — the next song is already playing under
  it). The skipped-to track rides in the JSON, not the speech.
- Anything that is not a Pandora track ("That's not a Pandora station"),
  nothing playing, or MA refusing a dislike are spoken as refusals, never
  claims.

## How it works

1. **MA's favorites are the interface.** Since MA 2.11.0b3 (#6482) a
   favorite is a per-user tri-state: like / dislike / cleared, set with
   `music/favorites/set_item(item, favorite)`. The core forwards it to the
   owning provider's `set_favorite(prov_item_id, media_type, favorite)` when
   the provider declares `ProviderFeature.FAVORITE_TRACKS_EDIT` (YouTube
   Music, Deezer, Apple Music, Plex and OpenSubsonic already do).
2. **Our Pandora patch implements that hook** (~30 lines, the upstream PR):
   find the track in the retained fragments (`_find_track`, freshest copy),
   refuse if gone / no `trackToken` / `allowFeedback: false`, POST Pandora's
   `station/addFeedback {trackToken, isPositive}`. Clearing (None) is
   ignored — Pandora deletes feedback by a feedback id that is not kept.
   Running image: `kronk/music-assistant:2.11.0b5-kronk.2` (stock 2.11.0b5
   + the patched Pandora package from fork commit adeb99d34; `ma/Dockerfile`,
   `ma/build.sh`). The
   Dockerfile hash-guards every file of the stock Pandora package.
3. **tool_service `POST /music/rate`** resolves the player as the other
   music endpoints do, reads HA's state for the playing track
   (`media_content_id = pandora://track/<id>`) and sends that URI to
   `music/favorites/set_item` over MA's **websocket** (`ma_command()`;
   `MA_URL`/`MA_TOKEN` in `.env`).
   - Thumbs up: `like_in_background()` runs as a detached task (strong ref
     in `_BACKGROUND_TASKS`), waits up to `MA_LIKE_TIMEOUT_S` (180 s), logs
     `thumbs up landed|failed`, and on failure calls `notify_operator()` →
     `HA_NOTIFY_SERVICE` (`notify/mobile_app_drew_pixel_11_pro`, tag
     `kronk-music-rate`).
   - Thumbs down: awaited (fast), then skip and verify.
4. **Kronk**: `_RATE_RE` routing pin → home agent → `rate_music` terminal
   tool (its result is spoken verbatim). Measured 4.1 s for "thumbs up"
   through `assist_pipeline/run` on 2026-10-16; operator "Okay Nabu" test
   of both thumbs passed the same day.

## Gotchas

- **A like on a new track blocks ~25–30 s inside MA.** `set_item_favorite`
  adds the track to the library first, and `add_item_to_library` awaits a
  full metadata scan (MusicBrainz, Wikipedia, Last.fm, LRCLIB, ...) before
  the favorite is stored or Pandora is called. Measured 24.6 s and 31.1 s;
  station tracks are almost always new. That is why thumbs up is
  fire-and-forget. `INVESTIGATION_2026-10-16_ma_like_latency.md`.
- **Dislikes are forwarded to Pandora in MA's own background task**, so a
  Pandora failure on thumbs down only reaches MA's log (`journalctl
  CONTAINER_NAME=music-assistant-server`), not Kronk and not the push.
- **A like puts the track (and its album/artist rows) in MA's library.**
  Liked Pandora tracks accumulate there; library sync never removes them
  because Pandora does not declare `LIBRARY_TRACKS`.
- **Favorites are per MA user.** `MA_TOKEN` is the admin user `drew`; the
  Pandora source is home-shared (owner none, sharing everyone), so MA's
  write filter (`_write_target_mappings`) reaches it. A Pandora source owned
  by another user would silently not be written.
- **MA's HTTP `/api` collapses every typed error into "Internal server
  error"**; the websocket carries `error_code` + `details`. Even there,
  `details` is MA's generic text per error class ("The requested media item
  could not be found."), not the provider's message.
- **MA tokens inherit the user's role**; `auth/token/create` cannot scope
  one down. A dedicated `service`-role user is the least-privilege
  follow-up. Long-lived tokens expire after one year and do not renew.
- **A thumb can land on the wrong station** if the same song is live in two
  stations' retained fragments (`_find_track` picks the freshest).
- **No HA fast tier yet.** A blueprint sentence → `rest_command` would make
  it ~1 s, but the first `rest_command` in HA needs an HA restart.
- **Carrying the patch** until upstream merges it: each MA bump means
  re-measuring the guard hashes, rebuilding, one named restart (backup
  first; b5 migrated the DB 58 → 68, so rollback = old image + tarball).

## Blog hooks

- Ask before you build: the maintainer's one-line "we already have
  favourite and dislike" turned a new API command into a 30-line
  implementation of an existing hook.
- Fire-and-forget with an operator back-channel: the family hears "Thumbs
  up" in 4 s; the one person who can fix a failure gets a push.
- Running a patched upstream image the pinned way: derive from the stock
  release, hash-guard the files you replace, one commit per feature so the
  same commit becomes the PR.
- "Say less": the thumbs-down reply shrank from a sentence to two words
  because the next song was already playing.
