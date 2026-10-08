# Pandora: create stations by voice, and thumbs — Plans

Status: **decided 2026-10-04: contribute upstream as two standalone PRs
(below). Parked the same day, not started.** To resume: post the drafted
comment (bottom of this doc) on music-assistant/server PR #5557, fork the
repo, then follow "Order and workflow". Nothing has been created on GitHub
or on Kronk's MA yet. Supersedes the mechanics
in `PANDORA_THUMBS_PLAN.md` (written against MA 2.8.8, where a station was
one stream; on 2.11 a station is a queue of real tracks). Operator goal:
(1) "play X radio" creates the station when it doesn't exist, the way the
Alexa skill does; (2) thumbs up/down by voice train those stations.

## How Alexa does it

Pandora wrote the Alexa skill themselves, on their private API: "if Alexa
can't find what you're asking for in your collection, she'll automatically
create a station" ([Pandora help](https://help.pandora.com/s/article/Pandora-and-Amazon-Echo-Alexa?language=en_US)).
There is no partner API to apply for — Pandora's developer program is
closed to new applicants. What we *can* reproduce is the call their own web
client makes when you press "create station": `POST
/api/v1/station/createStation` on `www.pandora.com`, the same unofficial
web API MA's provider already logs into (`API_BASE =
https://www.pandora.com/api/v1`; `auth/login`, `station/getStations`,
`playlist/getFragment`, `station/playbackResumed`).

## What the API offers (unofficial docs, 6xq.net, 2017 — the web client still uses v1)

| Need | Endpoint | Request | Notes |
|---|---|---|---|
| Find a seed | `/v1/search/fullSearch`, `/v1/search/getSeedSuggestions` | query text | Listed, not documented in detail. Results carry `pandoraId` like `AR:450147` (artist), `TR:…` (track), `GE:…` (genre). |
| Create station | `/v1/station/createStation` | `pandoraId` **or** `searchQuery`, `stationName`, `creationSource` | Returns the station object (`stationId`, `name`, `initialSeed`). Whether `searchQuery` alone works without a seed id is unverified — gate 0. |
| Thumb | `/v1/station/addFeedback` | `trackToken`, `isPositive` | `deleteFeedback`, `getStationFeedback` exist too. |
| Remove | `/v1/station/removeStation` | `stationId` | Cleanup for experiments. |

Headers: `X-CsrfToken` (cookie from the site), `X-AuthToken` (from login) —
exactly what MA's `create_auth_headers()` builds.

## What MA 2.11.0b2 has and lacks

