# Investigation 2026-10-25 — "90s, 2000's and today's hits" radio fails though the station exists

**Status:** fixed 2026-10-25 (tool_service only; no HA/MA change).

## Symptom

Operator, chat UI, 20:39 UTC: `Play "90s, 2000's and today's hits" radio
from Pandora, in the kitchen`. Playback failed. A voice-style phrasing a
minute earlier ("…play 90s, 2000s, and today's hits…") failed the same way.
The household owns the station: **"90s, 2000s, Today's Hits Radio"**
(`library://radio/263`, Pandora).

## Evidence

- orchestrator rids `f3ccfe49` (20:38:00, route direct) and `0a3759b5`
  (20:39:14).
- tool_service, both requests:
  - `music request query="90s, 2000's and today's hits" media_type='radio' player='kitchen'`
  - `HA music_assistant.play_media failed: code=home_assistant_error message=Could not resolve ["90s, 2000's and today's hits"] to playable media item`
  - `POST /music … 502`
- MA `music/item_by_name` (media_type radio), measured afterwards:

  | name | result |
  |---|---|
  | `90s, 2000s, and today's hits` | None |
  | `90s, 2000's and today's hits` | None |
  | `90s 2000s todays hits` | "90s, 2000s, Today's Hits Radio", `library://radio/263` |

## Root cause

1. **Radio skips Kronk's resolver.** `/music` sends a radio request to MA's
   `music/item_by_name` only (`_pandora_station`), so a new Pandora station
   can be created from a `seed:` item. On a miss it plays the literal text,
   which fails in HA the same way. Every other media type goes through
   `music_resolve` (fuzzy scoring against search results); radio was left
   out.
2. **The resolver would have rejected it anyway.** Offline score of the
   request against the station: name similarity 0.88, but
   `word_coverage` 0.5 because the word "and" has no match in the station
   name. Without an artist, coverage < 0.85 caps the score at 0.74, under
   `PLAY_MIN_SCORE` 0.75. `fold()` already turns "&" into "and" and
   `similarity()` drops "and"; `word_coverage()` did not.

Pandora names stations "<seed> Radio", and people drop or add filler
("and", "radio") when they say them. Exact-name lookup can't absorb that.

## Hypotheses tried

- **Resolver alone would fix it.** Wrong. After routing radio misses to
  the resolver, the live test still failed: `music resolve … [radio]
  stage 1, 0 candidates`. MA's `music/search` matches substrings. Measured:

  | search_query (radio) | result |
  |---|---|
  | `90s, 2000's and today's hits` | [] |
  | `90s 2000s todays hits` | the station |
  | `Today's Hits` | the station, plus a catalogue "Todays Hits Radio" |

  So search never handed the resolver a candidate to score.

## Fix

- `tool_service/main.py`: a radio request whose exact-name lookup
  (`_pandora_station`) misses now goes through `_resolve_named` with type
  radio. The candidates are the radio search plus every library station
  (`music/radios/library_items`; 11 today). A winner plays by URI and is
  announced as the Pandora station. Candidates but no winner → 502 "I
  couldn't find a station called …" with the closest, no literal play.
  The exact-name path, which creates new Pandora stations from `seed:`
  items, is unchanged and still runs first.
- `tool_service/music_resolve.py`: `word_coverage()` ignores "and" as it
  already ignored "by".
- Tests (`tests/test_music_resolve.py`):
  `test_rank_station_name_with_and_and_without_radio`,
  `test_word_coverage_ignores_and_but_not_content_words`,
  `test_play_radio_miss_by_name_resolves_the_station`,
  `test_play_radio_exact_station_skips_the_search`,
  `test_play_radio_near_miss_names_the_closest_station`,
  `test_play_radio_scores_the_library_stations_when_search_finds_none`.
  Suite: 782 passed, 2 skipped.

Verified live (basement Voice PE, idle before, paused after):

| Path | Request | Result |
|---|---|---|
| `/music` | "90s, 2000's and today's hits from Pandora", radio | resolved `library://radio/263` (0.96), playing in 2.3 s |
| `/music` | "zxqvb florp", radio | 502 "I couldn't find a station called zxqvb florp in Music Assistant." in 0.3 s |
| `/api/chat` shim | the operator's sentence, "in the basement" | "Playing your Pandora station, 90s, 2000s, Today's Hits Radio. Now playing Flowers by Miley Cyrus…", 6.4 s |

## Not fixed

- **The HA blueprint fast path** plays the literal words for "play X
  radio" and fails fast on a miss (change 8). It does not hand radio
  misses to Kronk, so the voice phrasing still fails there. Options: hand
  radio misses to Kronk like named misses (adds ~6–8 s to a miss), or
  leave it.

## What would have caught it sooner

A test for a station name with the filler words people say. Every radio
test used one-word names ("Woodstock", "Santana").
