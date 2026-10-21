# Pandora station upsert — "play X radio" creates the station if missing

Status: **planned 2026-10-16, Gate 0 in progress.** Steps 1 (dev re-check)
and 3 (read-only probe, 2026-10-19) done; step 4 done (`createStation` is
idempotent); Discussion
deferred until the PR is ready. Path 1 chosen (Decision 5). Branch
`pandora-create-station` cut; no code
yet. Supersedes Feature A's
`pandora/create_station` command design in
`MA_LOCAL_PANDORA_FEATURES_PLAN.md` §2 and drops the YouTube Music fallback
from ROADMAP item 23 (see Decisions).

Read first: `MA_UPSTREAM_LESSONS.md` (runbook, test rig, gotchas) and
`MA_FORK_STATUS.md` (what is running, the fork, restart recipe).

## In plain words

You say "play Orbital radio". Kronk asks Music Assistant to play Orbital
radio. That's Kronk's whole job.

Inside Music Assistant, the Pandora provider does the rest:

1. **Check.** Do you already have a station seeded from Orbital? It compares
   Pandora's id for Orbital against the seed of each of your stations, so a
   renamed station still counts.
2. **Found** → play your station. Same as today.
3. **Not found** → look Orbital up in Pandora's catalogue. If nothing there
   is named exactly "Orbital", refuse: "Pandora has no artist or song called
   'Orbital'." No junk stations from a near miss.
4. **Create** the station on Pandora, then play it. This happens the moment
   playback starts, inside the same request. Nothing is added to MA's
   library on the way, so MA's slow metadata scan never runs (from the
   code; the first live test times it to confirm).
5. Kronk replies "Made a new Pandora station: Orbital Radio. Playing in the
   office." (or the usual reply if the station already existed).

The new station shows up in MA's station list at the next library sync.
Removing it from the MA library deletes it on Pandora.

Why this path: Kronk doing the steps itself (check, "add to library",
wait, play) would go through MA's add-to-library command, which waits for
a full metadata scan before answering — 24–31 s when thumbs hit it. That is
a dealbreaker for voice. If testing shows the create-and-play path is still
too slow, Kronk falls back to fire-and-forget: reply at once, start playback
in the background, push the operator's phone on failure (the thumbs-up
pattern, `MA_UPSTREAM_LESSONS.md` §9).

## Goal

"Play Orbital radio" plays the account's Orbital station if it exists, and
otherwise creates it on Pandora and plays it — in one request from the
caller's point of view (an upsert). No check-then-create round trip from
Kronk or HA. Works on both voice tiers: the HA blueprint fast path
(`music_assistant.play_media`, typed radio) and the Kronk `play_music` tool,
with no new Kronk endpoint.

## Decisions (operator, 2026-10-16)

1. **Transparent creation.** "Play X radio" creates on a miss. Misfires
   (STT mishears creating junk stations) are accepted.
2. **"Radio" means Pandora.** YouTube Music radio only when the request
   says "on YouTube Music". ROADMAP item 23's YT fallback is dropped.
3. **Create on a station miss; refuse on a catalogue miss.** No station by
   that name → create. But Pandora's `fullSearch` is fuzzy and returns a
   best guess for anything (the Akon lesson); if no catalogue result's name
   matches the query, refuse with "Pandora has no artist or song called
   'X'" rather than seed a station from an unrelated result.
4. **The upsert lives in the MA Pandora provider**, not in Kronk. Kronk
   talking to Pandora directly was considered and rejected: MA still has to
   learn about the new station before it can play it (more round trips, not
   fewer), it duplicates the Pandora credentials (tenet 8), and a second
   Pandora session risks a stream takeover (seen as HTTP 400 on 2026-10-16).

5. **(2026-10-19) Path 1: MA creates on play; no metadata scan on the
   voice path.** Kronk only searches and plays. Rejected: Kronk driving
   check → `music/library/add_item` → poll → play, because `add_item`
   awaits a full metadata scan (`controllers/music/controller.py:1797`) and
   its provider call (`library_add`) runs in the background with errors
   only in MA's log. The play path does not await a scan: `get_item` only
   *schedules* a background refresh, and only for library items
   (`controllers/music/media/base.py:782-784`). Hard requirement: no
   create path on voice may wait on a metadata scan. Fallback if latency
   is still bad: Kronk fire-and-forget (see "In plain words").

## Design (provider-only; iHeartRadio pattern; no core/models change)

