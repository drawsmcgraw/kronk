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
blueprint fork → Kronk. "Put on…", "shuffle my…", and other loose
phrasings are what reach Kronk.

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
3. Unavailable targets are caught from the same list (503 "may be
   powered off"); a room with two speakers plays on both.
4. Calls `play_media`, then **polls the targets for `playing`** before
   reporting success — MA queues async, so HA's 200 alone proves nothing.
   Expired provider auth (e.g. YouTube Music) surfaces here as a clean
   failure instead of a silent no-play.

**The two tiers must agree.** The blueprint's Jinja
(`player_entity_id_by_player_name` → `_by_area_name` → `_by_assist_area`
→ default) and `resolve_players()` in tool_service cannot share code;
`tests/test_music_players.py` pins the Python order. If the blueprint's
rule ever changes, change both.

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
  continues with similar tracks when the queue runs dry. The kitchen
  queue (`up20f83b0ac919`) has Don't stop the music **on** (set via the MA
  API `player_queues/dont_stop_the_music`, 2026-09-16); the other six
  queues are off. Whether MA keeps the flag across its own restart is
  unverified — check it after the next deliberate MA restart.
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

## Blog hooks

- Terminal tools: when prompt engineering loses to a 4B model, change the
  loop, not the prompt.
- "HTTP 200 means nothing": verifying async playback actually started.
- Two-tier voice design: strict-grammar fast path + LLM fuzzy fallback.
