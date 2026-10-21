# Investigation 2026-10-19 — YouTube Music album requests fail to resolve

**Status:** root-caused; no code changed yet (fix options below, operator call).

## Symptom

Operator asked Kronk (chat UI) for a specific album on YouTube Music three
times in two minutes; every attempt failed. A similar chain on 2026-10-17
(Hotel Costes) also ended without playback.

## Timeline (orchestrator `kronk.events` + tool_service log)

All rows below except the first came from the chat UI (`POST /message`,
Firefox on Android, per nginx). The first was spoken to the Voice PE and
handled entirely by the HA blueprint; Kronk never saw it.

| Time (UTC) | rid | Utterance | Route | What reached MA | Result |
|---|---|---|---|---|---|
| 10-19 11:25:58 | — (HA) | spoken to Voice PE | HA blueprint `kronk_music_assistant_voice_device_first` (local tier) | `'Destiny Waving by Ulrich Schnauss'` | `Error for call_service at pos 3: Could not resolve ['Destiny Waving by Ulrich Schnauss'] to playable media item`; no fallback to Kronk |
| 10-19 11:26:53 | f2ab9f5c | "Play the album Destiny Waving by Ulrich Schnauss" | music shortcut → home | `query='Destiny Waving by Ulrich Schnauss' media_type='album'` | 502 — `Could not resolve ['Destiny Waving by Ulrich Schnauss'] to playable media item` |
| 10-19 11:27:50 | f9cd7d4b | "It's this album on YouTube music https://music.youtube.com/playlist?list=OLAK5uy…" | url shortcut → research | nothing — `fetch_url` on the link, then a text answer | no play attempt |
| 10-19 11:28:35 | 8fd863bb | "Play the album Destiny Waving by Ulrich Schnauss from YouTube music, in the kitchen" | music shortcut → home | `query='Destiny Waving by Ulrich Schnauss on YouTube Music' media_type='album' player='kitchen'` | 502 — `Could not resolve [...]` |
| 10-17 23:09:22 | 7123027c | "Play hotel costes albums from Atlas, on the kitchen" | music shortcut → home | (query not logged at this line; 502) | 502 |
| 10-17 23:10:21 | 31b054fb | "Pay the album "hotel costes 3" from Atlas, on the kitchen" | direct → coordinator → ask_home | `query='Hotel Costes 3 from Atlas' media_type='album' player='Kitchen'` | 502 — `Could not resolve ['Hotel Costes 3 from Atlas']` |
| 10-17 23:11:03 | 2e7f51e1 | "Try playing it from YouTube music, still on the kitchen" | direct (rule default) | nothing — coordinator synthesis only, no tool call | no play attempt |

MA's YouTube Music provider was loaded and healthy throughout
(`Loaded music provider YouTube Music`, 07:01:21). No ytmusic errors in the
MA log for the window.

## Root causes

Verified with read-only `music/search` (media_types=album) calls against MA
over its websocket from inside tool_service:

1. **Misspelled title.** The album is **"Destiny Waiving"** (Ulrich Schnauss
   & Mark Peters, 2021, `ytmusic--kGmHoQDn://album/MPREb_FZkLnRytAxt`).
   `'Destiny Waving'` returns five unrelated albums and nothing by Schnauss;
   `'Destiny Waving by Ulrich Schnauss'` returns zero. `'Destiny Waiving by
   Ulrich Schnauss'` returns the album first. MA's search is a passthrough
   to YTM with no spelling tolerance once the artist words are added.
   The "by <artist>" phrasing itself is fine.
2. **Provider hint leaks into the query.** The home agent appended the
   source to the query: `'Destiny Waiving by Ulrich Schnauss on YouTube
   Music'` → **zero results**, even with the correct spelling.
   `'Hotel Costes 3 from Atlas'` → zero; `'Hotel Costes 3'` → three hits.
   The prompt only tells the model what to do with "on Pandora"; YouTube
   Music and Atlas/NAS hints have no rule, so the model folds them into the
   query. Nothing in code strips them.
3. **A pasted YTM link has no play path.** The `url` route shortcut sends
   any message with a URL to the research agent, which fetches the page and
   chats about it. MA could have played it directly: `music/item_by_uri`
   on `ytmusic--kGmHoQDn://playlist/OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU`
   resolves to "Destiny Waiving".
4. **Follow-up re-requests skip the music route** (10-17 23:11, 2e7f51e1).
   "Try playing it from YouTube music…" doesn't match `_MUSIC_RE` (no
   "play"/"start" at the start — "try playing" is not covered) and the
   coordinator answered with no tool call. Same class as the 2026-10-05
   music-pin misses.

5. **Voice tier: STT cannot spell this title, and the blueprint has no
   fallback.** "Waiving" and "Waving" are homophones, so voice will always
   produce the miss in cause 1. A blueprint `play_media` failure ends the
   turn instead of escalating to Kronk. Fix (a) does not apply here (the
   blueprint does not append provider hints); only a fuzzy
   search-then-play — in the blueprint or by handing misses to Kronk —
   helps voice.

## Secondary observations

- Blueprint step 5 fails recurrently: `If at step 5: Error executing script.
  Error for call_service at pos 1: Invalid or unsupported command.` (HA log
  10-16 16:01, 10-17 07:44 / 08:22 / 13:59, 10-19 07:29 local). The 10-19
  instance coincides with MA's `player_queues/shuffle: Cannot change
  shuffle while the queue is in dynamic mode` at the same second — the
  blueprint sets shuffle on a radio (dynamic) queue. Same requests log
  `Template variable warning: 'media_name' is undefined` from the
  `'radio' in media_name` template.

- Local-files scan errors for Hotel Costes CUE sheets (`Audio file not found
  for CUE sheet: Hotel Costes/hotelcostes3-etage/...`) — the NAS copy of
  that album would not have resolved either. Separate issue.
- bgutil PO-token plugin/server version skew warning (plugin 2.0.2, HTTP
  server 2.0.0) on every YTM stream. Not implicated here; nothing failed to
  stream.

## Fix options (not built)

- **(a) Strip provider hints in code** (tool_service `/music` or
  `_tool_play_music`): remove trailing `on|from|via` + `youtube( music)?|
  ytmusic|pandora|spotify|atlas|the nas|my library`. Optionally pass the
  hint through as a provider filter. Structural, tenet 5.
- **(b) Search-then-play fallback on "Could not resolve"**: the ROADMAP
  "media_type miss" item already specifies the safe shape — run search,
  play only a hit whose name shares the query's words. Fuzzy word overlap
  (`waving`≈`waiving`) would need an edit-distance tolerance; keep the
  gibberish-stays-a-failure guarantee.
- **(c) Music URLs → play_music**: a `music.youtube.com` / `pandora.com`
  link alongside a play verb (or alone) routes to home and plays by URI.
- **(d) Widen `_MUSIC_RE`** for "try playing …" / "play it from …".

## What would have caught it sooner

A failed-play log line carrying the rid, and a pipeline_bench prompt that
names a provider ("play X from YouTube Music") — the suffix bug reproduces
on every such request.
