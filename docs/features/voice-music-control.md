# Feature: Voice music control

**Shipped:** 2026-07-03 · **Plan:** `../plans/MUSIC_ASSISTANT_PLAN.md` · **Journal entries:** `../VOICE_SETUP.md` timeline (2026-07-03)

## What it does

"Play Pink Floyd on the Sonos Move" — spoken or typed — plays music through
Music Assistant on any mapped player. Two tiers, by design (Option C):

| Tier | Path | Latency | Grammar |
|---|---|---|---|
| 1 | **Kronk fork** of the MA local-assist blueprint (`ha/blueprints/mass_assist_kronk.yaml`) | ~2 s | strict: needs a media-type keyword ("play the **artist** X [in Y]") |
| 2 | Kronk `home` agent → `play_music` tool | ~15–25 s | fuzzy, anything |

Utterances the blueprint's grammar can't parse fall through HA's Assist
pipeline to Kronk automatically — the user just waits longer.

## Tier 1 targeting: the device that asked (2026-09-04)

Plan: `../plans/VOICE_MUSIC_DEVICE_FIRST_PLAN.md`. Upstream's blueprint
resolves by *area*; with two satellites in one room that means both play,
unsynchronized. The fork resolves **named player → named room →
own device → own device's room → default**, and a room with a Music
Assistant sync group (`mass_player_type == group`) targets the group
only. "Own device" is a MAC join inside the blueprint: the ESPHome
satellite's `connections` MAC → the MA player whose device identifier is
`up` + that MAC. The confirmation says "playing in the Office" when the
match was by device or room, so technical device names are never read
aloud. Verified 2026-09-04 by text runs as each satellite (unnamed →
own device only; "in the office" from the kitchen → office; no device →
default); the group rung is dry-run only until a sync group exists.

**This fork is a maintenance line.** MA's blueprint updates do not flow
into it. The pristine upstream is kept at
`ha/blueprints/upstream/mass_assist_blueprint_en.yaml`; when MA ships a
new version, diff it against that copy and re-apply the three marked
changes (`# KRONK change 1/2/3` in the fork). Install = `docker cp` into
`homeassistant:/config/blueprints/automation/kronk/` + automation reload.
The automation built from it is `automation.kronk_music_assistant_voice_device_first`
(default player: kitchen Voice PE); both upstream-blueprint automations
are disabled and kept for rollback.

## Tier 2 targeting: Kronk knows who asked (2026-09-04)

