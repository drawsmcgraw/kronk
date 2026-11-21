# M22 build report: the basement hand-off (blueprint change 14, truthful target errors, corpus row)

Branch `refactor`. Criteria: `docs/sprint/M22-criteria.md`. Source:
`docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md`, fixes 2 and
3 and the corpus row. Built 2026-11-21 by the build agent. Build commit
`7d18c68`, first report `aa94a12`. Fix round 0 (the hand-off order, at the
coordinator's request) is the commit before this revision of the report,
which is the last commit.

## Summary

- Blueprint fork change 14 (`20250404-kronk14`): the hand-off text names the
  spoken target; else the satellite's room when the room's MA players are
  exactly its own player; else the own player; else, with no own player,
  the room when it holds an MA player; else the bare sentence. Repo only. Nothing
  was installed in HA and nothing was reloaded. The install steps are below.
- tool_service: the players template also returns every HA area, in the
  same call. A spoken room that HA has but that holds no MA player now gets
  "The Bedroom has no Music Assistant speaker. Rooms with a speaker: …".
  When the origin room holds no player, the request still plays on the
  default, and the label now says why. No request changes target.
- Corpus row `inc-1119-get-a-grit-aerosmith-basement` is in, with no
  `xfail`. The snapshot was re-recorded for that row only: 6 new reads,
  none changed. The live pin gained exactly that row.
- Registry check `scripts/satellite_area_check.py` (reads only). On the real
  registry today: 3 satellites, 0 not ok.
- Tests: before `5683 passed, 3 skipped, 53 xfailed`; after fix round 0
  `5734 passed, 3 skipped, 53 xfailed, 6 warnings in 81.23s`.
- Production untouched. Only the dev pair was rebuilt, twice, with the
  sanctioned command.

## Criteria

### 1. `kronk_text` render test

**Change.** `ha/blueprints/mass_assist_kronk.yaml`:
- header entry 14: `:47-53`
- version `20250404-kronk14`: `:313`
- comment above the template: `:373-398`
- template: `:401-409`

The template now reads:

```jinja
{%- set own = player_entity_id_by_assist_device | first | default(none) -%}
{%- set room = area_name(assist_device_id) if assist_device_id else none -%}
{%- set in_room = (area_entities(area_id(assist_device_id)) | select('in', ma_players) | list) if room else [] -%}
{%- if area_or_player_name -%}{{ trigger.sentence }}
{%- elif own and in_room == [own] -%}{{ trigger.sentence }} in the {{ room }}
{%- elif own -%}{{ trigger.sentence }} on the {{ state_attr(own, 'friendly_name') }}
{%- elif room and player_entity_id_by_assist_area -%}{{ trigger.sentence }} in the {{ room }}
{%- else -%}{{ trigger.sentence }}{%- endif -%}
```

The order:
1. the target the speaker named: the sentence as spoken;
2. `in the <room>`, only when the room's MA players are exactly the own
   player (one player, and it is the own one);
3. else `on the <own player's friendly name>`;
4. else `in the <room>` when there is no own player and
   `player_entity_id_by_assist_area` is not empty;
5. else the bare sentence (Kronk's default).

**Fix round 0.** The first version of change 14 (`7d18c68`) put the own
player before the room. That fixed the basement but changed the spoken
label of every kitchen and office hand-off from "the Kitchen speaker" to
"the kitchen voice pe ma speaker", every day. The coordinator asked for the
order above. Rung 2 keeps the room wording wherever the room wording plays
on the own player, and rung 3 names the player only where the room would
not.

**Rung 2's test.** `player_entity_id_by_assist_area` is the room's MA
players with a sync group preferred (`gs or ps`, `:360-363`). Comparing it
with `[own]` would give the same answer today, because the own player is
never a group: own plus a group reads `[group]`, own plus another reads
`[own, other]`, and neither equals `[own]`. Rung 2 reads the raw list
anyway (`in_room`), so the test is the rule as stated and does not depend
on the group preference.

**Test.** `tests/test_blueprint_handoff.py`. It loads the blueprint file with
PyYAML (`!input` is kept as a marker). It renders every `variables:` value in
file order up to `kronk_text`, through `tests/ha_template_env.py`. That
module is Jinja2 3.1.6 in HA's sandboxed environment, plus the HA functions
the blueprint uses, answered from a fake registry. Each value is read back
the way HA reads variables: stripped, then parsed as a literal when it is a
list, dict or bool.

The test then does what Kronk does with the text. The words after
"on the" / "in the" become play_music's `player`; the bare sentence passes
nothing, and a named target passes the named words. The player list comes
from tool_service's own `_players_template`, rendered over the same
registry. Then `resolve_players` runs with no origin, because the hand-off
carries none. The registry builder takes two options for the new shapes:
`kitchen_other` (a second MA player, not a group, in the Kitchen) and
`office_group` (an MA sync group in the Office).

| Shape | Rung | Hand-off suffix | Kronk plays on = blueprint's own target |
|---|---|---|---|
| own player alone in its room (kitchen) | 2 | ` in the Kitchen` | kitchen voice pe ma |
| own player alone in its room (office satellite 1) | 2 | ` in the Office` | satellite 1 ma |
| own player alone in its room (basement after fix 1) | 2 | ` in the Basement Theater` | basement voice pe ma |
| own player, room without it (basement before fix 1) | 3 | ` on the basement voice pe ma` | basement voice pe ma |
| own player plus another MA player in the room (kitchen) | 3 | ` on the kitchen voice pe ma` | kitchen voice pe ma |
| own player plus a sync group in the room (office satellite 1) | 3 | ` on the satellite 1 ma` | satellite 1 ma |
| own player in another room (basement player in the Kitchen) | 3 | ` on the basement voice pe ma` | basement voice pe ma |
| no own player, room with a player (second Office satellite) | 4 | ` in the Office` | satellite 1 ma |
| no own player, room with a sync group (second Office satellite) | 4 | ` in the Office` | Office Sync (the group) |
| no own player, room without one (Garage satellite) | 5 | none (bare) | default (kitchen) |
| named target ("… in the office", from the kitchen) | 1 | none (the sentence) | satellite 1 ma |

`test_handoff_target_is_the_blueprints_own_target` asserts, for every
shape, that Kronk's target equals the blueprint's `mass_player_entity_id`.
That is the cross-tier test the incident asked for. 25 tests in the file
(11 shapes, twice, plus 3).

**On older templates.** I copied the tree to scratch with only the
blueprint swapped and ran the new test file:
- the first version of change 14 (`aa94a12`): 3 failed, 22 passed. The
  three rung-2 shapes fail on the text (` on the …` where ` in the …` is
  wanted); the targets agree.
- the kronk13 blueprint (`f381a2d`): 12 failed, 13 passed. Besides the
  basement 400 from the incident (`… in the Basement Theater`), it misses
  two new shapes: with own plus another in the Kitchen it wrote
  ` in the Kitchen`, which Kronk plays on both players; with own plus a
  sync group in the Office it wrote ` in the Office`, which Kronk plays on
  the group. In both cases the blueprint itself targeted the own player.

With the new template all 25 pass.

### 2. Registry check

`scripts/satellite_area_check.py`. One POST to HA's `/api/template` renders
`SATELLITE_TEMPLATE` (`:44`). For each `assist_satellite` the template gives:
- its device and area;
- its MAC, and the MA player(s) whose device identifier is `up` + MAC (the
  blueprint's join);
- each such player's area, per entity, which is how both tiers read it.

`classify` (`:68`) returns `ok`, `mismatch`, `no_area` or `no_player`. Exit
codes: 0 all ok, 1 any not ok, 2 HA unreadable. The token is read from the
environment or `.env` into a variable and never printed.

Tests: `tests/test_satellite_area_check.py` renders the same template over
the fake registry. The basement before fix 1 is `mismatch`, after fix 1 it
is `ok`, a satellite without a player is `no_player`, a player in another
room is `mismatch`, and a satellite in no area is `no_area`.

**Result on the real registry, 2026-11-21:**

```
ok         assist_satellite.home_assistant_voice_0ab117_assist_satellite  device='basement voice pe' area=Basement Theater  MA player: basement voice pe ma (media_player.home_assistant_voice_0ab117) area=Basement Theater
ok         assist_satellite.home_assistant_voice_0ac919_assist_satellite  device='kitchen voice pe' area=Kitchen  MA player: kitchen voice pe ma (media_player.home_assistant_voice_0ac919_pe_01) area=Kitchen
ok         assist_satellite.satellite1_d8d1bc_assist_satellite  device='satellite 1' area=Office  MA player: satellite 1 ma (media_player.satellite1_d8d1bc) area=Office
3 satellites, 0 not ok
```

exit 0. Fix 1 holds.

### 3. `resolve_players`

**Change.** `tool_service/main.py`:

| Line | Change |
|---|---|
| `:1286` | The template returns `'areas': areas() \| map('area_name') \| list`. Same call; nothing else is read from HA. |
| `:1335` | New `parse_player_payload_areas` returns `(players, origin_key, areas)`. `parse_player_payload` keeps its 2-tuple for the scripts that use it. |
| `:1505` | `_fetch_players` returns the areas. Its 5 callers pass them as `ha_areas=`: `:1579`, `:2370`, `:2678`, `:2833`, `:2931`. |
| `:1370` | `_no_player_room`: the HA area the spoken key names, exact match or else a single substring hit (like rungs 2 and 4), limited to areas with no MA player. |
| `:1466` | 2(a): refuse with "The <room> has no Music Assistant speaker. Rooms with a speaker: …". |
| `:1487`, `:1502` | 2(b): the own-room fallback appends `_NO_SPEAKER_NOTE` (`:1388`) to the default's label. With no default configured, the 400 names the room (`:1491`). |
| `:1392` | `_unavailable_sentence` keeps that note in the "is unavailable" sentences (`:1587`, `:2375`, `:2681`). |

`ha_areas` is only read after rungs 1 to 4 have all missed (2a), or after
rung 6 has found nothing (2b). It cannot change which players a request
picks.

**Tests that fail on the old code** (`tests/test_music_players.py`):
- 2(a): `test_area_without_a_player_says_so` (`:141`), `…_matches_like_the_area_rungs` (`:150`), `test_route_area_without_a_player_is_refused_with_the_cause` (`:385`)
- 2(b): `test_origin_area_without_a_player_falls_to_default` (`:256`, see below), `test_own_room_without_a_player_plays_the_default_and_says_why` (`:191`), `…_and_no_default_names_the_room` (`:206`), `test_route_own_room_without_a_player_names_the_cause_in_the_reply` (`:394`), `test_route_unavailable_default_for_a_room_without_a_player_keeps_the_cause` (`:403`)
- the template: `test_players_template_asks_for_every_area_in_the_same_call` (`:214`)

Run on a `git archive HEAD` with the new test file: 15 of these fail. Most
fail on the missing `ha_areas` parameter. `test_origin_area_without_a_player_falls_to_default`
runs without it and fails on the label alone.

**Unchanged resolution.**
- `test_ha_areas_never_change_a_target` (`:181`) runs 10 requests with and
  without the area list: same players every time.
- The live-path pin (`tests/test_music_live_pin.py`) passes for all 98
  pre-existing rows, and the regenerated pin differs only by the new row.
- The shadow and corpus layers pass.
- The "two tiers must agree" tests pass unchanged.
- On the real player list, "living room" still resolves to the Living Room
  Sony Bravia by substring name, and "basement theater" to the basement
  player.

**One existing test changed.** `test_origin_area_without_a_player_falls_to_default`
asserted the old label `"the Kitchen speaker"`. Silence on that rung is what
criterion 2(b) removes. The target assertion (`[KITCHEN]`) stays; the label
assertion is now `"the Kitchen speaker (no Music Assistant speaker in the Bedroom)"`.

### The own-room rung: say so and play on the default (not refuse)

**Chosen:** play on the default, and say why inside the label. Reasons:

1. "Requests that resolve today resolve the same way." A refusal would turn
   a request that plays today into one that does not.
2. The blueprint tier does the same in the same case. Its own-room rung
   finds nothing and it falls to its default input. Its confirmation names
   the room it played in ("playing in the Kitchen"), not the room that
   asked. Both tiers now play on the default, and Kronk's reply also says
   why.
3. The cost is a play in another room. It is now announced, not silent.
   Refusing would be the stricter choice; it is a one-line change in
   `resolve_players` if Drew prefers it.

**Why the label, not a separate field.** The label is the one value every
reply already speaks: play, dry run, control, now playing, "did not start",
and via `_unavailable_sentence` "is unavailable". So the note reaches the
user without touching the orchestrator. A parenthesis reads as one sentence
wherever the label sits:
- "Now playing X on the Kitchen speaker (no Music Assistant speaker in the Bedroom)."
- "… did not start on the Kitchen speaker (…) within 10s …"

The tests and `docs/features/voice-music-control.md` state the same choice.

### 4. Messages

Every failure names the most specific cause. On the real player list today:

| Case | Before | After |
|---|---|---|
| `player: "Bedroom"` (an HA area with no MA player) | 261 chars: "I don't know a speaker or room called 'Bedroom'. Speakers: Cameron’s MacBook Pro, Living Room Sony Bravia, SHIELD, Sonos Move Derp, Upper Shield, basement voice pe ma, kitchen voice pe ma, satellite 1 ma. Rooms with a speaker: Basement Theater, Kitchen, Office." | 100 chars: "The Bedroom has no Music Assistant speaker. Rooms with a speaker: Basement Theater, Kitchen, Office." |
| `origin_area: "Bedroom"`, nothing named | label "the Kitchen speaker" | label "the Kitchen speaker (no Music Assistant speaker in the Bedroom)" |
| same, default unavailable | "kitchen voice pe ma is unavailable — it may be powered off or asleep." | "… is unavailable (no Music Assistant speaker in the Bedroom) — it may be powered off or asleep." |
| a name HA does not know at all ("garage" in production) | unchanged | unchanged |

The new refusal lists rooms only, never more than the old sentence
(`test_area_without_a_player_message_is_no_longer_than_the_old_one`). A
substring that hits two bare rooms ("gar": Garage, Garden) keeps the old
sentence; it makes no claim about either room.

### 5. The corpus row

Added as the last line of `tests/voice_corpus.jsonl` (`:161`): the row from
the incident document with `xfail` removed and the note brought up to date.

The Music Assistant snapshot was re-recorded first, for this row only:
`tests/.venv/bin/python scripts/music_snapshot_record.py --path both --id inc-1119-get-a-grit-aerosmith-basement`.
I read the header first and checked that it is reads only:
- MA commands outside `READ_COMMANDS` are refused by the recorder.
- `offline_env` refuses every HA service call and forces `MUSIC_DRY_RUN`.
- HA was not contacted, because the snapshot already holds the players.
- The taste genres and taste file came from the snapshot, not `./data`.

Before the run, nothing was playing (17 media players read, none active).
Output: `1 rows, 6 reads asked, 8 reused, 346 in snapshot, 3 s`.

**What changed in `tests/fixtures/music_snapshot.json`:**
- Content: 6 keys added under `ma`, no existing key changed. `players`,
  `genres` and `taste` are identical. `meta.recorded` went from
  `2026-11-17T12:19:19Z` to `2026-11-21T11:52:08Z`.
- Text: 1371 lines added, 1 changed (`git diff --diff-algorithm=histogram`).
  Git's default algorithm shows 6014/4644 for the same change, because the
  `indent=0` format repeats `{`/`}` lines. The file format is byte-identical
  to a re-dump of the old file.
- The 6 reads:
  - `music/albums/library_items` "Get a Grit" (0 items)
  - `music/search` album "Get a Grip by Aerosmith" (8)
  - `music/search` album "Get a Grip" (10)
  - `music/search` album "Get a Grit by Aerosmith" (0)
  - `music/search` album "Get a Grit" (10)
  - `music/search` album "Aerosmith", limit 50 (50)

**Pin.** `KRONK_WRITE_MUSIC_PIN=1 ./scripts/run_tests.sh tests/test_music_live_pin.py -k test_write_pin`
changed one thing: the new row (+54 lines), dry run of `library://album/247`
(Get A Grip by Aerosmith) on `media_player.home_assistant_voice_0ab117`, no
snapshot misses. The other 98 rows are byte-identical.

**What the row checks now:**
- routing: `home/music`, no direct call;
- the M11 parser and decision on the snapshot: `named`, album, name Get A
  Grip (folded), artist Aerosmith, player = the basement player through the
  own-device rung. It passes, so the `xfail.parser` the incident wrote
  ("snapshot holds no Aerosmith") was dropped: re-recorded, it no longer
  fails;
- the live `/music` pin;
- in the live replay, the `album` trigger, read-only.

**What it cannot check:** the hand-off text. The replay never executes
HA-local rows, and the blueprint always hands off to production. That is
criterion 1's test.

### 6. Nothing installed: steps for the coordinator

The installed file today is byte-identical to the pre-M22 repo file
(sha256 `b629388914d87e8d…`, `version: 20250404-kronk13`, owner 1000:1000,
mode 664, read with `docker exec … cat`). **No HA restart is needed.** HA
reads blueprints when automations load, so an automation reload picks the
new file up. That is how changes 8 to 13 were installed. Pick a moment when
no puck is mid-request: the reload re-creates every automation, and the
music automation is `mode: single`.

```bash
cd ~/git-repos/drawsmcgraw/kronk
B=/config/blueprints/automation/kronk/mass_assist_kronk.yaml
# 0. back up what is installed (expect version 20250404-kronk13)
docker cp homeassistant:$B /tmp/mass_assist_kronk.kronk13.yaml
grep -n 'version: 20250404-kronk' /tmp/mass_assist_kronk.kronk13.yaml
# 1. install (-a keeps owner 1000:1000, as the current file is)
docker cp -a ha/blueprints/mass_assist_kronk.yaml homeassistant:$B
# 2. reload automations (no restart)
HA_TOKEN=$(grep '^HA_TOKEN=' .env | cut -d= -f2-)
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services/automation/reload
# 3. read back
docker exec homeassistant grep -n 'version: 20250404-kronk' $B         # expect :312 ... kronk14
docker exec homeassistant cat $B | sha256sum; sha256sum < ha/blueprints/mass_assist_kronk.yaml   # equal
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://localhost:8123/api/states/automation.kronk_music_assistant_voice_device_first | jq -r .state   # expect "on"
docker logs --since 5m homeassistant 2>&1 | grep -i 'blueprint\|kronk_mass_voice' || echo "no blueprint errors"
```

The new version also shows as `version` in the `variables` of the
automation's next trace (Settings → Automations → the automation → Traces).

**Undo:** copy the backup back and reload:

```bash
docker cp -a /tmp/mass_assist_kronk.kronk13.yaml homeassistant:$B
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services/automation/reload
```

The same file is `git show f381a2d:ha/blueprints/mass_assist_kronk.yaml`.

Fix 3 (tool_service) reaches production only through the next tagged
deploy. It works with either blueprint version.

### 7. Docs

- `docs/features/voice-music-control.md`:
  - new "The hand-off to Kronk (changes 9 and 14)" paragraph: the five-rung
    order, why it changed, which satellites keep the room wording, and the
    render test;
  - "How it works (tier 2)" step 2: the two new messages and the choice for
    the own-room rung;
  - "The two tiers must agree": the render test;
  - the M11 target line: the basement player's area since 2026-11-20;
  - "Adding a satellite": the registry check and today's result.
- `docs/features/music-taste.md`: it stated the old order (room first); now
  the new one.
- `docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md`: status
  header, dated lines for fixes 2 and 3, and the corpus row section.
- No other doc states the old order. I grepped the docs for the template,
  "satellite's room" and "room appended". The M7 and M19 build reports
  mention change 9 as history; I left them.

### 8. Tests and dev

`./scripts/run_tests.sh`: before `5683 passed, 3 skipped, 53 xfailed`; after
the build `5724 passed, 3 skipped, 53 xfailed, 6 warnings in 81.49s
(0:01:21)`; after fix round 0 `5734 passed, 3 skipped, 53 xfailed, 6 warnings
in 81.23s (0:01:21)`. 51 new tests:
- 14 in `test_music_players.py`
- 25 in `test_blueprint_handoff.py` (15 at the build; fix round 0 added 5
  shapes, each in both parametrized tests)
- 6 in `test_satellite_area_check.py`
- 6 parametrizations of the new row: 4 in `test_voice_corpus.py`, 1 in
  `test_music_live_pin.py`, 1 in `test_music_shadow.py`

The xfail count is unchanged: the new row carries none.

**New test dependency:** `Jinja2==3.1.6` and `MarkupSafe==3.0.3` in
`tests/requirements.txt`, the versions homeassistant 2026.5.4 runs (read with
`docker exec homeassistant python3 -c …`). Installed into `tests/.venv` with
`uv pip install` from uv's local cache (21 ms; no download shown). A fresh
test venv needs them.

**Dev.** The dev pair was rebuilt twice with
`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`:
once for fix 3, once for `_unavailable_sentence`. Before any request each
time:
- `docker exec kronk-tool_service-dev-1 printenv MUSIC_DRY_RUN` gave `1`;
- the startup log said `WARNING:tool_service:MUSIC_DRY_RUN is on: music requests resolve but nothing is sent to a player or MA`;
- the new code was in `/app/main.py`;
- an HA state read showed 17 media players, none playing, buffering or
  paused (also checked after).

Requests went to `127.0.0.1:8103/music`, text only:

```
> POST /music {"query":"Aerosmith","media_type":"artist","player":"Bedroom"}
{"detail":"The Bedroom has no Music Assistant speaker. Rooms with a speaker: Basement Theater, Kitchen, Office."}  [HTTP 400]
> POST /music {"query":"Aerosmith","media_type":"artist","origin_area":"Bedroom"}
{"detail":"satellite 1 ma is unavailable (no Music Assistant speaker in the Bedroom) — it may be powered off or asleep."}  [HTTP 503]
```

Dev's default (`satellite 1 ma`) was unavailable, so the own-room fallback
could only show its unavailable form on dev. The first build's answer to the
same request was `satellite 1 ma is unavailable — it may be powered off or
asleep.`, with no cause; that is why `_unavailable_sentence` was added. The
200 form ("… on the Kitchen speaker (no Music Assistant speaker in the
Basement Theater)") is shown by
`test_route_own_room_without_a_player_names_the_cause_in_the_reply`.

Two more requests on the first build:
- `player: "Basement Theater"` gave a dry run on the basement player ("the
  Basement Theater speaker");
- `player: "garage"` gave the old unknown-name sentence. Garage is not an HA
  area in production.

### 9. Production unchanged

Containers before and after (name, image, container id):
- Every production container has the same id and image as before.
- Only `kronk-tool_service-dev-1` (87696935e83a → 71509fa06f84) and
  `kronk-orchestrator-dev-1` (84da57f14fcb → ce8d0be88033) were recreated.
- `homeassistant` stayed 98f56e2549d0 ("Up 3 days") and
  `music-assistant-server` stayed cf6b980f6630 ("Up 2 weeks").

Units: `systemctl --user list-units 'llama-*' 'wyoming-*' 'kronk-*' --all`
is identical before and after. The hot tub monitor is active. Timers: the
same 14, none started by me.

**What I did against HA, all reads:**
- 3 `/api/template` renders: the satellite check, the players template
  (twice);
- `/api/states` reads: player state, and the automation state;
- `docker exec`/`docker cp` reads of the installed blueprint and of HA's
  Jinja2 version.

**What I did not do:**
- no HA or MA write, reload or restart;
- no pipeline run;
- no sound, no notification;
- no request to production's chat or music routes;
- no backup or upgrade script, no `pipeline_bench.sh`, no `deploy_tag.sh`.

## Not done, and why

- **Install of change 14 in HA:** the coordinator's step (criterion 6);
  steps above.
- **Production deploy of fix 3:** not in scope; next tagged deploy.
- **Live replay (`scripts/voice_replay.py`):** not run. `--check-only`
  sends each row through HA's conversation debug, and the full replay runs
  `assist_pipeline/run` on dev rows. This item allows no HA pipeline run of
  any stage, so I left the replay for the e2e agent or the coordinator to
  decide. `--answers` (dev `/music/say` and `/music`, dry run) would be
  allowed; I skipped it to keep dev traffic to the requests criterion 8
  asks for.
- **ROADMAP:** the M22 line stays where it is until change 14 is installed.
  The roadmap was being edited in this tree by another session during the
  build (committed as `eb68da3`), and I did not touch it.
- **Spoken label after change 14:** fixed in fix round 0. The three real
  satellites, after fix 1, hand off `in the <room>` as before, so Kronk's
  reply still says "the Kitchen speaker". A satellite whose room lacks its
  own player, or holds other players or a sync group with it, hands off
  `on the <player>`, and the reply names the player ("the kitchen voice pe
  ma speaker"). No real satellite is in that state today (registry check:
  3 of 3 `ok`). Fix 5 (the device id across the hand-off) removes the need
  for any suffix.
- **Fix 4 (the M11 parser's rooms come only from players):** out of scope.
  The parser does not use `ha_areas`.
- **Blueprint header:** it still says "Three changes, everything else
  verbatim" above a list of 14. Pre-existing, left as is.

## Scratch

`/tmp/kronk-m22-build` (0700) held:
- before/after state lists and test logs;
- the HEAD blueprint, a `git archive HEAD` tree and a copy of the tree with
  the old blueprint (for the old-code runs);
- two read-only HA helper scripts, which read `HA_TOKEN` from `.env` into a
  variable and never write it out.

No symlinks. Removed at the end with `rm -rf /tmp/kronk-m22-build`.
