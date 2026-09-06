# Pandora thumbs by voice — Plan

Status: **planned 2026-09-05, not started.** Operator scope: Pandora
only (YouTube "like" and NAS favorites deferred — they are the easy
half and can ride the same tool later). Prerequisite: the Pandora bot
account (ROADMAP item 17).

## What the operator wants

"Thumbs up" / "thumbs down" said to a satellite while a Pandora station
plays, the way an Echo does it: the feedback lands on the station that
is playing, and thumbs down moves on.

## What was found (read-only, 2026-09-05, MA 2.8.8)

- **MA plays a Pandora station as one continuous "radio" item.** The
  provider (`music_assistant/providers/pandora/provider.py`) builds a
  1000-part playlist of fragment URLs and serves them itself through a
  dynamic HTTP route it registers on MA's stream server
  (`register_dynamic_route(f"/{instance_id}_stream", …)`). MA's queue
  therefore knows the *station*, not the track.
- **The current track exists only inside the provider's session.**
  `_update_stream_metadata()` picks it by elapsed time from cached
  `playlist/getFragment` responses (every 5 s → title/artist on the
  player). Those cached fragment tracks are Pandora's raw objects — the
  provider reads `songTitle`/`artistName`/`albumTitle`/`audioURL`/…, and
  the same objects carry the **`trackToken`** a thumb needs.
- **The provider exposes no feedback call**, and MA's provider model has
  no thumbs concept (only library add/remove for media items, which
  doesn't fit a station's current track).
- Consequence: **a client outside MA cannot thumb** — the track token
  never leaves the provider. A Pandora client in tool_service with the
  bot account's credentials would have its own session and different
  tracks. Route (c) below is dead; the plan is (a) with (b) as the
  long-term home.

## Design

### The one thing that has to live inside MA: a feedback route

A patch to the Pandora provider registers a sibling dynamic route,
`/{instance_id}_feedback?station_id=…&positive=1|0&key=…`, that:

1. looks up the station's session, takes the track the last metadata
   update marked current (store `current_track_idx` there — one line),
2. POSTs Pandora's web API `station/addFeedback`
   `{stationId, trackToken, isPositive}` with the provider's existing
   auth headers (same `_api_request` path as `getFragment`; the exact
   endpoint is verified in step 1 below before any patch is written),
3. returns `{title, artist, positive}` or Pandora's error message
   (tenet 7 — it goes straight to the speaker).

`key` is a shared secret (env on both sides): the stream server's
dynamic routes carry no MA auth, players must fetch them freely, and
tool_service arrives from a Docker bridge address.

**Delivery of the patch — a maintenance line, like the blueprint fork:**
`ma/patches/2.8.8/pandora/provider.py` in this repo, bind-mounted
read-only over the container path in `docker-compose.ma.yml`, with a
compose-time guard script that refuses to start unless the image's
original file hashes to the value the patch was written against. An MA
version bump (deliberate, tenet 3) means re-basing the patch or dropping
it. Upstream contribution (route (b)) is the exit: MA would need a
provider capability for "feedback on the playing item" plus an API
command — worth proposing once this works locally.

### Everything else lives where it does today

- **tool_service `POST /music/rate`** `{thumb: up|down, player?,
  origin_area?, origin_device?}`: resolve the player exactly as `/music`
  does (named → own device → own room → default); find the playing
  station — decision point: HA's `media_player` state for the MA entity
  (`media_content_id`, expected `pandora://radio/<station_id>`) if it
  carries the id, else MA's `player_queues/get_active_queue` with an MA
  service-user token (MA 2.8 has users/auth; least-privilege user for
  tool_service); refuse cleanly when the player isn't playing a Pandora
  station ("That's not a Pandora station"); call the feedback route;
  **thumbs down also moves on** — first version restarts the station
  (`play_media` again → fresh session, new tracks; Pandora's own skip
  limits apply either way), a true in-stream skip needs the provider to
  end the current part early and is a follow-up.
- **Kronk tier**: `rate_music(thumb)` terminal tool on the home agent,
  origin-aware; result spoken verbatim ("Thumbs up for Glory Box by
  Portishead" / Pandora's error).
- **Fast tier**: sentences in the blueprint fork ("thumbs (up|down)",
  "I (like|love|hate) this song", "never play this again") → HA
  `rest_command` to tool_service `/music/rate` with the satellite's
  device id — same origin logic, ~2 s.

### Which account the training lands in — decided by physics

Pandora thumbs go to the account that is playing. Kronk plays as the bot
account, so Kronk's thumbs train the bot's stations. Shared stations
seed the bot with the operator's training at share time and stay linked
mirrors **until the first thumb from the bot side forks them** into
independent copies. From then on the bot's copies are the house-trained
versions; the operator's personal stations keep only the operator's own
listening. Nothing already learned is lost; the two diverge afterwards.
If that's not wanted, the alternative is to keep Kronk on the personal
account for Pandora and accept the one-stream limit — the operator's
call, recorded before the build.

## Steps

1. **Verify the feedback API** with a one-off script against the bot
   account: log in the way the provider does, fetch one fragment of a
   *test* station, thumb one track up, confirm in Pandora's UI. No
   patch until this passes. (Also confirms whether `trackToken` is in
   the fragment objects — expected, not yet seen with eyes.)
2. Patch: `current_track_idx` bookkeeping + feedback route + shared key;
   overlay mount + hash guard in `docker-compose.ma.yml`; verify the
   route from the host.
3. tool_service `/music/rate` + tests (fake HA state / MA queue, fake
   feedback route, not-a-Pandora-station refusal, thumbs-down restart).
4. `rate_music` tool + home-agent prompt line + `_terminal_speech`
   mapping + tests; blueprint sentences + `rest_command`.
5. Live: thumb up a track from the office satellite, see it in the bot
   account's station; thumb down, hear the station move on; the
   not-a-Pandora case on a NAS album.
6. Docs; ROADMAP; upstream issue/PR sketch for MA.

## Out of scope (for now)

YouTube Music like/dislike (MA already supports library-add for tracks —
one call), NAS favorites, in-stream skip, thumbs from the web UI.