Precedent: iHeartRadio — `search` returns synthetic dynamic `Radio` items,
`get_radio` resolves them, the first `get_dynamic_radio_tracks` creates the
station (`iheartradio/provider.py:143-146, 316-325`, `parsers.py:91-122`,
`stations.py:112, 274-290`).

1. **`search`** (`pandora/provider.py:178`). Today: one live `getStations`
   per radio search, substring match on station names. New, when
   `MediaType.RADIO` is requested and the toggle (5) is on:
   - Run `getStations` and `fullSearch {query, count: 5}` concurrently
     (`asyncio.gather`) — one Pandora round trip of latency, not two.
   - Existing stations that match → returned first, as today.
   - No existing match → pick the first `fullSearch` item whose `pandoraId`
     prefix is AR/CO/GE/TR **and** whose name matches the query
     (`compare_strings` or a normalized equality — decide after the probe
     shows the field names). Return it as a synthetic dynamic `Radio`:
     id `seed:<pandoraId>`, name "<Seed name> Radio".
   - No name match → no seed item; MA reports "could not resolve" as today.
   - `fullSearch` failure must not break station search: log, return
     existing-station results only.
   - Search must never create anything (the MA UI searches as you type).
2. **`get_radio`** (`provider.py:210`) resolves `seed:<pandoraId>` to the
   synthetic `Radio` without calling Pandora (or with one cheap lookup).
3. **`get_dynamic_radio_tracks`** on a `seed:` id: if a station for that
   seed already exists (dedupe — by seed id if `getStations` exposes it,
   else by name), use it; otherwise `POST station/createStation {pandoraId,
   stationName}`. Then stream from the real station id. Keep a
   `seed → stationId` map so later fragment fetches skip the lookup.
   Pandora errors stay typed via `handle_pandora_error`; 1005 "Station
   limit reached" is already mapped and must reach the caller verbatim.
4. **Library.** The created station is **not** added to the MA library on
   the play path (that would await the metadata scan, Decision 5). The
   provider refreshes its own station list in memory after `createStation`;
   MA's library picks the station up at the next sync. `LIBRARY_RADIOS_EDIT`
   + `library_remove` → `deleteStation` ships in the first PR (operator,
   2026-10-19); it is also the test cleanup. `library_add` on a `seed:`
   item may also create the station (MA UI "add to library"); that path
   pays the scan, which is acceptable in the UI and never used by Kronk.
5. **Config toggle** for upstream: provider config entry "Offer new
   stations in search" (translatable label — `check_translatable_labels`),
   default off upstream, on in Kronk's image config.

## Kronk side (small)

- No new endpoint. `/music` radio requests already resolve through MA
  search; the seed radio is just another result.
- Reply text: when the playing content id/name shows a just-created station,
  say "Made a new Pandora station: Orbital Radio. Playing in the office."
  Mechanism TBD after the probe (seed id in `media_content_id`, or compare
  against the station list before/after).
- "Radio" defaults to Pandora: confirm the blueprint / `play_music` radio
  path targets Pandora unless "on YouTube Music" is said; adjust routing
  pins and tests if not.
- Tests in `tests/` for the reply and routing changes; `./scripts/run_tests.sh`.

## Gate 0 results

### Step 1 — `dev` re-check (2026-10-16, upstream `dev` eb4694bc5)