- Provider speaks login / getStations / getFragment / playbackResumed only.
  Its "search" matches names of the account's own stations. No create, no
  feedback. The provider model has no feedback concept; maintainers
  declined star ratings (discussion #2461) but the ask there was library
  ratings, not radio feedback.
- Tracks are real queue items: HA exposes the playing track as
  `media_content_id = pandora://track/TR:<id>`. Kronk can tell *what* is
  playing without reaching into MA.
- The **`trackToken`** a thumb needs lives only in the provider's fragment
  cache (raw `getFragment` dicts; `_find_track(pandoraId)` returns one).
  It never reaches HA or MA's library.
- **Upstream is moving on (1):** the author of the dynamic-stations PR
  (#5557, merged 2026-08-31) wrote on 2026-08-11 that he is drafting
  "station creation and direct-play features for premium accounts" on a
  `feat/pandora-station-management` branch, with station deletion and a
  search wizard requested by testers. Nothing on feedback.

## Gate 0 — experiments that decide the plan (operator OK required)

Each one is a call to Pandora on the Kronk account, so: credentials in
`.env` (`PANDORA_USER`/`PANDORA_PASSWORD`, the bot account), a throwaway
script in the scratchpad, and anything created gets removed afterwards.

1. `fullSearch` / `getSeedSuggestions` for "Orbital": does it answer, and
   with `AR:` ids?
2. `createStation` with `searchQuery` only, then with `pandoraId`. Does
   the station appear in `getStations` and, after a Pandora sync, in MA?
   Remove it after.
3. **The one that matters for thumbs:** while a station plays in the
   office, call `addFeedback` from a *second* session (Kronk's own login)
   with `{trackToken}` obtained by that session's own `getFragment` for the
   same station — does Pandora accept feedback for a track the session did
   not serve? And does `addFeedback` accept `{stationId, pandoraId:
   "TR:…"}` without a token at all (the web client's track-page thumb)?
   Verified via `getStationFeedback`, then `deleteFeedback`.
   - **Yes to either** → thumbs need no MA change (Plan C covers both).
   - **No** → thumbs need code inside MA (Plan A or B).
4. Does a second login invalidate MA's session? (Pandora limits concurrent
   *streams*, not logins — the provider's `takeover_stream` is about
   streams. Verify by playing through MA before and after the experiment.)

## Plan A — Ride upstream

Wait for `feat/pandora-station-management` to ship (station creation via
MA's own API), test it on a beta, and have Kronk's "X radio" call it when
the station is missing. For thumbs, propose a provider feature
(`TRACK_FEEDBACK`: thumb the playing item on a dynamic station) plus an
API command to MA, with a Pandora implementation.
- Cost: none locally; a contribution upstream for thumbs.
- Risk: creation lands on their schedule (months, 2.12-ish); thumbs need
  maintainer buy-in that may not come.
- Fits tenets 2 and 3 best. Does nothing for the next few months.

## Plan B — Patch MA's Pandora provider (full result, a maintenance line)

An overlay of `providers/pandora/provider.py` bind-mounted over the image
copy (`ma/patches/<version>/pandora/provider.py` in the repo), with a
compose guard that refuses to start MA if the image file's hash isn't the
one the patch was written against. The patch adds:
- `create_station(query)`: `fullSearch` → `createStation`, then returns the
  station as an MA radio item.
- `feedback(track_id, positive)`: `_find_track(track_id)` → `trackToken` →
  `addFeedback`.
- Exposure to Kronk: a dynamic route on MA's stream server (no MA auth —
  guarded by a shared key, as in the old plan) or two provider-registered
  API commands.
- Cost: every deliberate MA bump means rebasing the patch (tenet 3's price,
  paid knowingly); the patch is ~150 lines. Also the natural seed for an
  upstream PR (Plan A's thumbs half).

## Plan C — Kronk-side Pandora client (no MA change for creation; thumbs if gate 0 says yes)

tool_service gets a small Pandora web-API client (login, csrf, the four
calls above) using the bot account's credentials from `.env`. "Play X
radio" becomes: Pandora station on the account? play it → else
`fullSearch` X; artist/track hit whose name matches X? `createStation`,
`tasks/run` MA's Pandora radio sync (seconds), play the new station,
speak "I made a Pandora station for Orbital." → else honest failure. No
match rule, no station (the Akon lesson).
- Thumbs ride here only if experiment 3 passes; the second session's
  tokens or a pandoraId-based feedback. Thumbs down also skips
  (`media_next_track` works on 2.11 queues).
- Cost: a second copy of Pandora's unofficial API in our code (same blast
  radius MA already carries — if Pandora changes v1, MA breaks too);
  ~3–6 s from "no station" to music (search + create + sync + start).
- Reversible: delete the client, stations stay.

## Decision (2026-10-04): two standalone upstream PRs, smallest possible

Operator direction: help upstream, keep it simple, station creation and
thumbs only, small commits. MA's integration branch is **`dev`** (there is
no `main`; stable tags are cut from dev; all recent PRs target dev).

The `feat/pandora-station-management` branch (chrisuthe's fork, last
commit 2026-08-11, never opened as a PR, 1,045 commits behind dev) is
stalled; its author moved to Sendspin/audio-analysis work in September.
Its station-creation commit is the reference: 118 lines including 74 of
tests, and it records Pandora API shapes already verified on a Premium
account — `POST /v1/search/fullSearch {query, count: 5}` → first item whose
`pandoraId` prefix is AR/CO/GE/TR → `POST /v1/station/createStation
{pandoraId, stationName}`; Pandora names the station after the seed
("Radiohead Radio"), not after the requested name. Reusing those shapes
replaces most of gate 0.

### PR 1 — Pandora: create a station from a name

- Seam: the existing provider hook `create_playlist(name, media_types)` +
  `ProviderFeature.PLAYLIST_CREATE`, name doubles as the seed query — the
  seam chrisuthe used and an MA member tested. It is slightly off (stations
  are Radio items since 2.11; the hook returns a Playlist), but it needs no
  change to the provider model or the models repo. A cleaner `create_radio`
  hook would be two PRs across two repos; ask the maintainers in the thread
  which they prefer before writing it, default to the existing seam.
- Content: ~50 lines in the provider (search → seed → create → return the
  station), 3 constants, the feature flag, tests with a fake `_api_request`
  (canned search/create payloads, assert the chosen seed). After creation
  the provider's station listing (live) already includes it; MA's library
  gets it at the next radio sync or `tasks/run`.
- Kronk side (separate, this repo): `/music/radio` → Pandora station →
  else MA `music/playlists/create` on the Pandora provider → sync → play →
  "I made a Pandora station for X." Honest failure when the search finds
  no seedable result.

### PR 2 — Thumbs on a dynamic radio

- No seam exists. Needs: a `ProviderFeature` (models repo, one line), a
  provider hook — scoped narrowly, e.g. `radio_track_feedback(prov_radio_id,
  prov_track_id, positive: bool)` — an API command on the music controller,
  and the Pandora implementation: `_find_track(pandoraId)` → the retained
  fragment's `trackToken` → `POST /v1/station/addFeedback {trackToken,
  isPositive}`; tests with canned fragments.
- Risk: maintainers declined star ratings (#2461, "favorites cover it").
  Frame this as training feedback a dynamic radio provider consumes, not a
  library rating; open the question in the #5557 thread or a discussion
  before writing code.
- Kronk side: `/music/rate` — resolve the player, read the playing
  `pandora://track/TR:…` from HA, call the command; thumbs down also
  `media_next_track` (real queues on 2.11).

### Order and workflow

1. One comment on PR #5557 (operator posts): we are porting the
   station-creation part of `feat/pandora-station-management` as a small
   standalone PR against dev, crediting the branch; ask if the author
   objects, and whether a radio-create hook or `create_playlist` is
   preferred. Mention thumbs as a follow-up and ask whether a
   feedback hook would be welcome.
2. Fork `music-assistant/server` under the operator's GitHub account;
   clone to `~/git-repos/<account>/music-assistant-server` (outside the
   kronk repo). Work on a branch per PR off `dev`.
3. PR 1: port, their test suite green, build a local image, run it as
   Kronk's MA (**a planned MA restart**, volume tarball first — same
   recipe as the 2.11.0b2 move), verify from the office: a name with no
   station creates one and plays; gibberish fails. Operator pushes and
   opens the PR.
