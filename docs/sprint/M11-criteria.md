# M11 pass criteria: the music decision in tool_service, built and shadowed

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M11. Move
the music decision into tool_service", rows C5 and C6 and the M11 line of
"Verified on Kronk, 2026-11-15", Drew's decisions of 2026-11-09, 11-11 and
11-14) and the items promoted since (M7, M14, M19, M21, M2). The tester
checks against this file, not against the build report.

## Scope of this sprint item

Built: the sentence parser and decision order in `tool_service`, a
`POST /music/say` endpoint, the music policy in `config/`, the corpus's
parser layer, and shadow mode. **Shadow only in production:** the new
decision runs beside the live path and is logged; no live request is
answered by it. Not in this item: the cutover (plan steps 5 and 6), any
change to Home Assistant (the thin automation, the blueprint, a blueprint
input, `rest_command`), retiring the blueprint, and removing the
literal-play fallback from the live `/music` path. Those wait for Drew's
review of shadow and corpus data.

## Criteria

### A. The parser

1. A pure function in `tool_service/music_resolve.py` (beside
   `radio_name()` and `normalize()`) turns a sentence, the list of real
   room and player names, and the origin device into a parsed request:
   verb, the words of the request, an explicit type word if present
   (album, track or song, artist, playlist, radio or station), "X by Y",
   shuffle, a named provider, and the target room or player. It makes no
   network call and is unit-tested offline.
2. Rooms and players are matched from the right-hand end of the sentence
   against the real names, never with a greedy slot: "play The Girl with
   the Sun in Her Head by Orbital" keeps its title whole and targets the
   origin; "… in the office" targets the office; a title that ends with
   a room word that is not preceded by a room preposition keeps the word.
   The basement Voice PE's player, which has no area in HA, is still
   reachable as today.
3. Non-music "play" requests are excluded with the same exclusions as
   `routing._MUSIC_RE` (news, games, videos, movies, podcasts): the
   parser says "not music" and nothing is resolved. One source for the
   exclusion list, or a test that the two lists agree.
4. The existing `radio_name()` behaviour (stops at a full stop) is kept
   and still tested. Messy transcripts are M14's: the decision order is
   one of the fast paths the gate skips, and the parser does not trim.

### B. The decision order

5. `tool_service` decides style or named item in the plan's order, each
   step its own tested function:
   (1) marker words decide outright ("some", "something", "music",
   "songs", "vibes" mean style; "album", "track", "song X by Y",
   "playlist", "radio", "station" mean a named item of that type);
   (2) unmarked: the genre list (hardcoded set, taste YAML, taste DB,
   Music Assistant genre tags where available) and a type word
   overrides ("play jazz" is the style, "play the album Jazz" is the
   album);
   (3) not a known genre: the resolver as a named item;
   (4) retry across types, accepting only a strong match and saying what
   was found;
   (5) the other reading once (an unknown style retried as a name; a
   name with no strong match retried as a style only if the genre list
   knows it);
   (6) still unclear: one model question, style or named item, output
   constrained to those two values; the model never writes arguments;
   (7) a weak title match never beats a style reading;
   (8) nothing resolves: say so plainly and name the closest match.
   The raw words are never passed to Music Assistant by this path.
6. Drew's two examples work on the new path, offline with Music
   Assistant stubbed and live on dev in dry-run: "play some deep house
   chill music" and "play italian music" are read as styles; "play deep
   house" is a style; "play Portishead" is a named artist; a title that
   contains a genre word is handled by rule 7 and the type-word
   override. Each is a corpus row added before the parser code (the
   commit order shows it).
7. Provider policy, as a scoring preference over the candidates one
   Music Assistant search returns, never one search per provider in
   sequence: playlists, songs, albums and artists prefer the NAS
   library, YouTube Music is the fallback; radio defaults to Pandora; a
   provider named in the sentence overrides. A poor NAS match never
   beats a good YouTube Music match: the preference applies only among
   candidates that pass the match threshold. Tests cover each case and
   count the searches made.
8. The fast case stays fast: an exact library-name hit is tried before
   search and score.
9. Marker words, the provider order, thresholds and the position of the
   model step live in `config/music.yaml` through the M21 loader
   (schema, comments, defaults equal to the file, pins, docs). The model
   step can be moved earlier by configuration (Drew's design principle,
   2026-11-11) and a test shows the order changes with the setting.
