# Pandora thumbs by voice

Shipped 2026-10-06. "Thumbs up" / "thumbs down" said to a satellite while a
Pandora station plays rates the playing track on the Kronk Pandora account;
thumbs down also skips. Plan: `docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md`.

## What it does

- "thumbs up", "I love this song", "more like this" → the track is rated
  up; spoken: "Thumbs up for *song* by *artist* on *station*."
- "thumbs down", "never play this again", "downvote" → rated down, then
  `media_next_track`, verified by the title changing; spoken: "Thumbs down."
  (kept to two words on purpose — the next song is already playing under
  it). The skipped-to track rides in the JSON, not the speech.
- Anything that is not a Pandora track ("That's not a Pandora station"),
  nothing playing, or MA refusing are spoken as refusals, never claims.

## How it works

1. **Music Assistant carries a patch.** The stock 2.11.0b2 Pandora provider
   has no feedback call. Our derived image
   (`kronk/music-assistant:2.11.0b2-kronk.1`, `ma/Dockerfile` + `ma/build.sh`,
   clone `~/git-repos/music-assistant/server` branch `kronk/pandora-2.11.0b2`,
   commit c814b2490) adds a provider-registered API command
   `pandora/feedback(item_id, positive)`: find the track in the retained
   fragments (`_find_track`, freshest copy), refuse if gone / no
   `trackToken` / `allowFeedback: false`, POST Pandora's
   `station/addFeedback`, return song/artist/station. Seven provider tests.
   The Dockerfile's hash guard refuses to build on a base whose Pandora
   files differ from the ones the patch was written against.
2. **tool_service `POST /music/rate`** resolves the player as the other
   music endpoints do, reads HA's state for the playing track
   (`media_content_id = pandora://track/<id>`), calls MA's command over
   MA's **websocket** (`ma_command()`; `MA_URL`/`MA_TOKEN` in `.env`), and
   for thumbs down skips and verifies.
3. **Kronk**: `_RATE_RE` routing pin → home agent → `rate_music` terminal
   tool (its result is spoken verbatim). ~3–5 s from the wake word.

## Gotchas

- **MA's HTTP `/api` collapses every typed error into "Internal server
  error"**; the websocket carries `error_code` + `details`. Use the
  websocket for anything that can fail. Even there, `details` is MA's
  generic text per error class ("The requested media item could not be
  found."), not the provider's message.
- **MA tokens inherit the user's role**; `auth/token/create` cannot scope
  one down. `MA_TOKEN` is minted under the admin user; a dedicated
  `service`-role user is the least-privilege follow-up (needs the MA UI).
  Long-lived tokens expire after one year and do not renew.
- **A thumb can land on the wrong station** if the same song is live in
  two stations' retained fragments (`_find_track` picks the freshest). The
  spoken line names the station so it is audible.
- **No HA fast tier yet.** A blueprint sentence → `rest_command` would make
  it ~1 s, but the first `rest_command` in HA needs an HA restart (shared
  with the shopping-list plan). Until then every thumb rides Kronk.
- **Carrying the patch**: every deliberate MA bump means rebasing the one
  commit, re-measuring the guard hashes, rebuilding, one named restart.
  Upstream acceptance is the exit (PR drafted in
  `PANDORA_STATIONS_AND_THUMBS_PLAN.md`).

## Blog hooks

- Running a patched upstream image the pinned way: derive from the stock
  release, hash-guard the files you replace, one commit per feature so the
  same commit becomes the PR.
- Three APIs to the same server (HA's integration, MA's HTTP, MA's
  websocket) and only one of them tells you why something failed.
- "Say less": the thumbs-down reply shrank from a sentence to two words
  because the next song was already playing.
