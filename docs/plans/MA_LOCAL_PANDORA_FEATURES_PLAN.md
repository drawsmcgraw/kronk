# Pandora thumbs and station creation in a locally built Music Assistant — Plan

Status: **planned 2026-10-05, not started.** Operator decision: build and
run both features in our own MA image before any upstream PR. Supersedes
the "PRs first" order in `PANDORA_STATIONS_AND_THUMBS_PLAN.md` (its API
research and draft #5557 comment still apply). Read-only research:
nothing built, called, or changed.

Status (2026-10-06): **thumbs live end to end, awaiting the operator's voice
test.** MA restart 1 done 08:51–08:53 (tarball
`~/backups/ma/ma-config-2.11.0b2-20261006-085110-quiesced.tgz`, 1.2 GB);
`docker-compose.ma.yml` → `kronk/music-assistant:2.11.0b2-kronk.1`;
`pandora/feedback` registered (bogus id → MediaNotFoundError); Pandora
plays and skips. Kronk side: `MA_TOKEN` (long-lived, minted under the
admin user via `auth/token/create` — MA tokens inherit the user's role, so
a least-privilege `service` user is a follow-up), tool_service
`ma_command()` over MA's **websocket** (the HTTP `/api` collapses typed
errors to "Internal server error") + `POST /music/rate`, `rate_music`
terminal tool on the home agent, `_RATE_RE` pin, 26 tests (suite 625).
Not done: the HA-tier blueprint sentence + `rest_command` (needs the first
HA restart — shared with ROADMAP 22), feature doc, Feature A.
Found during the restart: YouTube Music's cookie had been invalidated
("rotated in the browser") — unrelated to the patch; needs a fresh cookie
from the operator.
Clone of the public repo at tag `2.11.0b2` in
`~/git-repos/music-assistant/server`, branch `kronk/pandora-2.11.0b2`,
commit `c814b2490` "Pandora: add the `pandora/feedback` API command" (50
lines in `provider.py`/`constants.py`, 7 tests in
`tests/providers/pandora/test_feedback.py`; Pandora suite 91 passed; ruff
clean). Derived image `kronk/music-assistant:2.11.0b2-kronk.1` built with
`ma/build.sh` (hash guard passed). Test rig: a throwaway container
`ma-test` from the stock image with the clone bind-mounted and the `[test]`
tools installed via `uv` (the venv has no pip). Not yet done: the restart,
the MA token, the Kronk side, the operator's fork.

## 1. How MA is built, and how we run our own image

