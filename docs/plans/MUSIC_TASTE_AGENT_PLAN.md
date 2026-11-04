# Music that knows the household — resolver, taste store, genre requests — Plan

Status (2026-10-21): **shipped, all steps 0–7.** Feature doc:
`docs/features/music-taste.md`. Built as written except:

- **Close calls are not handed to the model.** Below the threshold the
  resolver falls back to the old literal play (and names the closest
  candidate on failure); above it, it plays. A model round for a 2–3-way
  pick would have meant restructuring the terminal `play_music` for little
  gain — operator decision 2 ("just play it") points the same way.
- **No room/time patterns in the learned profile.** MA's play history has
  no timestamps or player (measured in step 0); `rules` in the taste file
  cover it.
- **Voice handoff uses `conversation.process`, not `rest_command`.** The
  plan said an automation reload; `rest_command` is not loaded in this HA,
  and loading it the first time needs an HA restart (not taken — HA/MA
  restart directive). `ha/rest_command.yaml` and tool_service
  `/music/voice` (published on 127.0.0.1:8003) are ready for that switch;
  it would keep the device id and skip the LLM hop.
- **Genre artist picks play the artist's tracks, shuffled, not artist
  radio** — radio took 11.3 s to start vs 1.0 s (`MUSIC_GENRE_ARTIST_RADIO=1`
  flips it).
- **Step 0 numbers:** 321 of 1,561 library artists carry MA genres; a full
  artist fetch takes 2.1 s; domain-only `ytmusic://` URIs resolve.

Original status: planned 2026-10-21. Operator decisions recorded below. Triggered by
`docs/incidents/INVESTIGATION_2026-10-19_ytmusic_album_misses.md` and the
"play some chill music" → album literally named "Some Chill Music" miss.

## Problem

Kronk's music path is a translator: one utterance becomes one literal
`music_assistant.play_media(media_id=<string>)`. It cannot tell a named
item from a genre, has no knowledge of what the household listens to,
and has one tool that means "play exactly this string". Every miss in the
2026-10-19 investigation and the blueprint's misses over the prior ten
days (`Kuru Dorf Meister`, `Tosco`, `Destiny Waving`) is a resolution
failure, not a reasoning failure — the request dies on the first literal
search.

Goal: Kronk acts like a household DJ. A named request resolves to the
right item despite STT spelling. A genre request ("deep house",
"downtempo chill") results in Kronk choosing something that fits the
genre **and** this household's listening, playing it without asking, and
saying in one line what it chose.

## Operator decisions (2026-10-21)

1. **Household taste**, not per-person. The Voice PE can't identify a
   speaker; no per-person profiles.
2. **Just play it.** No confirmation step. Kronk announces the pick in
   one line; correction is cheap ("not that").
3. **Play history outranks the NAS library** as a taste signal. The
   library is weaker evidence (some of it is inherited or stale).
4. **Genre, not mood.** "Chill" means a genre like deep house or
   downtempo, not an abstract mood. No mood tagging.
5. **One editable taste file** in addition to telling Kronk by voice/chat.

## What exists today (measured 2026-10-21, read-only MA queries)

- MA library: 1,769 artists, 1,578 albums, 15,369 tracks, 48 playlists,
  258 radios. Mostly the NAS (`filesystem_local`).
- `music/recently_played_items` (last 200): 115 Pandora tracks, 57 YTM
  tracks, 8 library tracks, 7 library artists, 7 radios, 3 YTM albums,
  1 playlist. History exists and is provider-tagged.
- Pandora stations are stated tastes (seed URIs:
  `seed:AR:` artist, `seed:TR:` track, `seed:GE:` genre).
- Pandora thumbs flow through `rate_music` / MA `set_favorite`
  (`docs/features/pandora-thumbs.md`).
- **Genre metadata is sparse and not in list responses.** `library_items`
  listings return empty `metadata`; a full `item_by_uri` fetch carries
  `genres` on some artists (Orbital → `['Techno']`, from MA's own
  metadata providers) and on almost no albums or tracks. Coverage across
  the library is unmeasured — step 0 measures it.
- Genre search already works against providers: `music/search "deep
  house"` → YTM playlist "Deep House 2026", Pandora "DEEP HOUSE Radio";
  `"downtempo chill"` → YTM playlist "Downtempo Instrumentals".

## Design

### 1. Request classification (code-first)

Every play request is one of:

- **Named** — a specific artist, album, track, playlist, station, or a
  pasted YTM/Pandora URL.
- **Genre** — a genre phrase with no specific item ("some deep house",
  "downtempo chill", "jazz").
- **Continue/adjust** — "more like this", "something else", "not that".

The home agent's `play_music` gains a `kind` argument (`named` | `genre`)
the model fills; tool_service also checks the query against the genre
vocabulary (below) and a named query that exactly matches a known genre
is treated as genre. The model picks, code verifies — tenet 5.

### 2. Resolver (named requests) — tool_service

Replaces the single literal `play_media` call. Deterministic, in order:

1. **Normalize**: strip provider hints (`on|from|via` + YouTube Music /
   ytmusic / Pandora / Spotify / Atlas / the NAS / my library), keeping
   the hint as a provider preference; split "X by Y" into name + artist;
   a `music.youtube.com` / `pandora.com` URL becomes an MA URI directly.
2. **Search wide**: `music/search` across the relevant media types and
   all providers (library first).
3. **Score** each candidate: word overlap plus phonetic similarity
   (Double Metaphone or similar) on name and artist separately. This is
   what catches `Waving`→`Waiving`, `Kuru Dorf Meister`→`Kruder &
   Dorfmeister`, `Tosco`→`Tosca`.
