# Music request accuracy — fixes 1 and 3 — Plan

Status: **shipped 2026-09-14 (same day).** Live from the kitchen device:
"Play Anime Pop Radio on Pandora" → the fork's new radio sentence fired
("Anime Pop playing in the Kitchen", 7.4 s) and the player's content id
was `library://radio/15` — the Pandora station — not a YouTube title
match; "what song is this" → "Playing: Give Me Everything (feat. Nayer)
by Pitbull, on the Anime Pop station, on the Kitchen speaker." (7.8 s);
"play the radio station anime pop" still works (4.9 s). Fork installed
by `docker cp` + `automation.reload`, no HA restart. 25 tests in
`tests/test_now_playing.py`; suite 543. Learned: MA reports a station
as `media_content_type: music` with the station name in
`media_album_name` and the id under `…://radio/N` — the sentence keys on
the id path, and `library://radio/…` hides the provider, so no source is
claimed for it.

## Trigger (analysis of 2026-09-13, HA pipeline debug + player history)

Five spoken play requests, three mismatches, all kitchen:

| heard | handled by | played | why |
|---|---|---|---|
| "Play the album Just Like Fire by Ratatan" | blueprint fork | Horror Skunx, "Ratatung…" (YT Music) | MA fuzzy match on a mangled artist; confirmation echoed the request |
| "Play Soul Food Radio from Pandora" | HA built-in `HassMediaSearchAndPlay` | Goodie Mob, "Soul Food (Radio Version)" (YT Music) | plain-text search across providers; a YT track outranked the Pandora station |
| "Play Mortiva Radio from Pandora" | HA built-in | nothing | HA said "Playing media" regardless |
| "Play Anime Pop Radio on Pandora" | HA built-in | Yui Horie, "Pandora" (YT Music) | matched a song titled "Pandora" |
| "What song is this" | Kronk | "I do not have access…" | no tool |

Root pattern: **station-first phrasing** ("X Radio on Pandora") misses
the fork's radio grammar ("radio station X"), so HA's built-in
search-and-play intent — which matches after sentence triggers but
before the LLM, searches plain text across all providers, and does not
verify — takes it and lands on YouTube Music title matches.

Operator decisions: fix 1 (grammar) and fix 3 (now-playing tool); fix 2
(confirm from the player's actual title in the fork) rejected — the
1–2 s it adds to the fast tier is too much.

## Fix 1 — station-first phrasing reaches the fork

Fourth marked change in `ha/blueprints/mass_assist_kronk.yaml`: a second
default sentence on the radio trigger,
`(play|listen to) [the ]{media_name} (radio|station) [(on|from|via) pandora] [(in|on) [the ]{area_or_player_name}]`.
Trigger id stays `radio` → `media_type: radio` → MA searches radio items
only (Pandora stations are radio items). The "on pandora" tail is
consumed explicitly so it is never read as a room. Sentence triggers
match before built-in intents, so the search-and-play intent no longer
sees these. Kronk side: one home-agent prompt line — "X radio" / "X
station" means `media_type: radio`.

## Fix 3 — `now_playing`

tool_service `POST /music/now_playing`: resolve the player exactly as
play does (named → own device → own room → default), read the player's
`media_title` / `media_artist` / `media_album_name` / `media_content_id`
from HA, answer one sentence ("Ratatung by Horror Skunx on the Kitchen
speaker, from YouTube Music." / "Nothing is playing on the Kitchen
speaker."). Terminal tool `now_playing` on the home agent, origin-aware,
spoken verbatim. Routing pin `_NOW_PLAYING_RE` for the bare questions
("what song is this", "what's playing", "who is this").

## Tests

`tests/test_now_playing.py`: route (source mapping, nothing playing,
own-device resolution), tool payload with origin, terminal speech, pin
matches/rejects, fork YAML carries the new sentence on the radio trigger.

## Verification (live, kitchen device)

"Play Anime Pop Radio on Pandora" → kitchen player's content id starts
with `pandora`, not `ytmusic`; "what song is this" → the real track;
"play the radio station anime pop" still works. Deploy: fork copied into
HA + `automation.reload` (no HA restart); tool_service + orchestrator
rebuilt; nginx restart; shim check.