4. Kronk-side creation path after PR 1 is merged or confirmed stable on
   our image (decide which: running our own image until a release carries
   it is a tenet-3 call for the operator).
5. PR 2 only after the thread answers the design question.

No GitHub credentials on Kronk: comments, fork, pushes and PRs are the
operator's actions.

## Draft comment for PR #5557 (operator posts; edit freely)

> Hi @chrisuthe — thanks for the dynamic-stations work, it's been solid
> here on 2.11.0b2 with two Voice PE satellites.
>
> We'd like to help land the station-creation half of your
> `feat/pandora-station-management` branch. To keep it easy to review, the
> idea is a small standalone PR against `dev` with only "create a station
> from a name" (your fullSearch → seedable pandoraId → createStation
> commit, ported onto the current provider, with its tests), crediting the
> branch and you as the design's author. Deletion and the search wizard
> would stay out of it.
>
> Two questions before we start:
> 1. Any objection, or anything you'd rather do yourself?
> 2. Your branch used the existing `create_playlist` hook with the name as
>    the seed. Now that stations are Radio items, would you and the
>    maintainers prefer that seam (no model change) or a dedicated
>    radio-creation hook (a models change too)? We'll go with whichever
>    you'd merge.
>
> Follow-up, separately: thumbs up/down on the playing track of a dynamic
> radio (Pandora `addFeedback`, token from the retained fragment). That
> needs a small provider hook + feature flag. Is a narrowly scoped
> "radio track feedback" hook something you'd consider, or is #2461's
> "favorites cover it" the standing answer?
