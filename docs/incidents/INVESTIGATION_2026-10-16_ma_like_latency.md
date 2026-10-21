# Investigation 2026-10-16 — "thumbs up" by voice reports failure, but the like lands

Status: **fixed 2026-10-16 (option 1, no wait).** Operator decision: thumbs
up replies at once and the like runs in the background; a failure pushes
the operator's phone (`notify_operator`, tag `kronk-music-rate`) so the
household never hears it. Verified live: "thumbs up" via
`assist_pipeline/run` answered in 4.1 s; the background like logged
`thumbs up landed: Booty Swing by Parov Stelar` and MA showed
`favorite: True`; a deliberate failure (bogus `pandora://track/TR:0`) logged
`thumbs up failed ... The requested media item could not be found.` and
the push was accepted by HA. Upstream ask (background metadata scan) is a
ROADMAP follow-up under item 16. Found during the live test of the Pandora
`set_favorite` rework, before the operator's own "Okay Nabu" test.

## Symptom

"thumbs up" through HA's Assist pipeline (text, `assist_pipeline/run`, default
pipeline) with a Pandora station playing on the Kitchen Voice PE answered,
after 23.5 s:

> I couldn't rate that. Music Assistant is unreachable (TimeoutError).

tool_service logged `POST /music/rate HTTP/1.1" 502 Bad Gateway`.

Afterwards MA showed the track ("Summers Past", `pandora://track/TR:75117112`)
in the library as `library://track/15351` with `favorite: True`. The rating
went through; the reply was a false failure.

## Context

Same session, earlier:

- MA moved from `kronk/music-assistant:2.11.0b2-kronk.1` to
  `kronk/music-assistant:2.11.0b5-kronk.1` (stock 2.11.0b5 plus the Pandora
  `set_favorite` patch; backup
  `~/backups/ma/ma-config-2.11.0b2-20261016-155012-quiesced.tgz`; DB migrated
  58 → 68).
- `/music/rate` switched from our `pandora/feedback` command to MA's own
  `music/favorites/set_item` (like = True, dislike = False).
- Direct `/music/rate` calls on "Teardrop" took about a second for both thumbs
  up and thumbs down. That track had already been played and was probably in
  the library already (not confirmed).

## Timeline (local, EDT)

- 15:53:27 MA 2.11.0b5 starts; Pandora authenticates at 15:53:49.
- 15:55:18 / 15:55:48 direct thumbs up / thumbs down on "Teardrop": 200, the
  skip is verified.
- 15:56:32.772 Assist "thumbs up" reaches MA (`WebSocket client authenticated
  as drew`).
- 15:56:33.620 `[music_assistant.LRCLIB] Attempt 1/5 failed: LRCLIB request
  failed` / `Retrying in 4.6 seconds...`
- ~15:56:53 tool_service's 20 s `ma_command` read timeout fires; the voice
  reply is the failure above.
- Later: the item has `favorite: True`. MA kept going after the client hung up.

## Measurements

A like (`music/favorites/set_item`, favorite=True) on a fresh Pandora track,
with no client timeout:

| Track | Time to reply |
|---|---|
| Kisses in the Wind (`TR:206238165`) | 31.1 s |
| Chrysalis (`TR:213504978`) | 24.6 s |

LRCLIB answers from the host in 0.23 s (`curl lrclib.net/api/get`), so its
retry accounts for about 5 s, not the bulk of the time.

## Root cause

MA 2.11.0b5 `controllers/music/controller.py`:

- `set_item_favorite`: when the item is not yet in the library and
  favorite=True, it awaits `self.add_item_to_library(full_item)` **before**
  storing the favorite or calling the provider's `set_favorite`.
- `add_item_to_library` ends with `await
  self.mass.metadata.update_metadata(library_item, overwrite_existing)` (line
  1627), which is a full metadata scan across every metadata provider
  (MusicBrainz, Wikipedia, fanart.tv, TheAudioDB, Last.fm, LRCLIB, ...).
