# "X radio" that isn't a Pandora station → YouTube Music radio — Plan

Status: **drafted 2026-10-04, awaiting operator decisions (below). Not started.**

## Problem

The operator is used to Pandora creating a station on request. MA's Pandora
provider cannot: it plays only stations already on the account, and its
search matches only those station names (`providers/pandora/provider.py`:
catalogue search and station creation need Pandora's legacy endpoints,
which the provider does not speak). Since the move to the Kronk Pandora
account (2026-10-02), "Orbital radio" / "French Dinner radio" fail unless
the station was added on that account
(`docs/incidents/INVESTIGATION_2026-10-04_radio_failures.md`).

## Behaviour

"Play X radio" / "X station" / "X radio on Pandora", in this order:

1. **Pandora station** whose name contains X (case-insensitive) — exactly
   today's behaviour. Plays; reply unchanged.
2. Else **YouTube Music artist radio**: search YT Music for artist X; if the
   top artist's name matches X (normalized: lowercase, punctuation and a
   trailing "radio"/"station" stripped, then equal or token-subset), play it
   with `radio_mode: true` — MA seeds the queue from the artist and keeps
   adding the provider's similar tracks (YT Music supports SIMILAR_TRACKS).
   Reply says what happened: "No Pandora station for Orbital, so here's
   Orbital radio from YouTube Music."
3. Else **honest failure**: "I couldn't find a station or an artist called X."

The name check in step 2 is the guard learned on 2026-09-30: an untyped or
loosely typed MA lookup always returns *something* (gibberish played Akon).
No match → no play.

If the operator said "on Pandora" explicitly, step 2 still runs but the
reply leads with "There's no Pandora station called X" so the substitution
is never silent.

Not in scope: creating real Pandora stations (unofficial API, tenet 3);
thumbs on fallback radio (item 16 is Pandora-only); song-seeded radio
("radio based on <song>") — later, same mechanism with media_type track.

## Where the logic lives — DECISION NEEDED

- **A. Two copies.** Jinja in the blueprint fork (search via
  `music_assistant.search` with `response_variable`, compare names in a
  template) + Python in tool_service for the Kronk tier. No HA restart.
  Two implementations of one matching rule — drift risk (tenet 8).
- **B. One copy (recommended).** tool_service gets `/music/radio`
  (Pandora → YT artist radio → failure, with the name rule in Python and
  tests). The blueprint's radio branch calls it through
  `rest_command.kronk_music_radio` and speaks the returned sentence; Kronk's
  `play_music` with `media_type: radio` calls the same endpoint. Cost: the
  first `rest_command` in HA needs **one HA restart** — the same restart
  the shopping-list plan (ROADMAP 22) needs, so take it once for both; and
  the HA fast path for radio then depends on tool_service being up (it
  already depends on HA → MA; Kronk being down would turn "X radio" into a
  spoken error, not silence).

## Latency (tenet 12)

- Step 1 hit: unchanged (~1 s to playing).
- Step 2: + one Pandora station listing (live, fast) + one YT artist search
  (~0.5–1.5 s uncached; MA caches searches for 7 days) + YT start (1–4 s
  observed). Target: speaking within ~5 s.
- Step 3: two lookups, no 10 s playback wait (the decision is made from
  search results before anything plays). Faster than today's blueprint
  failure path.

## Gate 0 (before building)

1. With the operator's OK, in the office: `music_assistant.play_media`
   artist "Orbital", YT Music, `radio_mode: true`. Confirm it plays and
   that the queue keeps growing past the artist's own tracks.
2. Time an uncached YT artist search and the Pandora station listing.

## Tests

Name rule table (exact, "X radio", punctuation, token subset, gibberish →
no match); `/music/radio` against a fake HA: Pandora hit, YT fallback with
`radio_mode`, no match → failure without any play call, "on Pandora"
wording; Kronk tool → endpoint; blueprint sentence routing via
`conversation/agent/homeassistant/debug` (no playback).

## Steps

1. Gate 0. 2. tool_service `/music/radio` + tests. 3. Kronk tool wired.
4. (B) `rest_command` + HA restart (named, operator picks the moment;
shared with item 22) + blueprint KRONK change 7. 5. Operator voice test:
a Pandora station, an artist with no station, gibberish. 6. Feature doc
section, ROADMAP to Shipped.

## Open decisions for the operator

- A or B (recommendation B).
- OK for the gate-0 play in the office.