- `dev` moved one commit past f7aec9c38 (#7065, Audiobookshelf). No change
  to `providers/pandora`, `providers/iheartradio`, `models/music_provider.py`
  or `models/media_capabilities.py`. The Pandora package is still
  byte-identical to 2.11.0b5, so one change still serves image and PR.
- No create-station seam exists. Provider hooks touching radios:
  `get_library_radios`, `library_add`/`library_remove`, `create_playlist`
  (Playlist only), and `get_radio`/`get_dynamic_radio_tracks`
  (`models/media_capabilities.py:97, 105`). The only station-creation code
  upstream is iHeartRadio's `stations.py:112, 274`.
- `ProviderFeature` enum: identical between models 1.1.223 (in the running
  image) and 1.1.225 (pinned by `dev`). Relevant members: `LIBRARY_RADIOS`,
  `LIBRARY_RADIOS_EDIT`, `FAVORITE_RADIOS_EDIT`, `PLAYLIST_CREATE_RADIOS`.
  Nothing station-create shaped.
- Pandora method line numbers on `dev`: `search` 177, `get_radio` 209,
  `get_dynamic_radio_tracks` 216. `get_radio` walks `_get_stations()` and
  raises `MediaNotFoundError` on a miss, so `seed:` needs its own branch
  before that loop.
- Open upstream PR #7021 (Apple Music stations under Radio) is a second
  precedent: Apple's `search` returns dynamic `Radio` items when radio is
  requested. It also changes `controllers/music/media/radio.py`
  `_assemble_versions` to skip dynamic radios when matching versions, so
  seed radios will not be merged into other providers' stations by name.
  Watch for it merging; no action needed.

### New constraints found

- **Throttling is an upstream rule.** Upstream `AGENTS.md` (usage policy):
  flag any change that would "call a service's API without throttling, or
  re-fetch what the cache already holds." The Pandora provider has no
  throttler today; `_api_request` calls the HTTP session directly. The
  Copilot review on our thumbs PR #7068 flagged exactly this ([PROBLEM],
  provider.py:342). Station upsert adds a `fullSearch` per radio search and
  the MA UI searches as you type, so the design needs: a provider-scoped
  `ThrottlerManager` + `@throttle_with_retries` on the request path
  (iHeart: `iheartradio/api.py:74, 113`, 5 req/s shared), and a short-lived
  cache of `fullSearch` results by normalized query. Do it once in
  `_api_request` so thumbs and upsert share it; coordinate with the #7068
  fix so the two branches don't conflict.
- The provider's own `search` comment says catalogue search "requires the
  legacy endpoints this provider does not speak". chrisuthe's branch used
  the REST `search/fullSearch`. The Gate 0 probe settles which is true for
  this account.
