# Investigation 2026-11-07 — Pandora plays the wrong songs; MA UI "failed to connect"

**Status:** playback mitigated 2026-11-08 (blueprint change 11 + station import); seed creation open upstream. UI: no server fault found.

## Symptom (as the operator saw it)

1. Asked for "Backyard Barbecue" from Pandora; Music Assistant played
   "something very incorrect".
2. Opening the Music Assistant web UI in a browser shows "failed to connect"
   repeatedly.

## Timeline

- 2026-11-04 — host rebooted; MA (`kronk/music-assistant:2.11.0b5-kronk.9`)
  up since.
- 2026-11-07 16:00 EST — operator reports both symptoms. Two read-only
  investigations started in parallel (playback path; UI connectivity).
- 2026-11-07 16:00 EST — snapshot of MA players via HA `/api/states`:
  nothing playing. `media_player.home_assistant_voice_0ac919_pe_01` idle,
  last item "Life Unimaginable" — Elicia Bailey — *Season of Harvest*
  (`pandora://track/TR:141863118`). `media_player.home_assistant_voice_0ab117`
  idle on "Crazy" — Aerosmith (`library://track/2054`).
- 2026-11-07 — operator confirms the UI URL: `http://kronk.local:8095`
  from another LAN device.

### Playback (times EST)

- 15:54:42.098 — kitchen Voice PE, sentence " Play Backyard Barbecue Radio on
  Pandora." Caught by the blueprint fast path
  (`automation.kronk_music_assistant_voice_device_first`, v
  `20250404-kronk10`, trace run_id `30f4234ea48c08fcd0f1a08974c4e01a`),
  trigger `radio`, `media_name: "Backyard Barbecue"`. Call:
  `music_assistant.play_media {"media_id": "Backyard Barbecue", "media_type":
  "radio", "entity_id": ["media_player.home_assistant_voice_0ac919_pe_01"]}`.
  The verify step saw the content change and set `play_ok: true`; no Kronk
  fallback.
- 15:54:42.898 — MA log: `[music_assistant.Pandora] Pandora station Backyard
  Barbecue Radio (169528202476145373) is serving seed:TR:210471389`. Logged
  only after `station/createStation`: a new station on the account, seeded
  from a **track**.
- 15:54:43.805 — playing "Just Keep On Living" — Lady Harmony.
- 15:55:22 — Kronk rid `b0f475f8` "Thumbs down." → `rate_music`; tool_service
  recorded `taste feedback artist:lady harmony -0.50 (thumb_down)`.
- 15:55:25 — "Life Unimaginable" — Elicia Bailey. 15:57:50 — paused.

Orchestrator logs show no barbecue request in 24 h; Kronk was not in the play
path.

### UI connectivity

- Server side healthy: container up since 2026-11-04T13:45:22Z, 0 restarts,
  not OOM-killed. 8095 bound on `0.0.0.0` and `[::]`. `GET /` 200 on
  localhost, LAN IP 192.168.1.179, the IPv6 link-local address and
  `http://kronk.local:8095/`. `/info`: `status running`, `base_url
  http://192.168.1.179:8095`, `onboard_done true`. `/ws` answers `101
  Switching Protocols` over IPv4 and IPv6 with `Origin:
  http://kronk.local:8095`; unauthenticated commands get error_code 20, bad
  tokens error_code 23. MA does **not** log failed websocket auth, so a
  browser looping on a stale token leaves no server-side trace.