4. **Decide**: one clear winner above threshold → play by exact URI. A
   close call → return the top 2–3 to the model to pick (no question to
   the user — decision 2). Nothing above threshold → fail as today.
   Gibberish must still fail (the 2026-09-30 "zzqx florbent" → Akon
   rule; the scoring threshold enforces it).
5. **Verify** playback as today (tenet 6).

This subsumes the ROADMAP "media_type miss" chore and fixes (a)–(c) in
the 2026-10-19 investigation.

### 3. Taste store

A small SQLite database in `./data/` owned by tool_service (tenet 4),
plus one operator-editable file. Three inputs, explicit outranks learned:

**Told (explicit).** One file, `data/music_taste.yaml`, human-readable,
the single source of truth for explicit preferences. Sections:
`love` (artists/albums/genres to favor), `never` (hard exclusions),
`rules` (e.g. "weekday mornings in the kitchen: jazz"), and `genres`
(household meaning of a genre word, e.g. `chill: [downtempo, deep
house, trip hop]`). A `remember_music_preference` tool appends to the
same file when the user says "we love Ulrich Schnauss" or "never play
Nickelback" — talking to Kronk and editing the file write the same
place. The file is re-read on change; malformed YAML fails loud and
keeps the last good version.

**Learned (derived, rebuildable).** A nightly job distills a profile
from, in weight order:
1. MA play history (frequency × recency; by room and time of day).
2. Pandora stations and thumbs.
3. The NAS library (lowest weight — decision 3).
Output: per-artist scores, per-genre artist lists, room/time patterns.
Derived only: deleting the table and rerunning rebuilds it.

**Corrected (feedback on Kronk's picks only).** For genre-request picks,
record: skip within ~30 s, "not that", thumbs down, stop within ~60 s →
negative; a long listen → positive. Named requests teach nothing —
Kronk learns from its guesses, not the user's choices.

### 4. Genre requests

`play_music(kind=genre, query="deep house")`:

1. Map the phrase through the taste file's `genres` table (household
   meaning), else use it literally.
2. Pull candidates from three sources: library/history artists whose
   genres match (weighted by the learned profile, minus `never`); a
   matching Pandora station if one exists; YTM genre playlists/radio
   from `music/search`.
3. Rank by taste score; prefer sources the household actually plays
   (history says Pandora and YTM, in that order today).
4. Play the top pick, verify, announce in one line: "Deep house — your
   Parov Stelar station." No question asked (decision 2).
5. "Not that" plays the next candidate from the same ranked list
   instantly and records the negative.

### 5. Genre coverage

Genre is the weak data. Sources, in order:
1. MA's existing artist metadata (already fetched by MA's metadata
   providers — no new egress).
2. Pandora station seeds (`seed:GE:`) and the genres their artists play.
3. The taste file's `genres` table (operator fills gaps by hand).
4. If coverage is still poor after step 0: a one-time local-model pass
   tagging the top ~300 history artists with genres. External tag
   services (Last.fm, MusicBrainz direct) are **not** in scope — that
   would be an explicit operator decision under tenet 1.

### 6. Voice tier

The HA blueprint keeps the fast path. On a `play_media` failure it
currently ends the turn; it changes to hand the utterance to Kronk's
`/api/chat` (resolver + taste). Cost: the Kronk tier's latency on misses
only. Requires a blueprint edit and an **HA automation reload — not an
HA restart**; called out here per the HA/MA restart directive. Genre
requests by voice ("play some deep house") must route to Kronk, not the
blueprint's literal search — the blueprint's trigger grammar may need to
exclude genre phrasing; verify against its triggers before building.

## Latency (tenet 12)

- Named hit (clear winner): one extra wide search, est. +0.5–1.5 s over
  today. To be measured in step 1 before it ships.
- Named close call: + one model round to pick, est. +2–4 s.
- Genre request: taste lookup (local SQLite, ms) + one or two provider
  searches + verify, est. comparable to today's ~8 s verify window.
- Voice blueprint miss → Kronk: the Kronk tier budget (~15–25 s worst
  case) applies only on a miss.

## Build order (single-variable — tenet 9)

0. **Measure**: genre coverage over the full artist library (full
   fetches), and resolver baselines on the misses in the 2026-10-19
   investigation. No behavior change.
1. **Normalize** only: provider-hint stripping, "X by Y" split, URL →
   URI. Tests for each shape. Ship, observe.
2. **Search + score + decide** for named requests. Tests: the five
   logged misses resolve; gibberish fails; existing successes unchanged.
3. **Taste file + `remember_music_preference`**. Read-only use first
   (`never` filter on any pick).
4. **Learned profile** nightly job.
5. **Genre requests** end to end (`kind=genre`, ranking, announce,
   "not that").
6. **Feedback capture** on Kronk-chosen picks.
7. **Blueprint miss → Kronk handoff** (HA automation reload).

Each step: `./scripts/run_tests.sh`, deploy, live check through the chat
UI and the `/api/chat` shim; steps touching routing or the agent loop
also run `./scripts/pipeline_bench.sh`. Live playback tests follow the
check-player-state-first rule.

## Open questions

- Room/time patterns: is the history's room attribution reliable enough
  (MA queue → player → HA area)? Measure in step 0.
- Does the taste store stay music-only, or become the first piece of a
  general household-preference store (ROADMAP item 5, "Context/fact
  cache", is adjacent)? Decide after step 5, with music as the pilot.
- Announcement wording for voice — keep under ~2 s of TTS.

## Blog hooks

- "Some Chill Music": why a literal pipeline isn't an assistant.
- Learned vs told vs corrected — three ways a house teaches its DJ.
- Phonetic matching as the fix for STT homophones ("Waving" vs "Waiving").
