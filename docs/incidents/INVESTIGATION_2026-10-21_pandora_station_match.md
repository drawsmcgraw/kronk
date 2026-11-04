# Investigation 2026-10-21 — "Woodstock radio" made the wrong station; no "new station" reply

Status: **open** — cause found, fix proposed, not built.

## Symptom (operator, voice, office satellite, ~09:26 EDT)

1. "90s, 2000s, and today's hits radio" — failed.
2. "Play 90s, 2000s, Today's Hits, radio from Pandora." — played; no "made a
   new station" sentence.
3. "Play Woodstock Radio from Pandora." — played, no "new station" sentence,
   and the music was grunge, not the 60s/Hendrix station expected.

## Evidence

tool_service (Kronk tier):

```
music request query='90s and 2000s Hits Radio' media_type='radio' player=None shuffle=False -> ['media_player.satellite1_d8d1bc']
HA music_assistant.play_media failed: code=home_assistant_error message=Could not resolve ['90s and 2000s Hits Radio'] to playable media item
```

Blueprint traces (`automation.kronk_music_assistant_voice_device_first`):

| Start (UTC) | Sentence | media_id | Said |
|---|---|---|---|
| 13:26:39 | "Play 90s, 2000s, Today's Hits, radio from Pandora." | `90s 2000s Today's Hits` (radio) | "90s 2000s Today's Hits playing in the Office" |
| 13:27:26 | "Play Woodstock Radio from Pandora." | `Woodstock` (radio) | "Woodstock playing in the Office" |

MA log:

```
09:26:39.640 Pandora station 90s, 2000s, Today's Hits Radio (197132541403077038) is serving seed:GE:1048
09:27:26.718 Pandora station WOODSTOCK Radio (164125202337246992) is serving seed:TR:209696925
```

Pandora `fullSearch` (read-only, 2026-10-21):

```
'Woodstock' {numArtists: 0, numTracks: 50, numGenres: 1}
   track TR:209696925 | WOODSTOCK | Compligtd
   track TR:2075260 | Woodstock | Laila Biali
   ...
   genre GE:26507 | Sounds of Woodstock | 36421 listeners
'90s and 2000s Hits'
   track TR:7243350 | Feliz Navidad | Jose Feliciano (Holiday)   (5 tracks)
   genre GE:1048 | 90s, 2000s, Today's Hits | 25,209,080 listeners
```

## Root causes

1. **Wrong Woodstock station (provider).** `_match_seed` takes the first
   seedable catalogue item whose name equals the query under
   `compare_strings(strict=True)`. Pandora's Woodstock station is the genre
   "Sounds of Woodstock" (GE:26507), which is not an exact match; the first
   exact match is a track named "WOODSTOCK" by Compligtd. The design treated
   a track title as just as good a seed as an artist or genre, and only
   allowed exact names.
2. **Kronk-tier miss (provider + model).** The model shortened the request to
   "90s and 2000s Hits Radio"; no catalogue item has that exact name, so the
   exact-name rule refused GE:1048. The same rule was the guard against junk
   ("zxqv" → tracks by "zhqv").
3. **No "new station" sentence (by design so far).** Both plays that worked
   went through the HA blueprint (fast tier), which answers "X playing in the
   Office". The station sentence exists only on the Kronk tier
   (tool_service `_pandora_station`, 2026-10-21). "… radio from Pandora"
   phrasing hits the blueprint's radio grammar, so it never reached Kronk.

Side effect: the account now has a junk station "WOODSTOCK Radio"
(164125202337246992, seeded from a track). "90s, 2000s, Today's Hits Radio"
(197132541403077038) is the station that was wanted.
