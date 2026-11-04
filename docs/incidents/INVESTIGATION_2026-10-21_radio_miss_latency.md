# Investigation 2026-10-21 — a radio that does not exist takes ~10 s to fail

Status: **fixed 2026-10-21** (both voice tiers). One side finding left open:
the first artist play after an MA restart (see the end).

## Symptom

During the Pandora create-station live tests (kronk.8, 2026-10-21 08:24),
"zxqv radio" through tool_service `/music` (`media_type=radio`, office
speaker) took **9.8 s** to return 502 "Playback failed: Could not resolve zxqv
radio to playable media item". The same path plays an existing or new Pandora
station in 1.1–2.7 s. Voice budget is ~2 s on the fast tier (tenet 12); a
miss should fail at least as fast as a hit.

## Timeline

- 08:24:4x — the 9.8 s miss (tool_service log: one `music_assistant.play_media`
  call, HA returned `home_assistant_error` "Could not resolve ['zxqv radio']
  to playable media item").
- MA log over the hour: no "did not return in time" / "timed out" search
  warnings, so no provider hit MA's 8 s soft search timeout
  (`SEARCH_PROVIDER_SOFT_TIMEOUT`).

## Hypotheses

1. A provider's search is slow (MA waits up to 8 s per provider). Weakened:
   no soft-timeout warning logged.
2. Time is spent outside MA search: HA's `music_assistant.play_media`, MA's
   `play_media` resolve path, or retries.

## Measurements

Per layer, fresh nonsense names so no search cache answers (08:35–08:37):

| Layer | Call | Time to fail |
|---|---|---|
| MA | `music/search` "qwfp radio", radio | 1.67 s |
| MA | `music/item_by_name` "qwfpb radio", radio | 1.70 s |
| HA | REST `music_assistant.play_media` "qwfpc/qwfpd radio" | 1.77 s, 1.27 s |
| tool_service | `/music` "zxqv radio" | 9.8 s |
| HA blueprint (fast voice tier) | `assist_pipeline/run` "play zxqvd radio" | 10.06 s |

Hypothesis 1 is out; the ~8 s is added above HA, by both callers.

## Root cause

Both callers verify playback after `play_media` (tenet 6), and both kept
waiting after the call had already failed:

- **tool_service** (`play_music`): on any failure it still polled for
  `playing` to the full `MUSIC_VERIFY_TIMEOUT_S` (8 s). The poll exists for
  the 2026-09-04 case (MA 2.8.8 YT Music 500 that started playback anyway).
- **Blueprint** (`ha/blueprints/mass_assist_kronk.yaml`, change 6): the
  `play_media` step has `continue_on_error`, then a `wait_template` with a
  10 s timeout runs unconditionally. HA does not expose a continued error to
  later steps, so the script cannot tell.

"Could not resolve … to playable media item" is raised by HA's
music_assistant integration (`media_player.py`, `async_play_media`) when name
resolution finds nothing, **before** it calls MA's `play_media`. Nothing is
queued, so no poll can succeed.

## Fix

- tool_service: `_RESOLVE_MISS_RE` matches HA's resolve-miss message; on that
  failure the verify poll is skipped. Every other failure still polls (the
  2026-09-04 case). Tests: `test_music_resolve_miss_fails_without_the_verify_poll`,
  `test_music_other_failure_still_polls` (`tests/test_error_surfacing.py`).
  Deployed with `docker compose up -d --build tool_service` (12:42 UTC).
- Blueprint change 8: `play_media` runs inside a nested `sequence` with
  `continue_on_error`; its second step sets `play_ok: true`, so the flag is
  true only when the call succeeded. The wait runs only when `play_ok`; the
  result goes to a `started` variable declared at top level (HA 2026.5
  propagates it out of the `if`; both behaviours checked with an ad-hoc
  `execute_script` first). Cost: the blueprint cannot see the error text, so
  a call that fails and then plays anyway is reported as a failure while the
  music starts. Deployed by `docker cp` (ownership kept 1000:1000) +
  `automation.reload`; no HA restart.

After (08:43–08:46):

| Path | Request | Before | After |
|---|---|---|---|
| tool_service `/music` | "zxqvb radio" (miss) | 9.8 s | 1.55 s |
| tool_service `/music` | "Santana radio" (hit) | 2.2 s | 1.38 s |
| `/api/chat` shim (Kronk tier) | "play zxqvc radio on the office speaker" | — | 4.39 s total |
| blueprint | "play zxqvf radio" (miss) | 10.06 s | 0.09 s (trace: play_media failed in 1 ms, "I couldn't play zxqvf radio.") |
| blueprint | "play Mushroom Jazz radio" (hit) | — | 1.26 s |

## What would have caught it sooner

A latency assertion on the failure path. The live checks timed hits; misses
were checked for the right sentence, not the time to speak it. ROADMAP item 8
(voice smoke test) should time a miss as well as a hit.

## Side finding (open): first artist play after an MA restart

"play the artist Orbital" through the blueprint at 12:45:25 UTC, shortly
after the kronk.8 restart: the `play_media` call itself took **112 s**
(trace: `action/3/sequence/0` 12:45:25.840 → 12:47:17.731). MA log: `Fetching
tracks to play for artist Orbital (selection: all_tracks, order:
shuffled_tracks)`, then nothing until playback. The same play via HA REST two
minutes later: 3.54 s. The blueprint is `mode: single`, so voice music
requests are dropped while it runs.

Follow-up (read-only agent, same day): not the restart. MA's caches are
persistent (`cache.db` on the volume); a probe after the 08:51 restart
answered in 0.15 s. It was the first-ever resolve of this YouTube Music
artist: `default_enqueue_select_artist=all_tracks`, YTM lacks
`ARTIST_TRACKS`, so MA core lists all 74 releases and fetches each one
sequentially (`artists.py:817`), plus a second `get_album` per release for
the album backfill (`albums.py:984-1004`) — 74 `get_album_tracks` cache rows
written 08:45:28–08:47:16, mean gap 1.48 s. Artists with no in-library
mapping are forced to `all_tracks` regardless of the setting
(`media_resolver.py:186-190`). Options and costs are in that report; the
cheapest is `default_enqueue_select_artist=prefer_library` in MA's UI.
Also: tool_service's `HA_WS_TIMEOUT_S = 20` would fail a cold Kronk-tier
artist play at 20 s while MA keeps going.

Applied 09:20 EDT (operator): MA core config `player_queues`
`default_enqueue_select_artist` `all_tracks` → `prefer_library` via
`config/core/save` (no restart; rollback: set it back to `all_tracks`).
Verified: "Orbital" artist play 0.33 s, MA log `selection: prefer_library`,
local track `library://track/10892`. Artists with no in-library mapping are
still forced to `all_tracks` (open; options 2–4 in the agent report).
