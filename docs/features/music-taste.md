# Music that knows the household

Shipped 2026-10-21. Plan: `docs/plans/MUSIC_TASTE_AGENT_PLAN.md`. Trigger:
`docs/incidents/INVESTIGATION_2026-10-19_ytmusic_album_misses.md` and "play
some chill music" playing an album literally named "Some Chill Music".

Kronk's music path used to be a translator: one utterance became one literal
`music_assistant.play_media(media_id=<string>)`. Now a named request is
resolved before it plays, a genre request is a pick from what this household
listens to, and Kronk learns from what happens to its picks.

## What it does

- **Named requests survive misspelling and STT.** "Destiny Waving by Ulrich
  Schnauss" plays the album *Destiny Waiving* and says its real name; "DJ
  Kicks by Kuru Dorf Meister" plays Kruder & Dorfmeister; "Hotel Costes 3
  from Atlas" plays *Hôtel Costes 3*, not *Hôtel Costes* or *… 6*.
- **Provider hints are understood, not searched for.** "… on YouTube Music",
  "… from Pandora", "… from Atlas / the NAS" are stripped from the title and
  kept as a preference.
- **A pasted YouTube Music link plays.** Playlist, album, artist, and track
  links become MA URIs; the chat UI routes them to music, not research.
- **Genre requests are Kronk's pick.** "Play some deep house", "something
  chill", "jazz", "play some music" → Kronk picks from the household's own
  artists tagged with the genre, its own stations, then YouTube Music /
  Pandora genre playlists, plays it, and says what it chose: "Now playing
  Massive Attack, for trip hop, on the Kitchen speaker."
- **"Not that" / "something else"** plays the next pick from the same list,
  instantly, and counts against the rejected one.
- **Told preferences.** "We love X", "never play X", "forget that we love X",
  "when I say chill I mean deep house and downtempo" → saved to one file the
  operator can also edit: `data/music_taste.yaml`.
- **Voice too.** The HA music blueprint hands loose phrasings ("play some …",
  "play something …") and failed named plays to Kronk instead of failing or
  letting HA's built-in search-and-play grab them.

## How it works

### Named requests — `tool_service/music_resolve.py`

1. `normalize()` strips provider hints, splits "X by Y" at the last "by",
   and maps YouTube Music links to `ytmusic://<type>/<id>`.
2. `/music` searches MA **one media type per call** and merges (see
   gotchas), for the full text and the title alone.
3. `rank()` scores every candidate: difflib ratio on folded strings
   (accents, punctuation, "&"→"and", leading "the" removed), plus the
   space-collapsed ratio that catches split/merged words. With an artist in
   the request the score is `title × (0.75 + 0.25 × artist)` — the right
   artist can't carry the wrong title. A number in the request that the
   title lacks costs 20%. Without an artist, a non-exact match must cover
   every word of the request (each ≥ 0.85) or it's capped below the line —
   "Destiny Waving" alone does not play "Destiny Wave" by a stranger.
4. Nothing ≥ 0.75 after the first search and the request named an artist →
   stage 2 lists that artist's albums/tracks (limit 50), where a misspelled
   title usually is.
5. A winner plays by URI. Candidates found but none above the line → fail,
   naming the closest ("The closest I found was …"); no literal play, since
   MA's own search would play one of the rejected candidates
   (`docs/incidents/INVESTIGATION_2026-10-21_playlist_miss_wrong_music.md`).
   No candidates at all, or MA unreachable → the old literal play.
6. Radio: MA's exact-name lookup runs first (it can create a new Pandora
   station). On a miss, the resolver scores the radio search plus every
   library station; MA's search matches substrings, so "90s, 2000's and
   today's hits" finds nothing by search
   (`docs/incidents/INVESTIGATION_2026-10-25_pandora_station_name_miss.md`).

### Household taste — `tool_service/taste.py`

- **Told:** `data/music_taste.yaml` (`love`, `never`, `genres`, `rules`).
  Round-trip YAML (ruamel) — Kronk's writes keep the operator's comments.
  Re-read on mtime change; a broken file keeps the last good version, the
  error shows at `GET /music/taste`, and Kronk refuses to write over it.
- **Learned:** `data/music_taste.db`, rebuilt at startup when stale and
  nightly at 03:30 local. Inputs, strongest first: MA play history (500
  items, weight 0.99ⁿ by recency), Pandora stations named after an artist,
  the NAS library (weight 0.3 — operator decision). Genres come from MA's
  metadata; history artists MA has no genres for are tagged once by the
  local model (`gemma-4-e4b`, 25 per call) and cached in `tags`.
  `GET /music/taste/profile` shows the result; `POST /music/taste/rebuild`
  runs it now (~100 s cold, ~20 s with tags cached).