Plan: `../plans/VOICE_MUSIC_ORIGIN_KRONK_PLAN.md`. Fuzzy requests that
miss every local grammar ("put on some jazz", "shuffle my YouTube
playlist anime bangers") fall through to Kronk, and Kronk now plays them
where they were asked:

- **HA stamps the origin** onto the system prompt it already sends every
  voice request — the Ollama conversation instructions are a Jinja
  template with `llm_context.device_id` in scope. The line (installed
  2026-09-04 by storage edit; also editable via the integration's
  Reconfigure form):
  `[kronk-origin] device={{ llm_context.device_id or '' }} area={{ area_name(llm_context.device_id) if llm_context.device_id else '' }}`
  A template error there breaks every voice request ("Sorry, I had a
  problem with my template") — dry-run any edit through `/api/template`
  first; rollback is deleting the line.
- **The shim reads that one line** (`orchestrator/origin.py`) and still
  discards the rest of HA's prompt; the origin rides a request-scoped
  ContextVar for the whole run (see the module docstring for why not a
  parameter), the play tool adds `origin_device`/`origin_area` to its
  request, and tool_service resolves with the fork's order — named
  player → named room → **own device** (MAC join, in the same template
  call) → own room → default. The model never sees the origin. Every
  request logs an `origin` event (rid-scoped) so a wrong-room play is
  one grep away.
- Web UI / OpenAI shim / other clients carry no stamp and play on the
  default, as before.

**Three local tiers, not two.** HA's built-in `HassMediaSearchAndPlay`
intent catches "play X [in/on Y]" phrasings before the blueprint or
Kronk see them, searches the media player in the requesting device's
area, and answers a terse "Playing media". It is area-aware on its own.
Order of precedence for a spoken request: HA built-in intent → Kronk
blueprint fork → Kronk. (Correction: conversation triggers — the blueprint
— are matched *before* built-in intents; see
`INVESTIGATION_2026-09-15_album_played_one_track.md`.) Since 2026-10-21 the
blueprint (change 9) owns "play some …" / "play something …" and hands them,
and any failed named play, to Kronk — `docs/features/music-taste.md`. "Put on…", "shuffle my…", and other loose
phrasings are what reach Kronk.

**Radio (change 11, 2026-11-08).** The radio sentences consume the word
"radio", and MA's library lookup (`get_item_by_name`) is exact-name only,
so the bare name ("Mushroom Jazz") never hit a Pandora station (named
"Mushroom Jazz Radio") and fell to a provider search: 0.6–1 s, and for a
station the account doesn't have, a catalogue seed that may be a song
(`INVESTIGATION_2026-11-07_ma_pandora_and_ui.md`). The blueprint now asks
for `"<name> Radio"` (an owned station plays in ~0.9 s end to end), and a
radio play MA rejects goes to Kronk like the other types, which scores every
library station (near-miss "90s, 2000s and today's hits" → "90s, 2000s,
Today's Hits Radio", 7 s). A name MA *can* match in the Pandora catalogue
still creates a station there; that fix belongs upstream.

**Catch-all (change 12, 2026-11-08).** Any "play / put on / listen to …"
that no music sentence above matches goes to Kronk, never to HA's built-in
search-and-play (which played the first search hit after a 61 s MA artist
fetch, `INVESTIGATION_2026-11-08_anime_pop_radio_hang.md`). The specific
sentences still win; pause/stop/timers never start with those verbs and stay
local.

**Playlists are the household's own (change 13, 2026-11-09).** For "play
the playlist X" the blueprint asks the MA library for X
(`music_assistant.get_library`, read-only) and plays the item whose name
matches exactly, by URI (0.3 s). Any other name, or a failed lookup, goes to
Kronk, which scores every library playlist plus search results ("Hotel
Costas" → the library "Hotel Costes", 6.2 s). Before this, MA's name lookup
fell through to a provider search and played its first hit, a YouTube
"Hôtel Costes - Best 50"; the verify step can't tell a wrong success from a
right one.

**The hand-off to Kronk (changes 9 and 14).** `conversation.process` can't
carry the device id, so the blueprint writes the target into the text it
hands Kronk (`kronk_text`). Since change 14 (2026-11-21) the order is:

1. a target the speaker named: the sentence as spoken, nothing added;
2. the satellite's room, only when the MA players in that room are exactly
   the satellite's own player (one player, and it is the own one):
   `<sentence> in the <room>`;
3. else the satellite's own MA player: `<sentence> on the <friendly name>`;
4. else, with no own player, the room when it holds an MA player:
   `<sentence> in the <room>` (Kronk targets the room's sync group if it
   has one, as the blueprint does);
5. else the bare sentence (Kronk plays it on `MUSIC_DEFAULT_PLAYER`).

Rungs 2 and 3 give the player the blueprint's own-device rung picks.
Rung 2 keeps the room wording, so Kronk's reply still says "the Kitchen
speaker" for the kitchen, office and (after the 2026-11-20 area fix)
basement satellites. Rung 3 covers a room that lacks the own player (the
basement before that fix) or holds other players or a sync group with it,
where "in the <room>" would make Kronk play somewhere else or on several
speakers; the reply then names the player ("the kitchen voice pe ma
speaker"). Rung 2 reads the room's MA players directly (`in_room` in the
template), not `player_entity_id_by_assist_area`, which prefers a sync
group.

Until change 14 the room came first and was written whether or not it held
a player. The basement satellite's ESPHome device was in "Basement
Theater" while its MA device had no area, so every unnamed basement
hand-off asked Kronk for a room it could not play in
(`../incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md`).
`tests/test_blueprint_handoff.py` renders the real `kronk_text` from the
blueprint file for eleven satellite shapes (the own player alone in its
room, three times; the own player in a room without it, with another
player, with a sync group, or in another room; no own player with a room
holding a player or a sync group; neither; a named target) over a fake
registry, resolves it through `resolve_players`, and checks that Kronk's
target equals the blueprint's own `mass_player_entity_id`; on the old
template the basement shape fails. Carrying the device id across the
hand-off (M11's `rest_command` cutover, "Not done" below) retires the
workaround.

**Kronk-side radio (2026-11-08).** MA's Pandora `offer_new_stations` is on
(briefly off the same day, back on by operator decision): a station name the
account doesn't have, by either tier, creates a station from the first
exact-name catalogue item — genre, artist or song. The orchestrator forwards the user's own
sentence with every `play_music` call (`origin.said`); when it names a
station, tool_service treats the request as radio whatever the model passed,
takes the station name from the sentence, and scores spoken numbers as
digits too ("ninety's two thousands" → "90s 2000s"). The model's arguments
are a hint, not the source of truth — a 4B model dropped the radio type in
4/10 runs and cut the name in 3/10 on one request.

Kronk's tier: shipped; the blueprint fork and Kronk agree on targeting.

## How it works (tier 2)

`orchestrator/tools.py:play_music` → `tool_service POST /music` → HA REST
`music_assistant.play_media` → MA resolves the free-text query against its
providers → audio on the player. The route:

1. **Discovers the players from HA** (since 2026-09-04,
   `docs/plans/MUSIC_PLAYERS_FROM_HA_PLAN.md`): one template call lists
   every `media_player` the Music Assistant integration registered — the
   set MA can drive — with friendly name, area and state. Nothing is
   configured in compose except `MUSIC_DEFAULT_PLAYER`.
2. **Resolves in the blueprint's order**, so both tiers agree: exact
   spoken **player name** → exact spoken **area** (every MA player in it)
   → substring on names, then areas (tolerance, deliberately after both
   exact rungs so "kitchen" reaches the Kitchen area rather than a device
   called "kitchen-voice-pe-ma") → the request's **origin area**
   (`origin_area`, filled once the shim knows which satellite spoke —
   follow-on plan) → `MUSIC_DEFAULT_PLAYER`. Ambiguous names and unknown
   rooms are errors that say what exists. Spoken labels come from HA: the
   area when matched by area, else the player's friendly name — so
   technical device names never get read aloud for room requests.
   Since sprint M22 the same template call also lists every HA area
   (`areas`), players or not, and two cases say the cause
   (`INVESTIGATION_2026-11-19_basement_aerosmith.md`, fix 3):
   - a spoken room HA has that holds no MA player (exact, or the only
     substring hit) is refused with "The Bedroom has no Music Assistant
     speaker. Rooms with a speaker: Basement Theater, Kitchen, Office."
     instead of "I don't know a speaker or room called …", which was
     false;
   - when the origin's room holds no MA player (and the origin device has
     none of its own), the request still plays on the default, as before,
     and the label says why: "Now playing … on the Kitchen speaker (no
     Music Assistant speaker in the Bedroom)." The parenthesis rides in the
     label, so every reply that names the target (play, control, now
     playing, the dry-run line, "did not start") carries it, and so does
     "<player> is unavailable (no Music Assistant speaker in the Bedroom)"
     when that default is off. Chosen over
     refusing because the blueprint tier also falls to the default player
     here (its confirmation names the room it played in), and the sprint rule was
     that every request that resolved before resolves the same; the cost
     is a play in another room, now announced instead of silent. With no
     default configured the refusal names the room too.
   Neither changes which players any request picks
   (`tests/test_music_players.py`, `test_ha_areas_never_change_a_target`,
   and the live-path pin).
3. Unavailable targets are caught from the same list (503 "may be
   powered off"); a room with two speakers plays on both.
4. Calls `play_media`, then **polls the targets for `playing`** before
   reporting success — MA queues async, so HA's 200 alone proves nothing.
   Expired provider auth (e.g. YouTube Music) surfaces here as a clean
   failure instead of a silent no-play. Every play carries `enqueue:
   replace` (below).

**Every play replaces the queue (sprint M23, 2026-11-21).** Music
Assistant's per-queue default enqueue option is `replace` for every media
type except a track, whose default is `play`: insert at the current
position, start it, and keep everything after it. Nothing stated the
option, so "play the song X" on a speaker with a queue played X and then
the old queue came back (`../incidents/INVESTIGATION_2026-11-21_cracker_radio.md`).
Now every `music_assistant.play_media` says `enqueue: replace`: the named
play, the literal fallback, stations, links, Kronk's picks and "not that"
in tool_service (`music_resolve.PLAY_ENQUEUE`, sent by
`main._named_play_data` and `taste.Pick.play_data`, shown in the dry-run
`would_play`), and the blueprint's one `play_media` (fork change 15,
`action_data.enqueue`). Albums, artists, playlists, stations and genres
already replaced by default, and no queue overrides the default
(`/data/settings.json` in the MA container holds no
`default_enqueue_option_*`), so only track plays change. "Play X" now means
X, then whatever X itself holds, then silence. No path adds to a queue
("play X next", "add X to the queue"): none existed before, and "queue up
X" has always been a play verb. Pinned by `tests/test_music_enqueue.py`,
which also counts the call sites (one in tool_service, one in the
blueprint, none in the orchestrator). HA's own built-in media intents
(`HassMediaSearchAndPlay`) still use MA's default; Kronk's paths take every
sentence those would. An explicit `replace` clears MA's list of enqueued
items as soon as the call arrives, before the item resolves (MA
`queue_loader.py:820`; the default path clears it after the first item
resolved), so a `play_media` that fails to resolve clears that list on the
queue that is still playing. The list seeds autoplay ("Don't stop the
music", off on every queue) and album credit for what is playing; no effect
on sound (`../sprint/M23-e2e.md` finding 8).

**The two tiers must agree.** The blueprint's Jinja
(`player_entity_id_by_player_name` → `_by_area_name` → `_by_assist_area`
→ default) and `resolve_players()` in tool_service cannot share code;
`tests/test_music_players.py` pins the Python order, and
`tests/test_blueprint_handoff.py` renders the blueprint's own Jinja (its
targeting variables and `kronk_text`) over a fake registry and checks that
Kronk resolves the hand-off to the player the blueprint itself targeted.
If the blueprint's rule ever changes, change both.

## One decision-maker in tool_service: built and shadowed (sprint M11, 2026-11-17)

**Status: built, running in shadow mode only. Nothing the household hears
changed.** Criteria: `../sprint/M11-criteria.md`; build report:
`../sprint/M11-build.md`; plan: `Kronk evaluation findings and implementation
plan.md`, M11. Today three systems decide music (HA's built-in intents, the
blueprint fork's Jinja, the `home` agent's model plus `/music`). M11 builds
the one place that will decide instead: `tool_service` reads the user's own
sentence and decides, and the model is kept for one narrow question.

### The path

`POST /music/say` takes `{sentence, origin_device, origin_area, dry_run}` and
answers like `/music/voice`: always 200, `ok` and `say`, plus the decision,
the parsed sentence, the trace of steps and the targets. It honours
`MUSIC_DRY_RUN` exactly as `/music` does; its `dry_run` field can only turn a
dry run on. Nothing calls it for a live request yet: HA's thin automation and
the orchestrator's direct dispatch move to it at cutover. Not reachable
through nginx (tool_service's ports are bound to loopback; nginx proxies only
`/solar/` and `/health` to it).

1. **Parse** (`music_resolve.parse_sentence`, pure): the request verb (after
   the routing pin's lead-ins: "please", "Kronk,", "tell Kronk to", "can
   you", "okay nabu,"), the words, a type word, "X by Y" ("songs by Y" is the
   artist), shuffle, a named provider, and the target. Rooms and players are
   matched **from the right-hand end** against the real names HA lists:
   exact player name, exact area, the clause as part of a name ("the sonos"),
   then a near match (`room_min_score`); never a name inside the clause. So
   "The Girl with the Sun in Her Head by Orbital" keeps its title and "…
   in the office" targets the office. A clause ending in a device noun
   ("on the blue group") that matches nothing is an unknown target. Non-music
   "play" (news, games, videos, movies, podcasts) uses `routing._MUSIC_RE`'s
   exclusions; a test reads both lists out of the routing pattern. A station
   name comes from `radio_name()`, which still stops at a full stop. The
   parser trims nothing else: messy transcripts are the M14 gate's.
2. **Target**: `resolve_players`, as `/music` does (named player or room, own
   device, own room, default). The basement Voice PE's player had no area
   until 2026-11-20 (now "Basement Theater"); it is also reached by its
   name or the device join.
3. **Decide** (`music_decide.decide`), one function per step, in the order
   `config/music.yaml` `decision.order` gives:
   - `markers`: a type word (album, song, artist, playlist, radio, station)
     means a named item of that type; a style word ("some", "something" first;
     "music", "songs", "vibes" last) means a style.
   - `genre_list`: unmarked or style-marked, a known genre is a style. The
     list is the taste DB's vocabulary (the built-in set, `music_taste.yaml`'s
     genres, and the genre tags of the household's artists, from Music
     Assistant and the tagging model), read-only. "play jazz" is the style,
     "play the album Jazz" the album.
   - `named`: the fast case first, an exact library name (one local MA
     library query per type; no search), then the resolver: one search per
     type and query variant, scored as `/music` scores. The **provider
     policy** is a preference over those candidates, never one search per
     provider: among candidates at least `provider_min_score` (0.95) good and
     of the best one's type, the first provider in `provider_order` wins
     (NAS, then YouTube Music; radio Pandora; a provider named in the
     sentence first). Below that bar the top score wins, so a poor NAS match
     never beats a good YouTube Music one. An untyped title track yields to
     its same-named album. On an unmarked request, a match of only part of a
     name ("play italian" → "Italian Lesson No. 3") is a weak title and does
     not decide alone. A radio with no strong match asks MA's own station
     lookup (a Pandora catalogue station is created on its first play).
   - `cross_type`: a typed request with no strong match looks for the name as
     another type, strong matches only, and says what it found ("Playing the
     album …").
   - `other_reading`: a style the genre list does not know ("play some
     Portishead") is retried as a name; a strong **artist** plays, a lone
     title does not (rule 7). A name with no strong match is a style only if
     the genre list knows it.
   - `model`: still unclear (a weak or lone title, or an unmarked name with
     no match), one question to the model: style or named item, output
     constrained by a JSON schema to those two words. The answer picks the
     branch; the words still come from the sentence. tool_service calls
     LiteLLM directly (as `news.py` and `taste.py` do) with thinking off: 0.3
     s against 1.5 to 2.5 s with reasoning, the same answer on 20 of 20 test
     requests. Down, slow (`timeout_s`) or off the two words: no answer, and
     the rules' reading stands.
   - Then rule 7 (a lone title never beats a style reading) and rule 8:
     nothing resolves, say so plainly and name the closest match. **The raw
     words are never passed to Music Assistant**: a play always carries a URI
     from the library, a search or MA's station lookup. (`/music` keeps its
     literal-play fallback until cutover; a test pins it.)
4. **Play**: the existing play path (`_play_named`, the shared tail of
   `/music`, or `_play_genre`), with its verify poll.

Every decision is a `music.decide` span (`music.decided_by`, `music.reading`,
`music.reading_by`, `music.uri`, `music.candidates` with scores,
`music.model_asked`, `music.model_would_ask`, `music.searches`) and a
`music decision` log line with the steps and candidates. The model question
is its own `music.model_question` span.

**The policy file.** `config/music.yaml` has two new sections, through the
M21 loader (schema, comments, defaults equal to the file): `parser` (style
markers, type words, fillers, `room_min_score`) and `decision` (`order`,
`strong_min_score`, `title_min_score`, `provider_min_score`,
`provider_order` per media type, and `model`: `enabled`, `model`,
`thinking`, `timeout_s`). Drew's design principle, the model step earlier as
it gets faster: move `model` in `decision.order`; the other five keep their
order, and the loader refuses anything else. Neither section touches the
live `/music` path.

### Shadow mode

`MUSIC_SHADOW=1` (env; off in code and in production compose, on for
`tool_service-dev`). Every request that reaches the live `/music` (the
`music` rule through the home agent, `play_music` from the coordinator,
`/music/voice`) is answered exactly as before. After the answer exists, the
user's sentence (`said`) runs through the parser and decision order and one
JSON line is appended to `MUSIC_SHADOW_LOG` (default `/data/music_shadow.jsonl`:
`./data/music_shadow.jsonl` for production, `./data/dev/music_shadow.jsonl`
for dev), plus a `music.shadow` span.

- **It cannot play.** `music_shadow.py` imports no HA or MA client and is
  handed one function, `decide_sentence` with the model never asked; that
  reads HA's players template and decides through `ReadOnlyMusic`, whose
  Music Assistant access goes through `ma_read` (read commands only; any
  other is refused before it is sent) and whose taste access opens the DB
  read-only. A test reads the two modules' imports and names.
- **It never asks the model.** Where the decision would, the record says
  `"would_ask": true`.
- **It never surfaces.** It starts after the live answer, runs one at a
  time, times out after `MUSIC_SHADOW_TIMEOUT_S` (30), and records any
  failure in its own line. Measured on dev: with requests 1.5 s apart, live
  `/music` p50/p95 32/43 ms with shadow on, 30/44 ms off (24 each); back to
  back, 37/60 against 21/37 ms (the previous request's shadow still
  running); answers identical in all 48 pairs.
- **Record** (`"v": 1`): `ts`, `trace_id`, `sentence`, `origin`,
  `live_request` (the model's arguments), `live` (`status`, `uri`, `targets`,
  `kind`, `media_type`, `name`, `artist`, `pick`, `literal` when the words
  were sent as they were, `error`, `ms`), `new` (the outcome: `action`,
  `reading`, `reading_by`, `decided_by`, `kind`, `media_type`, `name`,
  `artist`, `uri`, `provider`, `via`, `station`, `pick`, `closest`,
  `targets`, `target_error`, `model`, `searches`, `candidates`, `parsed`,
  `ms`, `error`), `agree` and `why`; or `skipped` when there is no sentence
  or the request was "not that".
- **Reading it**: `python3 scripts/music_shadow_report.py [LOG] [--since
  2026-11-18] [--json]` prints the agreement rate over the requests the
  parser read as music, the declined sentences apart (no request verb: they
  stay on the model path at cutover, since HA's trigger is on request
  verbs), `decided_by` counts, how often the model question would have
  fired, both paths' latency, and every disagreement with both outcomes and
  the trace id.
- **What it cannot see**: requests the blueprint plays itself through Music
  Assistant never reach Kronk, so they are never shadowed; and what does
  reach Kronk is, by selection, what the blueprint did not take (mostly
  sentences without a request verb at the start). Per the plan (C5) the
  corpus replay is the main evidence for cutover; `scripts/voice_replay.py
  --answers` runs every corpus row through dev's `/music/say` and `/music`.

### Not done (waits for Drew's review of the shadow and corpus data)

- Cutover, one trigger type at a time behind a blueprint input, loose and
  catch-all first, named albums and stations last (plan steps 5 and 6).
- The thin HA automation and `rest_command.kronk_music` to `/music/say`
  (needs `rest_command` loaded: plan C6).
- The orchestrator's `music` route as a direct dispatch to `/music/say`.
- Retiring the blueprint's Jinja resolution and the fork's changes (keep the
  file for rollback).
- Removing the literal-play fallback from `/music`.
- Charting `music.decided_by` on the Music dashboard.

## Adding a satellite

Assign an **area** in HA to both of its devices — the ESPHome one that
hears you and the Music Assistant one that plays — and it is playable by
voice with no Kronk change. Each satellite is two HA devices; the join
key is the MAC: the MA device's identifier is `up` + the MAC without
colons, and both original names end in the MAC's last three bytes. List
the pairs from HA's registry:

```bash
docker exec homeassistant cat /config/.storage/core.device_registry \
  | jq -r '.data.devices[] | "\(.name_by_user // .name)\t\(.name)\t\(.connections[]?[1] // .identifiers[][1])"' \
  | grep -i "voice\|satellite" | sort -k2
```

Name devices however you like (`satellite-01-ha` / `-ma` is fine); keep
**area names speakable** — that is what voice addresses. When renaming a
device in HA, decline the offer to rename entity ids.

**Check the pair (sprint M22).** `python3 scripts/satellite_area_check.py`
lists every `assist_satellite`, its area, the MA player that carries its
MAC and that player's area, and says `ok`, `mismatch` (the player is in
another area or none), `no_area` or `no_player`. Reads only: one render of
HA's `/api/template` with `HA_TOKEN` from `.env`, never printed. Exit 0 when
every satellite is ok, 1 otherwise, 2 when HA could not be read. On
2026-11-21 it listed 3 satellites, all ok (basement, kitchen, office). The
basement pair was a `mismatch` from 2026-09-04 until fix 1 on 2026-11-20,
and that is what sent "in the Basement Theater" to Kronk. Run it after
adding or moving a satellite.

## The terminal-tool mechanism (the interesting part)

`play_music` is a **terminal tool** (`AgentConfig.terminal_tools` in
`orchestrator/agents.py`): its result is converted to speech verbatim
(`_terminal_speech`) and the agent turn ends immediately. This is a
*structural* guardrail, added after prompt engineering failed three ways:
gemma-4-e4b narrated fake tool scaffolding aloud, hallucinated "Jazz is now
playing" after a 503, and retried the tool to budget exhaustion. With the
mechanism, the model never gets a chance to editorialize about the result.

## Music sources

Streaming providers are configured in MA's UI. The **Synology library**
(since 2026-09-04) arrives as a host-side read-only CIFS mount
(`/mnt/nas-music`, `/etc/fstab`, credentials root-only in `/etc/kronk/`)
bound read-only into the container at `/media/nas` with `rslave`
propagation, feeding MA's local filesystem provider — no container
capabilities involved. If the NAS was down at boot the directory is
simply empty until `sudo mount /mnt/nas-music`; the container sees the
remount without a restart. Details and the SMB-dialect gotcha:
`../plans/MUSIC_ASSISTANT_PLAN.md` Phase 6.

## Request accuracy: station-first phrasing, and "what song is this" (2026-09-14)

Plan: `../plans/MUSIC_ACCURACY_PLAN.md`. Analysis of a day's requests
found "Play X Radio on Pandora" missing the fork's radio grammar and
being taken by **HA's built-in search-and-play intent**, which searches
plain text across all providers, doesn't verify, and lands on YouTube
Music title matches ("Anime Pop Radio on Pandora" → a song called
"Pandora"). Fork change 4 adds a station-first radio sentence, so those
requests reach MA typed as radio. `now_playing` (terminal, origin-aware,
pinned by `routing._NOW_PLAYING_RE` for "what song is this" and kin)
reads the speaker and answers one sentence: track, artist, station or
album, room, source. Gotcha: MA reports a station as `media_content_type:
music` with the station name in `media_album_name`; the id path
(`…/radio/N`) is what says "station". Rejected on latency: confirming
the fork's fast-tier play from the player's actual title (would add
1–2 s); the fork still echoes the request, so a fuzzy mismatch there is
silent until you ask "what song is this".

## Shuffle, and keep playing when the queue ends (2026-09-16)

- **Shuffle** was always in the blueprint: any matched sentence that starts
  with "shuffle" sets shuffle on the player after `play_media`. "Shuffle my
  YouTube playlist X" did not match (`[the ]playlist` only) and fell to
  Kronk, whose tool had no shuffle, so the playlist played in order.
  Two fixes: KRONK change 5 in the blueprint accepts `[my ][the ][youtube
  [music] ]playlist`, and `play_music` gained a `shuffle` flag —
  tool_service sets `media_player.shuffle_set` after a verified play,
  **on or off every time** (like the blueprint, so a previous shuffle never
  leaks into the next album), and reports the player's own `shuffle`
  attribute back; the spoken line says "shuffled" only if the player says so.
- **Keep playing after the song/playlist ends** — two MA features, both
  supported by YouTube Music (similar-tracks), not by Pandora:
  *radio mode* per request (blueprint: "... with radio mode"; Kronk's tool
  does not pass it) seeds the queue with the item and keeps adding the
  provider's song-radio picks; *Don't stop the music* per player queue
  continues with similar tracks when the queue runs dry. It was turned
  on for the kitchen queue (`up20f83b0ac919`) via the MA API
  `player_queues/dont_stop_the_music` on 2026-09-16. On 2026-11-21 every
  queue read off, the kitchen included
  (`../incidents/INVESTIGATION_2026-11-21_cracker_radio.md`); whether an MA
  restart dropped it or someone turned it off is not known. Turning it back
  on is an MA setting, the operator's call.
- Gotcha kept from the same day: song titles containing "in"/"on" get
  chopped by the blueprint's optional `[(in|on|using) {area_or_player_name}]`
  clause ("The Girl with the Sun in Her Head by Orbital" → media "The Girl
  with the Sun", room "Her Head by Orbital"); it fell through to the kitchen
  and found the track by luck.

## Playback control: stop, pause, resume, skip, volume (2026-09-10)

Plan: `../plans/PLAYBACK_CONTROL_PLAN.md`. `control_music(action)` is a
terminal tool on the home agent next to `play_music`: the model supplies
only the action (and a speaker/room if the user named one); the origin
stamp supplies the speaker that heard the request; tool_service
`/music/control` resolves the target with the same rule as play, calls
the `media_player` service over the websocket helper, and **verifies
the effect** (state reaches paused/playing, or the volume moved) before
answering. **"stop" is pause** — it keeps the queue so "resume" works.
Bare playback verbs ("Stop!", "pause", "skip this song", "louder") are
**pinned** to the home agent by `routing._PLAYBACK_RE` — the coordinator
otherwise answered "Stop what?". Gotchas: MA reports a Sendspin player
`idle` after pause while keeping the queue (resume works from idle);
the spoken label names the player unless its MA device has an HA area.
HA's own "resume" intent sometimes answers first (2 s) — fine, same
result. The ~1 s sentence-trigger fast path is still open (ROADMAP 18).

## Failures are spoken verbatim (2026-09-05)

Plan: `../plans/ERROR_SURFACING_PLAN.md`. Two rules, both structural:

- **tool_service calls HA services over the websocket**, not REST. HA's
  REST API answers a bare "500 Internal Server Error" for any error an
  integration raises during a service call; the websocket returns the
  message ("Playback failed for Portishead Radio - no more tracks
  available"). `ha_call_service()` is the helper; REST stays for reads.
  A failed call is still followed by the playback poll — MA has failed
  the call and played anyway.
- **A delegated specialist's terminal result passes through the
  coordinator untouched.** The home agent ends its turn verbatim on
  `play_music`; the coordinator used to receive that as an ordinary
  `ask_home` result and reword it ("can't play music right now"). Now
  `run_delegated()` reports "ended on a terminal tool" and the
  coordinator ends its own turn with the same text (`terminal_passthrough`
  event). Success sentences pass through the same way.

Limit: the deepest cause (Pandora's HTTP 429 under that Portishead
failure) never leaves MA's own log; HA's message is the most specific
thing obtainable while HA is the broker.

## Gotchas

- `MUSIC_DEFAULT_PLAYER` must be an **MA entity** (`_2`-suffixed, platform
  `music_assistant`) — native Sonos/Cast entities can't be driven by MA.
  Discovery keys on the integration, not on `mass_player_type`: an
  unavailable player drops its attributes.
- The blueprint matches **exact MA player names**: "sonos move" ≠ "Sonos Move
  Derp" → silently plays on the default player. Fix: rename the player in
  MA's UI (entity_id survives renames).
- Blueprint grammar traps: "play the album X **by Y**" stuffs Y into the
  media name; it needs "by the **artist** Y".
- MA 2.8.8's YT Music provider can 500 transiently (`ytmusicapi has no
  attribute YTMusicError` — upstream bug). tool_service logs the full body,
  speaks one clean sentence. **Observed 2026-09-04: `play_media` can
  return 500 and still start playback** ("put on some jazz" — Kronk said
  it failed while the kitchen played). Follow-up on the roadmap: on a
  5xx, poll for `playing` before declaring failure (tenet 6 cuts both
  ways).

- **Local library credits come from the album-artist tag, and ours has
  none.** MA's filesystem provider setting "Action when a track is
  missing the Albumartist ID3 tag" defaults to *Use Various Artists*;
  with that, 85% of the NAS albums were "Various Artists" and "album X
  by Y" resolved to the YouTube Music copy instead of the local one.
  Set to *Use Track artist(s)* (2026-09-15). Name resolution
  (`music_assistant_client.get_item_by_name`) is exact-match on the
  library first, then a provider search — a misheard word never matches
  locally, and streaming search is more forgiving than the library.
- **A rescan does not re-read unchanged files** (size+mtime checksum),
  so a provider setting that changes how files are parsed only applies
  after removing and re-adding the provider — and **removing a
  filesystem provider makes MA reset the whole library database** and
  re-sync every provider, quietly. Back up `/data/library.db` and
  `settings.json` first; never do it as a side effect.

- **YouTube Music runs as a dedicated Google account (2026-10-02).** MA's
  provider searches with the account cookie, so every voice request (often
  one search per media type) landed in the operator's YouTube search
  history. The provider now uses a Kronk-owned account (search history
  off). Cookie recipe: private window, sign in as that account only,
  DevTools → Network → a `browse` request → copy the whole `cookie` header,
  close the window without signing out, paste into the provider's
  reconfigure form. Proving which account is live: MA serves provider
  browse from its library DB, the home feed from a 1 h cache, and searches
  from a 7-day cache — only a fresh, unique search shows up in the right
  account's history. The library then follows the new account at the next
  YT sync; the operator's playlists must be shared to it to stay playable.

- **Kronk never lets the coordinator answer a music request (2026-10-05).**
  Blueprint misses ("Start an orbital radio station on YouTube Music")
  used to land on the coordinator, which answered with invented refusals
  or copied failure text and no tool call. `_MUSIC_RE` in `routing.py`
  pins request-verb-at-start music phrasings to the home agent; the play
  tool's verbatim result is what gets spoken. Known miss: a STT-mangled
  verb ("like massive attack radio") still reaches the coordinator.

- **MA 2.11 refuses shuffle on a dynamic radio** ("Cannot change shuffle
  while the queue is in dynamic mode"). The blueprint's shuffle step is
  `continue_on_error: true` (KRONK change 7, 2026-10-07) — without it the
  script aborted after the station had started and Assist said "Done".
  `/music` wraps its own shuffle_set the same way.

## Blog hooks

- One decision-maker for music: a parser that reads rooms from the
  right-hand end, a decision order with one function per step, and the
  model kept for a single two-word question (0.3 s with thinking off).
- Shadow mode that cannot play by construction: hand the shadow a
  read-only function, not a flag.
- The selection effect: what reaches the fallback path is what the fast
  path could not read, so a rules-only shadow mostly declines it.

- Terminal tools: when prompt engineering loses to a 4B model, change the
  loop, not the prompt.
- "HTTP 200 means nothing": verifying async playback actually started.
- Two-tier voice design: strict-grammar fast path + LLM fuzzy fallback.