**The stock image.** `ghcr.io/music-assistant/server:2.11.0b2` is labelled
revision `7db7a160882b71cc7bb0c1dfc4b6a86c4ebc4876`; the annotated tag
[`2.11.0b2`](https://github.com/music-assistant/server/releases/tag/2.11.0b2)
points at that commit (2026-09-08). The six files in
`music_assistant/providers/pandora/` in the container hash identically to
that commit (checked 2026-10-05): a change written against the tag
applies to the running image exactly.

**How upstream builds it**
([Dockerfile](https://github.com/music-assistant/server/blob/7db7a160882b71cc7bb0c1dfc4b6a86c4ebc4876/Dockerfile),
[release.yml](https://github.com/music-assistant/server/blob/7db7a160882b71cc7bb0c1dfc4b6a86c4ebc4876/.github/workflows/release.yml)):

- Base: `ghcr.io/music-assistant/base:1.6.3` (Debian 13, Python 3.14.7,
  ffmpeg, a prebuilt PyAV wheel, jemalloc). The same base for stable, beta
  and nightly at this revision.
- The frontend is not built: it is the PyPI wheel
  `music-assistant-frontend==2.17.302`, a plain dependency.
- The release job stamps `project.version` into `pyproject.toml` (it is
  `0.0.0` in git), fetches a private `music_assistant/helpers/app_secrets.json`
  from `music-assistant/appvars` (bundled API keys for 13 providers —
  spotify, tidal, qobuz, deezer, apple_music, fanarttv, theaudiodb,
  lastfm, acoustid; see `helpers/app_vars.py`), runs `python3 -m build`,
  then `docker build` with `MASS_VERSION` and `BASE_IMAGE_VERSION`. The
  Dockerfile `ADD`s `dist/` and `uv pip install`s `requirements_all.txt`
  plus the wheel, then precompiles bytecode. Everything in the venv is
  pinned except seven `>=` ranges (aiodns, Brotli, awesomeversion,
  propcache, pyjwt, python-mpd2, uv), `uv:latest`, and one git-branch
  dependency (`aiolibdatachannel @ feat/certpem-testdrive`, installed in
  the image at commit `950e7f0f`). Torch CPU wheels are in the
  requirements; the image is 757 MB.

**Option 1 — full image build from the fork.** Clone the fork at the tag,
commit our changes, stamp a version (`2.11.0b2+kronk.1`), copy
`app_secrets.json` out of the running container (or accept that fanarttv /
theaudiodb / lastfm_recommendations lose their bundled keys — the three we
run; Pandora, YouTube Music and the filesystem provider need none), pin
the git dependency to `950e7f0f`, `python3 -m build`, `docker build`.
First build is network-bound (base image + torch), estimate 10–20 min;
rebuilds a few minutes. Result is not byte-identical to upstream: the
seven unpinned ranges and `uv:latest` resolve to today's versions, which
is a dependency upgrade as a side effect (tenet 3). Running their full
test suite needs the same venv (`scripts/setup.sh`, several GB).

**Option 2 — derived image: stock image plus our six files.**

```dockerfile
# ma/Dockerfile  (build context = the fork checkout)
FROM ghcr.io/music-assistant/server:2.11.0b2
ARG SP=/app/venv/lib/python3.14/site-packages/music_assistant/providers/pandora
# refuse to build on any base whose Pandora files are not the ones the patch was written for
RUN cd $SP && printf '%s\n' \
  '5ce725904292a8490621dde2e7dbaf5e6a2eadddeabc5f6a28a99c8776ed9226  provider.py' \
  '8bfac3d5340d5b27b18d2922e13c4b0c94daf49c33ea1268a4d7b179dc9aae17  constants.py' \
  '7333f539580396f81d558b67ab2a1f8e2c9e678ef1985289bea284bd44f15b85  __init__.py' \
  'c968117766b71c3e3c050220aa058cccb3e30e39424b6a3858605258fba94981  fragments.py' \
  'a0a8ec09f9be0e0723e215450177bfd798707bde5281d938658e33294876e9ae  helpers.py' \
  | sha256sum -c -
COPY music_assistant/providers/pandora/ $SP/
RUN python -m compileall -q $SP
LABEL io.kronk.ma.patch="pandora: feedback + create_station" io.kronk.ma.fork_rev="<git sha>"
```

Builds in seconds with no pull. Everything but the patched package is
the upstream artifact; the hash guard turns a base bump into a build
failure instead of a silent mismatch. Tests run in a throwaway container
from the stock image with the `[test]` extras installed into it
(`pytest==9.1.1`, `pytest-aiohttp==1.1.1`, `pytest-timeout`, `syrupy`)
and the fork bind-mounted — no multi-GB venv, nothing touches the running
container. A bind mount over the container path in compose would skip
the build but run an unpinned working tree; not used.

**Recommendation: option 2.** Tenets 2, 3 and 11 agree. Option 1 is only
needed once a change leaves the provider package (a
`music_assistant_models` change would force it — a reason to keep the
local version model-free).

**Pinning and the swap.** `docker-compose.ma.yml` keeps `image:` only —
no `build:` block, so no compose invocation can rebuild or recreate MA by
accident. `ma/build.sh` runs `docker build` and tags
`kronk/music-assistant:2.11.0b2-kronk.1`; the compose line becomes that
tag with a comment naming the fork commit. The swap is one **named MA
restart**, tarball first, the recipe from 2026-09-19 (ROADMAP "Skip on a
Pandora station"):

```bash
docker compose -f docker-compose.ma.yml stop music-assistant
docker run --rm -v kronk-ma_ma-config:/data:ro -v ~/backups/ma:/backup \
  --entrypoint tar ghcr.io/music-assistant/server:2.11.0b2 \
  czf /backup/ma-config-2.11.0b2-$(date +%Y%m%d-%H%M%S)-quiesced.tgz -C /data .
# edit the image tag, then:
docker compose -f docker-compose.ma.yml up -d music-assistant
```

Rollback: stock tag back, `up -d` — same MA version, no schema change.
Worst case, restore the tarball with MA stopped. Upstream bump: rebase the
two commits onto the new tag (`provider.py` is actively edited upstream —
expect a conflict every few releases), re-measure the guard hashes,
rebuild, one named restart; ~30 min when clean.

## 2. Feature A — create a Pandora station from a name

**The seam.** chrisuthe's reference commit
([1cac2b18](https://github.com/chrisuthe/server/commit/1cac2b18), branch
`feat/pandora-station-management`, 118 lines incl. tests) implements
`create_playlist(name, media_types)` + `ProviderFeature.PLAYLIST_CREATE_TRACKS`:
`POST /v1/search/fullSearch {query, count: 5}` → first item whose
`pandoraId` prefix is in `("AR", "CO", "GE", "TR")` → `POST
/v1/station/createStation {pandoraId, stationName}` → `_parse_station()`.
That seam is wrong on 2.11: the command behind it,
`music/playlists/create_playlist` (`controllers/music/media/playlists.py`,
scope `library.write`), hands the returned item to the playlists
controller's `_add_library_item(item: Playlist)` — a station is now a
`Radio(is_dynamic=True)` and would land in the playlists table. There is
no radio-creation hook in `models/music_provider.py`, and HA's Music
Assistant integration (client 1.3.5) exposes only `play_media`,
`play_announcement`, `transfer_queue`, `get_queue`, `search`,
`get_library` — no creation, no command passthrough.

Locally, then: a **provider-registered API command** —
`self.mass.register_api_command(name, handler, required_scope=…)` from
`loaded_in_mass()`, as `sonic_similarity`, `party`, `ai_radio` and
`smart_playlist` do, unregistered in `unload()`. Command
`pandora/create_station(query: str) -> Radio`, scope `Scope.LIBRARY_WRITE`:

1. `fullSearch` as in the reference commit, keeping its seed-prefix rule.
2. **Name match (the Akon lesson):** accept the seed only if the item's
   display name matches the query (`music_assistant.helpers.compare.
   compare_strings`, or substring either way); otherwise
   `MediaNotFoundError("Pandora has no artist or song called 'X'")`.
   The shape of a `fullSearch` item beyond `pandoraId` is not verified on
   our account — one read-only probe (`fullSearch {query: "Orbital",
   count: 5}` with the bot credentials, keys logged) before the code is
   written. chrisuthe measured the prefixes on a Premium account; the bot
   account's tier may answer differently.
3. `createStation {pandoraId, stationName: query}` — Pandora names the
   station after the seed ("Orbital Radio"), not after `stationName`.
4. `radio = self._parse_station(response)`, then `await
   self.mass.music.radio.add_item_to_library(radio)` so it is in the
   library immediately — no 12 h wait, no `music/sync` task (which exists,
   `providers=["pandora"], media_types=["radio"]`, but is a background
   task and racy for an immediate play). Return the library item: its
   `uri` (`library://radio/N`) and `name` are what the caller plays.
5. Errors stay typed: `handle_pandora_error` already maps Pandora's
   `errorCode`s; the JSON-RPC handler turns `InvalidDataError` into HTTP
   400 with the message and anything else into a logged 500.

Tests: upstream's `tests/providers/pandora/test_provider.py` builds the
provider with `PandoraProvider.__new__` and stubs `_api_request` by
endpoint URL; the reference commit's four tests port over with `Radio` as
the return type, plus two: name mismatch refuses, library add is called.
Lint: ruff 0.16.5, line length 100, Sphinx `:param:` docstrings,
`scripts/check_method_order.py`.

**Caller path.** tool_service talks to MA directly over its HTTP JSON-RPC
endpoint — `POST http://host.docker.internal:8095/api` with
`{"message_id": "1", "command": "pandora/create_station", "args": {"query": "Orbital"}}`
and `Authorization: Bearer <long-lived token>` (see §3 auth). New route
`POST /music/radio {query, player?, origin_device?, origin_area?}`:
try HA `music_assistant.play_media` typed `radio` exactly as `/music`
does (the Pandora provider's `search` lists the account's stations live,
so an existing station plays without creating anything) → on "Could not
resolve" call `pandora/create_station` → play the returned `uri` → poll
for `playing` → "I made a Pandora station: Orbital Radio. Playing in the
office." Failure text is Pandora's or MA's, verbatim (tenet 7).

**On demand first, transparent later.** "Create a station for X" /
"make a Pandora station for X" as a blueprint sentence (new KRONK change;
HA `rest_command` → tool_service, `rest_command.reload`, no HA restart)
and a `create_station` terminal tool on the home agent with a routing pin
in `routing.py`. Making "play X radio" create transparently means every
misheard "play X radio" from STT mutates the Pandora account; run the
on-demand path for a while, then decide with data. Latency, on-demand:
fullSearch + createStation ≈ 1–2 s, library add < 0.5 s, play + first
fragment 2–4 s → **~4–7 s** on the fast tier, +15–25 s if it rides the
LLM tier. Transparent inside "play X radio" would add the failed resolve
first (~1–2 s).

## 3. Feature B — thumbs on the playing track

**Where the token is.** `getFragment` tracks carry `trackToken`,
`allowFeedback`, `rating` and `stationId`
([6xq playlist](https://6xq.net/pandora-apidoc/rest/playlist/)); the
provider keeps the raw dicts in `PandoraStationSession.fragments`
(deque of 4 per station, up to 10 stations, `fragments.py`) and
`_find_track(prov_track_id)` returns the freshest copy. The call is
`POST /v1/station/addFeedback {trackToken, isPositive}`
([6xq stations](https://6xq.net/pandora-apidoc/rest/stations/#add-feedback));
the response names the station, song and artist — enough for the spoken
line without a second lookup. `getStationFeedback {stationId, positive,
pageSize}` verifies; `deleteFeedback` exists for cleanup. The thumb goes
out through the same `_api_request` and session that fetched the fragment,
so the old plan's cross-session question (gate 0 item 3) does not arise.

**Exposure options.**

| Option | Verdict |
|---|---|
| (i) Provider-registered API command | **Yes.** Supported in 2.11 (above). `pandora/feedback(item_id: str, positive: bool) -> dict`, scope `Scope.QUEUES_CONTROL`. Real auth, real scopes, ~30 lines. |
| (ii) Dynamic route on the stream server | Works (`helpers/webserver.py:register_dynamic_route`, used by `live_announcements` and the image proxy) but carries no auth — the old plan's shared key was a workaround for a problem (i) no longer has. No. |
| (iii) Config `ACTION` button | No arguments, and "which track" would have to be guessed from queues. No. |
| (iv) Favorites (`music/favorites/add_item` → provider hook) | Up only, the track is not a library item, and HA has no favorites service either. No — but it is the framing to offer upstream ("favoriting a track on a dynamic radio is a thumb"). |

Provider side: `_find_track(item_id)` → refuse with a named error when
not retained or `allowFeedback` is false → `addFeedback` → return
`{song, artist, station, positive}`. Note `_find_track` picks the freshest
fragment holding the song across stations; if one song sits in two
stations' live fragments the thumb can land on the other station. Speak
the station Pandora names in the response so a mis-land is audible;
accept the rest. Command names are global — a second Pandora instance
would hit `RuntimeError: already registered`; register once, keyed on
the first instance, and log it (we run one).

**Kronk side.** HA exposes the playing item as
`media_content_id = pandora://track/TR:…` (`queue.current_item.uri`; the
instance id is literally `pandora`, confirmed in `/data/settings.json`)
and the station name in `media_album_name`. `POST /music/rate {thumb:
up|down, player?, origin_*}`: resolve the player as `/music/control`
does → state must carry `pandora://track/` else "That's not a Pandora
station" → `pandora/feedback` → for down, `media_player.media_next_track`
over `ha_call_service` and poll for `media_title` to change (the skip
verification the ROADMAP already wants) → "Thumbs down for X by Y on
Orbital Radio. Skipping." `rate_music(thumb)` terminal tool on the home
agent, a `_RATE_RE` pin ("thumbs up/down", "I like/love/hate this
song", "never play this again"), and a blueprint sentence → `rest_command`
for the ~2 s tier.

**Auth for the caller.** MA 2.11 has users, roles and scopes
(`music_assistant_models/auth.py`); the JSON-RPC handler checks a Bearer
token and the handler's scope. Create a dedicated MA user for Kronk and a
long-lived token (MA UI → user profile → "Long-lived access tokens", or
`auth/token/create` after `auth/login`; expires after 1 year, no
auto-renew); `MA_URL` and `MA_TOKEN` in `.env`, passed to tool_service
only (tenet 10; CLAUDE.md's "no other secrets exist" line gets updated).
Open: whether a non-admin role carries `library.write` and
`queues.control` — check `auth/scopes` for that user before wiring.

Tests: provider — canned fragment with `trackToken`, assert the
addFeedback body, refusal when not retained / `allowFeedback` false,
response passthrough. Kronk — `tests/test_music_rate.py` with fake HA
state and a fake MA `/api` (httpx mock): up, down + verified skip, the
not-a-Pandora refusal, MA 400 text spoken verbatim; pins in
`test_music_pin.py`; `./scripts/run_tests.sh`.

## 4. Sequencing

1. **Fork and branch (operator: fork on GitHub; clone to
   `~/git-repos/<account>/music-assistant-server`, outside this repo).**
   Branch `kronk/pandora-2.11.0b2` off tag `2.11.0b2`. One commit per
   feature, each self-contained with its tests, so either can be offered
   upstream as-is; nothing else on the branch.
2. **Thumbs first.** Smaller, its API shape is fully documented and the
   token is visibly in the fragments, it mutates nothing but feedback, and
   it exercises the whole new chain (fork → test container → derived
   image → named restart → MA token → tool_service → voice) with the least
   unknown. Creation has one unverified shape (fullSearch items) and a
   bigger Kronk side. If the missing household stations matter more than
   proving the chain, the operator adds Orbital / French Dinner / French
   Cafe by hand in Pandora's UI today and the order stands.
3. **Build and prove.** Provider tests green in the throwaway container;
   `ma/build.sh` → `kronk/music-assistant:2.11.0b2-kronk.1`.
4. **Named MA restart 1** (operator picks the moment): tarball, tag swap,
   `up -d`. Verify: MA log shows the command registered; `curl` the `/api`
   endpoint with the token — `pandora/feedback` on a bogus id returns the
   named 400, not a 500; Pandora station plays and skips as before.
5. Kronk side for thumbs: route, tool, pin, blueprint sentence +
   `rest_command`; `run_tests.sh`; `up -d --build tool_service` and
   orchestrator, nginx restart, `/api/chat` check.
6. **Office satellite**: play "Massive Attack radio" in the office →
   "thumbs up" → confirmation names the song and station; "thumbs down"
   → song changes within ~1 s and says so; a NAS album → "thumbs up" →
   the refusal. Operator confirms the thumb in Pandora's UI signed in as
   the bot account (or the provider logs `getStationFeedback`).
7. Creation: the one fullSearch probe (operator OK — it is a call on the
   bot account), code + tests, `…-kronk.2`, **named MA restart 2**, Kronk
   route/tool/sentence, office test: "create a station for Orbital" → "I
   made a Pandora station: Orbital Radio" → "play Orbital radio" plays on
   the fast tier (it is now a library item); "create a station for zzqx
   florbent" → refusal and `getStations` count unchanged.
8. Docs: `docs/features/pandora-thumbs-and-stations.md`, ROADMAP lines,
   this header. Then upstream: post the drafted #5557 comment, open PR 1
   (creation) against `dev` — the maintainers may still want the
   `create_playlist` seam or a new radio hook; PR 2 (feedback) framed as
   a provider feature + `models` flag. Both from the same two commits.

One branch, shipped one feature at a time; two planned MA restarts (plus
rollback if needed), tarball first, never as a side effect.

## 5. Risks and open decisions

- **Unofficial API, automated client.** Pandora can change v1 or object
  to scripted feedback/creation; MA already carries that exposure. Volume
  is a few calls a day.
- **Account tier.** fullSearch/createStation behaviour was measured by
  chrisuthe on Premium; the bot account's answers are unverified until
  the probe. Creation may need a Premium bot account.
- **Account mutation by voice.** On-demand only until the name-match rule
  has data; transparent creation is a separate decision.
- **Wrong-station thumb** when a song is live in two stations (rare,
  audible).
- **Carrying a patch.** Every deliberate MA bump costs a rebase and a
  rebuild; the hash guard makes forgetting it fail at build, not at 7 am
  in the kitchen. Upstream acceptance is the exit.
- **A new secret** (`MA_TOKEN`, 1-year expiry, no renew) and a new MA
  user — role/scope to confirm; a calendar note for the expiry.
- **Base-image pull.** None needed for option 2; `docker build` needs
  only the image already present.

Operator decisions: option 2 (derived image) vs a full build; thumbs
first; on-demand only for creation at first; a dedicated MA user for
tool_service; thumbs-down always skips (Alexa's behaviour) — yes unless
told otherwise; image tag scheme `2.11.0b2-kronk.N`.

Operator's hands: GitHub fork and pushes; the MA long-lived token; the
fullSearch probe approval; the two restart windows; the "Okay Nabu"
tests; checking the thumb in Pandora's UI as the bot account.