- PR #7068 is held as a draft by the automated critical-issue gate: two
  [CRITICAL] findings (clearing a rating leaves Pandora's thumb in place;
  `_find_track` picks the newest fragment across stations, so a rating can
  hit the wrong station's token) plus the throttling [PROBLEM]. Not this
  feature's scope, but the second finding touches the same session/fragment
  code the upsert extends.

### Step 2 — Discussion

Draft written for the operator to rewrite and post (AI policy). Asks: is the
iHeart pattern the right seam; is create-on-first-play acceptable behind an
opt-in toggle; any history on `fullSearch`. Status: not posted.

### Step 3 — read-only probe (2026-10-19, operator OK)

Run as a one-off script inside `music-assistant-server` (no restart),
credentials from Kronk `.env` (`PANDORA_USERNAME`/`PANDORA_PASSWORD`), using
the provider's `get_csrf_token` + `create_auth_headers` and MA's `json_dumps`
as the session serializer (hand-rolled requests trip Pandora's bot check,
403 `s2s_high_score`). Calls: login, `fullSearch` ×2, `getStations`. No
fragment, playback, create or feedback calls. MA logged nothing Pandora-related
afterwards; the second login did not disturb the provider's session.

- **`fullSearch` works on the REST API.** `POST /api/v1/search/fullSearch
  {query, count: 5}` → HTTP 200 in 0.96 s ("Orbital") and 0.33 s ("zxqv").
  The provider comment about "legacy endpoints" is wrong for this endpoint.
- **Response shape:** `{items: [...], numArtists, numTracks, numGenres,
  numOther, numAll, type}`. Each item: `type` ("artist"/"track"),
  `pandoraId` (`AR:`/`TR:` prefix), `musicId` (`R…`/`S…`), `name`, `art`,
  `seoToken`; artists add `listenerCount`, tracks add `artistName`.
  No annotations map; items are self-contained.
- **`count` is not honored:** "Orbital" returned 10 items (5 artists, then 5
  tracks). Artists come before tracks.
- **Duplicate exact names exist:** two artists named exactly "Orbital"
  (`AR:70895`, `AR:8551735`), listed in that order. Tie-break needed: take
  the first exact match (Pandora's own ranking), or the higher
  `listenerCount` (not captured by this probe; check during build).
- **The fuzzy-junk case is real:** "zxqv" returned three tracks by artist
  "zhqv". No item name equals the query, so the name-match rule (Decision 3)
  refuses it, as intended.
- **`getStations` exposes the seed:** every entry carries
  `initialSeed {musicId, pandoraId}` (e.g. "Santana Radio" →
  `AR:2455`, "Mushroom Jazz Radio" → `TR:75117110`) plus
  `stationFactoryPandoraId` (`SF:<n>:<musicId digits>`). Dedupe is an exact
  seed-id match, not a name match. This matters: several of the 8 stations
  are renamed ("Anime Pop", "German Soundscape") and would never match by
  name.
- Other station fields worth knowing: `stationId` (what MA uses today),
  `pandoraId` `ST:0:<stationId>`, `allowDelete`, `stationType`
  (`SEEDED_STATION`).

Design consequences:

- Name match: normalized exact equality on `name` for artists and genres;
  for tracks, `name` and optionally "<name> by <artistName>". First exact
  match in Pandora's order wins.
- `search` dedupes the seed against `initialSeed.pandoraId` of existing
  stations and returns the existing station instead of a seed item.
- `get_dynamic_radio_tracks` on `seed:<pandoraId>` does the same dedupe
  before `createStation`, so a station made elsewhere since the search is
  reused.

### Step 4 — operator decisions (2026-10-19)

1. Test seed: Orbital (`AR:70895`, the first exact-name artist match).
2. Idempotency check approved: one `createStation` for Santana (`AR:2455`),
   which the account already has. A duplicate "Santana Radio" is acceptable.
3. Build `deleteStation` (via `library_remove`, below) in the first PR; it is
   also the test cleanup.
4. Upstream Discussion is posted once the PR is ready, not before. (Deviates
   from `MA_UPSTREAM_LESSONS.md` §2 by operator choice; the seam check below
   is the mitigation.)

### Step 4 result — `createStation` is idempotent (2026-10-19)

One `POST /api/v1/station/createStation {pandoraId: "AR:2455", stationName:
"Santana Radio"}` against an account that already had a Santana station
(seed `AR:2455`, created 2026-10-02):

- HTTP 200 in 0.12 s. The response is the **existing** station: same
  `stationId` 181450207056112163, same `dateCreated`. The response has the
  same shape as a `getStations` entry (plus richer `initialSeed`: artist
  name, art), so it parses with `_parse_station`.
- Station count 8 before, 8 after. No duplicate; nothing to clean up.
- MA logged nothing Pandora-related afterwards.

Consequences:

- `createStation` is itself an upsert for a matching initial seed. The play
  path can call it directly on a `seed:` id, with no `getStations`
  pre-check: it returns the existing station or a new one, and the
  `stationId` in the response is what we stream.
- Search still dedupes against `initialSeed.pandoraId`, so the result list
  shows the real station (with its real name, e.g. a renamed one) rather
  than a seed item.
- Not covered by this test: a station whose name was changed, and an artist
  that is a secondary (added) seed of another station. Cover both in unit
  tests by trusting whatever `stationId` Pandora returns; check the renamed
  case live during the build ("Anime Pop", seed `AR:1442243`).
- First-play cost of a new station is ~0.1 s for the create plus the first
  fragment, well under the 4–6 s estimate's create share.

### Seam check for create/delete (2026-10-19, upstream `dev` 7e6da608f)

The thumbs lesson: a maintainer pointed at an existing hook. For this
feature:

- **Delete has an existing seam, and we use it.** "Remove from library"
  (`controllers/music/controller.py:1690-1711`) calls
  `provider.library_remove(item_id, media_type)` for providers that declare
  `LIBRARY_RADIOS_EDIT`, only on mappings the user may write to
  (`_write_target_mappings`), and only when 2-way library sync is enabled
  for that provider (`library_sync_back_enabled`). The call is a
  `create_task` — fire-and-forget; errors only reach MA's log.
  `LIBRARY_RADIOS_EDIT` precedents: iheartradio, siriusxm, builtin.
- **Create via "add to library" also exists** (`controller.py:1760-1785`,
  `provider.library_add`, same gates, also `create_task`). iHeartRadio uses
  it. We don't use it as the primary create path: the goal is "play X radio"
  in one request, and a seed radio is not in anyone's library. `library_add`
  on a `seed:` item may also create the station (cheap to support).
- **Overlap to expect in review: MA's own radio.** `radio_mode` (deprecated)
  → the `radio_playlist` provider builds a dynamic queue from a seed item
  using providers' `SIMILAR_TRACKS` (`player_queues/queue_loader.py:789`,
  `controllers/music/media/tracks.py:1939-1956`). That is MA-generated radio,
  not a provider station, and Pandora has no similar-tracks API. The likely
  maintainer question is "why not radio mode?"; the answer is that Pandora's
  value is its own station (thumbs train it, it follows the account across
  apps). Put that in the Discussion and PR text.
- No `ProviderFeature` or hook for provider-side station creation exists in
  models 1.1.225 or `dev`.

## Branches and image

- `pandora-create-station` in `~/git-repos/drawsmcgraw/musicassistant-server`,
  created 2026-10-19 off `upstream/dev` 7e6da608f (Pandora package unchanged
  since f7aec9c38). Independent of `pandora-track-feedback` (PR #7068) so
  each PR stands alone.
- Throttler: lives in thumbs commit 7822176fd, which does not apply alone
  onto `dev` (`provider.py` conflicts with the earlier thumbs commits). Do
  not duplicate it. Build upsert without its own throttler; the upsert PR
  waits for #7068 to merge, then rebases onto `dev` and routes
  `fullSearch`/`createStation`/`deleteStation` through the merged throttler
  and adds the `fullSearch` cache. If #7068 stalls, split the throttler into
  its own small PR that both branches rebase onto.
- Kronk image: built from a local-only `kronk-image` branch that merges
  `pandora-track-feedback` and `pandora-create-station`. Never pushed or
  PR'd; the image's `io.kronk.ma.fork_rev` label carries its merge commit.

## Build log

### 2026-10-19 — provider built, tested, image built; restart pending

Branch `pandora-create-station` (worktree
`~/git-repos/drawsmcgraw/musicassistant-server-create-station`), off
`upstream/dev` 7e6da608f. Uncommitted; the operator commits.

What the provider change does (`providers/pandora/`):

- `__init__.py`: `+ProviderFeature.LIBRARY_RADIOS_EDIT`.
- `constants.py`: `SEARCH_ENDPOINT` (`search/fullSearch`),
  `CREATE_STATION_ENDPOINT`, `REMOVE_STATION_ENDPOINT` (`station/removeStation
  {stationId}`, from chrisuthe's branch; not yet called live),
  `SEEDABLE_PREFIXES`, `SEED_RADIO_PREFIX = "seed:"`, catalogue count/cache
  TTL, two config keys.
- `strings.json` + regenerated `translations/en.json` (the
  `build_translations_source` hook writes it): "Offer new stations in search"
  and "Allow deleting stations", both default off.
- `provider.py`:
  - `search`: with the option on, `getStations` and the catalogue search run
    concurrently. Stations matching by substring come first, as before. The
    first catalogue item with a seedable prefix whose name matches the query
    under `compare_strings(strict=True)` (a trailing " radio" in the query is
    ignored) becomes a seed radio `seed:<pandoraId>` named "<name> Radio",
    unless a station's `initialSeed.pandoraId` equals it — then that station
    is returned (renamed stations included). Catalogue failures are logged and
    the station results still return. Search never creates.
  - `_search_catalogue` is cached with MA's `@use_cache` (1 h).
  - `get_radio("seed:…")` answers from the seed radios search offered; after a
    restart it borrows the name of the station seeded from it, else a
    placeholder. No Pandora write.
  - `get_dynamic_radio_tracks("seed:…")` → `_station_id_for`: `createStation`
    once (under a lock), map seed → station id, stream that station. Relies on
    `createStation` being idempotent (Gate 0 step 4).
  - `library_add`: a seed radio is created; an existing station is a no-op.
  - `library_remove`: only with "Allow deleting stations" on;
    `removeStation` for the station (a seed id resolves via the map or
    `initialSeed`, never by creating); forgets the session and mapping.
- `tests/providers/pandora/test_create_station.py`: 21 tests (seed offered on
  miss, existing station wins incl. renamed, no duplicates, junk refused,
  first exact match, trailing "radio", option off, catalogue failure degrades,
  search never creates, get_radio without Pandora, create once then reuse,
  streams the returned station, 1005 surfaces, plain station unchanged,
  library add ×2, library remove ×4). `test_provider.py` fixture turns options
  off.

Verification (ma-pr-test rig, Python 3.14):

- Pandora suite 106 passed. `pre-commit run --all-files` rc=0 (after fixing
  three mypy findings in the new test file and taking the hook's ruff fixes).
- Full suite: 21,914 passed, 5 failed, 11 errors; clean `dev` baseline
  (confirmed free of the change): 21,893 passed, the same 5 failures
  (sonic_analysis ×3, sendspin, tags) and the same 11 benchmark errors.

Image `kronk/music-assistant:2.11.0b5-kronk.5`:

- Built with `docker build` (uncommitted), label `io.kronk.ma.fork_rev =
  3e92d690e+uncommitted-create-station`, from a scratch worktree of
  `pandora-track-feedback` (3e92d690e, thumbs + review fixes + throttler) with
  this diff applied. Conflicts were additive only (feature set, constants,
  imports, init) and resolved by keeping both. Merged tree: 132 Pandora tests
  pass, pre-commit rc=0. The thumbs throttler sits in `_api_request`, so
  catalogue/create/delete calls are throttled in the image.
- The Dockerfile copies only the provider package, so the image lacks the two
  new `translations/en.json` strings (cosmetic: labels in MA's UI).
- `ma/Dockerfile` label updated to name both patches. Hash guard unchanged.
- Rebuild with `ma/build.sh` from a committed `kronk-image` merge branch after
  the operator commits.

Pending: the named MA restart to swap kronk.4 → kronk.5 (all players idle at
the check), then enable both options in the Pandora provider config (a
provider reload, not a restart), then live tests on the office speaker.

Known gaps:

- MA's `play_media` by name tries an exact library name first ("orbital" ≠
  "Orbital Radio"), then takes the first radio of a cross-provider search.
  Whether the seed radio wins against other radio providers is the first live
  question.
- `library_add` on a seed radio leaves a `seed:` library item next to the real
  station once the library syncs (a UI-only path; Kronk never uses it).
- Kronk's "Made a new Pandora station" reply is not built: HA exposes Pandora
  playback as `pandora://track/…`, so the signal has to come from what the
  live test shows HA/MA exposing.

## Gate 0 — before any code

1. Re-verify against upstream `origin/dev` (not 2.11.0b5): grep for
   station/radio/seed/create seams and any `ProviderFeature` that fits
   (lessons §1). Check Pandora package diff `2.11.0b5..origin/dev`.
2. Draft an upstream Discussion for the operator to post in their own words
   (AI policy): "Pandora station creation via the iHeartRadio pattern,
   behind a provider config toggle — acceptable seam?" Build may proceed
   locally in parallel; the PR waits for an answer.
3. **One read-only probe, with operator OK at the time of the call:**
   `fullSearch {query: "Orbital", count: 5}` on the Kronk account; log
   keys and prefixes. Also record whether `getStations` entries carry the
   seed's `pandoraId`. No `createStation` until step 4 is agreed.
4. (Done 2026-10-19, see results.) Agree test seeds and cleanup with the operator before the first
   `createStation` (stations persist; station limit exists). Find out
   whether `createStation` is idempotent for an already-seeded artist.

## Build, test, ship

- Work on `pandora-create-station` (see Branches and image). Tests in
  `tests/providers/pandora/` (stub `_api_request` by endpoint, as
  `test_favorite.py` does): seed returned on miss, existing station wins,
  name mismatch → no seed, `fullSearch` error degrades, search never
  creates, first play creates once, second play reuses, 1005 surfaces.
- Full `pre-commit run --all-files` + Pandora suite + full suite in the
  `ma-pr-test` rig (lessons §5). Baseline failures on clean `dev`.
- Image: next `kronk/music-assistant:2.11.0b5-kronk.N` (kronk.4 is live as
  of 2026-10-19; verify) via `ma/build.sh` from the `kronk-image` merge
  branch, after the operator commits. Hash guard unchanged unless the base
  moves.
- **MA restart is its own named step** — announce it, check every
  `media_player` state first, take the quiesced tarball, never interrupt
  the operator's music (CLAUDE.md, `MA_FORK_STATUS.md` recipe).
- Live verify (tenet 6): time search, create, first fragment. Test via
  `assist_pipeline/run` and ask the operator for an "Okay Nabu" test.
  Run `./scripts/pipeline_bench.sh` pre/post (confirm first — it clears
  chat history).

## Latency (estimate; measure)

Search with the concurrent `fullSearch`: +0–1 s over today on radio
searches. First play of a new station: `createStation` ~1 s + first
fragment 2–4 s → ~4–6 s on the fast tier, +15–25 s on the Kronk tier.
Existing stations: unchanged.

## Open questions

- Name-match rule details: normalized exact equality is the plan (probe,
  2026-10-19); confirm the normalization (case, "&"/"and", punctuation).
- How Kronk detects "newly created" for the reply (search result was a
  `seed:` item vs a real station is the likely signal).
- Measure first-play latency of a new station on both voice tiers; decide
  whether Kronk needs the fire-and-forget fallback.

## Docs lifecycle

After ship: `docs/features/pandora-create-station.md` (with Blog hooks),
update this status header, move the ROADMAP line to Shipped, update item 23,
and refresh `MA_FORK_STATUS.md`.