- A dislike does not take this path (it adds the library row through the
  media controller directly), so thumbs down stays fast.

So every like on a Pandora station track (almost always a new track) blocks
for 25–31 s, past tool_service's 20 s `ma_command` read timeout and past the
voice latency budget (~15–25 s worst case, tenet 12).

## Hypotheses tried

- Pandora 400 at startup (`Invalid fragment data ... HTTP 400` right after a
  stream takeover from the old container): unrelated. Playback worked on the
  next request.
- LRCLIB outage: partial. One retry on two of the likes; the host reaches
  LRCLIB fine. Not the main cost.
- Websocket/auth problem: no. Auth succeeded and the command completed
  server-side.

## Fix options (operator decision)

1. **Short wait, then report honestly.** tool_service waits a few seconds for
   MA. If MA hasn't answered, it replies with an honest "still saving"
   sentence and keeps the websocket open in a background task (up to ~2 min)
   so the final result or error is logged and traceable (tenet 7). MA has
   been seen finishing after the client hangs up.
2. Raise the timeout to ~45 s. This breaks the voice latency budget.
3. Upstream: ask MA to run the metadata scan in the background for
   favorites (a `create_task` instead of an `await`), so a like returns in
   about a second. This is the right long-term fix; it is a core change and
   needs its own discussion.

## What would have caught it sooner

A timed like on a *fresh* track in the PR test plan. The first live test
reused a track that was already in the library and took the fast path.

## Addendum 2026-10-21: LRCLIB retries under its own throttle

During the kronk.7 field test (fork f7acc5c62, kitchen speaker), a like on
"Datte Atashino Hero" (`TR:32850922`, new to the library) took 55–70 s to
reach Pandora. The next like, "Cold Sun" (`TR:15074119`), took about 9 s.

| Time | Event |
|---|---|
| 07:39:14.874 | The like reaches MA (`music/favorites/set_item`). |
| 07:39:16.495 | `LRCLIB Attempt 1/5 failed` / `Retrying in 5.1 seconds...` |
| 07:39:46.513 | `Attempt 2/5 failed` / `Retrying in 11.9 seconds...` |
| ~07:40:16 | Attempt 3 succeeds (no log line); the scan finishes. |
| 07:40:09–07:40:24 | tool_service logs `thumbs up landed`. |

Each failed attempt cost about 30 s, not the logged backoff: the LRCLIB
provider throttles itself to one request per 30 s on the public API
(`providers/lrclib/__init__.py`, `ThrottlerManager(rate_limit=1, period=30)`)
and every retry takes a throttle slot (`helpers/throttle_retry.py`,
`throttle_with_retries`). With 5 attempts and backoffs of 5/10/20/40 s, the
worst case is about 130 s for LRCLIB alone. "LRCLIB request failed" is any
`ClientError`, timeout, bad JSON or non-404 error status. Two requests from
inside the MA container at 07:5x returned 404 (1.25 s) and then 503 (1.14 s),
so the public server is intermittently refusing requests.

Other serial steps on the same path, not the cost this time:
`controllers/metadata/controller.py` `Throttler(1, 30)` (one metadata scan
start per 30 s across MA, shared with background refreshes), MusicBrainz
linking (bounded at 60 s, `MUSICBRAINZ_LINK_ITEM_TIMEOUT`), then each of the
nine loaded metadata providers in turn.

Only three of the nine loaded metadata providers take part in a track
scan (`TRACK_METADATA`): LRCLIB, MusicBrainz and TheAudioDB.

Operator decisions, 2026-10-21: LRCLIB disabled in MA as the interim fix
(`config/providers/save`, `enabled: false`, no MA restart; verified
unloaded); the long-term fix is an upstream PR that runs the post-like
scan in the background, planned in `docs/plans/MA_ASYNC_LIKE_METADATA_PLAN.md`.