10. The model question: how `tool_service` reaches the model is stated
    (direct to LiteLLM or an orchestrator endpoint) with the reason; it
    cannot run concurrently with another model call in a way that
    breaks the existing lock's purpose, or the report shows why that is
    safe; it has a timeout and a defined result when the model is down
    or answers outside the two values (the request fails plainly or
    takes the non-model reading; say which). Its latency is measured on
    dev (the plan measured 1.6 to 2.0 s).
11. Every decision records which step decided it (`music.decided_by`),
    the reading, the candidates considered and their scores, as span
    attributes and a log event.

### C. The endpoint

12. `POST /music/say` takes `{sentence, origin_device}` (and what the
    existing request carries for origin), runs the parser and decision
    order, builds the existing `MusicRequest` and calls the existing
    play path. It honours `MUSIC_DRY_RUN` exactly as `/music` does and
    has a per-request dry-run field that can only turn dry-run on.
13. It is not reachable from the LAN through nginx, and nothing in
    production calls it for a live request in this sprint item. The
    orchestrator's `music` route is unchanged for live requests.

### D. Shadow mode

14. With shadow mode on, every music request that reaches Kronk's live
    path (the `music` rule through the home agent, `play_music` from the
    coordinator, `/music/voice`) also runs the new parser and decision
    order on the user's sentence, with playing impossible by
    construction (not merely dry-run by setting: the shadow path cannot
    reach a `play_media`, queue, transport or rating call), and logs one
    comparison record: the sentence, origin, what the live path
    resolved and played, what the new path would have played, whether
    they agree, `decided_by`, and timings.
15. Shadow mode changes nothing the user can observe: the live answer,
    what plays and where, and the live request's latency are the same
    with it on and off (measured on dev, at least ten requests each
    way; the shadow work runs after or beside the live call, never in
    front of it). A failure or timeout in the shadow path is logged and
    never surfaces. If the shadow path would ask the model question, it
    does so without delaying the live request and without holding the
    model lock ahead of a live request; if that cannot be guaranteed,
    shadow records "would have asked the model" and does not ask.
16. Shadow mode is a setting, off by default in code, on for dev, and
    turned on in production only by the coordinator at promotion. The
    comparison records go to a file under `./data` (and to traces) with
    a documented format and a script that summarises agreement rate,
    disagreements with both outcomes, `decided_by` counts and how often
    the model question fired, for the review with Drew.
17. What shadow mode cannot see is stated: requests the HA blueprint
    plays directly through Music Assistant never reach Kronk, so they
    are not shadowed. Per the plan (C5), the corpus replay is the main
    evidence for cutover; the shadow week is secondary.

### E. Corpus and evidence

18. Layer one of the corpus runs every music row through the parser and
    decision order offline (the M10 hook), asserting expected reading,
    type, name, provider and player. Rows that are known failures on the
    new path are marked `xfail.parser` with reasons; none is dropped.
19. The replay can target the new endpoint on dev in dry-run and compare
    what would be played, not only the lane and tool (the answer check
    the M10 promotion note asks for): for each music row, the new path's
    result against the row's expectation and against today's live path
    on dev. The result file and a table of every disagreement are
    committed.
20. The report gives, for the corpus: agreement between the new path
    and today's path, every row where the new path is better, every row
    where it is worse, and the rows HA handles locally today (which the
    new path would take over at cutover) with what the new path does
    with each.
21. Latency of the new path on dev per media type (owned station, named
    album, artist, style, model question), against the plan's baseline
    for today's Kronk path (p50 6.6 s, p95 11.9 s) and, where it can be
    read from the Music dashboard or traces, the blueprint's direct
    path.

### F. Regression and safety

22. The live paths are unchanged: `/music`, `/music/voice`,
    `play_music`, the blueprint and the literal-play fallback behave as
    before; a test pins the live `/music` behaviour for the corpus rows
    before and after.
23. `./scripts/run_tests.sh` passes: at least `3725 passed, 2 skipped,
    23 xfailed` plus the new tests. New tests do not reach the network.
24. The voice replay on the dev path (the existing one) matches the M10
    baseline row by row apart from the known flapping rows.
25. No sound; dev only; production untouched by the build; no change to
    Home Assistant or to Music Assistant's settings; no new custom
    Music Assistant image code; no HA or MA restart; no command to the
    MagicMirror Pi. Searches against Music Assistant from dev are reads.
26. Docs: `docs/features/voice-music-control.md` describes the new path,
    the decision order, the policy file, shadow mode and how to read
    its report; the cutover steps that remain are listed as not done.

## Reports

Build: `docs/sprint/M11-build.md`. End to end: `docs/sprint/M11-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