- `kronk.local` answers mDNS with the real NIC's A and AAAA records only.
- 16:01:03 — first appearance since the 11-04 container start of the
  operator's Firefox web player (`drew's Windows (Firefox)`, aiosendspin
  log); `WebSocket client authenticated as drew` at 16:02–16:03. The
  operator's device then held stable connections to 8095.
- Not verifiable without root or the client: host firewall rules, dmesg, the
  browser's console and storage. "Failed to connect" is not an on-screen
  string in the English frontend, only the console message `Sendspin: Failed
  to connect`.

## Hypotheses

- Playback: Kronk resolver mis-scored a library station — ruled out (request
  never reached Kronk). Blueprint fast path + MA Pandora seed matching —
  confirmed.
- UI: server down / crash-looping — ruled out. base_url or origin mismatch
  after the reboot — ruled out (base_url matches, handshake accepts the
  kronk.local origin). Stale browser state for `kronk.local:8095` (stored
  token / server address / service worker, possibly a tab open across the
  reboot) — most likely, unconfirmed. Hostname/IPv6 path from the client —
  possible; tested only from the host.

## Root cause

**Playback.** Pandora has no station or genre named "Backyard Barbecue". MA's
Pandora provider (`providers/pandora/provider.py` `_match_seed()`, kronk.9)
returns the first catalogue item whose name matches, and treats a track
(`TR`) as a seed as good as a genre/artist/composer (`GE`/`AR`/`CO`). The
match was a song titled "Backyard Barbecue" (inferred: by Lady Harmony — the
seed `TR:210471389` could not be resolved by `item_by_uri`; MA search shows
that title by that artist, and the first track played was hers). MA created a
station from that one song, which plays its artist's neighbourhood (southern
soul / gospel). Same defect as `INVESTIGATION_2026-10-21_pandora_station_match.md`
("WOODSTOCK"), cause found then, fix not built. The Kronk path would not have
saved it either: tool_service tries `_pandora_station()` (MA `item_by_name`)
first and gets the same seed.

Side damage: a junk "Backyard Barbecue Radio" station (169528202476145373) on
the Pandora account, and a `-0.50` taste entry for Lady Harmony earned by a bad
seed, not real taste.

**UI.** Not determined on the server; see hypotheses. Pending the operator's
answer on whether the UI works after 16:01.

## Found in passing

- Every night at 03:30 MA logs ~2,040 websocket auths in about one second
  (2037 on 11-05, 2048 on 11-06, 2051 on 11-07), with a flood of
  `music/item_by_uri: track://TR:... not found on provider pandora`.
  Coincides with the household taste build (`MUSIC_TASTE_BUILD_AT=03:30`,
  `tool_service/taste.py`), which appears to open one MA websocket per
  lookup. Not the cause of either symptom.

## Fix

**Playback (2026-11-08).**
- Operator imported the household's familiar stations into the Kronk Pandora
  account; MA radio sync took the library from 15 to 48 Pandora stations.
  Owned names now resolve to the owned station (read-only `item_by_name`:
  "Backyard Barbecue", "Woodstock", "Hotel Costes" → library stations).
- Measured: MA's exact library step never hit a Pandora station, because the
  blueprint strips "radio" and Pandora names stations "X Radio". The bare name
  falls to a provider search: "Mushroom Jazz Radio" 2 ms vs "Mushroom Jazz"
  1015 ms; "Thievery Corporation Radio" 2 ms vs "Thievery Corporation" 812 ms.