- **Corrected:** feedback only on Kronk's own picks — a watcher polls the
  speaker every 10 s: skipped inside 30 s −0.5, stopped inside 60 s −0.5,
  still playing at 10 minutes +0.3; "not that" −1.0. First outcome wins.
  Pandora thumbs add ±0.5 to the artist.

### Genre requests

`play_music(kind="genre")`, or any untyped query that is a known genre
(built-in list + every genre in the profile + the file's `genres` words):

1. The phrase is cleaned ("some deep house music" → "deep house") and mapped
   through the file's `genres` table. No phrase ("play some music") → the
   first matching `rules` entry, else the top of the profile.
2. Candidates: profile artists with a matching genre (score + feedback, +2
   for `love`, `never` excluded), our own stations named for the genre, and
   MA search playlists/stations whose *name* carries the genre (base
   0.25–0.28, below anything the household owns; a Pandora `seed:` station
   0.15 — playing it would create a station).
3. One of the top three plays, weighted by score; the ranked list is kept
   per speaker for three hours for "not that". A pick that won't start falls
   to the next.
4. Artist picks play the artist's own tracks, shuffled (see latency).

### Voice — `ha/blueprints/mass_assist_kronk.yaml` change 9

Conversation triggers outrank HA's built-in intents, so the blueprint now
owns "play/put on some {x}", "play something {x}", "play some music".
Those, and a failed play of a named album/track/artist/playlist (MA
rejected the words), go to Kronk with `conversation.process`
(`conversation.ollama_conversation`). That call can't carry the device id,
so the blueprint appends the satellite's room ("… in the Office"), or its
own MA player name when the satellite has no room. Radio misses stay a fast
failure (change 8).

## Latency (measured 2026-10-21, basement Voice PE)

| Path | Time |
|---|---|
| Named, clear winner (fresh search) | +1–1.5 s over the old literal play |
| Named, stage 2 (misspelled title) | +~0.1–1.5 s more |
| Genre pick → playing (tool_service) | 5–9 s |
| Voice, loose phrasing via blueprint → Kronk | 7.6–8.1 s end to end |
| Voice, misspelled album via blueprint → Kronk | 8.1 s |
| Artist start: plain / artist radio | 1.0 s / 11.3 s |

## Gotchas

- **MA syncs YouTube Music playlists every 12 hours.** A playlist made
  today isn't in the library, and MA's search only reaches public
  playlists. It fails now instead of playing a stranger's playlist; a
  manual sync (`music/sync`, playlist, the ytmusic instance) fixes it.

- **MA shares one `limit` across a combined search.** "Ulrich Schnauss" as
  album+track, limit 50 → 7 albums, 49 tracks, and no *Destiny Waiving*;
  playlist+radio for "deep house" returned only the radio. Every search
  here is one type per call.
- **MA play history has no timestamps and no player.** No learned
  room/time patterns; `rules` in the file cover that.
- **`stop` in an HA script discards the conversation response set before
  it** — Assist said "Done". The blueprint nests its own path under
  `else` instead.
- **Artist radio is slow to start** (11.3 s vs 1.0 s). Default is the
  artist's own tracks, shuffled; `MUSIC_GENRE_ARTIST_RADIO=1` switches.
- **Shuffle jumps tracks** — the watcher reads the title *after* the shuffle,
  or it scores Kronk's own jump as a skip.
- **Badly tagged NAS files look like artists** ("00", "[02] Memories
  Erased…"). Names that are a bare number or start with a track number are
  dropped from the profile.
- **Testing pollutes taste.** Every live genre test records picks and
  feedback. Clear them after (`DELETE FROM picks; DELETE FROM feedback;`
  in `data/music_taste.db`).
- **tool_service is published on `127.0.0.1:8003`** for a future HA
  `rest_command` (`ha/rest_command.yaml`, endpoint `/music/voice`). Not in
  use: loading `rest_command` the first time needs an HA restart. It would
  carry the device id and skip the LLM hop (~4–5 s faster on voice).

## Blog hooks

- "Some Chill Music": a literal pipeline isn't an assistant. The fix wasn't
  a smarter model; it was giving the model a household to reason about.
- Learned vs told vs corrected — three ways a house teaches its DJ, and why
  only Kronk's own guesses get scored.
- "Waving" vs "Waiving": speech-to-text can never fix a homophone; scoring
  against what exists can.
- The combined-search trap: one shared limit across types quietly hid the
  album we were looking for.
- Latency vs taste: artist radio was the better mix and 10 seconds too slow.
