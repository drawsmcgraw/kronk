# Investigation 2026-11-19 — "Play the album Get a Grip by Aerosmith" in the basement: "I don't know a speaker or room called 'Basement Theater'"

**Status:** root cause determined; nothing fixed (read-only investigation).
Not a sprint regression: the same failure was captured on 2026-11-08, before
the sprint, and the code and blueprint involved are unchanged since
2026-11-14.

## Symptom (as the operator saw it)

Drew asked the basement Voice PE to play Aerosmith. Kronk understood the
request but answered that it could not find the playback device. In the next
turns Kronk understood him and the album played on the basement speaker.
His question: why was the device not carried through from the original voice
request?

What the speaker said on the first turn, verbatim:

> I couldn't play that. I don't know a speaker or room called 'Basement
> Theater'. Speakers: Cameron’s MacBook Pro, Living Room Sony Bravia, SHIELD,
> Sonos Move Derp, Upper Shield, basement voice pe ma, kitchen voice pe ma,
> satellite 1 ma. Rooms with a speaker: Kitchen, Office.

## Short answer

The device was not lost in Home Assistant. The blueprint fork resolved the
basement player correctly on the first turn (`bp.target =
media_player.home_assistant_voice_0ab117`). It was lost at the hand-off to
Kronk. `conversation.process` cannot carry a device id, so blueprint change 9
writes the target into the words, and it prefers the satellite's **room**
over the satellite's **own player**. The basement satellite's ESPHome device
is in the area "Basement Theater", but its Music Assistant device ("basement
voice pe ma") has no area. The hand-off text named a room that holds no
Music Assistant player, and tool_service's resolver rejected it.

The hand-off happened at all because Whisper heard "Get a Grit" for "Get a
Grip", so Music Assistant could not resolve the album and the blueprint
passed the request to Kronk.

## Setup at the time

- Satellite: basement Voice PE,
  `assist_satellite.home_assistant_voice_0ab117_assist_satellite`, ESPHome
  device `1b6bd3b03f90e52f08dd98dca41eac82` ("basement voice pe", area
  `basement_theater`). Its Music Assistant device:
  `b91145f1a0afef63089911ffb5ba8b6b` ("basement voice pe ma", identifier
  `up20f83b0ab117`, **area empty**), entity
  `media_player.home_assistant_voice_0ab117`. Both devices were last modified
  in the registry on 2026-09-04 (21:36:07 and 21:36:40 UTC); the area
  "Basement Theater" was created that day at 21:30:49 UTC. The kitchen and
  office pairs have both devices in their area.
- Pipeline: production `kronk` (`01ksc2pw5bkhtk37dh2d6zwcxp`, Okay Nabu),
  STT `stt.faster_whisper` (production Whisper, no vocabulary prompt),
  conversation engine `conversation.ollama_conversation`,
  `prefer_local_intents: true`. Not the dev path; no dry run.
- Blueprint: `kronk_mass_voice_device_first`, version `20250404-kronk13`.
  The installed file in HA is identical to
  `ha/blueprints/mass_assist_kronk.yaml` (diffed read-only).
- Kronk: production runs the `sprint-M18` release
  (`../kronk-releases/sprint-M18`); `VOICE_STREAM=off`,
  `TELEMETRY_RECORD_TEXT=true`, tool_service `MUSIC_SHADOW=1`, no
  `MUSIC_DRY_RUN`. `git diff sprint-M18 HEAD` is empty for
  `tool_service/main.py`, `orchestrator/routing.py`, `orchestrator/origin.py`,
  the blueprint and `config/`.

## Timeline (2026-11-19, EST; UTC in parentheses)

All three turns came from the basement satellite on the `kronk` pipeline, in
one HA conversation (`01M7T3S6DNVE84WC5Y67M8F9VG`).

**Turn 1** — voice run `01M7T3S6DN76GF3B7ERW36P4YQ`, voice trace
`3df6dd34184469fff2b6755753601bac`, blueprint trace
`6cec394572bc6c453ad4d459d85d5296`, Kronk trace
`31dc799b602fad030540a0d372cb3600` (rid `7aaa9497`).

- 09:36:14.647 (14:36:14.647) — run start; VAD start 1050 ms, VAD end
  4230 ms.
- 09:36:19.196 (14:36:19.196) — STT returned
  `Play the album Get a Grit by Aerosmith.` (40 characters).
- 09:36:19.197 — blueprint trigger `album`; `area_or_player_name` empty;
  target by the device join: `media_player.home_assistant_voice_0ab117`
  ("basement voice pe ma").
- 09:36:19.200 → 09:36:20.018 — `music_assistant.play_media`
  (media type album, name `Get a Grit by Aerosmith`) failed: `Could not
  resolve ['Get a Grit by Aerosmith'] to playable media item`.
- 09:36:20.018 — blueprint `bp.handoff_kronk`: `conversation.process` to
  `conversation.ollama_conversation`.
- 09:36:20.026 — orchestrator `request`, text
  `Play the album Get a Grit by Aerosmith. in the Basement Theater`. No
  `origin` event: the hand-off carries no `[kronk-origin]` stamp.
- 09:36:20.036 — `route_shortcut`, rule `music`, route `home` (model path,
  not a direct dispatch). M14 gate not triggered (one sentence by
  `transcript_gate`: ". in" does not start a sentence). M19 lead-in not
  present.
- 09:36:20.044 — model lock `127.0.0.1:11438`, priority voice, wait 0 ms.
- 09:36:24.329 — home agent round `plan_1`, gemma-4-e4b, 4.28 s, 3994
  prompt tokens, 195 completion tokens.
- 09:36:24.341 — tool call `play_music`
  `{"media_type": "album", "player": "Basement Theater", "query": "Get a Grit"}`.
- 09:36:24.360 — `POST tool_service:8003/music` → `400 Bad Request`. Tool
  result: `[Could not play music: I don't know a speaker or room called
  'Basement Theater'. Speakers: … Rooms with a speaker: Kitchen, Office.]`
  (terminal tool; spoken as above).
- 09:36:24.361 — `request_complete`, 4.33 s. TTS at 09:36:24.365; run end
  09:36:24.366 (9.72 s total).
- 09:36:24.369 → 09:36:27.337 — M11 shadow (after the answer; 2978 ms).
  Record in `data/music_shadow.jsonl` (`trace_id 31dc799b…`): origin
  `{area: null, device: null}`; `live.status error`; the new path would have
  played the **track** "Get A Grip" by Aerosmith
  (`ytmusic--kGmHoQDn://track/9yH6-CIt7DE`, `decided_by cross_type`) on
  `media_player.home_assistant_voice_0ac919_pe_01`, the **kitchen** (the
  default); its parser left the room inside the artist
  (`artist: "Aerosmith. in the Basement Theater"`).

**Turn 2** — voice run `01M7T3T2ZMYBKNEWXT3TJCNSG7`, voice trace
`8c925a5324726f5a66c45a9027f6e96e`, blueprint trace
`0c840743a076553742e39e17667682bc`, Kronk trace
`3c6cedaa204747ccfe6be64c8cd2d606` (rid `0d55fa6a`).

- 09:36:43.894 (14:36:43.894) — run start.
- 09:36:49.154 (14:36:49.154) — STT returned
  `play it on the basement voice Pima` ("pe ma" heard as "Pima").
- 09:36:49.156 — blueprint trigger `catchall` (it captures no target), hand
  off to Kronk.
- 09:36:49.163 — orchestrator `request`, text
  `play it on the basement voice Pima in the Basement Theater`; rule
  `music`, route `home`.
- 09:36:52.276 — home agent round `synthesis`, 3.10 s, 3992 prompt tokens
  (3975 cached), no tool call. Reply: `I can play music on the Pima in the
  Basement Theater, but what would you like to listen to?` Run end
  09:36:52.293. No `/music` call, so no shadow record.

**Turn 3** — voice run `01M7T3THW45BVXT815AT4S5F5K`, voice trace
`176dd3762770817e15116624c9175177`, blueprint trace
`04a8a35bda15de9fb445e15727bf7bbf`. Never reached Kronk.

- 09:36:59.142 (14:36:59.142) — run start.
- 09:37:04.073 (14:37:04.073) — STT returned
  `Play the album Get a Grip by Aerosmith.`
- 09:37:04.074 — blueprint trigger `album`, target by the device join
  (`media_player.home_assistant_voice_0ab117`); `play_media` 4.25 s
  including the verify wait (`bp.wait_completed true`).
- 09:37:08.325 (14:37:08.325) — HA state of
  `media_player.home_assistant_voice_0ab117`: `idle` → `playing` (history,
  read-only; it was `idle` from at least 14:30:00).
- 09:37:08.329 — reply `Get a Grip by Aerosmith playing on basement voice pe
  ma`. Run end 09:37:08.33.

## The path

1. HA: wake word on the satellite; Whisper on `:10300`; HA's sentence
   triggers run first. The Kronk blueprint's `album` trigger took turns 1
   and 3, the `catchall` trigger turn 2. HA's built-in intents never
   answered.
2. Blueprint: resolves the target in its own order (named player → named
   room → own device → own room → default). On turns 1 and 3 that was the
   own-device rung. Turn 1's `play_media` failed on the misheard name, so the
   blueprint handed the request to Kronk with `kronk_text`
   (`ha/blueprints/mass_assist_kronk.yaml:373-379`):

   ```jinja
   {%- set room = area_name(assist_device_id) if assist_device_id else none -%}
   {%- set own = player_entity_id_by_assist_device | first | default(none) -%}
   {%- if area_or_player_name -%}{{ trigger.sentence }}
   {%- elif room -%}{{ trigger.sentence }} in the {{ room }}
   {%- elif own -%}{{ trigger.sentence }} on the {{ state_attr(own, 'friendly_name') }}
   {%- else -%}{{ trigger.sentence }}{%- endif -%}
   ```

   The room branch wins whenever the satellite's ESPHome device has an
   area, whether or not that area holds a Music Assistant player. The
   comment above it (`:366-370`) says the basement satellite has no area.
   That is wrong: the satellite has the area; its Music Assistant device does
   not.
3. Kronk: no origin (the hand-off has none); rule `music` → home agent →
   gemma-4-e4b → `play_music` with `player: "Basement Theater"`, which the
   text asked for. The model did what the words said.
4. tool_service `resolve_players` (`tool_service/main.py:1356-1435`):
   exact player name → exact area → substring name → substring area, where
   the area list is built only from players' areas (`main.py:1373`). No
   Music Assistant player has the area "Basement Theater", so none of the
   four rungs matches and the call raises the 400 (`main.py:1411-1415`).

## Evidence for the other questions

**Would the same sentence have worked before the sprint?** No.

- `resolve_players` is byte-identical between `78375ba` (2026-11-14, the
  last commit before the sprint) and `HEAD`; the blueprint and its install
  are unchanged since `c7c4ec5` (2026-11-14, change 13). The `in the
  {{ room }}` suffix arrived in `aa56b8c` (2026-11-04).
- Offline, the pre-sprint `routing._classify_inner` (from `git archive
  78375ba orchestrator`) and the current one both route
  `Play the album Get a Grit by Aerosmith. in the Basement Theater` and
  `play it on the basement voice Pima in the Basement Theater` to
  `('home', 'music')`.
- The same failure is in production's log on 2026-11-08 22:12:24 UTC, before
  the sprint (below).
- None of the sprint changes acted: M7 (the `music` rule is not a direct
  dispatch), M14 (one sentence; the gate did not fire), M19 (no lead-in),
  M9 (lock wait 0 ms, well inside the 25 s deadline), M8 (streaming off in
  production; the spoken-reply line is coordinator-only and the home agent
  answered), M11 (shadow ran after the answer).

**Offline reproduction** (unit level: `resolve_players` and
`music_resolve.parse_sentence` with the live player list rebuilt from the
400 body and HA's registries; nothing called):

| Input | Result |
|---|---|
| `player="Basement Theater"`, no origin (turn 1) | 400, the exact sentence the speaker said |
| no player, no origin | kitchen voice pe ma (the default) |
| no player, origin area "Basement Theater" only | kitchen voice pe ma (own-room rung finds no player, falls to the default without a word) |
| no player, own-device key `up20f83b0ab117` | basement voice pe ma |
| `player="basement voice pe ma"` (the blueprint's `own` branch) | basement voice pe ma |
| `player="basement voice Pima"` (turn 2 as heard) | 400, unknown speaker |

Parser, same player list: `… Aerosmith. in the Basement Theater` → room
none, artist `Aerosmith. in the Basement Theater`; `… Aerosmith. on the
basement voice pe ma` → room `basement voice pe ma` (player), artist
`Aerosmith`.

**Had the target resolved, would turn 1 have played?** Not through today's
live path. Dev, dry run confirmed first (`MUSIC_DRY_RUN=1` in
`kronk-tool_service-dev-1`'s environment and its startup warning),
loopback `:8103`, text only:

- `POST /music` `{"query": "Get a Grit", "media_type": "album", "player":
  "basement voice pe ma", ...}` → `I couldn't find an album called Get a
  Grit in Music Assistant. The closest I found was I GET IT by GRIT.` The
  model passed no artist, and the live path does not reread the sentence
  for albums.
- `POST /music/say` (dry run) with the sentence ending `on the basement
  voice pe ma` → `Dry run: would play the album Get A Grip by Aerosmith on
  the basement voice pe ma speaker.` (`library://album/247`,
  `decided_by named`). At 14:36 the shadow found no library album and
  reached the track by `cross_type`; turn 3's play has since put the album
  in the library.

**Hypotheses tried**

1. *The room word in the transcript ("basement" vs "Basement Theater" vs
   the player's name).* Rejected. The first transcript names no room at all.
   "Basement Theater" came from the blueprint, not from Drew.
2. *The model invented the room from the origin stamp.* Rejected. The model
   never sees the origin, and this request had none; `kronk.input` on the
   pipeline span shows the room in the text HA sent.
3. *The satellite's device id not reaching Kronk.* True and known (the
   hand-off carries no device id; the shadow record says `origin {area:
   null, device: null}`), but not the cause by itself. Without the suffix
   the resolver would have played on the kitchen default without a word;
   the blueprint writes the target into the words to compensate, and the
   words it chose were wrong for this satellite.
4. *The resolver's matching thresholds in the live config.* Rejected.
   `resolve_players` uses no thresholds. The only threshold near the target
   is the M11 parser's `room_min_score` (0.85 in both
   `../kronk-config/prod/music.yaml:77` and `config/music.yaml:77`), and the
   parser decides nothing live.
5. *The player unavailable or off.* Rejected. `idle` from 14:30:00 until
   turn 3 started it at 14:37:08; the blueprint targeted it on turn 1.
6. *A sprint regression (M7, M14, M19, M11, M8, M9).* Rejected; see above.
7. *Misheard album name.* True, and the reason the request left the
   blueprint, but it is not why the device was lost. The blueprint had the
   right device and handed Kronk the wrong room.

## Root cause

Two facts that are each harmless alone:

1. In HA's device registry the basement satellite's ESPHome device is in
   the area "Basement Theater" and its Music Assistant device has no area.
   The "Adding a satellite" rule in `docs/features/voice-music-control.md`
   (assign the area to both devices) was not met for this satellite; the
   kitchen and office pairs meet it.
2. Blueprint change 9's `kronk_text` writes the satellite's room before its
   own player, assuming the room contains the player.

Together, every blueprint → Kronk hand-off from the basement that does not
name a target carries "in the Basement Theater", a room tool_service cannot
play in. tool_service then answers with a correct, specific error that names
everything except the cause.

## Why the follow-up turns worked (and the second did not)

- **Turn 2 did not work.** It went the same way (catch-all → Kronk, the same
  suffix). The model asked what to play instead of calling the tool. Kronk
  had no memory of turn 1: the blueprint's `conversation.process` call
  passes only `agent_id` and `text` (`mass_assist_kronk.yaml:416-420`,
  `:526-530`), so each hand-off is a fresh conversation with no prior turns
  for `_shim_context` to read (prompt tokens 3994 and 3992). Had it called
  `play_music`, "basement voice Pima" and "Basement Theater" both resolve to
  nothing (offline table above).
- **Turn 3 worked because Kronk was not involved.** Whisper heard "Grip",
  Music Assistant resolved "Get a Grip by Aerosmith", and the blueprint
  played it on the player it had found by the device join, the same player
  it found on turn 1. Its confirmation read the technical name aloud ("on
  basement voice pe ma") for the same registry reason: the player has no
  area, so change 3 cannot say "in the Basement Theater".

## How often (the stopping rule)

Sources: Tempo (since 2026-11-08), journald for the orchestrator (since
2026-10-15; text previews only where text recording was on), the shadow
logs (since 2026-11-17).

- **Basement satellite voice runs in Tempo: 5** (2026-11-08 23:24, 2026-11-14
  15:37, and today's three). Blueprint → Kronk hand-offs from the basement
  device in Tempo: **3**, all carrying the suffix: 2026-11-08 22:12:24 UTC,
  2026-11-19 14:36:19 and 14:36:49. The two that called `play_music` got
  this 400; the third is turn 2.
- **`/music` 400s in Tempo: 25.** Production: **2**, both this failure
  (`82f37a34939a767ae2808f614aeb1822` on 2026-11-08, `31dc799b…` today). The
  other 23 are dev.
- **2026-11-08 22:12 UTC**, production, a scripted text run with the
  basement device id during the telemetry v2 verification
  (`docs/plans/TELEMETRY_V2_REPORT.md`, row "Put on Portishead on the
  basement voice pe ma. That is all."): the catch-all trigger captured no
  target, so the suffix was added although the sentence named the player,
  and the model chose the appended room. Same 400, recorded in the report;
  the report fixed the trace join and did not open the refusal.
- **Sprint M14 e2e** (`docs/sprint/M14-e2e.md`, "Other real messy
  transcripts" and "Gate off"): that sentence, with the suffix, replayed 10
  times on dev; 8 of 10 got this refusal, 2 a neighbouring target error.
  Recorded as "refused for its speaker name in 5 of 5 … the gate does not
  change it" and left there.
- **Production shadow log: 4 records, every one a blueprint hand-off**
  (origin null in all four). The 3 from the kitchen resolved through the
  suffix "Kitchen"; the 1 from the basement is this one. Dev shadow log:
  1473 records, 9 unknown-target errors, all deliberate test targets ("blue
  group", "garage speaker", …), none of this shape.

Household captures: 1 (today). Test captures in production: 1. Synthetic
replays on dev: 10. Every basement hand-off with no named target has hit it,
3 of 3.

The stopping rule is about new special-case routing rules. None of the fixes
below is one. A routing rule for this (for example, stripping "in the
<room>" in `routing.py`) is not admitted: one household capture, and the
defect is upstream of routing.

## What would have caught it sooner

- **Reading the two earlier refusals.** The telemetry v2 report and the M14
  e2e both printed this exact sentence. Neither became an incident. A
  "couldn't play" that names a room the household has is a bug, not test
  noise.
- **A cross-tier test for the hand-off text.** "The two tiers must agree"
  is pinned for resolution order (`tests/test_music_players.py`) but not
  for `kronk_text`. A test that renders `kronk_text` for each satellite in
  the device registry and resolves the result through `resolve_players`
  would have failed for the basement on 2026-11-04.
- **A registry check for the satellite rule.** Each satellite's ESPHome
  device and its Music Assistant device share an area. A read-only check
  (`/api/template` or the registry) would have flagged the basement on
  2026-09-04.
- **The replay cannot see it.** `scripts/voice_replay.py` checks HA-local
  rows read-only and never executes them, and the blueprint always hands
  off to production Kronk (corpus doc, Gotchas). `--answers` sends the
  transcript, not the hand-off text. So nothing exercised `kronk_text`.

## Proposed fixes (ranked; none applied)

1. **Assign the "basement voice pe ma" device
   (`b91145f1a0afef63089911ffb5ba8b6b`) to the area Basement Theater in HA.**
   Settings → Devices → the device → Area; decline the entity-id rename. No
   restart, no code, no Kronk deploy: this is the documented satellite
   setup step. Effect: the suffix resolves on the exact-area rung in both
   tiers; the blueprint's confirmation says "in the Basement Theater"; the
   M11 parser can read the room; the own-room rung works. Cost: one UI
   change by the operator (an HA config change, so not done here), then
   `./scripts/run_tests.sh` and `python3 scripts/voice_replay.py
   --check-only --tag music` and `--answers` on the basement rows. Check
   one side effect first: the area will hold two media players (the
   ESPHome one and the Music Assistant one), as Kitchen and Office already
   do, so HA's built-in media intents by area behave as they do there.
   Stopping rule: admitted (a data fix, not a rule).
2. **Blueprint `kronk_text`: name the own player first, and the room only
   when it holds a Music Assistant player.** Order: named target → own
   player (`on the <own player>`) → room with an MA player (`in the
   <room>`, using the non-empty `player_entity_id_by_assist_area`) → the
   bare sentence. Fixes the class for any later satellite whose room lacks
   a player, and corrects the comment at `:366-370`. Cost: one marked fork
   change (change 14), `docker cp` and an automation reload (no HA
   restart), plus the cross-tier render test above. Without fix 1, the
   spoken label is then "the basement voice pe ma speaker" (a technical
   name read aloud), so fix 1 is still wanted. Stopping rule: admitted (a
   hand-off bug fix).
3. **tool_service: name the real cause.** When the spoken target is an area
   HA has but no Music Assistant player is in it, say so ("The Basement
   Theater has no Music Assistant speaker") instead of "I don't know a …
   room", which is false. And the own-room rung (`main.py:1421-1425`)
   should not fall through to the default without a word when the origin
   area has no player: today that plays in the kitchen for a basement
   request with no other target. Cost: small code change and tests in
   `tests/test_music_players.py`. Stopping rule: admitted (tenet 7, not
   routing).
4. **Before M11 cutover: the parser and decision on this sentence.** The
   shadow would have played the track in the kitchen: the room did not
   parse (areas come only from players), the "X by Y" artist ran past the
   full stop, and the target fell to the default. At cutover this
   incident turns from a spoken refusal into a wrong-room play. Fix 1
   removes the first cause; the other two are parser work, gated on the
   cutover review with the corpus row below. Cost: parser change and
   tests. Stopping rule: admitted (no routing rule).
5. **Structural: carry the device id across the hand-off.** M11's cutover
   plan already moves the blueprint to `rest_command` → `/music/say` with
   `origin_device`, which retires the target-in-words workaround. It needs
   `rest_command` loaded, so an HA restart named in its own plan step.
   Already planned (`voice-music-control.md`, "Not done"); no new roadmap
   item. This incident is evidence for doing it.

Not proposed: a production Whisper vocabulary prompt for "Grip" (one
mishearing; the M13 decision waits on the M12 clips); passing a
conversation id on the hand-off so "play it" has context (one case, and it
would not have resolved the target).

## Corpus row (to add with the fix commit; not added here)

The transcript as recognised, from the basement satellite. HA's `album`
trigger takes it, so the live replay checks it read-only and passes; the
routing and direct-call layers pass offline (checked: `('home', 'music')`,
no direct call). The parser layer reads `Get a Grit` against the recorded
snapshot, which holds no Aerosmith (`decided_by not_found`), so it carries
`xfail.parser` until the snapshot is re-recorded with this row. It does not
exercise the hand-off text; that is the render test in fix 2.

```json
{"id": "inc-1119-get-a-grit-aerosmith-basement", "text": "Play the album Get a Grit by Aerosmith.", "origin": {"area": "Basement Theater", "device": "basement_pe"}, "expect": {"tier": "ha_local", "ha": {"source": "trigger", "trigger": "album"}, "route": "home", "rule": "music", "tool": "play_music", "kind": "named", "media_type": "album", "name": "Get a Grip", "artist": "Aerosmith", "player": "entity:media_player.home_assistant_voice_0ab117"}, "source": ["docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md"], "tags": ["music", "album", "mishearing", "stt-mangled", "incident", "handoff"], "xfail": {"parser": "STT heard 'Grit' for 'Grip'; the recorded Music Assistant snapshot holds no Aerosmith, so the rules' reading keeps the misheard name (decided_by not_found). Re-record the snapshot with this row to test the resolver's tolerance."}, "note": "The blueprint's album play fails on the misheard name and hands off to Kronk with the satellite's room appended ('in the Basement Theater'), a room with no Music Assistant player: 400 unknown room. The player expectation is the basement Voice PE's own player.", "ref": {"trace": "31dc799b602fad030540a0d372cb3600", "first": "2026-11-19"}}
```

## Blog hooks

- The workaround that writes the device into the sentence: when the API
  between two systems cannot carry "who asked", the words carry it, and
  then the words are wrong.
- Two registry entries for one puck: a satellite is two HA devices, and
  only one of them was in the room.
- A correct error message that names everything except the cause, seen
  three times in test reports before a household member heard it.