- Blueprint change 11 (`20250404-kronk11`, docker cp + `automation.reload`,
  no HA restart): radio asks MA for `"<name> Radio"`; a radio play MA rejects
  goes to Kronk. Previous install kept for rollback. Live tests via
  `conversation/process`, on the basement Voice PE (office Satellite1 was
  unavailable — HA silently skips service calls to an unavailable player, so
  MA never saw those first two attempts):

  | Utterance | Result |
  |---|---|
  | Play Mushroom Jazz radio … | 0.9 s, `Mushroom Jazz Radio`, Pandora track playing |
  | Play zxqv florbent radio … | 4.8 s, MA rejected → Kronk: "couldn't find a station called zxqv florbent"; best 0.37, nothing created |
  | Play ninety's two thousands and today's hits radio … | 8.7 s, MA rejected → Kronk; the home agent passed `query="ninety's two thousands"`, no media type; nothing played (0.69 below threshold) |
  | Play 90s, 2000s and today's hits radio … | 7.0 s, MA rejected → Kronk scored 0.91 → `90s, 2000s, Today's Hits Radio` played |

- Not fixed: a name the account doesn't have can still match a song in the
  Pandora catalogue and create a station from it. Operator directive
  2026-11-08: no new local MA code; the provider fix goes upstream.
- After the 2026-11-08 sync, "Backyard Barbecue Radio" in the library is the
  operator's imported station (pandora 163116950174576396); the junk station
  created 2026-11-07 (169528202476145373) is no longer in the library.
  Cleanup pending the operator: "WOODSTOCK Radio" (164125202337246992, still in
  the library beside "Sounds of Woodstock Radio") and the
  `artist:lady harmony -0.50` taste entry.

**UI.** No server change.

## Found in passing

- The home agent can truncate a request when it builds the `play_music`
  call: "Play ninety's two thousands and today's hits radio …" became
  `query="ninety's two thousands"` with no media type. The resolver's
  threshold kept it from playing the wrong thing.

## play_music argument bench (2026-11-08)

Ten runs of "Play ninety's two thousands and today's hits radio on the
basement voice pe ma" through `/voice/api/chat`, before and after a
`play_music` description change (stations listed in `query`; "pass the whole
name, never shorten"; "write numbers as digits", with the test's own words as
the example; `media_type` 'radio' whenever the user said radio/station).

| | pre | post |
|---|---|---|
| tool called | 8/10 | 9/10 |
| `media_type='radio'` | 4/8 | 8/9 |
| full name passed | 5/8 | 6/9 |
| numbers as digits | 1/8 ('90s and 2000s hits') | 0/9 full, 1/9 partial ('90s two thousands') |
| intended station played | 0/10 | 1/10 (query '90s', by luck) |

With the full name in words and `media_type='radio'`, the resolver scores
"90s, 2000s, Today's Hits Radio" at 0.51–0.63, below the 0.75 bar; with digits
it scored 0.91 (test 3b). Side effect of the post run: query "today's hits
radio" resolved through MA `item_by_name` to a catalogue genre seed and
**created a Pandora station**, "Today's Hits Radio" (190844434403829610, seed
`GE:5`), at 15:34:05 EST; run 7 then played it as "your Pandora station".
Deleted on the operator's go (library remove with `allow_station_delete` on;
absent after a fresh sync).

Then, one change per run of the same bench:

1. MA Pandora `offer_new_stations` → off (operator, via
   `config/providers/save`; provider reload, no MA restart). Uncached search
   "Bluegrass" offers no seed. No request path creates a station now; new
   stations come from the Pandora app + sync.
2. tool_service scores a digit form of the query alongside the query as said
   (`music_resolve.numerals`): right station 5/10, but 4/10 wrong successes —
   the model omitted `media_type`, and "90s and 2000s" matched YouTube albums
   and playlists at 0.94.
3. The orchestrator forwards the user's sentence (`origin.said`) with every
   `play_music` call; when it names a station ("… X radio [on/in …]",
   "… the radio station X …") tool_service forces `media_type=radio`, looks
   the station up by the name cut from the sentence, and scores that name too:
   6/10 — the override skipped requests the model had marked `kind=genre`
   (an album "NINETY's", a 90s playlist), and 2 runs never called the tool.
4. The station sentence overrides `kind=genre` as well: **10/10** played
   "90s, 2000s, Today's Hits Radio" (scores 0.91–1.03), every run called the
   tool, no station created.

The model declining to call the tool ("can't play two stations at once") was
2/10 in run 3 and 0/10 in run 4; it is model variance, not fixed by any of
these.

## What would have caught it sooner

- The blueprint's success check proves only that something new is playing,
  not that it is what was asked for; a station created from a seed passes it.
  Logging the resolved MA item next to the spoken name would make a wrong
  success visible in the trace.
